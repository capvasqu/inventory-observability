"""
notifier/notifier.py — Multi-channel notification system

Sends anomaly reports to:
  1. Console (rich formatted — always)
  2. GitHub Issues (HIGH/CRITICAL — via GitHub REST API)
  3. Slack (MEDIUM+ — via Incoming Webhook)
  4. incident-agent / Project 5 (HIGH/CRITICAL — HTTP POST)

Real-world:
  - In production, also consider: PagerDuty, OpsGenie, VictorOps
  - All credentials come from environment variables — NEVER hardcode
  - Use exponential backoff for failed webhook calls (tenacity)
"""

import json
import os
import requests
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

SEVERITY_COLORS = {
    "LOW":      "green",
    "MEDIUM":   "yellow",
    "HIGH":     "red",
    "CRITICAL": "bold red",
}

SEVERITY_EMOJI = {
    "LOW":      "✅",
    "MEDIUM":   "⚠️",
    "HIGH":     "🔴",
    "CRITICAL": "🚨",
}

SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _meets_threshold(severity: str, min_severity: str) -> bool:
    return SEVERITY_ORDER.get(severity, 0) >= SEVERITY_ORDER.get(min_severity, 0)


class Notifier:
    def __init__(self, config: dict, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.notif_config = config.get("notifications", {})

    async def notify(self, evidence: dict, analysis: dict, correlation: dict):
        severity = analysis.get("severity", "LOW")

        # 1. Always print to console
        self._print_console_report(evidence, analysis, correlation)

        if self.dry_run:
            console.print("[yellow]  [DRY RUN] Skipping external notifications[/yellow]")
            return

        # 2. GitHub Issue
        gh_cfg = self.notif_config.get("github_issues", {})
        if gh_cfg.get("enabled") and _meets_threshold(severity, gh_cfg.get("min_severity", "HIGH")):
            self._create_github_issue(evidence, analysis, correlation)

        # 3. Slack
        sl_cfg = self.notif_config.get("slack", {})
        if sl_cfg.get("enabled") and _meets_threshold(severity, sl_cfg.get("min_severity", "MEDIUM")):
            self._send_slack(analysis, sl_cfg.get("channel", "#alerts"))

        # 4. incident-agent (Project 5)
        ia_cfg = self.notif_config.get("incident_agent", {})
        if ia_cfg.get("enabled") and analysis.get("trigger_incident_agent", False):
            self._trigger_incident_agent(evidence, analysis, ia_cfg.get("endpoint"))

    # ── CONSOLE ──────────────────────────────────────────────────────────────

    def _print_console_report(self, evidence: dict, analysis: dict, correlation: dict):
        sev = analysis.get("severity", "LOW")
        color = SEVERITY_COLORS.get(sev, "white")
        emoji = SEVERITY_EMOJI.get(sev, "")

        console.print()
        console.print(Panel(
            f"[{color}]{emoji} Severity: {sev}[/{color}]\n"
            f"Confidence: {analysis.get('confidence', 0)}%\n\n"
            f"{analysis.get('explanation', 'No explanation provided.')}",
            title="[bold]AI Analysis Report[/bold]",
            border_style=color,
        ))

        anomalies = analysis.get("anomalies", [])
        if anomalies:
            table = Table(title="Detected Anomalies", box=box.ROUNDED, show_header=True)
            table.add_column("Type", style="cyan", width=16)
            table.add_column("Title", width=36)
            table.add_column("Root Cause")
            for a in anomalies:
                table.add_row(a.get("type",""), a.get("title",""), a.get("root_cause",""))
            console.print(table)

        patterns = correlation.get("patterns", [])
        if patterns:
            console.print("[bold]Correlations:[/bold]")
            for p in patterns:
                boost = " [red][severity boosted][/red]" if p.get("boost") else ""
                console.print(f"  ↳ {p['pattern']}{boost}")

        timeline = correlation.get("timeline", [])
        if timeline:
            console.print("[bold]Timeline:[/bold]")
            for e in timeline:
                console.print(f"  [{e['minutes_ago']}m ago] {e['type']}: {e['description']}")

    # ── GITHUB ───────────────────────────────────────────────────────────────

    def _create_github_issue(self, evidence: dict, analysis: dict, correlation: dict):
        """
        Real-world:
          GITHUB_TOKEN in .env — needs scope: repo
          POST https://api.github.com/repos/{owner}/{repo}/issues
          Same pattern as Project 5 (incident-agent/reporter.py)
        """
        token = os.getenv("GITHUB_TOKEN")
        repo  = self.notif_config.get("github_issues", {}).get("repo", "")

        if not token or not repo:
            console.print("  [yellow]⚠ GitHub: GITHUB_TOKEN or repo not configured — skipping[/yellow]")
            return

        sev = analysis.get("severity", "LOW")
        emoji = SEVERITY_EMOJI.get(sev, "")
        body = self._build_github_body(evidence, analysis, correlation)

        try:
            resp = requests.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept":        "application/vnd.github+json",
                },
                json={
                    "title":  f"{emoji} [{sev}] Anomaly detected — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    "body":   body,
                    "labels": ["observability", "anomaly", sev.lower()],
                },
                timeout=10,
            )
            if resp.status_code == 201:
                issue_url = resp.json().get("html_url", "")
                console.print(f"  [green]✓[/green] GitHub Issue created: {issue_url}")
            else:
                console.print(f"  [yellow]⚠ GitHub Issue failed: {resp.status_code}[/yellow]")
        except Exception as e:
            console.print(f"  [yellow]⚠ GitHub request error: {e}[/yellow]")

    def _build_github_body(self, evidence: dict, analysis: dict, correlation: dict) -> str:
        sev = analysis.get("severity", "LOW")
        anomalies_md = ""
        for a in analysis.get("anomalies", []):
            anomalies_md += f"""
### {a.get('title', 'Anomaly')}
- **Type:** {a.get('type')}
- **Evidence:** {a.get('evidence')}
- **Root cause:** {a.get('root_cause')}
- **Pattern:** {a.get('pattern')}
- **Recommendation:** {a.get('recommendation')}
"""
        patterns_md = "\n".join(f"- {p['pattern']}" for p in correlation.get("patterns", []))

        return f"""## Observability Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Severity:** {sev}
**Confidence:** {analysis.get('confidence', 0)}%
**Scenario:** {evidence.get('scenario', 'unknown')}
**Model:** {analysis.get('model_used', 'N/A')}
**Tokens used:** {analysis.get('tokens_used', {})}

### AI Explanation
{analysis.get('explanation', '')}

## Anomalies Detected
{anomalies_md or '_No anomalies detected_'}

## Temporal Correlations
{patterns_md or '_None detected_'}

## Metrics Snapshot
| Metric | Value |
|---|---|
| Error rate | {evidence.get('metrics', {}).get('metrics', {}).get('error_rate_pct', 'N/A')}% |
| Latency p95 | {evidence.get('metrics', {}).get('metrics', {}).get('latency_p95_ms', 'N/A')}ms |
| DB pool | {evidence.get('database', {}).get('db_metrics', {}).get('pool_usage_pct', 'N/A')}% |

---
*Generated by inventory-observability — Project 7*
*Trigger: automated anomaly detection | incident-agent: {"triggered" if analysis.get("trigger_incident_agent") else "not triggered"}*
"""

    # ── SLACK ─────────────────────────────────────────────────────────────────

    def _send_slack(self, analysis: dict, channel: str):
        """
        Real-world:
          SLACK_WEBHOOK_URL in .env
          Create at: api.slack.com/apps → Incoming Webhooks
          Format: Block Kit (rich formatting with buttons)
        """
        webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        if not webhook_url:
            console.print("  [yellow]⚠ Slack: SLACK_WEBHOOK_URL not set — skipping[/yellow]")
            return

        sev = analysis.get("severity", "LOW")
        emoji = SEVERITY_EMOJI.get(sev, "")
        color_map = {"LOW": "#22c55e", "MEDIUM": "#f59e0b", "HIGH": "#ef4444", "CRITICAL": "#7f1d1d"}

        try:
            payload = {
                "channel":     channel,
                "attachments": [{
                    "color": color_map.get(sev, "#888"),
                    "blocks": [
                        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} [{sev}] Inventory Anomaly Detected"}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Confidence:* {analysis.get('confidence', 0)}%\n{analysis.get('explanation', '')[:500]}"}},
                    ],
                }],
            }
            resp = requests.post(webhook_url, json=payload, timeout=10)
            if resp.status_code == 200:
                console.print(f"  [green]✓[/green] Slack notification sent to {channel}")
            else:
                console.print(f"  [yellow]⚠ Slack failed: {resp.status_code}[/yellow]")
        except Exception as e:
            console.print(f"  [yellow]⚠ Slack error: {e}[/yellow]")

    # ── INCIDENT-AGENT (Project 5) ────────────────────────────────────────────

    def _trigger_incident_agent(self, evidence: dict, analysis: dict, endpoint: str):
        """
        Real-world:
          HTTP POST to incident-agent service endpoint (Project 5)
          In production: service discovery via Kubernetes Service or Consul
          Payload matches incident-agent's expected format (SCENARIOS structure)
        """
        payload = {
            "type":      f"observability_{analysis.get('severity', 'LOW').lower()}",
            "severity":  analysis.get("severity", "LOW"),
            "source":    "inventory-observability",
            "timestamp": evidence.get("timestamp"),
            "metrics": {
                "error_rate":  evidence.get("metrics", {}).get("metrics", {}).get("error_rate_pct"),
                "latency_p95": evidence.get("metrics", {}).get("metrics", {}).get("latency_p95_ms"),
                "db_pool":     evidence.get("database", {}).get("db_metrics", {}).get("pool_usage_pct"),
            },
            "anomalies":   analysis.get("anomalies", []),
            "explanation": analysis.get("explanation", ""),
        }

        console.print(f"  [cyan]→[/cyan] Triggering incident-agent at {endpoint}...")
        try:
            resp = requests.post(endpoint, json=payload, timeout=5)
            if resp.status_code == 200:
                console.print(f"  [green]✓[/green] incident-agent triggered successfully")
            else:
                console.print(f"  [yellow]⚠ incident-agent returned {resp.status_code}[/yellow]")
        except requests.exceptions.ConnectionError:
            console.print(
                f"  [yellow]⚠ incident-agent not reachable at {endpoint}[/yellow]\n"
                f"  [dim]Start it with: cd /d/IA/workspace/incident-agent && python agent/main.py[/dim]"
            )
        except Exception as e:
            console.print(f"  [yellow]⚠ incident-agent error: {e}[/yellow]")
