"""
sources/database_source.py — MySQL / HikariCP database metrics

Real-world setup:
  MySQL slow query log:
    slow_query_log = ON
    slow_query_log_file = /var/log/mysql/mysql-slow.log
    long_query_time = 2
  HikariCP metrics via Actuator:
    GET /actuator/metrics/hikari.connections.active
    GET /actuator/metrics/hikari.connections.pending
  Full metrics: micrometer exposes hikari.* to Prometheus automatically
"""

import random


SIMULATED_DB = {
    "normal": {
        "pool_active":    11,
        "pool_idle":      39,
        "pool_max":       50,
        "pool_usage_pct": 22,
        "slow_queries":   0,
        "pending_connections": 0,
        "avg_query_ms":   18,
        "deadlocks":      0,
    },
    "high_error_rate": {
        "pool_active":    28,
        "pool_idle":      22,
        "pool_max":       50,
        "pool_usage_pct": 56,
        "slow_queries":   2,
        "pending_connections": 3,
        "avg_query_ms":   320,
        "deadlocks":      0,
    },
    "db_exhaustion": {
        "pool_active":    50,
        "pool_idle":      0,
        "pool_max":       50,
        "pool_usage_pct": 100,
        "slow_queries":   18,
        "pending_connections": 23,
        "avg_query_ms":   29800,
        "deadlocks":      2,
    },
    "latency_spike": {
        "pool_active":    40,
        "pool_idle":      10,
        "pool_max":       50,
        "pool_usage_pct": 80,
        "slow_queries":   8,
        "pending_connections": 5,
        "avg_query_ms":   3820,
        "deadlocks":      0,
    },
    "stock_bug": {
        "pool_active":    22,
        "pool_idle":      28,
        "pool_max":       50,
        "pool_usage_pct": 44,
        "slow_queries":   1,
        "pending_connections": 0,
        "avg_query_ms":   95,
        "deadlocks":      0,
    },
    "ci_failure": {
        "pool_active":    2,
        "pool_idle":      2,
        "pool_max":       50,
        "pool_usage_pct": 4,
        "slow_queries":   0,
        "pending_connections": 0,
        "avg_query_ms":   0,
        "deadlocks":      0,
    },
}


