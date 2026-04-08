"""
config/settings.py — Configuration loader

Real-world pattern: reads config.yaml as baseline, then overrides with
environment variables (12-factor). In production, also supports:
  - Consul KV store (consul-python client)
  - AWS Parameter Store (boto3)
  - Kubernetes ConfigMaps mounted as files
"""

import os
import yaml
from pathlib import Path


DEFAULTS = {
    "monitoring": {"interval_seconds": 30, "log_window_lines": 200, "correlation_window_minutes": 15},
    "thresholds": {
        "error_rate":           {"warn": 5,  "critical": 15},
        "latency_p95_ms":       {"warn": 500, "critical": 2000},
        "db_pool_usage":        {"warn": 80,  "critical": 95},
        "requests_per_minute":  {"drop_warn": 30, "spike_warn": 100},
        "ai_confidence_min":    70,
    },
    "sources": {
        "logs":     {"path": "logs/spring-boot-sample.log", "format": "spring_boot"},
        "metrics":  {"endpoint": "http://localhost:8080/actuator/prometheus", "simulate": True},
        "database": {"host": "localhost", "port": 3306, "database": "inventory", "simulate": True},
        "github":   {"repo": "capvasqu/inventory-management", "simulate": True},
    },
    "ai": {"model": "claude-sonnet-4-20250514", "max_tokens": 1000},
    "notifications": {
        "github_issues":  {"enabled": True,  "repo": "capvasqu/inventory-management", "min_severity": "HIGH"},
        "slack":          {"enabled": False, "channel": "#alerts-inventory", "min_severity": "MEDIUM"},
        "incident_agent": {"enabled": True,  "endpoint": "http://localhost:8001/trigger", "min_severity": "HIGH"},
    },
    "dashboard": {"websocket_port": 8765, "http_port": 3000},
}


def load_config(path: str = "config/config.yaml") -> dict:
    """
    Load config from YAML file, fall back to defaults if file missing.
    Env vars override file values for sensitive/deployment-specific settings.
    """
    config = dict(DEFAULTS)

    config_path = Path(path)
    if config_path.exists():
        with open(config_path) as f:
            file_config = yaml.safe_load(f) or {}
        # Deep merge file config over defaults
        config = _deep_merge(config, file_config)

    # Env var overrides (real-world: set in .env or deployment platform)
    _apply_env_overrides(config)

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _apply_env_overrides(config: dict):
    """
    Map environment variables to config keys.

    Real-world: these env vars are set in:
      - .env file (local dev)
      - Docker: -e ALERT_ERROR_RATE_WARN=5
      - Kubernetes: envFrom: configMapRef / secretRef
      - GitHub Actions: secrets.* in workflow YAML
    """
    overrides = {
        "ALERT_ERROR_RATE_WARN":   ("thresholds", "error_rate", "warn"),
        "ALERT_ERROR_RATE_CRIT":   ("thresholds", "error_rate", "critical"),
        "ALERT_LATENCY_P95_WARN":  ("thresholds", "latency_p95_ms", "warn"),
        "ALERT_LATENCY_P95_CRIT":  ("thresholds", "latency_p95_ms", "critical"),
        "ALERT_DB_POOL_WARN":      ("thresholds", "db_pool_usage", "warn"),
        "ALERT_DB_POOL_CRIT":      ("thresholds", "db_pool_usage", "critical"),
        "ALERT_AI_CONFIDENCE_MIN": ("thresholds", "ai_confidence_min"),
        "MONITORING_INTERVAL":     ("monitoring", "interval_seconds"),
        "GITHUB_REPO":             ("sources", "github", "repo"),
    }
    for env_key, config_path in overrides.items():
        val = os.getenv(env_key)
        if val is not None:
            node = config
            for part in config_path[:-1]:
                node = node.setdefault(part, {})
            try:
                node[config_path[-1]] = int(val)
            except ValueError:
                node[config_path[-1]] = val
