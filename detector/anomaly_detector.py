"""
detector/anomaly_detector.py — AI-powered anomaly detection

Uses Claude API (Anthropic SDK) to analyze multi-source evidence
and produce a structured anomaly report.

Real-world:
  - This runs every N seconds (configured in config.yaml: monitoring.interval_seconds)
  - Evidence is structured to maximize Claude's context efficiency
  - Response is strict JSON — parsed and validated before use
  - Cost: ~$0.003–0.015 per analysis (claude-sonnet-4-6, ~1000 tokens in/out)
  - In production: wrap with retry + circuit breaker (tenacity library)

API call location:
  ANTHROPIC_API_KEY → .env (local) or Kubernetes Secret (production)
  Model: claude-sonnet-4-20250514
  Endpoint: https://api.anthropic.com/v1/messages
"""

import json
import os
import anthropic
from rich.console import Console

console = Console()

SYSTEM_PROMPT = """You are an expert SRE (Site Reliability Engineer) analyzing observability data
from a Spring Boot 3.2 + MySQL 8 inventory management system.

Your job: analyze the provided multi-source evidence and detect anomalies.

ALWAYS respond with ONLY valid JSON matching this exact schema:
{
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "anomalies": [
    {
      "type": "error_spike|latency_spike|db_issue|business_rule|deployment|correlation",
      "title": "Short title (max 60 chars)",
      "description": "What is happening",
      "evidence": "Specific data points that indicate this anomaly",
      "root_cause": "Most likely cause",
      "pattern": "Pattern detected (e.g., 'error appears 2min after deploy')",
      "recommendation": "Concrete fix or investigation step"
    }
  ],
  "correlations": ["string describing temporal/causal correlations between events"],
  "confidence": 0-100,
  "trigger_incident_agent": true|false,
  "explanation": "One paragraph plain-language summary for on-call engineer"
}

Rules:
- severity=CRITICAL: service down or data corruption — trigger_incident_agent=true
- severity=HIGH: SLA breach or error rate >15% — trigger_incident_agent=true
- severity=MEDIUM: degraded performance or elevated errors
- severity=LOW: minor issues, informational
- confidence reflects your certainty (70+ = actionable alert)
- If no anomalies: return severity=LOW, anomalies=[], confidence=95
"""


def _build_prompt(evidence: dict) -> str:
    logs = evidence.get("logs", {})
    metrics = evidence.get("metrics", {})
    db = evidence.get("database", {})
    github = evidence.get("github", {})

    raw_logs = "\n".join(logs.get("raw_lines", []))
    met = metrics.get("metrics", {})
    dbm = db.get("db_metrics", {})
    ghd = github.get("github_data", {})

    prompt = f"""
EVIDENCE — {evidence.get('timestamp', 'N/A')} — Scenario: {evidence.get('scenario', 'unknown')}

=== LOGS (Spring Boot) ===
Error rate: {logs.get('error_rate', 0)}%
Error count: {logs.get('error_count', 0)} | Warn count: {logs.get('warn_count', 0)}
Recent log lines:
{raw_logs}

=== METRICS (Prometheus/Actuator) ===
Error rate p5m: {met.get('error_rate_pct', 0)}%
Latency p95:    {met.get('latency_p95_ms', 0)}ms
Latency p50:    {met.get('latency_p50_ms', 0)}ms
Req/min:        {met.get('requests_per_min', 0)}
CPU:            {met.get('cpu_usage_pct', 0)}%
Heap:           {met.get('heap_usage_pct', 0)}%
Active threads: {met.get('active_threads', 0)}
Metric alerts:  {json.dumps(metrics.get('alerts', []))}

=== DATABASE (MySQL/HikariCP) ===
Pool usage:          {dbm.get('pool_usage_pct', 0)}% ({dbm.get('pool_active', 0)}/{dbm.get('pool_max', 50)} active)
Pool status:         {db.get('pool_status', 'OK')}
Slow queries:        {dbm.get('slow_queries', 0)}
Avg query time:      {dbm.get('avg_query_ms', 0)}ms
Pending connections: {dbm.get('pending_connections', 0)}
Deadlocks:           {dbm.get('deadlocks', 0)}

=== GITHUB (Recent Activity) ===
Recent commits: {json.dumps(ghd.get('recent_commits', []))}
Open PRs:       {json.dumps(ghd.get('open_prs', []))}
Failing CI runs: {ghd.get('failing_runs', 0)}
Last deploy:    {ghd.get('last_deploy_min', 999)} minutes ago

Analyze all evidence above. Return ONLY JSON.
"""
    return prompt.strip()


class AnomalyDetector:
    """
    Calls Claude API with structured evidence and parses anomaly report.

    Real-world deployment:
      - Runs as a scheduled job (APScheduler or Kubernetes CronJob)
      - ANTHROPIC_API_KEY injected as env var from K8s Secret
      - Results stored in PostgreSQL for trend analysis
      - Prometheus counter: anomalies_detected_total{severity="HIGH"}
    """

    def __init__(self, config: dict):
        self.config = config.get("ai", {})
        self.model = self.config.get("model", "claude-sonnet-4-20250514")
        self.max_tokens = self.config.get("max_tokens", 1000)
        self.min_confidence = config.get("thresholds", {}).get("ai_confidence_min", 70)
        # Real-world: ANTHROPIC_API_KEY from .env or Kubernetes Secret
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    async def analyze(self, evidence: dict) -> dict:
        """
        Send evidence to Claude API and parse anomaly JSON response.
        Returns structured analysis dict.
        """
        prompt = _build_prompt(evidence)

        console.print(f"  [dim]→ Calling Claude API ({self.model})...[/dim]")

        # Real-world: add retry with exponential backoff (tenacity library)
        # @retry(wait=wait_exponential(min=1, max=30), stop=stop_after_attempt(3))
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()

        try:
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            analysis = json.loads(raw)
        except json.JSONDecodeError as e:
            console.print(f"  [yellow]⚠ Could not parse Claude JSON response: {e}[/yellow]")
            analysis = {
                "severity":              "LOW",
                "anomalies":             [],
                "correlations":          [],
                "confidence":            0,
                "trigger_incident_agent": False,
                "explanation":           f"Parse error: {e}\nRaw: {raw[:200]}",
                "parse_error":           True,
            }

        analysis["model_used"] = self.model
        analysis["tokens_used"] = {
            "input":  response.usage.input_tokens,
            "output": response.usage.output_tokens,
        }

        # Filter low-confidence results
        if analysis.get("confidence", 0) < self.min_confidence:
            console.print(f"  [dim]Confidence {analysis.get('confidence')}% < threshold {self.min_confidence}% — suppressing alert[/dim]")
            analysis["suppressed"] = True

        return analysis
