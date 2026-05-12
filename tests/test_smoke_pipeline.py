"""End-to-end smoke tests for the Hook-to-Prometheus metrics pipeline.

Verifies the complete metrics flow:
    Claude Code hook script fires
    -> OTel SDK emits metric to Collector (gRPC/HTTP)
    -> OTel Collector exports to Prometheus
    -> Prometheus scrapes and stores the metric
    -> Metric is queryable via PromQL

Each test method validates a single OTel metric by:
    1. Running the corresponding hook script as a subprocess with sample JSON
       input and ``OTEL_EXPORTER_OTLP_ENDPOINT`` pointing to the collector.
    2. Waiting for the metric to propagate through the pipeline.
    3. Querying Prometheus to confirm the metric appeared with the expected
       labels.

Requires a running Docker Compose stack (OTel Collector, Prometheus).
Run via::

    python -m pytest tests/test_smoke_pipeline.py -v -m smoke

Exclude from unit test runs via::

    python -m pytest tests/ -v -m "not smoke"
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Path to the hooks directory (relative to project root).
_HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")

# OTLP endpoint for the local OTel Collector container.
_OTEL_ENDPOINT = "http://localhost:4317"

# Environment variables passed to every hook subprocess invocation.
_HOOK_ENV = {
    "OTEL_EXPORTER_OTLP_ENDPOINT": _OTEL_ENDPOINT,
    "PYTHONUNBUFFERED": "1",
}

# Prometheus metric name mapping: OTel metric name -> Prometheus convention.
# OTel counters receive a ``_total`` suffix when exported to Prometheus.
# OTel histograms are exported as ``_bucket``, ``_sum``, ``_count`` series.
_PROMETHEUS_COUNTER_NAMES: dict[str, str] = {
    "claude.tool.invocations": "claude_tool_invocations_count_total",
    "claude.session.count": "claude_session_count_total",
    "claude.files.modified": "claude_files_modified_count_total",
}

_PROMETHEUS_HISTOGRAM_NAMES: dict[str, str] = {
    "claude.tool.duration": "claude_tool_duration_milliseconds_count",
    "claude.session.duration": "claude_session_duration_seconds_count",
    "claude.tokens.estimated": "claude_tokens_estimated_count",
}

# All OTel metric names that the pipeline must carry end-to-end.
_ALL_METRIC_NAMES = list(_PROMETHEUS_COUNTER_NAMES) + list(
    _PROMETHEUS_HISTOGRAM_NAMES
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_hook_script(
    script_name: str,
    input_data: dict[str, Any],
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a hook script as a subprocess with JSON input on stdin.

    The hook script is invoked with ``OTEL_EXPORTER_OTLP_ENDPOINT`` set to
    the local OTel Collector so that emitted metrics flow into the pipeline.

    Args:
        script_name: Filename of the hook script (e.g. ``"session_start.py"``).
        input_data: Dictionary serialised as JSON and piped to stdin.
        extra_env: Additional environment variables merged into the hook's
            environment.

    Returns:
        The completed process result for assertion inspection.
    """
    script_path = os.path.join(_HOOKS_DIR, script_name)
    env = {**os.environ, **_HOOK_ENV}
    if extra_env:
        env.update(extra_env)

    logger.debug(
        "Running hook %s with input: %s",
        script_name,
        json.dumps(input_data),
    )

    return subprocess.run(
        [sys.executable, script_path],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _prometheus_name(otel_name: str) -> str:
    """Translate an OTel metric name to its Prometheus equivalent.

    Args:
        otel_name: The OTel metric name (e.g. ``"claude.tool.invocations"``).

    Returns:
        The Prometheus metric name (e.g. ``"claude_tool_invocations_total"``).

    Raises:
        ValueError: If the OTel metric name is not recognised.
    """
    if otel_name in _PROMETHEUS_COUNTER_NAMES:
        return _PROMETHEUS_COUNTER_NAMES[otel_name]
    if otel_name in _PROMETHEUS_HISTOGRAM_NAMES:
        return _PROMETHEUS_HISTOGRAM_NAMES[otel_name]
    raise ValueError(f"Unknown OTel metric name: {otel_name}")


# ---------------------------------------------------------------------------
# Pipeline Diagnostic Reporting
# ---------------------------------------------------------------------------

# Human-readable labels for each pipeline stage.
_PIPELINE_STAGES = [
    "hook_execution",
    "otel_export",
    "collector_receive",
    "prometheus_scrape",
    "metric_query",
]


@dataclass
class PipelineDiagnosticReport:
    """Structured result of a single pipeline stage diagnostic check.

    Attributes:
        stage: Name of the pipeline stage that was checked (one of
            ``_PIPELINE_STAGES``).
        passed: Whether the stage check succeeded.
        message: Human-readable summary of the check result.
        details: Additional context or error output relevant to the failure.
        suggestions: List of remediation hints shown when the stage fails.
    """

    stage: str
    passed: bool
    message: str
    details: str = ""
    suggestions: list[str] = field(default_factory=list)

    def as_error_string(self) -> str:
        """Format the report as a detailed multi-line error string.

        Returns:
            A formatted string suitable for inclusion in assertion messages
            or diagnostic logs.  Includes the stage name, message, details,
            and any suggestions.
        """
        lines = [f"[{self.stage}] {self.message}"]
        if self.details:
            lines.append(f"  Details: {self.details}")
        if self.suggestions:
            lines.append("  Suggestions:")
            for suggestion in self.suggestions:
                lines.append(f"    - {suggestion}")
        return "\n".join(lines)


class TestPipelineDiagnostics:
    """Diagnostic helpers for the Hook-to-Prometheus metrics pipeline.

    Each public method checks **one** pipeline stage independently and
    returns a ``PipelineDiagnosticReport`` indicating pass/fail with
    detailed context and remediation suggestions.  Tests can call the
    methods individually or use ``run_full_diagnosis`` to check all
    stages in sequence and collect a complete report.

    The five pipeline stages are:

    1. **hook_execution** — The hook script runs successfully (exit code 0).
    2. **otel_export** — The hook script's stderr does not contain OTel
       export errors.
    3. **collector_receive** — The OTel Collector health endpoint is
       reachable (verifies the collector is accepting telemetry).
    4. **prometheus_scrape** — Prometheus is healthy and its ``/targets``
       endpoint shows the collector target is up.
    5. **metric_query** — The expected metric is queryable via the
       Prometheus HTTP API.

    Usage example::

        diag = TestPipelineDiagnostics(prometheus_client)
        reports = diag.run_full_diagnosis(
            "session_start.py", sample_data, "claude_session_count_total",
        )
        failures = [r for r in reports if not r.passed]
        assert not failures, "\\n".join(r.as_error_string() for r in failures)
    """

    def __init__(
        self,
        prometheus_client: Any,
        collector_url: str = "http://localhost:13133",
        prometheus_url: str = "http://localhost:9090",
    ) -> None:
        self._prom = prometheus_client
        self._collector_url = collector_url
        self._prometheus_url = prometheus_url

    def _query_collector_metrics(self, metric_name: str) -> str:
        """Query collector /metrics endpoint for debug info.

        Returns a string with matching metric lines or an error message.
        """
        import urllib.request
        try:
            url = "http://localhost:8889/metrics"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode()
                lines = [l for l in data.splitlines()
                         if l.startswith("#") or metric_name.replace("_total", "").replace("_count", "").replace("_sum", "").replace("_bucket", "") in l
                         or "claude" in l]
                return "\n\nCollector /metrics claude lines:\n" + "\n".join(lines[:50])
        except Exception as exc:
            return f"\n\nCould not query collector /metrics: {exc}"

    # ------------------------------------------------------------------
    # Stage 1: Hook Execution
    # ------------------------------------------------------------------

    def check_hook_execution(
        self,
        script_name: str,
        input_data: dict[str, Any],
        extra_env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], PipelineDiagnosticReport]:
        """Check that a hook script runs and exits with code 0.

        Args:
            script_name: Filename of the hook script (e.g. ``"session_start.py"``).
            input_data: Dictionary serialised as JSON and piped to stdin.
            extra_env: Additional environment variables for the hook process.

        Returns:
            A tuple of ``(completed_process, diagnostic_report)`` so callers
            can inspect the raw process output if needed.
        """
        try:
            result = _run_hook_script(script_name, input_data, extra_env=extra_env)
        except FileNotFoundError:
            return (
                subprocess.CompletedProcess(
                    ["python", script_name], -1, "", "script not found"
                ),
                PipelineDiagnosticReport(
                    stage="hook_execution",
                    passed=False,
                    message=(
                        f"Hook script '{script_name}' not found at "
                        f"{os.path.join(_HOOKS_DIR, script_name)}"
                    ),
                    details="The hooks directory may be missing or incomplete.",
                    suggestions=[
                        "Verify the hooks directory exists at the expected path.",
                        "Run 'git status' to ensure hooks/ is not gitignored.",
                        "Check that the script filename is spelled correctly.",
                    ],
                ),
            )
        except subprocess.TimeoutExpired:
            return (
                subprocess.CompletedProcess(
                    ["python", script_name], -1, "", "timeout"
                ),
                PipelineDiagnosticReport(
                    stage="hook_execution",
                    passed=False,
                    message=f"Hook script '{script_name}' timed out after 30s",
                    details="The script did not exit within the configured timeout.",
                    suggestions=[
                        "Check if the OTel Collector is reachable from the host.",
                        "Inspect hook logs for infinite loops or hanging I/O.",
                        "Increase the subprocess timeout if the environment is slow.",
                    ],
                ),
            )

        if result.returncode == 0:
            report = PipelineDiagnosticReport(
                stage="hook_execution",
                passed=True,
                message=f"Hook script '{script_name}' exited successfully (code 0)",
            )
        else:
            report = PipelineDiagnosticReport(
                stage="hook_execution",
                passed=False,
                message=(
                    f"Hook script '{script_name}' exited with code "
                    f"{result.returncode}"
                ),
                details=f"stderr: {result.stderr.strip()}" if result.stderr else "",
                suggestions=[
                    "Check the hook script for Python syntax errors.",
                    "Verify all required environment variables are set.",
                    "Run the script manually with the same input to reproduce.",
                ],
            )

        return result, report

    # ------------------------------------------------------------------
    # Stage 2: OTel Export
    # ------------------------------------------------------------------

    def check_otel_export(
        self,
        result: subprocess.CompletedProcess[str],
        script_name: str,
    ) -> PipelineDiagnosticReport:
        """Check that the hook script did not emit OTel export errors.

        Inspects the subprocess stderr for common OTel export failure
        indicators (connection refused, timeout, gRPC errors).

        Args:
            result: The completed process from ``_run_hook_script``.
            script_name: Name of the hook script (for reporting).

        Returns:
            A diagnostic report for the OTel export stage.
        """
        error_indicators = [
            "Connection refused",
            "connection refused",
            "OTLP export failed",
            "grpc",
            "timeout",
            "unreachable",
        ]

        stderr_lower = result.stderr.lower() if result.stderr else ""

        if result.returncode != 0:
            return PipelineDiagnosticReport(
                stage="otel_export",
                passed=False,
                message=(
                    f"OTel export check skipped — '{script_name}' exited "
                    f"with code {result.returncode}"
                ),
                details="Cannot verify OTel export when the hook itself failed.",
                suggestions=[
                    "Fix the hook execution error first (see hook_execution stage).",
                ],
            )

        matched = [ind for ind in error_indicators if ind.lower() in stderr_lower]
        if matched:
            return PipelineDiagnosticReport(
                stage="otel_export",
                passed=False,
                message=(
                    f"OTel export errors detected in '{script_name}' stderr"
                ),
                details=(
                    f"Matched error indicators: {', '.join(matched)}. "
                    f"Full stderr: {result.stderr.strip()}"
                ),
                suggestions=[
                    "Verify the OTel Collector is running: docker compose ps otel-collector",
                    f"Check that {_OTEL_ENDPOINT} is reachable from the host.",
                    "Inspect collector logs: docker compose logs otel-collector",
                    "Ensure no firewall rules block gRPC (port 4317) or HTTP (port 4318).",
                ],
            )

        return PipelineDiagnosticReport(
            stage="otel_export",
            passed=True,
            message=f"No OTel export errors detected for '{script_name}'",
            details=(
                f"stderr: {result.stderr.strip()}"
                if result.stderr.strip()
                else "(no stderr output)"
            ),
        )

    # ------------------------------------------------------------------
    # Stage 3: Collector Receive
    # ------------------------------------------------------------------

    def check_collector_receive(self) -> PipelineDiagnosticReport:
        """Check that the OTel Collector is healthy and accepting telemetry.

        Probes the collector's health/readiness endpoint to verify it is
        running and capable of receiving OTLP data.

        Returns:
            A diagnostic report for the collector receive stage.
        """
        import urllib.error
        import urllib.request

        health_url = f"{self._collector_url}/"

        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if 200 <= resp.status < 400:
                    return PipelineDiagnosticReport(
                        stage="collector_receive",
                        passed=True,
                        message=(
                            f"OTel Collector is healthy at {health_url} "
                            f"(HTTP {resp.status})"
                        ),
                    )
                return PipelineDiagnosticReport(
                    stage="collector_receive",
                    passed=False,
                    message=(
                        f"OTel Collector returned unexpected status "
                        f"{resp.status} at {health_url}"
                    ),
                    suggestions=[
                        "Check collector logs: docker compose logs otel-collector",
                        "Verify the collector configuration file is valid.",
                        "Restart the collector: docker compose restart otel-collector",
                    ],
                )
        except urllib.error.URLError as exc:
            return PipelineDiagnosticReport(
                stage="collector_receive",
                passed=False,
                message=f"OTel Collector is unreachable at {health_url}",
                details=str(exc),
                suggestions=[
                    "Check if the collector container is running: docker compose ps",
                    "Verify port mapping: docker compose port otel-collector 8889",
                    "Restart the stack: docker compose down && docker compose up -d",
                    "Check Docker network configuration.",
                ],
            )
        except Exception as exc:
            return PipelineDiagnosticReport(
                stage="collector_receive",
                passed=False,
                message=f"Unexpected error checking OTel Collector: {exc}",
                details=str(exc),
                suggestions=[
                    "Check Docker daemon status.",
                    "Verify docker-compose.yml service configuration.",
                ],
            )

    # ------------------------------------------------------------------
    # Stage 4: Prometheus Scrape
    # ------------------------------------------------------------------

    def check_prometheus_scrape(self) -> PipelineDiagnosticReport:
        """Check that Prometheus is healthy and scraping the OTel Collector.

        Verifies two things:

        1. The Prometheus health endpoint (``/-/healthy``) returns 200.
        2. The ``/api/v1/targets`` endpoint shows the collector scrape target
           is in the ``up`` state.

        Returns:
            A diagnostic report for the Prometheus scrape stage.
        """
        import urllib.error
        import urllib.request

        # Check Prometheus health first.
        health_url = f"{self._prometheus_url}/-/healthy"
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    return PipelineDiagnosticReport(
                        stage="prometheus_scrape",
                        passed=False,
                        message=(
                            f"Prometheus health check returned HTTP "
                            f"{resp.status}"
                        ),
                        suggestions=[
                            "Check Prometheus logs: docker compose logs prometheus",
                            "Verify prometheus.yml configuration.",
                            "Restart Prometheus: docker compose restart prometheus",
                        ],
                    )
        except urllib.error.URLError as exc:
            return PipelineDiagnosticReport(
                stage="prometheus_scrape",
                passed=False,
                message=f"Prometheus is unreachable at {self._prometheus_url}",
                details=str(exc),
                suggestions=[
                    "Check if Prometheus container is running: docker compose ps",
                    "Verify port mapping: docker compose port prometheus 9090",
                    "Restart the stack: docker compose down && docker compose up -d",
                ],
            )

        # Check collector scrape target via /api/v1/targets.
        targets_url = f"{self._prometheus_url}/api/v1/targets"
        try:
            req = urllib.request.Request(targets_url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            return PipelineDiagnosticReport(
                stage="prometheus_scrape",
                passed=False,
                message="Failed to query Prometheus targets API",
                details=str(exc),
                suggestions=[
                    "Verify Prometheus is fully started (may need more time).",
                    "Check Prometheus logs for configuration errors.",
                ],
            )

        # Inspect active targets for the collector.
        active_targets = data.get("data", {}).get("activeTargets", [])
        collector_targets = [
            t for t in active_targets
            if "otel-collector" in t.get("labels", {}).get("job", "")
            or "8889" in t.get("scrapeUrl", "")
        ]

        if not collector_targets:
            # Fallback: check if any target with the collector port exists.
            all_scrape_urls = [
                t.get("scrapeUrl", "") for t in active_targets
            ]
            return PipelineDiagnosticReport(
                stage="prometheus_scrape",
                passed=False,
                message=(
                    "No OTel Collector scrape target found in Prometheus"
                ),
                details=f"Active targets: {json.dumps(all_scrape_urls)}",
                suggestions=[
                    "Verify prometheus.yml includes a scrape_config for the collector.",
                    "Ensure the collector's Prometheus exporter port (8889) matches.",
                    "Reload Prometheus config: docker compose exec prometheus kill -SIGHUP 1",
                ],
            )

        for target in collector_targets:
            health = target.get("health", "unknown")
            if health != "up":
                last_error = target.get("lastErrorScrape", "") or target.get(
                    "lastError", ""
                )
                return PipelineDiagnosticReport(
                    stage="prometheus_scrape",
                    passed=False,
                    message=(
                        f"Prometheus collector scrape target health is "
                        f"'{health}' (expected 'up')"
                    ),
                    details=(
                        f"Target: {target.get('scrapeUrl', 'unknown')}. "
                        f"Last error: {last_error}"
                    ),
                    suggestions=[
                        "Check if the OTel Collector Prometheus exporter is enabled.",
                        "Verify the collector port matches Prometheus scrape_config.",
                        "Inspect collector logs: docker compose logs otel-collector",
                    ],
                )

        return PipelineDiagnosticReport(
            stage="prometheus_scrape",
            passed=True,
            message="Prometheus is healthy and scraping the OTel Collector",
        )

    # ------------------------------------------------------------------
    # Stage 5: Metric Query
    # ------------------------------------------------------------------

    def check_metric_query(
        self,
        metric_name: str,
        wait: bool = True,
    ) -> PipelineDiagnosticReport:
        """Check that a specific metric is queryable in Prometheus.

        Args:
            metric_name: Full Prometheus metric name (e.g.
                ``"claude_tool_invocations_total"``).
            wait: If True, use ``wait_for_metric`` (with retry/timeout).
                If False, perform a single query via ``query_metric_exists``.

        Returns:
            A diagnostic report for the metric query stage.
        """
        if not self._prom.is_healthy():
            return PipelineDiagnosticReport(
                stage="metric_query",
                passed=False,
                message="Cannot query metrics — Prometheus is not healthy",
                suggestions=[
                    "Check Prometheus logs: docker compose logs prometheus",
                    "Verify Prometheus is running: docker compose ps prometheus",
                    "Restart Prometheus: docker compose restart prometheus",
                ],
            )

        try:
            if wait:
                self._prom.wait_for_metric(metric_name)
            else:
                found = self._prom.query_metric_exists(metric_name)
                if not found:
                    # Query collector /metrics for debug info
                    collector_debug = self._query_collector_metrics(metric_name)
                    return PipelineDiagnosticReport(
                        stage="metric_query",
                        passed=False,
                        message=f"Metric '{metric_name}' not found in Prometheus",
                        details=(
                            "A single query found no data points for this metric."
                            + collector_debug
                        ),
                        suggestions=[
                            "The metric may need more time to propagate. Retry with wait=True.",
                            "Verify the hook script emitted this metric (check otel_export stage).",
                            "Query all metrics: curl http://localhost:9090/api/v1/label/__name__/values",
                            "Check Prometheus targets to confirm scrape is working.",
                        ],
                    )
        except Exception as exc:
            collector_debug = self._query_collector_metrics(metric_name)
            return PipelineDiagnosticReport(
                stage="metric_query",
                passed=False,
                message=f"Failed to query metric '{metric_name}' in Prometheus",
                details=str(exc) + collector_debug,
                suggestions=[
                    "Verify the metric name is correct (check _PROMETHEUS_COUNTER_NAMES/_PROMETHEUS_HISTOGRAM_NAMES).",
                    "Check that the hook script ran successfully and exported the metric.",
                    "Inspect Prometheus logs: docker compose logs prometheus",
                    "Try querying Prometheus directly: curl 'http://localhost:9090/api/v1/query?query=UP'",
                ],
            )

        return PipelineDiagnosticReport(
            stage="metric_query",
            passed=True,
            message=f"Metric '{metric_name}' found in Prometheus",
        )

    # ------------------------------------------------------------------
    # Full Pipeline Diagnosis
    # ------------------------------------------------------------------

    def run_full_diagnosis(
        self,
        script_name: str,
        input_data: dict[str, Any],
        prometheus_metric_name: str,
        extra_env: dict[str, str] | None = None,
    ) -> list[PipelineDiagnosticReport]:
        """Run all five pipeline stages and collect diagnostic reports.

        Executes each stage in order.  If a stage fails, subsequent stages
        that depend on it are still checked so that the caller gets a
        complete picture of all issues.

        Args:
            script_name: Filename of the hook script to run.
            input_data: Dictionary serialised as JSON and piped to stdin.
            prometheus_metric_name: Expected Prometheus metric name.
            extra_env: Additional environment variables for the hook process.

        Returns:
            A list of five ``PipelineDiagnosticReport`` instances, one per
            pipeline stage, in execution order.
        """
        reports: list[PipelineDiagnosticReport] = []

        # Stage 1: Hook execution.
        result, hook_report = self.check_hook_execution(
            script_name, input_data, extra_env=extra_env,
        )
        reports.append(hook_report)

        # Stage 2: OTel export (inspect hook stderr).
        otel_report = self.check_otel_export(result, script_name)
        reports.append(otel_report)

        # Stage 3: Collector health.
        collector_report = self.check_collector_receive()
        reports.append(collector_report)

        # Stage 4: Prometheus scrape.
        scrape_report = self.check_prometheus_scrape()
        reports.append(scrape_report)

        # Stage 5: Metric query.
        query_report = self.check_metric_query(prometheus_metric_name, wait=True)
        reports.append(query_report)

        return reports

    # ------------------------------------------------------------------
    # Utility: format all failures
    # ------------------------------------------------------------------

    @staticmethod
    def format_failures(reports: list[PipelineDiagnosticReport]) -> str:
        """Format a list of diagnostic reports into a human-readable summary.

        Only includes failed stages in the output.  Returns an empty string
        if all stages passed.

        Args:
            reports: List of diagnostic reports from ``run_full_diagnosis``
                or individual stage checks.

        Returns:
            A multi-line string summarising all failures, suitable for
            inclusion in an assertion message.
        """
        failures = [r for r in reports if not r.passed]
        if not failures:
            return ""

        lines = [
            "Pipeline diagnostic failures:",
            "=" * 60,
        ]
        for report in failures:
            lines.append(report.as_error_string())
            lines.append("")

        lines.append("=" * 60)
        lines.append(f"Failed stages: {len(failures)}/{len(reports)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sample hook input data
# ---------------------------------------------------------------------------

_SAMPLE_SESSION_START: dict[str, Any] = {
    "session_id": "smoke-sess-20260509-000001",
    "project": "smoke-test-project",
    "user": "smoke-test-user",
}

_SAMPLE_SESSION_END: dict[str, Any] = {
    "session_id": "smoke-sess-20260509-000001",
    "project": "smoke-test-project",
    "user": "smoke-test-user",
    "duration_seconds": 42.0,
}

_SAMPLE_PRE_TOOL_USE: dict[str, Any] = {
    "session_id": "smoke-sess-20260509-000001",
    "tool_name": "Read",
    "project": "smoke-test-project",
}

_SAMPLE_POST_TOOL_USE: dict[str, Any] = {
    "session_id": "smoke-sess-20260509-000001",
    "tool_name": "Read",
    "duration_ms": 120,
    "status": "success",
    "project": "smoke-test-project",
}

_SAMPLE_STOP: dict[str, Any] = {
    "session_id": "smoke-sess-20260509-000001",
    "project": "smoke-test-project",
    "tools_used": 7,
    "files_modified": 3,
    "duration_seconds": 42.0,
    "tokens_estimated": 8500,
    "stop_reason": "completed",
}


# ---------------------------------------------------------------------------
# Tests: Hook-to-Prometheus end-to-end pipeline
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestHookToPrometheusFlow:
    """End-to-end tests validating metrics flow from hooks to Prometheus.

    Each test method exercises one pipeline path:

        hook script (subprocess)
        -> OTel SDK (gRPC export to Collector)
        -> OTel Collector (Prometheus exporter on :8889)
        -> Prometheus (scrape from Collector)
        -> PromQL query confirms metric exists

    Tests depend on the ``docker_compose_stack`` and ``prometheus_client``
    fixtures defined in ``tests/conftest.py``.
    """

    # ------------------------------------------------------------------
    # claude.session.count
    # ------------------------------------------------------------------

    def test_session_count_metric(
        self,
        prometheus_client,
    ) -> None:
        """claude.session.count should appear in Prometheus after session_start runs.

        The ``session_start.py`` hook emits a ``claude.session.count`` counter
        which Prometheus exposes as ``claude_session_count_total``.
        """
        prom_name = _prometheus_name("claude.session.count")
        diag = TestPipelineDiagnostics(prometheus_client)
        reports = diag.run_full_diagnosis(
            "session_start.py", _SAMPLE_SESSION_START, prom_name,
        )
        failures = TestPipelineDiagnostics.format_failures(reports)
        assert not failures, failures

    # ------------------------------------------------------------------
    # claude.session.duration
    # ------------------------------------------------------------------

    def test_session_duration_metric(
        self,
        prometheus_client,
    ) -> None:
        """claude.session.duration should appear in Prometheus after session_end runs.

        The ``session_end.py`` hook emits a ``claude.session.duration`` histogram
        which Prometheus exposes as ``claude_session_duration_count`` (and
        ``_bucket``, ``_sum`` series).
        """
        prom_name = _prometheus_name("claude.session.duration")
        diag = TestPipelineDiagnostics(prometheus_client)
        reports = diag.run_full_diagnosis(
            "session_end.py", _SAMPLE_SESSION_END, prom_name,
        )
        failures = TestPipelineDiagnostics.format_failures(reports)
        assert not failures, failures

    # ------------------------------------------------------------------
    # claude.tool.invocations
    # ------------------------------------------------------------------

    def test_tool_invocations_metric(
        self,
        prometheus_client,
    ) -> None:
        """claude.tool.invocations should appear in Prometheus after pre_tool_use runs.

        The ``pre_tool_use.py`` hook emits a ``claude.tool.invocations`` counter
        with ``status="started"``.  Prometheus exposes it as
        ``claude_tool_invocations_total``.
        """
        prom_name = _prometheus_name("claude.tool.invocations")
        diag = TestPipelineDiagnostics(prometheus_client)
        reports = diag.run_full_diagnosis(
            "pre_tool_use.py", _SAMPLE_PRE_TOOL_USE, prom_name,
        )
        failures = TestPipelineDiagnostics.format_failures(reports)
        assert not failures, failures

    # ------------------------------------------------------------------
    # claude.tool.duration
    # ------------------------------------------------------------------

    def test_tool_duration_metric(
        self,
        prometheus_client,
    ) -> None:
        """claude.tool.duration should appear in Prometheus after post_tool_use runs.

        The ``post_tool_use.py`` hook emits a ``claude.tool.duration`` histogram
        recording tool invocation duration in milliseconds.  Prometheus exposes
        it as ``claude_tool_duration_count`` (and ``_bucket``, ``_sum``).
        """
        prom_name = _prometheus_name("claude.tool.duration")
        diag = TestPipelineDiagnostics(prometheus_client)
        reports = diag.run_full_diagnosis(
            "post_tool_use.py", _SAMPLE_POST_TOOL_USE, prom_name,
        )
        failures = TestPipelineDiagnostics.format_failures(reports)
        assert not failures, failures

    # ------------------------------------------------------------------
    # claude.files.modified
    # ------------------------------------------------------------------

    def test_files_modified_metric(
        self,
        prometheus_client,
    ) -> None:
        """claude.files.modified should appear in Prometheus after stop runs.

        The ``stop.py`` hook emits a ``claude.files.modified`` counter with the
        number of files created/edited/deleted.  Prometheus exposes it as
        ``claude_files_modified_total``.
        """
        prom_name = _prometheus_name("claude.files.modified")
        diag = TestPipelineDiagnostics(prometheus_client)
        reports = diag.run_full_diagnosis(
            "stop.py",
            _SAMPLE_STOP,
            prom_name,
            extra_env={
                "GITLAB_TOKEN": "",
                "GITLAB_HOST": "",
                "GITLAB_PROJECT": "",
            },
        )
        failures = TestPipelineDiagnostics.format_failures(reports)
        assert not failures, failures

    # ------------------------------------------------------------------
    # claude.tokens.estimated
    # ------------------------------------------------------------------

    def test_tokens_estimated_metric(
        self,
        prometheus_client,
    ) -> None:
        """claude.tokens.estimated should appear in Prometheus after stop runs.

        The ``stop.py`` hook emits a ``claude.tokens.estimated`` histogram
        recording estimated token usage.  Prometheus exposes it as
        ``claude_tokens_estimated_count`` (and ``_bucket``, ``_sum``).
        """
        prom_name = _prometheus_name("claude.tokens.estimated")
        diag = TestPipelineDiagnostics(prometheus_client)
        reports = diag.run_full_diagnosis(
            "stop.py",
            _SAMPLE_STOP,
            prom_name,
            extra_env={
                "GITLAB_TOKEN": "",
                "GITLAB_HOST": "",
                "GITLAB_PROJECT": "",
            },
        )
        failures = TestPipelineDiagnostics.format_failures(reports)
        assert not failures, failures

    # ------------------------------------------------------------------
    # Full pipeline: all metrics in one pass
    # ------------------------------------------------------------------

    def test_all_metrics_flow_through_pipeline(
        self,
        prometheus_client,
    ) -> None:
        """All 6 OTel metrics should appear after running the complete hook sequence.

        Executes every hook script in order (session_start -> pre_tool_use ->
        post_tool_use -> session_end -> stop) and verifies that all 6 metrics
        are queryable in Prometheus.  Uses ``TestPipelineDiagnostics`` to
        report exactly which stage failed if any metric is missing.
        """
        hook_sequence: list[tuple[str, dict[str, Any], dict[str, str] | None]] = [
            ("session_start.py", _SAMPLE_SESSION_START, None),
            ("pre_tool_use.py", _SAMPLE_PRE_TOOL_USE, None),
            ("post_tool_use.py", _SAMPLE_POST_TOOL_USE, None),
            ("session_end.py", _SAMPLE_SESSION_END, None),
            (
                "stop.py",
                _SAMPLE_STOP,
                {
                    "GITLAB_TOKEN": "",
                    "GITLAB_HOST": "",
                    "GITLAB_PROJECT": "",
                },
            ),
        ]

        # Run each hook and collect diagnostics for any failures.
        diag = TestPipelineDiagnostics(prometheus_client)
        hook_errors: list[str] = []

        for script_name, input_data, extra_env in hook_sequence:
            result, hook_report = diag.check_hook_execution(
                script_name, input_data, extra_env=extra_env,
            )
            if not hook_report.passed:
                hook_errors.append(hook_report.as_error_string())

        assert not hook_errors, (
            "Hook execution failures:\n" + "\n".join(hook_errors)
        )

        # Check all metrics with diagnostic detail for any missing ones.
        missing_reports: list[PipelineDiagnosticReport] = []
        for otel_name in _ALL_METRIC_NAMES:
            prom_name = _prometheus_name(otel_name)
            query_report = diag.check_metric_query(prom_name, wait=True)
            if not query_report.passed:
                missing_reports.append(query_report)

        assert not missing_reports, (
            "Pipeline metric query failures:\n"
            + "\n".join(r.as_error_string() for r in missing_reports)
        )


# ---------------------------------------------------------------------------
# Grafana Dashboard Provisioning Verification
# ---------------------------------------------------------------------------

# Directory containing provisioned dashboard JSON files.
_GRAFANA_DASHBOARDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "infra", "grafana", "dashboards"
)

# Expected provisioned dashboard titles and their JSON filenames.
_EXPECTED_DASHBOARDS: dict[str, str] = {
    "Global Overview": "global-overview.json",
    "Individual Activity": "individual-activity.json",
    "Project Detail": "project-detail.json",
}


def _extract_panel_queries(dashboard_path: str) -> list[str]:
    """Extract unique PromQL expressions from a provisioned dashboard JSON file.

    Reads the dashboard JSON, iterates over all panels (skipping row headers
    which are layout containers), and collects the ``expr`` field from each
    panel target.  Returns deduplicated expressions in order of appearance.

    Args:
        dashboard_path: Absolute or relative path to the dashboard JSON file.

    Returns:
        A list of unique PromQL expression strings.
    """
    with open(dashboard_path, encoding="utf-8") as fh:
        dashboard = json.load(fh)

    queries: list[str] = []
    seen: set[str] = set()

    for panel in dashboard.get("panels", []):
        if panel.get("type") == "row":
            continue
        for target in panel.get("targets", []):
            expr = target.get("expr", "")
            if expr and expr not in seen:
                seen.add(expr)
                queries.append(expr)

    return queries


def _substitute_template_vars(expr: str) -> str:
    """Replace Grafana template variables with safe test values.

    Substitutes dashboard template variables so that PromQL expressions
    can be executed against Prometheus without Grafana's variable resolver.

    Replacements:

        - ``$__rate_interval`` -> ``5m``
        - ``$project`` -> ``.*``
        - ``$user`` -> ``.*``

    Args:
        expr: Raw PromQL expression containing Grafana template variables.

    Returns:
        The expression with template variables replaced by safe defaults.
    """
    # Replace $__rate_interval first to avoid partial matches on $project/$user.
    expr = expr.replace("$__rate_interval", "5m")
    expr = expr.replace("$project", ".*")
    expr = expr.replace("$user", ".*")
    return expr


@pytest.mark.smoke
class TestGrafanaProvisioning:
    """Verifies Grafana datasource configuration and dashboard provisioning.

    Validates the Grafana side of the metrics pipeline:

        1. **Datasource** — A Prometheus datasource is configured via the
           Grafana API (``/api/datasources``) with the correct type and
           default flag.
        2. **Dashboards** — All 3 dashboard JSON files (Global Overview,
           Individual Activity, Project Detail) are provisioned and
           discoverable via the Grafana search API (``/api/search``).
        3. **Panel queries** — Every PromQL expression used by dashboard
           panels returns a valid response from Prometheus (no parse errors
           or execution errors).

    Depends on the ``grafana_client`` and ``prometheus_client`` fixtures
    defined in ``tests/conftest.py``.
    """

    # ------------------------------------------------------------------
    # Datasource verification
    # ------------------------------------------------------------------

    def test_prometheus_datasource_configured(
        self,
        grafana_client,
    ) -> None:
        """Prometheus datasource should exist and be configured as default.

        Queries the Grafana ``/api/datasources`` endpoint and verifies that
        a datasource named ``"Prometheus"`` exists with ``type ==
        "prometheus"`` and ``isDefault == True``, matching the provisioning
        configuration in
        ``infra/grafana/provisioning/datasources/datasource.yml``.
        """
        datasources = grafana_client._api_request("/api/datasources")
        assert isinstance(datasources, list), (
            f"Expected /api/datasources to return a list, "
            f"got {type(datasources).__name__}"
        )

        prom_ds = None
        for ds in datasources:
            if ds.get("name") == "Prometheus":
                prom_ds = ds
                break

        assert prom_ds is not None, (
            "No datasource named 'Prometheus' found in Grafana. "
            f"Available datasources: "
            f"{[ds.get('name') for ds in datasources]}"
        )

        assert prom_ds.get("type") == "prometheus", (
            f"Prometheus datasource has unexpected type: "
            f"{prom_ds.get('type')!r} (expected 'prometheus')"
        )

        assert prom_ds.get("isDefault") is True, (
            "Prometheus datasource is not configured as the default "
            "datasource.  Check "
            "infra/grafana/provisioning/datasources/datasource.yml"
        )

        logger.info(
            "Prometheus datasource verified: url=%s, access=%s",
            prom_ds.get("url"),
            prom_ds.get("access"),
        )

    # ------------------------------------------------------------------
    # Dashboard provisioning verification
    # ------------------------------------------------------------------

    def test_all_dashboards_provisioned(
        self,
        grafana_client,
    ) -> None:
        """All 3 dashboards should be provisioned and discoverable via API.

        Queries the Grafana ``/api/search?type=dash-db`` endpoint and
        verifies that each expected dashboard title is present.
        """
        dashboards = grafana_client.list_dashboards()
        provisioned_titles = [d.get("title") for d in dashboards]

        missing: list[str] = []
        for title in _EXPECTED_DASHBOARDS:
            if title not in provisioned_titles:
                missing.append(title)

        assert not missing, (
            "The following dashboards are not provisioned in Grafana: "
            f"{', '.join(missing)}. "
            f"Provisioned dashboards: {provisioned_titles}. "
            "Check infra/grafana/provisioning/dashboards/dashboard.yml "
            "and the dashboard JSON files in infra/grafana/dashboards/."
        )

        logger.info(
            "All %d dashboards provisioned: %s",
            len(_EXPECTED_DASHBOARDS),
            ", ".join(_EXPECTED_DASHBOARDS),
        )

    # ------------------------------------------------------------------
    # Panel query validation
    # ------------------------------------------------------------------

    def test_dashboard_panel_queries_valid(
        self,
        grafana_client,
        prometheus_client,
    ) -> None:
        """All dashboard panel PromQL queries should return valid Prometheus responses.

        For each provisioned dashboard JSON file:

            1. Load the JSON and extract all panel PromQL expressions.
            2. Substitute Grafana template variables with safe defaults.
            3. Execute each query against Prometheus.
            4. Verify the response status is ``"success"`` (no parse or
               execution errors).

        An empty result set (``data.result == []``) is acceptable — the
        test only verifies that queries are syntactically valid and
        Prometheus can execute them without errors.
        """
        errors: list[str] = []

        for title, filename in _EXPECTED_DASHBOARDS.items():
            dashboard_path = os.path.join(
                _GRAFANA_DASHBOARDS_DIR, filename
            )
            queries = _extract_panel_queries(dashboard_path)

            for raw_expr in queries:
                expr = _substitute_template_vars(raw_expr)
                try:
                    result = prometheus_client.query(expr)
                except Exception as exc:
                    errors.append(
                        f"[{title}] Query failed: {raw_expr[:80]}... "
                        f"-> {exc}"
                    )
                    continue

                if result.get("status") != "success":
                    error_msg = result.get("error", "unknown error")
                    errors.append(
                        f"[{title}] Query returned error: "
                        f"{raw_expr[:80]}... -> {error_msg}"
                    )

        assert not errors, (
            "Dashboard panel query validation failures:\n"
            + "\n".join(errors)
        )

        logger.info(
            "All dashboard panel queries validated successfully "
            "across %d dashboards",
            len(_EXPECTED_DASHBOARDS),
        )
