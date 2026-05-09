# AI Coding Progress Harness

An observability and progress-tracking system for AI-assisted coding with Claude Code. Collects tool usage metrics via OpenTelemetry, visualizes them in Grafana dashboards at three hierarchical levels, and records development progress in GitLab issues.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Claude Code Session                         │
│                                                                 │
│  ┌──────────┐  ┌───────────┐  ┌───────────┐  ┌──────────────┐  │
│  │Session   │  │PreToolUse │  │PostToolUse│  │Stop Hook     │  │
│  │Start Hook│  │Hook       │  │Hook       │  │              │  │
│  └────┬─────┘  └─────┬─────┘  └─────┬─────┘  └──────┬───────┘  │
│       │              │              │                │           │
│       ▼              ▼              ▼                ▼           │
│  ┌──────────────────────────────────────────┐  ┌────────────┐  │
│  │       lib/ (Shared Python Modules)       │  │  GitLab    │  │
│  │                                          │  │  (glab)    │  │
│  │  ┌─────────┐ ┌──────────────┐ ┌────────┐│  └─────┬──────┘  │
│  │  │config.py│ │otel_metrics  │ │gitlab_ ││        │         │
│  │  │         │ │init_meter()  │ │integ.  ││        │         │
│  │  │         │ │flush_metrics()│ │.py     ││        │         │
│  │  └─────────┘ └──────┬───────┘ └────────┘│        │         │
│  └─────────────────────┼───────────────────┘        │         │
│                        │ OTLP gRPC                   │         │
└────────────────────────┼────────────────────────────┼─────────┘
                         │                            │
                         ▼                            ▼
┌──────────────────────────────────────┐   ┌──────────────────┐
│        Docker Infrastructure         │   │   GitLab Project │
│                                      │   │                  │
│  ┌────────────────────────────────┐  │   │  ┌────────────┐  │
│  │     OTel Collector             │  │   │  │ Progress   │  │
│  │                                │  │   │  │ Issues     │  │
│  │  Receivers: OTLP gRPC+HTTP    │  │   │  └────────────┘  │
│  │  Processors: batch            │  │   └──────────────────┘
│  │  Exporters: Prometheus+debug  │  │
│  │                                │  │
│  │  Ports: 4317, 4318, 8889,     │  │
│  │         8888                   │  │
│  └──────────────┬─────────────────┘  │
│                 │ :8889               │
│                 ▼                     │
│  ┌────────────────────────────────┐  │
│  │     Prometheus                 │  │
│  │                                │  │
│  │  Scrape: otel-collector:8889   │  │
│  │  Interval: 10s                 │  │
│  │  Port: 9090                    │  │
│  └──────────────┬─────────────────┘  │
│                 │ :9090               │
│                 ▼                     │
│  ┌────────────────────────────────┐  │
│  │     Grafana                    │  │
│  │                                │  │
│  │  Datasource: Prometheus        │  │
│  │  Port: 3000                    │  │
│  │                                │  │
│  │  Dashboards:                   │  │
│  │  ├─ Global Overview            │  │
│  │  ├─ Project Detail             │  │
│  │  └─ Individual Activity        │  │
│  └────────────────────────────────┘  │
│                                      │
└──────────────────────────────────────┘
```

## Data Flow

1. **Claude Code** triggers hook scripts on events (tool use, session start/end, stop)
2. **Hook scripts** parse JSON from stdin and emit OTel metrics via gRPC to the OTel Collector
3. **OTel Collector** receives metrics, batches them, and exposes a Prometheus scrape endpoint
4. **Prometheus** scrapes the collector every 10 seconds and stores time-series data
5. **Grafana** queries Prometheus and renders three-tier dashboards (global, project, individual)
6. **Stop hook** creates/updates GitLab issues with human-readable progress summaries via `glab` CLI

## Prerequisites

- **Docker** & **Docker Compose** (v2+)
- **Python** 3.10+
- **glab CLI** ([install guide](https://gitlab.com/gitlab-org/cli)) — `brew install glab` on macOS
- **Claude Code** CLI

## Quick Start

### 1. Start Infrastructure Services

```bash
# Start all infrastructure (OTel Collector, Prometheus, Grafana)
docker compose up -d

