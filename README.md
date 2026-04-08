# inventory-observability — Project 7

> **AI reasoning layer on top of existing observability stack.**
> Not a replacement for Datadog or New Relic — an extension that adds
> natural-language root cause analysis to whatever stack you already have.

---

## The problem this solves

Standard observability tools (Datadog, New Relic, ELK, Prometheus) are excellent
at telling you **what** happened:

> "Error rate crossed 15% at 14:23. Alert fired."

What they don't tell you — at least not without expensive AI add-ons or manual
investigation — is **why** it happened, or **what to do next**:

> "The error spike started 2 minutes after commit `a423f1b` (ProductService refactor),
> correlates with a NullPointerException on line 184 introduced in that change.
> Recommend rollback or hotfix targeting `ProductService.findById()`."

That gap — between "alert fired" and "engineer understands the situation" —
is what this project addresses.

---

## When does this make sense? (honest build vs buy analysis)

| Scenario | Recommendation |
|---|---|
| Large enterprise, budget for tooling | Buy: Datadog AI, New Relic AI, or Dynatrace |
| Already using ELK/Prometheus, tight budget | **Build: this pattern costs ~$50–200/month in Claude API** |
| Domain-specific rules (business logic, known bugs) | **Build: generic tools can't know your domain** |
| Need to demonstrate AI integration capability | **Build: this is the skill** |
| Startup or bank with existing log infrastructure | **Build: adds reasoning without replacing infra** |

The Claude API cost for this pattern: ~$0.003–0.015 per analysis cycle.
At 30-second intervals, that's roughly **$15–50/month** — versus $50,000+/year
for enterprise observability with AI features.

---

## What this project actually demonstrates

This is a **portfolio project** built to demonstrate a specific engineering
capability: embedding LLM reasoning into an existing Python + Spring Boot
system, using the Anthropic SDK directly.

The three things it shows:

**1. Multi-source evidence aggregation**
Pulls data from Spring Boot logs, Prometheus metrics, HikariCP pool stats,
and GitHub recent activity — correlates them before sending to the model.
Generic tools see each source in isolation.

**2. Domain-aware prompting**
The AI prompt includes business context: known bugs (`BUG #11`, `BUG #12`),
service names, SLA thresholds specific to this system. A generic observability
tool cannot know that `stock_quantity < 0` is a business rule violation in
your domain, not just a metric anomaly.

**3. Actionable natural-language output**
Output is not "threshold exceeded". Output is a structured explanation with
root cause, temporal correlation, and a concrete recommendation — in the
on-call engineer's language.

---

## How it fits in a real stack

Designed to sit **alongside** existing tools, not replace them:

```
Existing stack                        This project adds
─────────────────────────────────────────────────────────────
Prometheus / Grafana  ──metrics──►  IngestionPipeline
ELK / Filebeat        ──logs────►   IngestionPipeline  ──►  Claude API
HikariCP / Actuator   ──db──────►   IngestionPipeline        │
GitHub                ──commits─►   IngestionPipeline         │
                                                              ▼
                                              Natural-language explanation
                                              Root cause + recommendation
                                              GitHub Issue (auto-created)
                                              Slack notification
                                              incident-agent trigger (P5)
```

In production: replace simulated sources with real Elasticsearch queries,
real Prometheus API calls, real GitHub API. The pipeline structure stays the same.

---

## Architecture

```
Sources (parallel ingestion)
  ├── LogSource       — Spring Boot logs (simulated / file / Elasticsearch)
  ├── MetricsSource   — Prometheus / Actuator metrics
  ├── DatabaseSource  — MySQL slow queries + HikariCP pool stats
  └── GitHubSource    — Recent commits, PRs, CI runs
        ↓
IngestionPipeline — collects all evidence in parallel
        ↓
AnomalyDetector — Claude claude-sonnet-4-6 via Anthropic SDK
  Input:  structured evidence (logs + metrics + DB + GitHub)
  Output: JSON { severity, anomalies, correlations, confidence, explanation }
        ↓
EventCorrelator — deterministic temporal pattern matching
        ↓
Notifier — console + GitHub Issues + Slack + incident-agent (P5)
```

---

## Difference vs Project 5

| Project 5 — incident-agent          | Project 7 — inventory-observability      |
|--------------------------------------|------------------------------------------|
| Reacts to a manually-triggered alert | Monitors continuously on a schedule      |
| Single point-in-time analysis        | Temporal pattern detection across events |
| Started by a human                   | Runs autonomously, triggers P5           |

P7 feeds P5: when P7 detects HIGH/CRITICAL, it calls incident-agent via HTTP
with pre-analyzed context — so P5 starts with a structured situation, not raw data.

---

## Setup

```bash
cd /d/IA/workspace/inventory-observability

# Install dependencies (one by one for Python 3.14)
pip install anthropic requests pyyaml python-dotenv rich pytest --break-system-packages

# Configure credentials
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY (same key as P5) and GITHUB_TOKEN

# Verify function signatures before running (lesson from P5)
grep -n "def " ingester/pipeline.py detector/anomaly_detector.py correlator/correlator.py notifier/notifier.py
```

## Usage

```bash
python main.py --list-scenarios
python main.py --scenario high_error_rate --dry-run   # no API calls
python main.py --scenario high_error_rate             # real Claude API
python main.py --scenario db_exhaustion
python main.py --scenario stock_bug
python main.py --continuous --dry-run                 # every 30s
python -m pytest tests/ -v                            # no API key needed
```

## Available Scenarios

| Scenario          | Expected severity | What it simulates                            |
|-------------------|-------------------|----------------------------------------------|
| `normal`          | LOW               | Baseline — no anomalies                      |
| `high_error_rate` | HIGH              | NullPointerException spike in ProductService |
| `db_exhaustion`   | CRITICAL          | HikariCP pool exhausted — connection leak    |
| `latency_spike`   | MEDIUM            | Slow query — missing index on stock_movements|
| `stock_bug`       | HIGH              | BUG #11 + BUG #12 active — negative stock    |
| `ci_failure`      | HIGH              | Broken build deployed without DB migration   |

## Parametrizable Thresholds

```yaml
# config/config.yaml
thresholds:
  error_rate:      { warn: 5,   critical: 15  }   # %
  latency_p95_ms:  { warn: 500, critical: 2000 }  # ms
  db_pool_usage:   { warn: 80,  critical: 95   }  # %
  ai_confidence_min: 70                           # suppress below this %
```

Override without editing YAML:
```bash
ALERT_ERROR_RATE_WARN=3 python main.py --scenario normal
```

## Real-World Connection Map

| Source              | Real-world config                                                    |
|---------------------|----------------------------------------------------------------------|
| Spring Boot logs    | `logging.file.path` in `application.yml` → Filebeat → Elasticsearch |
| Prometheus metrics  | `/actuator/prometheus` → `prometheus.yml` scrape config              |
| MySQL slow queries  | `slow_query_log=ON` in `my.cnf` → `performance_schema`              |
| HikariCP pool       | `spring.datasource.hikari.*` in `application.yml`                   |
| GitHub API          | `GITHUB_TOKEN` in `.env` (scope: `repo`)                            |
| Claude API          | `ANTHROPIC_API_KEY` in `.env` or Kubernetes Secret                  |
| Slack               | `SLACK_WEBHOOK_URL` in `.env` → api.slack.com/apps                  |
| incident-agent (P5) | HTTP POST to `localhost:8001/trigger`                                |

---

*Part of a 7-project AI portfolio. See also:*
*Project 5 — incident-agent · Project 6 — inventory-rag*
