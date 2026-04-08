"""
sources/metrics_source.py — Spring Boot Actuator / Prometheus metrics

Real-world setup:
  1. Add to pom.xml: spring-boot-starter-actuator + micrometer-registry-prometheus
  2. Enable in application.yml:
       management.endpoints.web.exposure.include: prometheus,health,metrics
       management.endpoint.prometheus.enabled: true
  3. Access at: GET http://localhost:8080/actuator/prometheus
  4. Prometheus scrapes this endpoint every 15s (configured in prometheus.yml)
  5. Alert rules in alerts.yaml trigger on PromQL expressions
"""

import random


SIMULATED_METRICS = {
    "normal": {
        "error_rate_pct":    0.8,
        "latency_p95_ms":    187,
        "latency_p50_ms":    45,
        "requests_per_min":  142,
        "cpu_usage_pct":     28,
        "heap_usage_pct":    41,
        "gc_pause_ms":       12,
        "active_threads":    18,
    },
    "high_error_rate": {
        "error_rate_pct":    18.4,
        "latency_p95_ms":    890,
        "latency_p50_ms":    340,
        "requests_per_min":  89,
        "cpu_usage_pct":     72,
        "heap_usage_pct":    67,
        "gc_pause_ms":       89,
        "active_threads":    48,
    },
    "db_exhaustion": {
        "error_rate_pct":    45.0,
        "latency_p95_ms":    30200,
        "latency_p50_ms":    30000,
        "requests_per_min":  12,
        "cpu_usage_pct":     18,
        "heap_usage_pct":    55,
        "gc_pause_ms":       8,
        "active_threads":    50,
    },
    "latency_spike": {
        "error_rate_pct":    3.1,
        "latency_p95_ms":    3420,
        "latency_p50_ms":    1200,
        "requests_per_min":  160,
        "cpu_usage_pct":     45,
        "heap_usage_pct":    52,
        "gc_pause_ms":       22,
        "active_threads":    40,
    },
    "stock_bug": {
        "error_rate_pct":    22.0,
        "latency_p95_ms":    640,
        "latency_p50_ms":    210,
        "requests_per_min":  110,
        "cpu_usage_pct":     55,
        "heap_usage_pct":    58,
        "gc_pause_ms":       34,
        "active_threads":    35,
    },
    "ci_failure": {
        "error_rate_pct":    88.0,
        "latency_p95_ms":    5100,
        "latency_p50_ms":    4800,
        "requests_per_min":  8,
        "cpu_usage_pct":     12,
        "heap_usage_pct":    30,
        "gc_pause_ms":       5,
        "active_threads":    5,
    },
}


class MetricsSource:
    def __init__(self, config: dict):
        self.config = config.get("sources", {}).get("metrics", {})
        self.simulate = self.config.get("simulate", True)
        self.thresholds = config.get("thresholds", {})

    async def collect(self, scenario: str) -> dict:
        base = SIMULATED_METRICS.get(scenario, SIMULATED_METRICS["normal"])
        # Add realistic jitter
        metrics = {k: round(v * random.uniform(0.92, 1.08), 1) for k, v in base.items()}

        err_warn = self.thresholds.get("error_rate", {}).get("warn", 5)
        err_crit = self.thresholds.get("error_rate", {}).get("critical", 15)
        lat_warn = self.thresholds.get("latency_p95_ms", {}).get("warn", 500)
        lat_crit = self.thresholds.get("latency_p95_ms", {}).get("critical", 2000)

        alerts = []
        if metrics["error_rate_pct"] >= err_crit:
            alerts.append({"type": "error_rate", "severity": "CRITICAL", "value": metrics["error_rate_pct"]})
        elif metrics["error_rate_pct"] >= err_warn:
            alerts.append({"type": "error_rate", "severity": "WARN", "value": metrics["error_rate_pct"]})

        if metrics["latency_p95_ms"] >= lat_crit:
            alerts.append({"type": "latency_p95", "severity": "CRITICAL", "value": metrics["latency_p95_ms"]})
        elif metrics["latency_p95_ms"] >= lat_warn:
            alerts.append({"type": "latency_p95", "severity": "WARN", "value": metrics["latency_p95_ms"]})

        return {
            "source":    "prometheus_actuator",
            "simulated": self.simulate,
            "events":    [{"metric": k, "value": v} for k, v in metrics.items()],
            "metrics":   metrics,
            "alerts":    alerts,
            "endpoint":  self.config.get("endpoint", "http://localhost:8080/actuator/prometheus"),
            "real_world_config": (
                "pom.xml: spring-boot-starter-actuator + micrometer-registry-prometheus\n"
                "application.yml: management.endpoints.web.exposure.include=prometheus\n"
                "prometheus.yml: scrape_configs → job_name: inventory, scrape_interval: 15s\n"
                "alerts.yaml: alert: HighErrorRate, expr: http_server_requests_error_rate > 0.05"
            ),
        }