# Verify all services are running
docker compose ps
```

Expected output: 3 services (`otel-collector`, `prometheus`, `grafana`) with status `Up`.

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

This installs the OpenTelemetry SDK (API, SDK, gRPC exporter) and test dependencies (pytest, pytest-mock).

### 3. Configure Environment Variables

```bash
# Copy the example env file and fill in your values
cp .env.example .env
```

Edit `.env` with your configuration:

```bash
# Minimum required for metrics collection
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
CLAUDE_USER_NAME=your-name

# Required for GitLab issue integration (optional)
GITLAB_TOKEN=glpat-xxxxx
GITLAB_HOST=https://gitlab.com
GITLAB_PROJECT=myorg/myproject
```

### 4. Authenticate glab CLI (for GitLab integration)

```bash
glab auth login
```

Or set the `GITLAB_TOKEN` environment variable directly. GitLab integration is optional — if not configured, hook scripts skip GitLab operations silently.

### 5. Configure Claude Code Hooks

Create `.claude/settings.local.json` in your project root:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/hooks/pre_tool_use.py",
            "timeout": 30
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/hooks/post_tool_use.py",
            "timeout": 30
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/hooks/session_start.py",
            "timeout": 30
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/hooks/session_end.py",
            "timeout": 30
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/hooks/stop.py",
            "timeout": 55
          }
        ]
      }
    ]
  }
}
```

> **Note:** This file is listed in `.gitignore` and must NOT be committed to git — it contains local paths.

### 6. Verify Everything Works

```bash
# Check Prometheus is scraping the collector
curl http://localhost:9090/api/v1/targets

# Open Grafana dashboards
open http://localhost:3000

# Test a hook script manually
echo '{"session_id":"test-123","project":"my-project","user":"dev"}' | python3 hooks/session_start.py
```

## Project Structure

```
.
├── docker-compose.yml                  # Orchestrates OTel Collector, Prometheus, Grafana
├── requirements.txt                    # Python dependencies (OTel SDK, pytest)
├── .env.example                        # Template for environment variables
├── .gitignore                          # Excludes .env, __pycache__, settings.local.json
├── README.md                           # This file
│
├── hooks/                              # Claude Code hook scripts
│   ├── __init__.py
│   ├── pre_tool_use.py                 # Captures tool invocation start events
│   ├── post_tool_use.py                # Captures tool completion (duration, status)
│   ├── session_start.py                # Records session start with metadata
│   ├── session_end.py                  # Records session duration histogram
│   └── stop.py                         # Aggregates metrics, creates GitLab issue
│
├── lib/                                # Shared Python modules
│   ├── __init__.py
│   ├── config.py                       # Environment variable loading and validation
│   ├── otel_metrics.py                 # OTel meter init, counter/histogram helpers, flush
│   └── gitlab_integration.py           # glab CLI wrapper (issue create/note/update)
│
├── infra/                              # Infrastructure configuration
│   ├── otel-collector/
│   │   └── otel-collector-config.yaml  # Pipeline: OTLP recv → batch → Prometheus+debug
│   ├── prometheus/
│   │   └── prometheus.yml              # Scrape config (10s interval, target :8889)
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/
│       │   │   └── datasource.yml      # Auto-configure Prometheus datasource
│       │   └── dashboards/
│       │       └── dashboard.yml       # Auto-provision dashboard JSON files
│       └── dashboards/
│           ├── global-overview.json     # All projects, all users
│           ├── project-detail.json      # Per-project with $project variable
│           └── individual-activity.json # Per-user with $user variable
│
├── tests/                              # Test suite
│   ├── __init__.py
│   ├── conftest.py                     # Shared fixtures (mock OTel, mock glab)
│   ├── test_otel_metrics.py            # Meter init, flush, counters, histograms
│   ├── test_config.py                  # Env var loading, defaults, validation
│   ├── test_gitlab_integration.py      # glab CLI args, timeout, graceful skip
│   └── test_hooks.py                   # JSON parsing, exit codes, flush on all hooks
│
└── .claude/
    └── settings.local.json             # Hook event registrations (gitignored)
```

