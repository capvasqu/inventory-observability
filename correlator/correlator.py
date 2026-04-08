"""
correlator/correlator.py — Temporal event correlation engine

Finds causal relationships between events across multiple sources.
Example: "error rate spiked 2 minutes after last deploy"

Real-world:
  - In production this is often done by the AI model itself (we also do it here)
  - Dedicated correlation: Jaeger (distributed tracing), Elastic APM, Datadog APM
  - For custom correlation: store events in TimescaleDB, query with window functions
  - Alert correlation: Prometheus AlertManager grouping rules
"""

from datetime import datetime


CORRELATION_RULES = [
    {
        "name": "deploy_before_errors",
        "description": "Deploy within last 30 minutes correlates with error spike",
        "condition": lambda ev, an: (
            ev.get("github", {}).get("github_data", {}).get("last_deploy_min", 999) < 30
            and any(a.get("type") == "error_spike" for a in an.get("anomalies", []))
        ),
        "pattern": "Deploy → Error spike correlation detected",
        "severity_boost": True,
    },
    {
        "name": "db_pool_with_latency",
        "description": "High DB pool usage co-occurring with latency spike",
        "condition": lambda ev, an: (
            ev.get("database", {}).get("db_metrics", {}).get("pool_usage_pct", 0) > 70
            and ev.get("metrics", {}).get("metrics", {}).get("latency_p95_ms", 0) > 500
        ),
        "pattern": "DB pool saturation → Latency cascade",
        "severity_boost": False,
    },
    {
        "name": "slow_query_with_pool",
        "description": "Slow queries causing pool exhaustion",
        "condition": lambda ev, an: (
            ev.get("database", {}).get("db_metrics", {}).get("slow_queries", 0) > 3
            and ev.get("database", {}).get("db_metrics", {}).get("pool_active", 0) > 40
        ),
        "pattern": "Slow queries holding connections → Pool starvation",
        "severity_boost": True,
    },
    {
        "name": "ci_failure_before_errors",
        "description": "Failing CI runs before deploy correlates with prod errors",
        "condition": lambda ev, an: (
            ev.get("github", {}).get("github_data", {}).get("failing_runs", 0) > 0
            and ev.get("metrics", {}).get("metrics", {}).get("error_rate_pct", 0) > 5
        ),
        "pattern": "CI failure ignored → Production errors match test failures",
        "severity_boost": True,
    },
]


class EventCorrelator:
    """
    Applies deterministic correlation rules on top of AI analysis.

    Real-world alternatives:
      - Distributed tracing: Jaeger, Zipkin (trace_id links services)
      - APM: Datadog APM, Elastic APM (automatic correlation)
      - Manual: TimescaleDB + window function queries over event tables
    """

    def __init__(self, config: dict):
        self.config = config
        self.window_minutes = config.get("monitoring", {}).get("correlation_window_minutes", 15)

    def correlate(self, evidence: dict, analysis: dict) -> dict:
        patterns = []
        boosts = []

        for rule in CORRELATION_RULES:
            try:
                if rule["condition"](evidence, analysis):
                    patterns.append({
                        "rule":    rule["name"],
                        "pattern": rule["pattern"],
                        "boost":   rule["severity_boost"],
                    })
                    if rule["severity_boost"]:
                        boosts.append(rule["name"])
            except Exception:
                pass

        # Merge AI-detected correlations
        ai_correlations = analysis.get("correlations", [])

        timeline = _build_timeline(evidence)

        return {
            "patterns":          patterns,
            "severity_boosted":  len(boosts) > 0,
            "boost_reasons":     boosts,
            "ai_correlations":   ai_correlations,
            "timeline":          timeline,
            "window_minutes":    self.window_minutes,
            "real_world_config": (
                "Distributed tracing: add spring-cloud-sleuth + zipkin to pom.xml\n"
                "Trace propagation: X-B3-TraceId header across service calls\n"
                "Storage: Jaeger all-in-one Docker container (dev) or Jaeger Operator (K8s)\n"
                "Query: jaeger-query UI at http://localhost:16686"
            ),
        }


def _build_timeline(evidence: dict) -> list:
    """Build a simplified timeline of notable events for reporting."""
    events = []

    gh = evidence.get("github", {}).get("github_data", {})
    for commit in gh.get("recent_commits", []):
        events.append({
            "minutes_ago": commit["minutes_ago"],
            "type":        "commit",
            "description": f"Commit {commit['sha']}: {commit['message']}",
        })
    if gh.get("last_deploy_min", 999) < 60:
        events.append({
            "minutes_ago": gh["last_deploy_min"],
            "type":        "deploy",
            "description": "Application deployed to production",
        })

    db = evidence.get("database", {}).get("db_metrics", {})
    if db.get("slow_queries", 0) > 0:
        events.append({
            "minutes_ago": 0,
            "type":        "db_slow_query",
            "description": f"{db['slow_queries']} slow queries detected (avg {db['avg_query_ms']}ms)",
        })

    met = evidence.get("metrics", {}).get("metrics", {})
    if met.get("error_rate_pct", 0) > 5:
        events.append({
            "minutes_ago": 0,
            "type":        "error_spike",
            "description": f"Error rate: {met['error_rate_pct']}%",
        })

    events.sort(key=lambda e: e["minutes_ago"], reverse=True)
    return events
