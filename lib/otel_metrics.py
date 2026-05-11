"""OTel metrics module for Claude Code hook scripts.

Provides meter initialization, counter/histogram creation helpers, and
a critical flush_metrics() shutdown function for short-lived hook processes.

Hook scripts execute in milliseconds, but PeriodicExportingMetricReader only
exports every 10 seconds.  Without an explicit force_flush before process
exit, recorded metrics would be silently lost.
"""

import logging
import warnings

from opentelemetry.metrics import Counter, Histogram, Meter, get_meter_provider, set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

from lib.config import Config

logger = logging.getLogger(__name__)

# Module-level provider reference for cleanup via flush_metrics()
_provider: MeterProvider | None = None


def init_meter(service_name: str) -> Meter:
    """Initialize OTel meter with OTLP gRPC export to collector.

    Creates a MeterProvider backed by a PeriodicExportingMetricReader (10 s
    interval) and an OTLPMetricExporter using gRPC.  The reader's endpoint
    is read from the OTEL_EXPORTER_OTLP_ENDPOINT environment variable via
    lib.config.Config.

    Args:
        service_name: Logical service name used as the meter name.

    Returns:
        An OpenTelemetry Meter ready for instrument creation.
    """
    global _provider

    config = Config()
    endpoint = config.otel_endpoint

    exporter = OTLPMetricExporter(
        endpoint=endpoint,
        insecure=True,
    )
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=10_000,
    )
    _provider = MeterProvider(metric_readers=[reader])
    set_meter_provider(_provider)

    return get_meter_provider().get_meter(service_name, "0.1.0")


def create_counter(meter: Meter, name: str, description: str, unit: str = "count") -> Counter:
    """Create an OTel Counter instrument.

    Args:
        meter: The meter to create the counter on.
        name: Metric name (e.g. ``claude.tool.invocations``).
        description: Human-readable description of what the counter measures.
        unit: Unit of measurement. Defaults to ``count``.

    Returns:
        An OpenTelemetry Counter instrument.
    """
    return meter.create_counter(
        name=name,
        description=description,
        unit=unit,
    )


def create_histogram(meter: Meter, name: str, description: str, unit: str = "ms") -> Histogram:
    """Create an OTel Histogram instrument.

    Args:
        meter: The meter to create the histogram on.
        name: Metric name (e.g. ``claude.tool.duration``).
        description: Human-readable description of what the histogram measures.
        unit: Unit of measurement. Defaults to ``ms``.

    Returns:
        An OpenTelemetry Histogram instrument.
    """
    return meter.create_histogram(
        name=name,
        description=description,
        unit=unit,
    )


def flush_metrics() -> None:
    """Force-flush all pending metrics.  CRITICAL for short-lived processes.

    Hook scripts are ephemeral processes (milliseconds).  The
    PeriodicExportingMetricReader exports every 10 s, so metrics would be
    lost without an explicit flush before the script exits.  This function
    must be called at the end of every hook script to guarantee metrics are
    exported to the OTel Collector.

    Calls ``provider.force_flush()`` which performs a synchronous
    collect-and-export cycle.  ``shutdown()`` is intentionally NOT called
    because it can interrupt the in-flight gRPC export started by
    ``force_flush()``.
    """
    global _provider
    if _provider is not None:
        try:
            _provider.force_flush()
        except Exception:
            warnings.warn(
                "Failed to force-flush OTel metrics provider",
                stacklevel=2,
            )
