#\!/usr/bin/env python3
"""SessionEnd hook: records session duration as a histogram metric.

Emits a ``claude.session.duration`` histogram recording the wall-clock
duration of each Claude Code session in seconds.  The histogram carries
``project`` and ``user`` labels for three-tier dashboard filtering.

Duration is calculated from the ``session_id`` embedded timestamp when
available, or from an explicit ``start_time`` field in the hook event
data.  If neither is parseable, the metric is emitted with a value of
0 and a warning is logged so the session is still counted.

Hook data arrives as JSON on stdin.  ``flush_metrics()`` is called before
exit to guarantee metric export from the short-lived hook process.

Exit codes:
    0 — success (metrics emitted or gracefully skipped)
    2 — blocking error (stderr fed back to Claude)
"""

import json
import logging
import sys
import time
import warnings
from typing import Any

from lib.config import Config
from lib.otel_metrics import create_histogram, flush_metrics, init_meter

logger = logging.getLogger(__name__)


def _extract_duration(event_data: dict[str, Any]) -> float:
    """Calculate session duration in seconds from hook event data.

    Tries the following strategies in order:

    1. An explicit ``duration_seconds`` field in the event payload.
    2. A ``start_time`` Unix-timestamp field, compared to ``time.time()``.
    3. A ``session_id`` containing an embedded timestamp
       (e.g. ``sess-20250508-143022``), compared to ``time.time()``.
    4. Falls back to 0.0 with a warning if nothing is parseable.

    Args:
        event_data: Parsed JSON dict from stdin.

    Returns:
        Session duration in seconds (float).
    """
    if "duration_seconds" in event_data:
        try:
            return float(event_data["duration_seconds"])
        except (TypeError, ValueError):
            pass

    if "start_time" in event_data:
        try:
            start = float(event_data["start_time"])
            return time.time() - start
        except (TypeError, ValueError):
            pass

    session_id = event_data.get("session_id", "")
    try:
        parts = session_id.split("-")
        if len(parts) >= 2:
            date_str = parts[-2]
            time_str = parts[-1]
            if len(date_str) == 8 and len(time_str) == 6:
                timestamp_str = f"{date_str}{time_str}"
                start_ts = time.mktime(
                    time.strptime(timestamp_str, "%Y%m%d%H%M%S")
                )
                return time.time() - start_ts
    except (ValueError, OverflowError):
        pass

    warnings.warn(
        "SessionEnd hook: could not determine session duration, "
        "recording 0.0 seconds",
        stacklevel=3,
    )
    return 0.0


def main() -> None:
    """Parse stdin JSON, emit session duration histogram, flush metrics."""
    try:
        event_data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        warnings.warn(f"SessionEnd hook: invalid JSON on stdin: {exc}", stacklevel=2)
        sys.exit(0)

    session_id = event_data.get("session_id", "unknown")

    config = Config()
    project = config.safe_project
    user = config.safe_user_name

    duration_seconds = _extract_duration(event_data)

    logger.debug(
        "SessionEnd hook: session_id=%s project=%s user=%s duration=%.1fs",
        session_id, project, user, duration_seconds,
    )

    try:
        meter = init_meter("claude-code-hooks")
        histogram = create_histogram(
            meter,
            name="claude.session.duration",
            description="Duration of Claude Code sessions in seconds",
            unit="s",
        )
        histogram.record(
            duration_seconds,
            attributes={"project": project, "user": user, "session_id": session_id},
        )
    except Exception as exc:
        warnings.warn(
            f"SessionEnd hook: failed to emit session duration: {exc}",
            stacklevel=2,
        )
        sys.exit(0)

    flush_metrics()
    sys.exit(0)


if __name__ == "__main__":
    main()
