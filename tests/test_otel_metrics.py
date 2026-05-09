"""Tests for the OTel metrics module (lib.otel_metrics).

Verifies:
    - Meter provider initializes correctly with the expected service name.
    - flush_metrics() calls force_flush() and shutdown() on the provider.
    - Tool invocation counter increments with correct labels.
    - Session duration histogram records values.
"""

from unittest.mock import MagicMock, patch

import pytest

from lib.otel_metrics import (
    create_counter,
    create_histogram,
    flush_metrics,
    init_meter,
)


# ---------------------------------------------------------------------------
# Tests: init_meter
# ---------------------------------------------------------------------------


class TestInitMeter:
    """Tests for init_meter() meter provider initialization."""

    def test_init_meter_creates_meter_with_correct_name(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """init_meter should return a meter whose name matches service_name."""
        service_name = "claude-code-hooks"
        meter = init_meter(service_name)

        # The mock provider's get_meter should have been called with the
        # service name and version "0.1.0".
        mock_meter_provider.get_meter.assert_called_once_with(
            service_name, "0.1.0"
        )
        assert meter is mock_meter_provider._mock_meter

    def test_init_meter_sets_provider_globally(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """init_meter should set the global _provider reference."""
        import lib.otel_metrics as otel_mod

        init_meter("test-service")
        assert otel_mod._provider is mock_meter_provider

    def test_init_meter_creates_exporter_with_configured_endpoint(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """init_meter should pass the OTLP endpoint from Config to the exporter."""
        with patch("lib.otel_metrics.OTLPMetricExporter") as mock_exporter_cls:
            init_meter("test-service")
            mock_exporter_cls.assert_called_once_with(
                endpoint="http://localhost:4317",
                insecure=True,
            )


# ---------------------------------------------------------------------------
# Tests: flush_metrics
# ---------------------------------------------------------------------------


class TestFlushMetrics:
    """Tests for flush_metrics() shutdown behavior."""

    def test_flush_metrics_calls_force_flush_and_shutdown(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """flush_metrics() must call force_flush() then shutdown()."""
        # Initialize provider first so _provider is set.
        init_meter("test-service")

        flush_metrics()

        mock_meter_provider.force_flush.assert_called_once()
        mock_meter_provider.shutdown.assert_called_once()

    def test_flush_metrics_order_force_flush_before_shutdown(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """force_flush() must be called before shutdown()."""
        init_meter("test-service")

        # Track call order using a list.
        call_order: list[str] = []
        mock_meter_provider.force_flush.side_effect = (
            lambda: call_order.append("force_flush")
        )
        mock_meter_provider.shutdown.side_effect = (
            lambda: call_order.append("shutdown")
        )

        flush_metrics()

        assert call_order == ["force_flush", "shutdown"]

    def test_flush_metrics_noop_when_provider_none(self) -> None:
        """flush_metrics() should be a no-op when _provider is None."""
        import lib.otel_metrics as otel_mod

        original = otel_mod._provider
        otel_mod._provider = None
        try:
            # Should not raise any exception.
            flush_metrics()
        finally:
            otel_mod._provider = original

    def test_flush_metrics_survives_force_flush_error(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """flush_metrics() should warn (not raise) if force_flush fails."""
        init_meter("test-service")
        mock_meter_provider.force_flush.side_effect = RuntimeError("boom")

        with pytest.warns(UserWarning, match="Failed to force-flush"):
            flush_metrics()

        # shutdown should still be called even if force_flush failed.
        mock_meter_provider.shutdown.assert_called_once()

    def test_flush_metrics_survives_shutdown_error(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """flush_metrics() should warn (not raise) if shutdown fails."""
        init_meter("test-service")
        mock_meter_provider.shutdown.side_effect = RuntimeError("boom")

        with pytest.warns(UserWarning, match="Failed to shut down"):
            flush_metrics()

        # force_flush should still have been called.
        mock_meter_provider.force_flush.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Tool invocation counter
# ---------------------------------------------------------------------------


class TestToolInvocationCounter:
    """Tests for tool invocation counter instrument creation and usage."""

    def test_tool_invocation_counter_increments_with_correct_labels(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Counter should increment with tool, project, user, status labels."""
        meter = init_meter("test-service")

        counter = create_counter(
            meter,
            name="claude.tool.invocations",
            description="Total tool invocations by tool type",
            unit="count",
        )

        # Verify the counter was created with the correct name.
        mock_meter_provider._mock_meter.create_counter.assert_called_with(
            name="claude.tool.invocations",
            description="Total tool invocations by tool type",
            unit="count",
        )
        assert counter is not None

    def test_counter_add_with_labels(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Counter.add() should be called with value and attribute labels."""
        meter = init_meter("test-service")
        counter = create_counter(
            meter,
            name="claude.tool.invocations",
            description="Total tool invocations",
        )

        labels = {
            "tool": "Read",
            "project": "test-project",
            "user": "test-developer",
            "status": "success",
        }
        counter.add(1, labels)

        counter.add.assert_called_once_with(1, labels)

    def test_counter_multiple_increments(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Counter should track multiple add() calls with different labels."""
        meter = init_meter("test-service")
        counter = create_counter(
            meter,
            name="claude.tool.invocations",
            description="Total tool invocations",
        )

        counter.add(1, {"tool": "Read", "project": "p1", "user": "u1", "status": "success"})
        counter.add(1, {"tool": "Write", "project": "p1", "user": "u1", "status": "success"})
        counter.add(1, {"tool": "Read", "project": "p2", "user": "u2", "status": "error"})

        assert counter.add.call_count == 3


# ---------------------------------------------------------------------------
# Tests: Session duration histogram
# ---------------------------------------------------------------------------


class TestSessionDurationHistogram:
    """Tests for session duration histogram instrument creation and usage."""

    def test_session_duration_histogram_records_values(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Histogram should record session duration in seconds."""
        meter = init_meter("test-service")

        histogram = create_histogram(
            meter,
            name="claude.session.duration",
            description="Total session duration",
            unit="s",
        )

        # Verify the histogram was created with the correct parameters.
        mock_meter_provider._mock_meter.create_histogram.assert_called_with(
            name="claude.session.duration",
            description="Total session duration",
            unit="s",
        )
        assert histogram is not None

    def test_histogram_record_with_labels(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Histogram.record() should be called with value and attribute labels."""
        meter = init_meter("test-service")
        histogram = create_histogram(
            meter,
            name="claude.session.duration",
            description="Total session duration",
            unit="s",
        )

        labels = {"project": "test-project", "user": "test-developer"}
        histogram.record(300.5, labels)

        histogram.record.assert_called_once_with(300.5, labels)

    def test_histogram_multiple_recordings(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Histogram should track multiple record() calls with different values."""
        meter = init_meter("test-service")
        histogram = create_histogram(
            meter,
            name="claude.session.duration",
            description="Total session duration",
            unit="s",
        )

        histogram.record(120.0, {"project": "p1", "user": "u1"})
        histogram.record(300.5, {"project": "p1", "user": "u2"})
        histogram.record(45.0, {"project": "p2", "user": "u1"})

        assert histogram.record.call_count == 3
