"""
sources/log_source.py — Spring Boot log ingestion

Real-world setup:
  1. Spring Boot writes to: /var/log/inventory/spring.log
     (configured via logging.file.path in application.yml)
  2. Filebeat watches the file and ships lines to Logstash/Elasticsearch
  3. THIS module: reads the file directly (dev) or tails via subprocess (prod-lite)

  For full production: replace this with an Elasticsearch query client
  querying the last N minutes of logs from your ELK cluster.

Log format (Spring Boot default):
  2024-01-15 14:23:45.123 ERROR 12345 --- [nio-8080-exec-1] c.d.i.service.ProductService : message
  %d{yyyy-MM-dd HH:mm:ss.SSS} %-5level %pid --- [%thread] %logger{36} : %msg%n
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

SPRING_LOG_PATTERN = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<level>ERROR|WARN|INFO|DEBUG|TRACE)\s+"
    r"(?P<pid>\d+)\s+---\s+\[(?P<thread>[^\]]+)\]\s+"
    r"(?P<logger>\S+)\s*:\s+(?P<message>.+)"
)

# ─── Simulated log data per scenario ─────────────────────────────────────────
SIMULATED_LOGS = {
    "normal": [
        "2024-01-15 14:23:45.001 INFO  1234 --- [nio-8080-exec-1] c.d.i.controller.ProductController : GET /api/products → 200 OK (42ms)",
        "2024-01-15 14:23:45.120 INFO  1234 --- [nio-8080-exec-2] c.d.i.service.StockMovementService : Movement SALE processed for product #88 — qty: -5",
        "2024-01-15 14:23:46.003 INFO  1234 --- [nio-8080-exec-3] c.d.i.service.WarehouseService : Warehouse capacity: 67.3% — OK",
        "2024-01-15 14:23:47.512 INFO  1234 --- [nio-8080-exec-4] c.d.i.service.SupplierService : Supplier #14 health check OK (340ms)",
        "2024-01-15 14:23:48.001 WARN  1234 --- [nio-8080-exec-5] c.d.i.service.SupplierService : Supplier #22 slow response: 1240ms (SLA: 800ms)",
        "2024-01-15 14:23:49.003 INFO  1234 --- [scheduling-1]   c.d.i.service.PurchaseOrderService : PO #2891 approved — total: $12,450",
    ],
    "high_error_rate": [
        "2024-01-15 14:25:01.001 ERROR 1234 --- [nio-8080-exec-1] c.d.i.service.ProductService : java.lang.NullPointerException: Cannot invoke method getStock() on null object",
        "2024-01-15 14:25:01.002 ERROR 1234 --- [nio-8080-exec-1] c.d.i.service.ProductService : \tat com.demo.inventory.service.ProductService.findById(ProductService.java:184)",
        "2024-01-15 14:25:01.003 ERROR 1234 --- [nio-8080-exec-1] c.d.i.service.ProductService : \tat com.demo.inventory.controller.ProductController.getProduct(ProductController.java:67)",
        "2024-01-15 14:25:02.015 WARN  1234 --- [metrics-1]       c.d.i.config.MetricsConfig : HTTP 5xx error rate: 18.4% — threshold: 5%",
        "2024-01-15 14:25:03.101 ERROR 1234 --- [nio-8080-exec-3] c.d.i.service.ProductService : NullPointerException at ProductService.java:184 (repeated 47 times in last 60s)",
        "2024-01-15 14:25:04.200 ERROR 1234 --- [nio-8080-exec-4] c.d.i.service.ProductService : Response 500 for GET /api/products/142 — user: carlos@demo.com",
        "2024-01-15 14:25:05.300 WARN  1234 --- [nio-8080-exec-5] c.d.i.controller.GlobalExceptionHandler : Unhandled exception forwarded to 500 handler",
    ],
    "db_exhaustion": [
        "2024-01-15 14:30:01.001 ERROR 1234 --- [nio-8080-exec-1] com.zaxxer.hikari.pool.HikariPool : HikariPool-1 - Connection is not available, request timed out after 30000ms",
        "2024-01-15 14:30:01.002 ERROR 1234 --- [nio-8080-exec-2] c.d.i.service.ProductService : Unable to acquire JDBC Connection — pool exhausted (50/50)",
        "2024-01-15 14:30:01.003 ERROR 1234 --- [nio-8080-exec-3] c.d.i.service.StockMovementService : SQLTransientConnectionException: Could not open JDBC Connection for transaction",
        "2024-01-15 14:30:02.015 WARN  1234 --- [nio-8080-exec-4] c.d.i.config.CircuitBreaker : Circuit breaker OPEN for StockMovementService — fallback activated",
        "2024-01-15 14:30:02.200 ERROR 1234 --- [nio-8080-exec-5] c.d.i.service.PurchaseOrderService : Failed to persist PO #3001 — no DB connection available",
        "2024-01-15 14:30:03.400 WARN  1234 --- [hikari-housekeeper] com.zaxxer.hikari.pool.HikariPool : HikariPool-1 - Pool stats (total=50, active=50, idle=0, waiting=23)",
    ],
    "latency_spike": [
        "2024-01-15 14:35:01.001 WARN  1234 --- [nio-8080-exec-1] c.d.i.service.WarehouseService : Slow query detected: SELECT * FROM stock_movements WHERE warehouse_id=1 — 3820ms",
        "2024-01-15 14:35:01.100 WARN  1234 --- [nio-8080-exec-2] c.d.i.service.ProductService : p95 response time: 3420ms — SLA breach (threshold: 500ms)",
        "2024-01-15 14:35:02.200 INFO  1234 --- [hikari-1]        com.zaxxer.hikari.pool.HikariPool : Pool usage: 40/50 — connections held by long-running queries",
        "2024-01-15 14:35:03.300 WARN  1234 --- [nio-8080-exec-3] c.d.i.service.WarehouseService : Full table scan warning: no index on stock_movements.movement_date",
        "2024-01-15 14:35:04.400 WARN  1234 --- [nio-8080-exec-4] c.d.i.controller.ProductController : Request timeout for GET /api/warehouses/1/stock after 5000ms",
    ],
    "stock_bug": [
        "2024-01-15 14:40:01.001 ERROR 1234 --- [nio-8080-exec-1] c.d.i.service.StockMovementService : BUSINESS RULE VIOLATION: stock_quantity=-15 for product #88 after SALE movement",
        "2024-01-15 14:40:01.002 ERROR 1234 --- [nio-8080-exec-1] c.d.i.service.StockMovementService : BUG #11: No pre-commit stock validation found — movement allowed to proceed",
        "2024-01-15 14:40:02.100 ERROR 1234 --- [nio-8080-exec-2] c.d.i.service.StockMovementService : BUG #12: Movement type ADJUSTMENT has no handler — IllegalStateException",
        "2024-01-15 14:40:02.200 WARN  1234 --- [nio-8080-exec-3] c.d.i.validator.InventoryValidator : Post-write validation failed: found 3 products with negative stock",
        "2024-01-15 14:40:03.300 INFO  1234 --- [rollback-1]      c.d.i.service.StockMovementService : Attempting rollback of last 3 movements — product IDs: [88, 91, 103]",
    ],
    "ci_failure": [
        "2024-01-15 14:45:01.001 WARN  1234 --- [startup-1]       c.d.i.Application : Application started with 2 bean validation errors",
        "2024-01-15 14:45:01.100 ERROR 1234 --- [startup-1]       c.d.i.config.DataSourceConfig : Failed to validate DB schema — liquibase migration pending",
        "2024-01-15 14:45:02.200 WARN  1234 --- [nio-8080-exec-1] c.d.i.service.ProductService : ProductRepository not initialized — service degraded",
        "2024-01-15 14:45:03.300 ERROR 1234 --- [nio-8080-exec-2] c.d.i.controller.ProductController : NullPointerException: productRepository is null (deployed without DB migration)",
    ],
}


class LogSource:
    """
    Ingests Spring Boot application logs.

    Real-world modes:
      simulate=True  → uses SIMULATED_LOGS dict (this file)
      simulate=False → reads from log file or Elasticsearch REST API
    """

    def __init__(self, config: dict):
        self.config = config.get("sources", {}).get("logs", {})
        self.window_lines = config.get("monitoring", {}).get("log_window_lines", 200)

    async def collect(self, scenario: str) -> dict:
        log_lines = SIMULATED_LOGS.get(scenario, SIMULATED_LOGS["normal"])

        # In production: replace above with one of:
        #   self._read_file()          — tail from disk
        #   self._query_elasticsearch() — ELK stack
        #   self._consume_kafka()      — Kafka topic

        events = []
        for line in log_lines:
            parsed = self._parse_line(line)
            if parsed:
                events.append(parsed)

        error_count  = sum(1 for e in events if e["level"] == "ERROR")
        warn_count   = sum(1 for e in events if e["level"] == "WARN")
        error_rate   = round((error_count / max(len(events), 1)) * 100, 1)

        return {
            "source":      "spring_boot_logs",
            "simulated":   True,
            "events":      events,
            "raw_lines":   log_lines,
            "error_count": error_count,
            "warn_count":  warn_count,
            "error_rate":  error_rate,
            # Real-world config hint:
            "real_world_config": (
                "Log path: application.yml → logging.file.path=/var/log/inventory/spring.log\n"
                "Filebeat: filebeat.yml → paths: [/var/log/inventory/*.log]\n"
                "Elasticsearch index: inventory-logs-YYYY.MM.DD"
            ),
        }

    def _parse_line(self, line: str) -> dict | None:
        m = SPRING_LOG_PATTERN.match(line.strip())
        if not m:
            return None
        return {
            "timestamp": m.group("timestamp"),
            "level":     m.group("level"),
            "thread":    m.group("thread"),
            "logger":    m.group("logger"),
            "message":   m.group("message"),
        }

    async def _read_file(self) -> list[str]:
        """
        Real-world: tail the log file.
        In production prefer Filebeat → Elasticsearch for scalability.
        """
        path = Path(self.config.get("path", "logs/spring-boot-sample.log"))
        if not path.exists():
            return []
        with open(path) as f:
            lines = f.readlines()
        return lines[-self.window_lines:]