class DatabaseSource:
    """
    Ingests MySQL / HikariCP connection pool metrics.

    Real-world modes:
      1. HikariCP metrics via /actuator/metrics/hikari.connections.*
      2. MySQL performance_schema.events_statements_summary_by_digest (slow queries)
      3. Direct JDBC query (requires read-only monitoring user)
    """

    def __init__(self, config: dict):
        self.config = config.get("sources", {}).get("database", {})
        self.thresholds = config.get("thresholds", {}).get("db_pool_usage", {})

    async def collect(self, scenario: str) -> dict:
        base = SIMULATED_DB.get(scenario, SIMULATED_DB["normal"])
        db = {k: v for k, v in base.items()}
        db["pool_usage_pct"] = round(db["pool_usage_pct"] * random.uniform(0.99, 1.05))

        warn = self.thresholds.get("warn", 80)
        crit = self.thresholds.get("critical", 95)
        if db["pool_usage_pct"] >= crit:
            pool_status = "CRITICAL"
        elif db["pool_usage_pct"] >= warn:
            pool_status = "WARN"
        else:
            pool_status = "OK"

        events = [{"metric": k, "value": v} for k, v in db.items()]
        if db["slow_queries"] > 0:
            events.append({"type": "slow_query", "count": db["slow_queries"], "avg_ms": db["avg_query_ms"]})

        return {
            "source":      "mysql_hikaricp",
            "simulated":   self.config.get("simulate", True),
            "events":      events,
            "db_metrics":  db,
            "pool_status": pool_status,
            "real_world_config": (
                "MySQL: slow_query_log=ON, long_query_time=2 in my.cnf\n"
                "HikariCP: spring.datasource.hikari.maximumPoolSize=50 in application.yml\n"
                "Actuator: GET /actuator/metrics/hikari.connections.active\n"
                "Monitoring user: GRANT SELECT ON performance_schema.* TO 'monitor'@'%'"
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────


SIMULATED_GITHUB = {
    "normal": {
        "recent_commits":  [{"sha": "a1b2c3d", "message": "chore: update dependencies", "author": "carlos", "minutes_ago": 120}],
        "open_prs":        [],
        "failing_runs":    0,
        "last_deploy_min": 480,
    },
    "high_error_rate": {
        "recent_commits":  [
            {"sha": "a423f1b", "message": "refactor: ProductService findById method", "author": "carlos", "minutes_ago": 6},
            {"sha": "b891d2e", "message": "feat: add product caching layer", "author": "carlos", "minutes_ago": 45},
        ],
        "open_prs":        [{"number": 34, "title": "Product cache refactor", "status": "merged_6m_ago"}],
        "failing_runs":    0,
        "last_deploy_min": 6,
    },
    "db_exhaustion": {
        "recent_commits":  [
            {"sha": "c789e3f", "message": "feat: bulk import endpoint for stock movements", "author": "carlos", "minutes_ago": 15},
        ],
        "open_prs":        [],
        "failing_runs":    0,
        "last_deploy_min": 15,
    },
    "latency_spike": {
        "recent_commits":  [
            {"sha": "d456f2a", "message": "feat: new warehouse region — 15,000 movements migrated", "author": "carlos", "minutes_ago": 35},
        ],
        "open_prs":        [],
        "failing_runs":    0,
        "last_deploy_min": 35,
    },
    "stock_bug": {
        "recent_commits":  [
            {"sha": "e123a4b", "message": "fix: attempt to resolve BUG #11 stock validation", "author": "carlos", "minutes_ago": 20},
        ],
        "open_prs":        [{"number": 28, "title": "Fix BUG #11 and #12", "status": "open"}],
        "failing_runs":    2,
        "last_deploy_min": 20,
    },
    "ci_failure": {
        "recent_commits":  [
            {"sha": "f890b5c", "message": "feat: liquibase migration for new schema", "author": "carlos", "minutes_ago": 8},
        ],
        "open_prs":        [],
        "failing_runs":    1,
        "last_deploy_min": 8,
    },
}


class GitHubSource:
    """
    Fetches recent commits, PRs, and CI runs from GitHub REST API.
    Correlates code changes with production incidents.

    Real-world:
      GET https://api.github.com/repos/{owner}/{repo}/commits?per_page=10
      GET https://api.github.com/repos/{owner}/{repo}/actions/runs?per_page=5
      Auth: Authorization: Bearer $GITHUB_TOKEN (in .env)
    """

    def __init__(self, config: dict):
        self.config = config.get("sources", {}).get("github", {})

    async def collect(self, scenario: str) -> dict:
        data = SIMULATED_GITHUB.get(scenario, SIMULATED_GITHUB["normal"])

        events = []
        for commit in data["recent_commits"]:
            events.append({
                "type":    "commit",
                "sha":     commit["sha"],
                "message": commit["message"],
                "author":  commit["author"],
                "age_min": commit["minutes_ago"],
            })
        if data["failing_runs"] > 0:
            events.append({"type": "ci_failure", "count": data["failing_runs"]})

        return {
            "source":       "github_api",
            "simulated":    self.config.get("simulate", True),
            "events":       events,
            "github_data":  data,
            "repo":         self.config.get("repo", "capvasqu/inventory-management"),
            "real_world_config": (
                "GITHUB_TOKEN in .env — scope: repo (includes issues + actions)\n"
                "Endpoint: GET https://api.github.com/repos/{owner}/{repo}/commits\n"
                "GitHub Actions: workflow runs via /actions/runs?per_page=5&branch=main"
            ),
        }
