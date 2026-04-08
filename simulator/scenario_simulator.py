"""
simulator/scenario_simulator.py — Test scenario definitions

Provides the same 5 scenarios as Project 5 (incident-agent),
plus a ci_failure scenario specific to this project.

Usage: called automatically by main.py when --scenario flag is used.
"""

SCENARIOS = {
    "normal": {
        "name":        "normal",
        "description": "Normal traffic — baseline monitoring",
        "expected_severity": "LOW",
    },
    "high_error_rate": {
        "name":        "high_error_rate",
        "description": "High error rate — NullPointerException spike in ProductService",
        "expected_severity": "HIGH",
        "notes":       "Correlates with recent commit a423f1b (ProductService refactor)",
    },
    "db_exhaustion": {
        "name":        "db_exhaustion",
        "description": "DB pool exhausted — HikariCP connection leak from bulk import",
        "expected_severity": "CRITICAL",
        "notes":       "trigger_incident_agent should be True",
    },
    "latency_spike": {
        "name":        "latency_spike",
        "description": "Latency spike — full table scan on stock_movements (missing index)",
        "expected_severity": "MEDIUM",
        "notes":       "Fix: CREATE INDEX idx_movement_date ON stock_movements(movement_date)",
    },
    "stock_bug": {
        "name":        "stock_bug",
        "description": "Stock business rule violation — BUG #11 and BUG #12 active",
        "expected_severity": "HIGH",
        "notes":       "Known bugs from inventory-management — CI was failing but deploy proceeded",
    },
    "ci_failure": {
        "name":        "ci_failure",
        "description": "CI failure — broken build deployed to production",
        "expected_severity": "HIGH",
        "notes":       "Liquibase migration pending — app started in degraded state",
    },
}


class ScenarioSimulator:
    """Simple scenario registry — used by main.py for --list-scenarios."""

    @staticmethod
    def get(name: str) -> dict:
        return SCENARIOS.get(name, SCENARIOS["normal"])

    @staticmethod
    def list_all() -> dict:
        return SCENARIOS
