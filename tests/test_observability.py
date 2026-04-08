"""
tests/test_observability.py — Unit tests (no real API calls)

Run with: python -m pytest tests/ -v

Same pattern as Project 5: tests verify logic without hitting
Anthropic API, GitHub API, or any external service.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from config.settings import load_config, _deep_merge
from sources.log_source import LogSource, SIMULATED_LOGS
from sources.metrics_source import MetricsSource
from sources.database_source import DatabaseSource
from correlator.correlator import EventCorrelator, _build_timeline
from simulator.scenario_simulator import ScenarioSimulator, SCENARIOS


# ── HELPERS ───────────────────────────────────────────────────────────────────

def default_config():
    return load_config("config/config.yaml")


def fake_evidence(scenario="normal"):
    """Build minimal evidence dict for testing correlator/notifier."""
    return {
        "scenario":   scenario,
        "timestamp":  "2024-01-15T14:23:45",
        "total_events": 10,
        "sources":    ["logs", "metrics", "database", "github"],
        "logs":     {"error_rate": 0.8, "error_count": 1, "warn_count": 2, "raw_lines": [], "events": []},
        "metrics":  {"metrics": {"error_rate_pct": 0.8, "latency_p95_ms": 187, "requests_per_min": 142}, "alerts": [], "events": []},
        "database": {"db_metrics": {"pool_usage_pct": 22, "pool_active": 11, "pool_max": 50, "slow_queries": 0, "avg_query_ms": 18, "deadlocks": 0}, "pool_status": "OK", "events": []},
        "github":   {"github_data": {"recent_commits": [], "open_prs": [], "failing_runs": 0, "last_deploy_min": 480}, "events": []},
    }


def fake_analysis(severity="LOW"):
    return {
        "severity":               severity,
        "anomalies":              [],
        "correlations":           [],
        "confidence":             90,
        "trigger_incident_agent": severity in ("HIGH", "CRITICAL"),
        "explanation":            f"Test explanation for {severity}",
    }


# ── CONFIG ────────────────────────────────────────────────────────────────────

class TestConfig:
    def test_defaults_loaded(self):
        config = default_config()
        assert "thresholds" in config
        assert "monitoring" in config
        assert config["thresholds"]["error_rate"]["warn"] == 5

    def test_deep_merge_overrides(self):
        base = {"a": {"b": 1, "c": 2}}
        override = {"a": {"b": 99}}
        result = _deep_merge(base, override)
        assert result["a"]["b"] == 99
        assert result["a"]["c"] == 2  # preserved

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("ALERT_ERROR_RATE_WARN", "10")
        config = load_config("config/config.yaml")
        assert config["thresholds"]["error_rate"]["warn"] == 10

    def test_all_scenarios_in_simulator(self):
        for name in ["normal", "high_error_rate", "db_exhaustion", "latency_spike", "stock_bug", "ci_failure"]:
            s = ScenarioSimulator.get(name)
            assert s["name"] == name
            assert "expected_severity" in s


# ── LOG SOURCE ────────────────────────────────────────────────────────────────

class TestLogSource:
    def test_collect_normal(self):
        config = default_config()
        source = LogSource(config)
        result = asyncio.run(source.collect("normal"))
        assert result["source"] == "spring_boot_logs"
        assert result["error_rate"] < 5  # normal should have low error rate

    def test_collect_high_error(self):
        config = default_config()
        source = LogSource(config)
        result = asyncio.run(source.collect("high_error_rate"))
        assert result["error_count"] > 0
        assert result["error_rate"] > 50  # most lines are ERROR

    def test_all_scenarios_parse(self):
        config = default_config()
        source = LogSource(config)
        for scenario in SIMULATED_LOGS:
            result = asyncio.run(source.collect(scenario))
            assert isinstance(result["events"], list)

    def test_parse_spring_log_line(self):
        config = default_config()
        source = LogSource(config)
        line = "2024-01-15 14:23:45.001 ERROR 1234 --- [nio-8080-exec-1] c.d.i.service.ProductService : NullPointerException"
        parsed = source._parse_line(line)
        assert parsed is not None
        assert parsed["level"] == "ERROR"
        assert "ProductService" in parsed["logger"]

    def test_unparseable_line_returns_none(self):
        config = default_config()
        source = LogSource(config)
        assert source._parse_line("garbage line without pattern") is None


# ── METRICS SOURCE ────────────────────────────────────────────────────────────

class TestMetricsSource:
    def test_normal_metrics_no_alerts(self):
        config = default_config()
        source = MetricsSource(config)
        result = asyncio.run(source.collect("normal"))
        assert result["source"] == "prometheus_actuator"
        assert len(result["alerts"]) == 0

    def test_high_error_triggers_alert(self):
        config = default_config()
        source = MetricsSource(config)
        result = asyncio.run(source.collect("high_error_rate"))
        alert_types = [a["type"] for a in result["alerts"]]
        assert "error_rate" in alert_types

    def test_db_exhaustion_latency_alert(self):
        config = default_config()
        source = MetricsSource(config)
        result = asyncio.run(source.collect("db_exhaustion"))
        assert any(a["severity"] == "CRITICAL" for a in result["alerts"])

    def test_custom_threshold_respected(self):
        """Lower WARN threshold should trigger alert on normal traffic."""
        config = default_config()
        config["thresholds"]["error_rate"]["warn"] = 0.1  # very low
        source = MetricsSource(config)
        result = asyncio.run(source.collect("normal"))
        assert any(a["type"] == "error_rate" for a in result["alerts"])


# ── DATABASE SOURCE ───────────────────────────────────────────────────────────

class TestDatabaseSource:
    def test_normal_pool_ok(self):
        config = default_config()
        source = DatabaseSource(config)
        result = asyncio.run(source.collect("normal"))
        assert result["pool_status"] == "OK"

    def test_db_exhaustion_critical(self):
        config = default_config()
        source = DatabaseSource(config)
        result = asyncio.run(source.collect("db_exhaustion"))
        assert result["pool_status"] == "CRITICAL"

    def test_latency_spike_warn(self):
        config = default_config()
        source = DatabaseSource(config)
        result = asyncio.run(source.collect("latency_spike"))
        assert result["pool_status"] in ("WARN", "CRITICAL")
        assert result["db_metrics"]["slow_queries"] > 0


# ── CORRELATOR ────────────────────────────────────────────────────────────────

class TestCorrelator:
    def test_no_correlations_on_normal(self):
        config = default_config()
        corr = EventCorrelator(config)
        ev = fake_evidence("normal")
        an = fake_analysis("LOW")
        result = corr.correlate(ev, an)
        assert isinstance(result["patterns"], list)
        assert result["severity_boosted"] is False

    def test_deploy_error_correlation(self):
        config = default_config()
        corr = EventCorrelator(config)
        ev = fake_evidence("high_error_rate")
        ev["github"]["github_data"]["last_deploy_min"] = 6
        an = fake_analysis("HIGH")
        an["anomalies"] = [{"type": "error_spike", "title": "Error spike"}]
        result = corr.correlate(ev, an)
        rule_names = [p["rule"] for p in result["patterns"]]
        assert "deploy_before_errors" in rule_names
        assert result["severity_boosted"] is True

    def test_timeline_built(self):
        ev = fake_evidence("high_error_rate")
        ev["github"]["github_data"]["recent_commits"] = [
            {"sha": "abc", "message": "refactor: something", "author": "carlos", "minutes_ago": 6}
        ]
        ev["github"]["github_data"]["last_deploy_min"] = 6
        ev["metrics"]["metrics"]["error_rate_pct"] = 18
        timeline = _build_timeline(ev)
        types = [e["type"] for e in timeline]
        assert "commit" in types
        assert "deploy" in types
        assert "error_spike" in types


# ── SCENARIO SIMULATOR ────────────────────────────────────────────────────────

class TestScenarioSimulator:
    def test_all_scenarios_have_expected_severity(self):
        for name, scenario in SCENARIOS.items():
            assert "expected_severity" in scenario
            assert scenario["expected_severity"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_unknown_scenario_returns_normal(self):
        result = ScenarioSimulator.get("nonexistent_scenario")
        assert result["name"] == "normal"

    def test_list_all_returns_dict(self):
        all_s = ScenarioSimulator.list_all()
        assert len(all_s) >= 5
