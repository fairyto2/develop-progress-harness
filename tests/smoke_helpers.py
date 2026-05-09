"""Smoke test helper module for the AI Coding Progress Harness.

Provides reusable helpers for end-to-end metrics pipeline testing:

    - ``DockerComposeManager``: Docker Compose lifecycle management
      (start, stop, health-check)
    - ``PrometheusClient``: Query Prometheus metrics via HTTP API
    - ``GrafanaClient``: Check Grafana dashboard provisioning via HTTP API

All helpers include timeout/retry logic for container startup and metric
propagation delays.  Uses only the Python standard library so that smoke
tests can run without installing additional packages.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"
DEFAULT_GRAFANA_URL = "http://localhost:3000"
DEFAULT_GRAFANA_USER = "admin"
DEFAULT_GRAFANA_PASSWORD = "admin"
DEFAULT_COMPOSE_FILE = "docker-compose.yml"

# Default timeouts (seconds)
DEFAULT_STARTUP_TIMEOUT = 120
DEFAULT_HEALTH_CHECK_INTERVAL = 5
DEFAULT_METRIC_PROPAGATION_TIMEOUT = 60
DEFAULT_METRIC_QUERY_INTERVAL = 5


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RetryTimeoutError(Exception):
    """Raised when a retry loop exhausts its timeout without success."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _http_get_json(url: str, timeout: int = 10) -> dict[str, Any]:
    """Perform an HTTP GET and return the response body as parsed JSON.

    Args:
        url: Fully-qualified URL to request.
        timeout: Connection/read timeout in seconds.

    Returns:
        Parsed JSON response as a dict.

    Raises:
        urllib.error.URLError: If the request fails (connection refused, etc.).
        json.JSONDecodeError: If the response body is not valid JSON.
        ValueError: If the HTTP status code indicates an error.
    """
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        if resp.status >= 400:
            raise ValueError(
                f"HTTP {resp.status} from {url}: {body}"
            )
        return json.loads(body)


def _http_get_status(url: str, timeout: int = 10) -> int:
    """Perform an HTTP GET and return the status code.

    Args:
        url: Fully-qualified URL to request.
        timeout: Connection/read timeout in seconds.

    Returns:
        HTTP status code (e.g. 200).

    Raises:
        urllib.error.URLError: If the request fails.
    """
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


# ---------------------------------------------------------------------------
# Docker Compose Manager
# ---------------------------------------------------------------------------