## Service URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | http://localhost:3000 | admin / admin (default) |
| Prometheus | http://localhost:9090 | — |
| OTel Collector (gRPC) | localhost:4317 | — |
| OTel Collector (HTTP) | localhost:4318 | — |
| OTel Collector (Metrics) | http://localhost:8889/metrics | — |
| OTel Collector (Self-telemetry) | http://localhost:8888/metrics | — |

## Metric Definitions

All metrics include `project` and `user` labels for three-tier dashboard filtering. OTel metric names (dot-separated) are automatically converted to Prometheus convention (underscore-separated, `_total` suffix for counters).

| OTel Metric Name | Prometheus Name | Type | Unit | Labels | Description |
|---|---|---|---|---|---|
| `claude.tool.invocations` | `claude_tool_invocations_total` | Counter | count | `tool`, `project`, `user`, `status` | Total tool invocations by tool type |
| `claude.tool.duration` | `claude_tool_duration` | Histogram | ms | `tool`, `project`, `user` | Duration of each tool invocation |
| `claude.session.duration` | `claude_session_duration` | Histogram | s | `project`, `user` | Total session duration |
| `claude.session.count` | `claude_session_count_total` | Counter | count | `project`, `user` | Number of sessions started |
| `claude.files.modified` | `claude_files_modified_total` | Counter | count | `project`, `user`, `operation` | Files created/edited/deleted per session |
| `claude.tokens.estimated` | `claude_tokens_estimated` | Histogram | tokens | `project`, `user` | Estimated token usage per tool invocation |

### Prometheus Name Conversion

- Dots (`.`) → underscores (`_`): `claude.tool.invocations` → `claude_tool_invocations`
- Counters receive `_total` suffix: `claude_tool_invocations` → `claude_tool_invocations_total`
- Histograms produce multiple series: `_bucket`, `_sum`, `_count` suffixes

## Grafana Dashboards

### Global Overview

Aggregates metrics across all projects and users. Provides template variables for filtering by `$project` and `$user`. Panels include:

- **Summary Statistics** — Total tool invocations, session count, average tool duration, files modified, estimated tokens
- **Tool Usage Trends** — Time series of invocation rates
- **Activity Breakdown** — Bar gauges by tool type, operation, and status
- **Token & Duration Distribution** — Histogram visualizations

### Project Detail

Filters all metrics to a specific project via the `$project` template variable. Shows:

- **Project Summary** — Scoped stat panels for key metrics
- **Tool Invocations** — Breakdown by tool type and invocation status
- **Session Durations** — Average, p99, and per-user breakdowns
- **File Modifications** — By operation type (create, edit, delete)
- **Activity Timeline** — Combined metrics and token usage by user

### Individual Activity

Filters all metrics to a specific user via the `$user` template variable. Shows:

- **User Summary** — Personal stat panels
- **Tool Usage Patterns** — Individual invocation trends and tool breakdown
- **Session History** — Session duration statistics and timeline
- **Files Modified** — Personal contribution breakdown
- **Token Consumption** — Estimated usage patterns and distribution

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Yes | `http://localhost:4317` | OTel Collector gRPC endpoint |
| `GITLAB_TOKEN` | No | *(empty)* | GitLab personal access token. Required for GitLab issue integration. |
| `GITLAB_HOST` | No | `https://gitlab.com` | GitLab instance host URL |
| `GITLAB_PROJECT` | No | *(empty)* | Target GitLab project in `owner/repo` format. Required for GitLab issue integration. |
| `CLAUDE_USER_NAME` | No | *(empty)* | User identifier for individual-level metrics. Falls back to `"unknown"` in dashboards. |
| `GF_SECURITY_ADMIN_PASSWORD` | No | `admin` | Grafana admin password (Docker Compose env var) |

When `GITLAB_TOKEN` or `GITLAB_PROJECT` is not set, GitLab operations are skipped silently. Metrics still use `"unknown"` for the `user` label if `CLAUDE_USER_NAME` is empty.

## Testing

