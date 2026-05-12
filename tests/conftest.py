"""Shared pytest fixtures for the AI Coding Progress Harness test suite.

Provides reusable fixtures for:
    - Mock OTel meter provider (no real network calls)
    - Mock glab subprocess (no real GitLab API calls)
    - Sample hook JSON data for each Claude Code event type
    - Environment variable setup/teardown
    - Docker Compose lifecycle for smoke tests
    - Prometheus and Grafana API clients for smoke tests
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Pytest Configuration
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register custom pytest markers.

    Registers the ``smoke`` marker used to segregate end-to-end smoke tests
    (which require Docker) from fast unit tests.  Smoke tests are run via::

        python -m pytest tests/ -v -m smoke

    And excluded from normal unit test runs via::

        python -m pytest tests/ -v -m "not smoke"
    """
    config.addinivalue_line(
        "markers",
        "smoke: end-to-end smoke test (requires Docker Compose stack)",
    )


# ---------------------------------------------------------------------------
# Environment Variable Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env_vars(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set all required environment variables for testing.

    Provides sensible test values for every environment variable consumed by
    ``lib.config.Config``.  Uses ``monkeypatch`` so values are automatically
    restored when the test completes.

    Returns:
        A dict of the environment variables that were set, for easy reference
        in test assertions.
    """
    values = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
        "GITLAB_TOKEN": "glpat-test-token-12345",
        "GITLAB_HOST": "https://gitlab.example.com",
        "GITLAB_PROJECT": "testorg/testproject",
        "CLAUDE_USER_NAME": "test-developer",
        "GF_SECURITY_ADMIN_PASSWORD": "test-admin",
    }
    for key, val in values.items():
        monkeypatch.setenv(key, val)
    return values


@pytest.fixture()
def env_vars_no_gitlab(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set environment variables *without* GitLab configuration.

    Simulates a deployment where ``GITLAB_TOKEN`` and ``GITLAB_PROJECT`` are
    not set, so that GitLab integration should be gracefully skipped.

    Returns:
        A dict of the environment variables that were set.
    """
    values = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
        "CLAUDE_USER_NAME": "test-developer",
    }
    for key, val in values.items():
        monkeypatch.setenv(key, val)

    # Ensure GitLab variables are explicitly unset.
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_HOST", raising=False)
    monkeypatch.delenv("GITLAB_PROJECT", raising=False)

    return values