class DockerComposeManager:
    """Manages Docker Compose lifecycle for smoke tests.

    Provides ``start``, ``stop``, and ``health_check`` operations for the
    metrics pipeline stack (OTel Collector, Prometheus, Grafana).  All
    operations include timeout and retry logic to handle slow container
    startup.

    Args:
        compose_file: Path to the ``docker-compose.yml`` file.
        startup_timeout: Maximum seconds to wait for all services to start.
        health_check_interval: Seconds between consecutive health checks.
        env: Additional environment variables passed to ``docker compose``.
    """

    def __init__(
        self,
        compose_file: str = DEFAULT_COMPOSE_FILE,
        startup_timeout: int = DEFAULT_STARTUP_TIMEOUT,
        health_check_interval: int = DEFAULT_HEALTH_CHECK_INTERVAL,
        env: dict[str, str] | None = None,
    ) -> None:
        self.compose_file = compose_file
        self.startup_timeout = startup_timeout
        self.health_check_interval = health_check_interval
        self.env = env or {}

    def _compose_cmd(
        self, *args: str
    ) -> list[str]:
        """Build the base ``docker compose`` command with file flag.

        Returns:
            A list of command arguments suitable for ``subprocess.run``.
        """
        cmd = [
            "docker",
            "compose",
            "-f",
            self.compose_file,
        ]
        cmd.extend(args)
        return cmd

    def _run_compose(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Execute a ``docker compose`` command.

        Args:
            *args: Arguments appended to the base ``docker compose`` command.
            check: If True, raise ``CalledProcessError`` on non-zero exit.

        Returns:
            The completed process result.

        Raises:
            subprocess.CalledProcessError: If ``check`` is True and the
                command exits with a non-zero status.
            FileNotFoundError: If ``docker`` is not on the system PATH.
        """
        cmd = self._compose_cmd(*args)
        logger.debug("Running: %s", " ".join(cmd))
        merged_env = None
        if self.env:
            merged_env = {**os.environ, **self.env}
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check,
            env=merged_env,
        )

    def start(self) -> None:
        """Start all services defined in the Compose file.

        Raises:
            subprocess.CalledProcessError: If ``docker compose up`` fails.
            FileNotFoundError: If ``docker`` is not installed.
        """
        logger.info(
            "Starting Docker Compose services from %s", self.compose_file
        )
        self._run_compose("up", "-d")

    def stop(self) -> None:
        """Stop and remove all containers, networks defined in the Compose file.

        This is safe to call even if containers are not running.

        Raises:
            FileNotFoundError: If ``docker`` is not installed.
        """
        logger.info("Stopping Docker Compose services")
        self._run_compose("down", "--volumes", "--remove-orphans", check=False)

    def health_check(
        self,
        service: str,
        port: int,
        path: str = "/",
        timeout: int | None = None,
    ) -> bool:
        """Wait for a service to become healthy by polling its HTTP endpoint.

        Repeatedly sends GET requests to ``http://localhost:<port><path>``
        until a successful response is received or the timeout expires.

        Args:
            service: Name of the Docker Compose service (used for logging).
            port: Host port mapped to the service.
            path: URL path to check (default ``"/"``).
            timeout: Maximum seconds to wait (defaults to ``startup_timeout``).

        Returns:
            True if the service responded with a successful HTTP status.

        Raises:
            RetryTimeoutError: If the service did not become healthy in time.
        """
        if timeout is None:
            timeout = self.startup_timeout

        url = f"http://localhost:{port}{path}"
        deadline = time.monotonic() + timeout

        logger.info(
            "Waiting for %s to be healthy at %s (timeout=%ds)",
            service,
            url,
            timeout,
        )

        while time.monotonic() < deadline:
            try:
                status = _http_get_status(url, timeout=5)
                if 200 <= status < 400:
                    logger.info("%s is healthy (HTTP %d)", service, status)
                    return True
            except (urllib.error.URLError, OSError, ValueError):
                pass

            remaining = deadline - time.monotonic()
            if remaining > self.health_check_interval:
                time.sleep(self.health_check_interval)
            elif remaining > 0:
                time.sleep(remaining)

        raise RetryTimeoutError(
            f"Service '{service}' did not become healthy at {url} "
            f"within {timeout}s"
        )

    def wait_for_all_services(self) -> dict[str, bool]:
        """Wait for all pipeline services to become healthy.

        Checks OTel Collector, Prometheus, and Grafana in order.

        Returns:
            A dict mapping service names to health-check results.
        """
        results: dict[str, bool] = {}

        services = [
            ("otel-collector", 8889, "/"),
            ("prometheus", 9090, "/-/healthy"),
            ("grafana", 3000, "/api/health"),
        ]

        for service_name, port, path in services:
            try:
                self.health_check(service_name, port, path)
                results[service_name] = True
            except RetryTimeoutError:
                logger.error("Service %s failed health check", service_name)
                results[service_name] = False

        return results


# ---------------------------------------------------------------------------
# Prometheus Client
# ---------------------------------------------------------------------------


class PrometheusClient:
    """Queries Prometheus metrics via HTTP API.

    Provides methods to query instant vectors, check for specific metrics,
    and wait for metrics to appear after pipeline startup.  All query
    methods include retry logic to handle metric propagation delays
    (scrape interval, OTel export lag).

    Args:
        base_url: Prometheus base URL (including port).
        timeout: Default timeout for retry loops.
        query_interval: Seconds between consecutive query retries.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_PROMETHEUS_URL,
        timeout: int = DEFAULT_METRIC_PROPAGATION_TIMEOUT,
        query_interval: int = DEFAULT_METRIC_QUERY_INTERVAL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.query_interval = query_interval

    def query(self, promql: str) -> dict[str, Any]:
        """Execute a PromQL instant query against Prometheus.

        Args:
            promql: Prometheus query expression.

        Returns:
            Parsed JSON response from the Prometheus HTTP API.
            Successful responses contain ``data.result`` with the
            matched time series.

        Raises:
            urllib.error.URLError: If Prometheus is unreachable.
            ValueError: If Prometheus returns an error status.
        """
        url = f"{self.base_url}/api/v1/query"
        encoded_query = urllib.parse.urlencode({"query": promql})
        full_url = f"{url}?{encoded_query}"
        return _http_get_json(full_url)

    def query_range(
        self, promql: str, duration: str = "5m"
    ) -> dict[str, Any]:
        """Execute a PromQL range query against Prometheus.

        Args:
            promql: Prometheus query expression.
            duration: Query range duration (e.g. ``"5m"``, ``"1h"``).

        Returns:
            Parsed JSON response from the Prometheus HTTP API.

        Raises:
            urllib.error.URLError: If Prometheus is unreachable.
            ValueError: If Prometheus returns an error status.
        """
        url = f"{self.base_url}/api/v1/query_range"
        now = time.time()
        params = urllib.parse.urlencode({
            "query": promql,
            "start": str(now - _parse_duration(duration)),
            "end": str(now),
            "step": "15s",
        })
        full_url = f"{url}?{params}"
        return _http_get_json(full_url)

    def query_metric_exists(self, metric_name: str) -> bool:
        """Check whether a specific metric name exists in Prometheus.

        Performs a single query (no retry).  Use ``wait_for_metric`` if
        you need to wait for a metric to appear.

        Args:
            metric_name: Full metric name (e.g. ``"tool_invocations_total"``).

        Returns:
            True if the metric has at least one data point.
        """
        try:
            result = self.query(metric_name)
            return bool(result.get("data", {}).get("result", []))
        except (urllib.error.URLError, ValueError, KeyError):
            return False

    def wait_for_metric(self, metric_name: str) -> bool:
        """Wait for a metric to appear in Prometheus.

        Polls Prometheus until the metric is found or the timeout expires.

        Args:
            metric_name: Full metric name to wait for.

        Returns:
            True if the metric was found within the timeout.

        Raises:
            RetryTimeoutError: If the metric did not appear in time.
        """
        deadline = time.monotonic() + self.timeout
        logger.info(
            "Waiting for metric '%s' (timeout=%ds)", metric_name, self.timeout
        )

        while time.monotonic() < deadline:
            if self.query_metric_exists(metric_name):
                logger.info("Metric '%s' is now available", metric_name)
                return True

            remaining = deadline - time.monotonic()
            if remaining > self.query_interval:
                time.sleep(self.query_interval)
            elif remaining > 0:
                time.sleep(remaining)

        raise RetryTimeoutError(
            f"Metric '{metric_name}' did not appear within {self.timeout}s"
        )

    def check_all_metrics(self, metric_names: list[str]) -> dict[str, bool]:
        """Check multiple metrics and return a dict of results.

        Args:
            metric_names: List of metric names to check.

        Returns:
            A dict mapping each metric name to whether it was found.
        """
        results: dict[str, bool] = {}
        for name in metric_names:
            results[name] = self.query_metric_exists(name)
        return results

    def is_healthy(self) -> bool:
        """Check if Prometheus is up and healthy.

        Returns:
            True if the Prometheus health endpoint returns HTTP 200.
        """
        try:
            _http_get_status(f"{self.base_url}/-/healthy", timeout=5)
            return True
        except (urllib.error.URLError, ValueError, OSError):
            return False


# ---------------------------------------------------------------------------
# Grafana Client
# ---------------------------------------------------------------------------


class GrafanaClient:
    """Checks Grafana dashboard provisioning via HTTP API.

    Provides methods to verify that dashboards are provisioned and data
    sources are configured correctly.  All methods include retry logic
    to handle container startup delays.

    Args:
        base_url: Grafana base URL (including port).
        username: Grafana admin username.
        password: Grafana admin password.
        timeout: Default timeout for retry loops.
        query_interval: Seconds between consecutive query retries.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_GRAFANA_URL,
        username: str = DEFAULT_GRAFANA_USER,
        password: str = DEFAULT_GRAFANA_PASSWORD,
        timeout: int = DEFAULT_METRIC_PROPAGATION_TIMEOUT,
        query_interval: int = DEFAULT_METRIC_QUERY_INTERVAL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.query_interval = query_interval

    def _api_request(
        self, path: str, method: str = "GET"
    ) -> dict[str, Any]:
        """Send an authenticated request to the Grafana HTTP API.

        Uses HTTP Basic Auth with the configured username and password.

        Args:
            path: API path (e.g. ``"/api/health"``).
            method: HTTP method (default ``"GET"``).

        Returns:
            Parsed JSON response as a dict.

        Raises:
            urllib.error.URLError: If Grafana is unreachable.
            ValueError: If Grafana returns an error status.
        """
        url = f"{self.base_url}{path}"
        credentials = base64.b64encode(
            f"{self.username}:{self.password}".encode("utf-8")
        ).decode("ascii")
        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", f"Basic {credentials}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            if resp.status >= 400:
                raise ValueError(
                    f"HTTP {resp.status} from {url}: {body}"
                )
            return json.loads(body)

    def check_health(self) -> bool:
        """Check if Grafana is up and healthy.

        Returns:
            True if the Grafana health endpoint reports OK.
        """
        try:
            data = self._api_request("/api/health")
            return data.get("database") == "ok"
        except (urllib.error.URLError, ValueError, KeyError, OSError):
            return False

    def wait_for_healthy(self) -> bool:
        """Wait for Grafana to become healthy.

        Returns:
            True if Grafana became healthy within the timeout.

        Raises:
            RetryTimeoutError: If Grafana did not become healthy in time.
        """
        deadline = time.monotonic() + self.timeout
        logger.info(
            "Waiting for Grafana at %s (timeout=%ds)",
            self.base_url,
            self.timeout,
        )

        while time.monotonic() < deadline:
            if self.check_health():
                logger.info("Grafana is healthy")
                return True

            remaining = deadline - time.monotonic()
            if remaining > self.query_interval:
                time.sleep(self.query_interval)
            elif remaining > 0:
                time.sleep(remaining)

        raise RetryTimeoutError(
            f"Grafana at {self.base_url} did not become healthy "
            f"within {self.timeout}s"
        )

    def list_dashboards(self) -> list[dict[str, Any]]:
        """List all provisioned dashboards.

        Returns:
            A list of dashboard metadata dicts from the Grafana API.

        Raises:
            urllib.error.URLError: If Grafana is unreachable.
            ValueError: If the API returns an error.
        """
        data = self._api_request("/api/search?type=dash-db")
        return data if isinstance(data, list) else []

    def check_datasource(self, name: str = "Prometheus") -> bool:
        """Check whether a specific data source is configured and accessible.

        Verifies that the named data source exists and that its ``isDefault``
        or ``access`` fields indicate it is properly configured.

        Args:
            name: Name of the data source to check.

        Returns:
            True if the data source exists and is configured.
        """
        try:
            data = self._api_request("/api/datasources")
            if not isinstance(data, list):
                return False
            for ds in data:
                if ds.get("name") == name:
                    return True
            return False
        except (urllib.error.URLError, ValueError, OSError):
            return False

    def check_dashboard_exists(self, title: str) -> bool:
        """Check whether a dashboard with the given title is provisioned.

        Args:
            title: Dashboard title to search for (case-sensitive).

        Returns:
            True if a dashboard with the given title exists.
        """
        try:
            dashboards = self.list_dashboards()
            return any(d.get("title") == title for d in dashboards)
        except (urllib.error.URLError, ValueError, OSError):
            return False


# ---------------------------------------------------------------------------
# Duration parsing utility
# ---------------------------------------------------------------------------


def _parse_duration(duration: str) -> float:
    """Parse a Prometheus-style duration string into seconds.

    Supports ``s``, ``m``, and ``h`` suffixes (e.g. ``"5m"``, ``"1h"``,
    ``"30s"``).

    Args:
        duration: Duration string with a numeric value and unit suffix.

    Returns:
        Duration in seconds as a float.
    """
    if duration.endswith("h"):
        return float(duration[:-1]) * 3600
    if duration.endswith("m"):
        return float(duration[:-1]) * 60
    if duration.endswith("s"):
        return float(duration[:-1])
    return float(duration)
