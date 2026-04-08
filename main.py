"""
inventory-observability — Main Orchestrator
Project 7: Intelligent Observability with Anomaly Detection

Architecture:
  Ingester (multi-source) → Detector (Claude API) → Correlator → Notifier → Dashboard (WebSocket)

Real-world entry point: run this as a service (systemd, Docker, Kubernetes CronJob).
"""

import argparse
import asyncio
import signal
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ingester.pipeline import IngestionPipeline
from detector.anomaly_detector import AnomalyDetector
from correlator.correlator import EventCorrelator
from notifier.notifier import Notifier
from simulator.scenario_simulator import ScenarioSimulator
from config.settings import load_config

load_dotenv()
console = Console()

BANNER = """
[bold cyan]╔══════════════════════════════════════════════════════════╗
║     inventory-observability — Project 7                  ║
║     Intelligent Observability + AI Anomaly Detection     ║
╚══════════════════════════════════════════════════════════╝[/bold cyan]
"""

SCENARIOS = {
    "normal":         "Normal traffic — baseline monitoring",
    "high_error_rate":"High error rate — NullPointerException spike in ProductService",
    "db_exhaustion":  "DB pool exhausted — HikariCP connection leak",
    "latency_spike":  "Latency spike — slow query on stock_movements (missing index)",
    "stock_bug":      "Stock bug — negative stock quantity (BUG #11 + BUG #12 active)",
    "ci_failure":     "CI failure — deploy of broken build to production",
}


def list_scenarios():
    console.print(BANNER)
    table = Table(title="Available Scenarios", show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan", width=20)
    table.add_column("Description")
    for name, desc in SCENARIOS.items():
        table.add_row(name, desc)
    console.print(table)
    console.print("\n[dim]Usage: python main.py --scenario high_error_rate[/dim]")
    console.print("[dim]       python main.py --scenario db_exhaustion --dry-run[/dim]")
    console.print("[dim]       python main.py --continuous  (monitor every N seconds)[/dim]\n")


async def run_once(scenario: str, config: dict, dry_run: bool = False):
    """
    Single observation cycle:
      1. Ingest data from all sources
      2. Detect anomalies with Claude API
      3. Correlate events across services
      4. Notify (console + GitHub Issue + Slack + incident-agent)
    """
    console.print(f"\n[bold]► Starting observation cycle[/bold] — scenario: [cyan]{scenario}[/cyan]")
    console.print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. INGESTION
    pipeline = IngestionPipeline(config)
    evidence = await pipeline.ingest(scenario)
    console.print(f"[green]✓[/green] Ingested {evidence['total_events']} events from {len(evidence['sources'])} sources")

    # 2. ANOMALY DETECTION (Claude API)
    if dry_run:
        console.print("[yellow]  [DRY RUN] Skipping Claude API call[/yellow]")
        analysis = {"anomalies": [], "severity": "LOW", "dry_run": True}
    else:
        detector = AnomalyDetector(config)
        analysis = await detector.analyze(evidence)
        sev_color = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "CRITICAL": "bold red"}.get(analysis.get("severity","LOW"), "white")
        console.print(f"[green]✓[/green] AI analysis complete — severity: [{sev_color}]{analysis.get('severity','N/A')}[/{sev_color}]")

    # 3. CORRELATION
    correlator = EventCorrelator(config)
    correlation = correlator.correlate(evidence, analysis)
    console.print(f"[green]✓[/green] Correlated {len(correlation.get('patterns', []))} temporal patterns")

    # 4. NOTIFICATION
    notifier = Notifier(config, dry_run=dry_run)
    await notifier.notify(evidence, analysis, correlation)

    return {"evidence": evidence, "analysis": analysis, "correlation": correlation}


async def run_continuous(config: dict, dry_run: bool = False):
    """
    Continuous monitoring loop.
    Real-world: replace with actual log streaming (Filebeat, Kafka consumer, etc.)
    """
    interval = config.get("monitoring", {}).get("interval_seconds", 30)
    console.print(f"\n[bold cyan]► Continuous monitoring started[/bold cyan]")
    console.print(f"  Analysis interval: [cyan]{interval}s[/cyan]")
    console.print(f"  Press Ctrl+C to stop\n")

    cycle = 0
    while True:
        cycle += 1
        console.rule(f"[dim]Cycle #{cycle}[/dim]")
        scenario = "normal"  # In production: determined by actual log analysis
        await run_once(scenario, config, dry_run)
        console.print(f"\n[dim]Next analysis in {interval}s...[/dim]")
        await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(
        description="inventory-observability — Intelligent log monitoring with AI anomaly detection"
    )
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="Scenario to simulate")
    parser.add_argument("--list-scenarios", action="store_true", help="List available scenarios")
    parser.add_argument("--continuous", action="store_true", help="Run in continuous monitoring mode")
    parser.add_argument("--dry-run", action="store_true", help="Skip Claude API and notification calls")
    parser.add_argument("--config", default="config/config.yaml", help="Path to config file")
    args = parser.parse_args()

    console.print(BANNER)

    if args.list_scenarios:
        list_scenarios()
        return

    config = load_config(args.config)

    def handle_sigint(sig, frame):
        console.print("\n[yellow]Stopping observability agent...[/yellow]")
        sys.exit(0)
    signal.signal(signal.SIGINT, handle_sigint)

    if args.continuous:
        asyncio.run(run_continuous(config, args.dry_run))
    elif args.scenario:
        result = asyncio.run(run_once(args.scenario, config, args.dry_run))
        if result["analysis"].get("severity") in ("HIGH", "CRITICAL"):
            console.print(Panel(
                "[bold red]⚠ HIGH/CRITICAL anomaly detected[/bold red]\n"
                "→ incident-agent (Project 5) should be triggered\n"
                "→ GitHub Issue created (if not --dry-run)\n"
                "→ Slack notification sent (if configured)",
                title="Action Required", border_style="red"
            ))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