@pytest.fixture()
def minimal_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Provide the bare minimum environment for OTel-only testing.

    Only ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.  All other variables are
    cleared so that ``Config.safe_user_name`` and ``Config.safe_project``
    fall back to ``"unknown"``.

    Returns:
        A dict of the environment variables that were set.
    """
    values = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
    }
    for key, val in values.items():
        monkeypatch.setenv(key, val)

    for var in ("GITLAB_TOKEN", "GITLAB_HOST", "GITLAB_PROJECT", "CLAUDE_USER_NAME"):
        monkeypatch.delenv(var, raising=False)

    return values


# ---------------------------------------------------------------------------
# Mock OTel Meter Provider Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_meter_provider():
    """Patch the OTel meter provider with a no-op mock.

    Replaces ``lib.otel_metrics.MeterProvider`` and related calls so that
    hook scripts can be tested without a running OTel Collector.  The mock
    meter provider tracks calls to ``force_flush()`` and ``shutdown()`` so
    tests can verify that ``flush_metrics()`` was called correctly.

    Yields:
        A ``MagicMock`` representing the mock MeterProvider instance.
        Inspect ``mock.force_flush.assert_called()`` and
        ``mock.shutdown.assert_called()`` in tests.
    """
    mock_provider = MagicMock()
    mock_provider.force_flush = MagicMock()
    mock_provider.shutdown = MagicMock()

    mock_meter = MagicMock()

    with (
        patch("lib.otel_metrics.MeterProvider", return_value=mock_provider),
        patch("lib.otel_metrics.set_meter_provider"),
        patch("lib.otel_metrics.get_meter_provider", return_value=mock_provider),
        patch.object(mock_provider, "get_meter", return_value=mock_meter),
    ):
        # Store the mock meter so tests can inspect created instruments.
        mock_provider._mock_meter = mock_meter

        # Patch the module-level _provider variable directly.
        import lib.otel_metrics as otel_mod

        original_provider = otel_mod._provider
        otel_mod._provider = mock_provider

        yield mock_provider

        # Restore the original module state.
        otel_mod._provider = original_provider


@pytest.fixture()
def mock_meter(mock_meter_provider):
    """Provide the mock meter from the mock_meter_provider fixture.

    This is a convenience fixture for tests that only need to assert on
    instrument creation (``create_counter``, ``create_histogram``) without
    directly referencing the provider.

    Yields:
        A ``MagicMock`` representing the mock OTel Meter.
    """
    return mock_meter_provider._mock_meter


# ---------------------------------------------------------------------------
# Mock glab Subprocess Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_glab_success():
    """Patch ``subprocess.run`` to simulate successful ``glab`` CLI calls.

    Returns a mock ``subprocess.CompletedProcess`` with returncode 0 and
    a realistic issue URL in stdout, simulating the output of
    ``glab issue create``.

    Yields:
        The ``MagicMock`` used as the ``subprocess.run`` replacement.
        Tests can inspect ``mock.call_args`` to verify CLI arguments.
    """
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = (
        "https://gitlab.example.com/testorg/testproject/-/issues/42\n"
    )
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        yield mock_run


@pytest.fixture()
def mock_glab_failure():
    """Patch ``subprocess.run`` to simulate a failed ``glab`` CLI call.

    Returns a mock ``subprocess.CompletedProcess`` with returncode 1 and
    an error message in stderr.

    Yields:
        The ``MagicMock`` used as the ``subprocess.run`` replacement.
    """
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "ERROR: Project not found"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        yield mock_run


@pytest.fixture()
def mock_glab_not_found():
    """Patch ``subprocess.run`` to simulate ``glab`` CLI not installed.

    Raises ``FileNotFoundError`` when ``subprocess.run`` is called, matching
    the behavior when ``glab`` is not on the system PATH.

    Yields:
        The ``MagicMock`` used as the ``subprocess.run`` replacement.
    """
    with patch("subprocess.run", side_effect=FileNotFoundError("glab not found")) as mock_run:
        yield mock_run


@pytest.fixture()
def mock_glab_timeout():
    """Patch ``subprocess.run`` to simulate a ``glab`` CLI timeout.

    Raises ``subprocess.TimeoutExpired`` when ``subprocess.run`` is called,
    matching the behavior when ``glab`` hangs beyond the 30-second timeout.

    Yields:
        The ``MagicMock`` used as the ``subprocess.run`` replacement.
    """
    import subprocess

    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="glab", timeout=30),
    ) as mock_run:
        yield mock_run


# ---------------------------------------------------------------------------
# Sample Hook JSON Data Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_session_start_data() -> dict:
    """Provide sample JSON data for a SessionStart hook event.

    Returns:
        A dict matching the JSON payload that Claude Code sends to the
        ``hooks/session_start.py`` script via stdin.
    """
    return {
        "session_id": "sess-20260509-143022",
        "project": "test-project",
        "user": "test-developer",
    }


@pytest.fixture()
def sample_session_end_data() -> dict:
    """Provide sample JSON data for a SessionEnd hook event.

    Returns:
        A dict matching the JSON payload that Claude Code sends to the
        ``hooks/session_end.py`` script via stdin.
    """
    return {
        "session_id": "sess-20260509-143022",
        "project": "test-project",
        "user": "test-developer",
        "duration_seconds": 300.5,
        "start_time": 1715253600.0,
    }


@pytest.fixture()
def sample_pre_tool_use_data() -> dict:
    """Provide sample JSON data for a PreToolUse hook event.

    Returns:
        A dict matching the JSON payload that Claude Code sends to the
        ``hooks/pre_tool_use.py`` script via stdin.
    """
    return {
        "session_id": "sess-20260509-143022",
        "tool_name": "Read",
        "project": "test-project",
    }


@pytest.fixture()
def sample_post_tool_use_data() -> dict:
    """Provide sample JSON data for a PostToolUse hook event.

    Returns:
        A dict matching the JSON payload that Claude Code sends to the
        ``hooks/post_tool_use.py`` script via stdin.
    """
    return {
        "session_id": "sess-20260509-143022",
        "tool_name": "Write",
        "duration_ms": 150,
        "status": "success",
        "project": "test-project",
    }


@pytest.fixture()
def sample_stop_data() -> dict:
    """Provide sample JSON data for a Stop hook event.

    Returns:
        A dict matching the JSON payload that Claude Code sends to the
        ``hooks/stop.py`` script via stdin.
    """
    return {
        "session_id": "sess-20260509-143022",
        "project": "test-project",
        "tools_used": 15,
        "files_modified": 3,
        "duration_seconds": 120.0,
        "tokens_estimated": 5000,
        "stop_reason": "completed",
    }


# ---------------------------------------------------------------------------
# Utility Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def json_stdin_factory():
    """Provide a factory for creating mock stdin objects with JSON data.

    Tests that simulate hook script execution can use this factory to
    create a mock ``sys.stdin`` that yields a specific JSON payload.

    Returns:
        A callable that accepts a dict and returns a ``MagicMock`` whose
        ``read()`` returns the JSON-encoded string.
    """

    def _factory(data: dict) -> MagicMock:
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = json.dumps(data)
        # Hook scripts use json.load(sys.stdin), which calls read() internally.
        mock_stdin.__enter__ = MagicMock(return_value=mock_stdin)
        mock_stdin.__exit__ = MagicMock(return_value=False)
        return mock_stdin

    return _factory


# ---------------------------------------------------------------------------
# Smoke Test Fixtures (Docker Compose, Prometheus, Grafana)
# ---------------------------------------------------------------------------
#
# These fixtures are used by end-to-end smoke tests marked with ``@pytest.mark.smoke``.
# They manage the lifecycle of the Docker Compose stack (OTel Collector, Prometheus,
# Grafana) and provide pre-configured API clients for querying metrics and dashboards.
#
# To run smoke tests:
#     python -m pytest tests/test_smoke_pipeline.py -v -m smoke
#
# To skip smoke tests during normal unit test runs:
#     python -m pytest tests/ -v -m "not smoke"
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_compose_stack():
    """Start the Docker Compose stack and tear it down after all tests.

    This session-scoped fixture starts the full metrics pipeline stack
    (OTel Collector, Prometheus, Grafana) via ``docker compose up -d``.
    The stack is torn down in the fixture's finalizer after all tests in
    the session have completed, regardless of pass/fail status.

    Yields:
        A ``tests.smoke_helpers.DockerComposeManager`` instance that can be
        used for additional Compose operations (e.g. checking logs).

    Example::

        def test_something(docker_compose_stack):
            manager = docker_compose_stack
            assert manager.wait_for_all_services()
    """
    from tests.smoke_helpers import DockerComposeManager

    manager = DockerComposeManager()

    # Start the stack.  Let CalledProcessError propagate if Docker is not
    # available or the Compose file is invalid — pytest will report it
    # clearly.
    manager.start()

    # Register a finalizer so the stack is torn down even if tests fail.
    yield manager

    manager.stop()


@pytest.fixture(scope="session")
def healthy_services(docker_compose_stack):
    """Wait for all pipeline services to become healthy.

    Depends on ``docker_compose_stack`` (which starts the containers) and
    then polls the health endpoints of OTel Collector, Prometheus, and
    Grafana until they respond successfully or the startup timeout expires.

    Yields:
        A dict mapping service names (``"otel-collector"``,
        ``"prometheus"``, ``"grafana"``) to booleans indicating whether
        each service passed its health check.

    Raises:
        tests.smoke_helpers.RetryTimeoutError: If any service fails to
            become healthy within the configured timeout.
    """
    results = docker_compose_stack.wait_for_all_services()

    # Report which services are unhealthy so the test output is clear.
    unhealthy = [name for name, ok in results.items() if not ok]
    if unhealthy:
        pytest.fail(
            f"Services failed health check: {', '.join(unhealthy)}. "
            "Check 'docker compose logs' for details."
        )

    yield results


@pytest.fixture()
def prometheus_client(healthy_services):
    """Provide a ``PrometheusClient`` for querying metrics.

    Depends on ``healthy_services`` to ensure Prometheus is up and ready
    before queries are attempted.  The client is created fresh for each
    test function so that query state does not leak between tests.

    Yields:
        A ``tests.smoke_helpers.PrometheusClient`` instance configured to
        query the local Prometheus server at ``http://localhost:9090``.
    """
    from tests.smoke_helpers import PrometheusClient

    yield PrometheusClient()


@pytest.fixture()
def grafana_client(healthy_services, monkeypatch: pytest.MonkeyPatch):
    """Provide a ``GrafanaClient`` for checking dashboard provisioning.

    Depends on ``healthy_services`` to ensure Grafana is up and ready
    before API calls are attempted.  Reads the ``GF_SECURITY_ADMIN_PASSWORD``
    environment variable (defaulting to ``"admin"``) for authentication.

    Yields:
        A ``tests.smoke_helpers.GrafanaClient`` instance configured to
        query the local Grafana server at ``http://localhost:3000``.
    """
    from tests.smoke_helpers import GrafanaClient

    password = os.environ.get("GF_SECURITY_ADMIN_PASSWORD", "admin")
    yield GrafanaClient(password=password)
