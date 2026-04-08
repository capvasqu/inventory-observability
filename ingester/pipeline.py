"""
ingester/pipeline.py — Multi-source ingestion pipeline

Collects evidence from all configured sources in parallel.
Each source is independent — failure in one does not block others.

Real-world equivalents:
  - This module replaces: Filebeat + Metricbeat + custom exporters
  - In production: sources run as separate processes/containers,
    publish to a message queue (Kafka, RabbitMQ, AWS SQS)
  - This simplified version polls sources synchronously for demo purposes
"""

import asyncio
from datetime import datetime
from rich.console import Console

from sources.log_source import LogSource
from sources.metrics_source import MetricsSource
from sources.database_source import DatabaseSource
from sources.github_source import GitHubSource

console = Console()


class IngestionPipeline:
    """
    Coordinates parallel ingestion from all configured sources.

    Real-world architecture:
      ┌──────────────┐   ┌─────────────┐   ┌──────────────┐   ┌───────────┐
      │  Filebeat    │   │ Prometheus  │   │  MySQL       │   │ GitHub    │
      │  (log tail)  │   │ (metrics)   │   │  exporter    │   │ API       │
      └──────┬───────┘   └──────┬──────┘   └──────┬───────┘   └─────┬─────┘
             │                  │                  │                 │
             └──────────────────┴──────────────────┴─────────────────┘
                                         │
                                  Kafka / SQS topic
                                         │
                                  IngestionPipeline (consumer)
    """

    def __init__(self, config: dict):
        self.config = config
        self.sources = {
            "logs":     LogSource(config),
            "metrics":  MetricsSource(config),
            "database": DatabaseSource(config),
            "github":   GitHubSource(config),
        }

    async def ingest(self, scenario: str) -> dict:
        """
        Collect evidence from all sources, return structured evidence dict.
        Failures are captured per-source without halting the pipeline.
        """
        start = datetime.now()
        results = {}

        # Run all sources concurrently
        tasks = {
            name: asyncio.create_task(self._safe_collect(name, source, scenario))
            for name, source in self.sources.items()
        }
        for name, task in tasks.items():
            results[name] = await task

        elapsed = (datetime.now() - start).total_seconds()
        total_events = sum(
            len(r.get("events", [])) for r in results.values() if isinstance(r, dict)
        )

        return {
            "scenario":     scenario,
            "timestamp":    start.isoformat(),
            "elapsed_ms":   round(elapsed * 1000),
            "sources":      list(results.keys()),
            "total_events": total_events,
            **results,
        }

    async def _safe_collect(self, name: str, source, scenario: str) -> dict:
        """Wrap source collection with error handling."""
        try:
            data = await source.collect(scenario)
            console.print(f"  [dim]↳ {name}: {len(data.get('events', []))} events[/dim]")
            return data
        except Exception as e:
            console.print(f"  [yellow]⚠ {name} source failed: {e}[/yellow]")
            return {"error": str(e), "events": []}