```bash
# Run the full test suite
pytest tests/ -v

# Run a specific test module
pytest tests/test_config.py -v

# Run with coverage (requires pytest-cov)
pytest tests/ -v --cov=lib --cov=hooks
```

The test suite uses mocks for the OTel meter provider and `glab` subprocess calls, so no running infrastructure is needed to run tests.

## Hook Scripts Reference

| Script | Event | Timeout | What It Does |
|--------|-------|---------|-------------|
| `hooks/session_start.py` | SessionStart | 30s | Emits `claude.session.count` counter |
| `hooks/session_end.py` | SessionEnd | 30s | Calculates duration, emits `claude.session.duration` histogram |
| `hooks/pre_tool_use.py` | PreToolUse | 30s | Emits `claude.tool.invocations` with status `started` |
| `hooks/post_tool_use.py` | PostToolUse | 30s | Emits `claude.tool.invocations` and `claude.tool.duration` |
| `hooks/stop.py` | Stop | 55s | Aggregates session metrics, creates GitLab progress issue |

All hook scripts follow the same pattern:

1. Parse JSON from stdin
2. Load configuration from environment variables
3. Emit OTel metrics with `project`/`user` labels
4. Call `flush_metrics()` before exit (critical for short-lived processes)
5. Exit with code 0 (errors are logged, never blocking)

## Troubleshooting

### Docker services won't start

```bash
# Check for port conflicts
lsof -i :3000 -i :4317 -i :9090

# Restart all services
docker compose down
docker compose up -d

# View service logs
docker compose logs otel-collector
docker compose logs prometheus
docker compose logs grafana
```

### Prometheus target shows as DOWN

- Verify the OTel Collector is running: `docker compose ps otel-collector`
- Check collector logs for config errors: `docker compose logs otel-collector`
- Verify the collector's Prometheus endpoint: `curl http://localhost:8889/metrics`
- Ensure Prometheus scrape target matches the Docker service name (`otel-collector:8889`)

### Grafana dashboards not appearing

- Check dashboard provisioning logs: `docker compose logs grafana | grep provisioning`
- Verify dashboard JSON files are mounted correctly:
  ```bash
  docker compose exec grafana ls /var/lib/grafana/dashboards/
  ```
- Verify the dashboard provider config is mounted:
  ```bash
  docker compose exec grafana cat /etc/grafana/provisioning/dashboards/dashboard.yml
  ```

### Metrics not appearing in Prometheus

1. **Check hook scripts run**: Claude Code should show hook output in the transcript
2. **Verify OTel Collector is receiving**: Check `docker compose logs otel-collector` for OTLP receiver logs
3. **Check the debug exporter**: The collector config includes a `debug` exporter that logs all received metrics to stdout
4. **Verify `flush_metrics()` is called**: Hook scripts are short-lived (ms). Without explicit flush, the PeriodicExportingMetricReader (10s interval) never exports. This is the most common cause of missing metrics.
5. **Check endpoint connectivity**: If running hook scripts outside Docker, ensure `OTEL_EXPORTER_OTLP_ENDPOINT` is `http://localhost:4317` (not `http://otel-collector:4317`)

### GitLab integration not working

- Verify `GITLAB_TOKEN` is set: `echo $GITLAB_TOKEN`
- Check `glab` is installed: `which glab`
- Test glab authentication: `glab auth status`
- Verify the project path format: `GITLAB_PROJECT=myorg/myproject` (not a URL)
- Check for timeout warnings in hook output (30s default for glab calls)

### Hook scripts exit with errors

- Verify Python 3.10+ is available: `python3 --version`
- Install dependencies: `pip install -r requirements.txt`
- Check the `CLAUDE_PROJECT_DIR` environment variable is set (Claude Code sets this automatically)
- Run a hook manually to see error output:
  ```bash
  echo '{"session_id":"test","project":"test"}' | python3 hooks/session_start.py
  ```

### Reset everything

```bash
# Stop and remove all containers, volumes, and networks
docker compose down -v

# Remove Python caches
find . -type d -name __pycache__ -exec rm -rf {} +

# Start fresh
docker compose up -d
```

## License

Internal project.
