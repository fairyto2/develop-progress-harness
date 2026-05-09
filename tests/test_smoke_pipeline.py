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
    "claude.tool.invocations": "claude_tool_invocations_total",
    "claude.session.count": "claude_session_count_total",
    "claude.files.modified": "claude_files_modified_total",
}

_PROMETHEUS_HISTOGRAM_NAMES: dict[str, str] = {
    "claude.tool.duration": "claude_tool_duration_count",
    "claude.session.duration": "claude_session_duration_count",
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
        ["python", script_path],
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
        result = _run_hook_script("session_start.py", _SAMPLE_SESSION_START)
        assert result.returncode == 0, (
            f"session_start.py exited with {result.returncode}: {result.stderr}"
        )

        prom_name = _prometheus_name("claude.session.count")
        found = prometheus_client.wait_for_metric(prom_name)
        assert found, (
            f"Metric '{prom_name}' not found in Prometheus after session_start"
        )

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
        result = _run_hook_script("session_end.py", _SAMPLE_SESSION_END)
        assert result.returncode == 0, (
            f"session_end.py exited with {result.returncode}: {result.stderr}"
        )

        prom_name = _prometheus_name("claude.session.duration")
        found = prometheus_client.wait_for_metric(prom_name)
        assert found, (
            f"Metric '{prom_name}' not found in Prometheus after session_end"
        )

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
        result = _run_hook_script("pre_tool_use.py", _SAMPLE_PRE_TOOL_USE)
        assert result.returncode == 0, (
            f"pre_tool_use.py exited with {result.returncode}: {result.stderr}"
        )

        prom_name = _prometheus_name("claude.tool.invocations")
        found = prometheus_client.wait_for_metric(prom_name)
        assert found, (
            f"Metric '{prom_name}' not found in Prometheus after pre_tool_use"
        )

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
        result = _run_hook_script("post_tool_use.py", _SAMPLE_POST_TOOL_USE)
        assert result.returncode == 0, (
            f"post_tool_use.py exited with {result.returncode}: {result.stderr}"
        )

        prom_name = _prometheus_name("claude.tool.duration")
        found = prometheus_client.wait_for_metric(prom_name)
        assert found, (
            f"Metric '{prom_name}' not found in Prometheus after post_tool_use"
        )

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
        result = _run_hook_script(
            "stop.py",
            _SAMPLE_STOP,
            extra_env={
                "GITLAB_TOKEN": "",
                "GITLAB_HOST": "",
                "GITLAB_PROJECT": "",
            },
        )
        assert result.returncode == 0, (
            f"stop.py exited with {result.returncode}: {result.stderr}"
        )

        prom_name = _prometheus_name("claude.files.modified")
        found = prometheus_client.wait_for_metric(prom_name)
        assert found, (
            f"Metric '{prom_name}' not found in Prometheus after stop"
        )

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
        result = _run_hook_script(
            "stop.py",
            _SAMPLE_STOP,
            extra_env={
                "GITLAB_TOKEN": "",
                "GITLAB_HOST": "",
                "GITLAB_PROJECT": "",
            },
        )
        assert result.returncode == 0, (
            f"stop.py exited with {result.returncode}: {result.stderr}"
        )

        prom_name = _prometheus_name("claude.tokens.estimated")
        found = prometheus_client.wait_for_metric(prom_name)
        assert found, (
            f"Metric '{prom_name}' not found in Prometheus after stop"
        )

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
        are queryable in Prometheus.
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

        for script_name, input_data, extra_env in hook_sequence:
            result = _run_hook_script(script_name, input_data, extra_env=extra_env)
            assert result.returncode == 0, (
                f"{script_name} exited with {result.returncode}: {result.stderr}"
            )

        missing: list[str] = []
        for otel_name in _ALL_METRIC_NAMES:
            prom_name = _prometheus_name(otel_name)
            try:
                prometheus_client.wait_for_metric(prom_name)
            except Exception:
                missing.append(prom_name)

        assert not missing, (
            f"The following metrics did not appear in Prometheus: "
            f"{', '.join(missing)}"
        )
