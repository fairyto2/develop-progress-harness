#\!/usr/bin/env python3
"""PostToolUse hook: records tool completion metrics.

Receives event data as JSON on stdin containing ``session_id``,
``tool_name``, ``duration_ms``, ``status``, and optional ``project``
fields.  Emits two OTel metrics:

- ``claude.tool.invocations`` counter with the completion status label
  (e.g. ``"success"``, ``"error"``).
- ``claude.tool.duration`` histogram recording the tool invocation
  duration in milliseconds.

Hook scripts are short-lived processes.  ``flush_metrics()`` is called
before exit to guarantee the PeriodicExportingMetricReader exports the
recorded metrics (it only fires every 10 s otherwise).

Exit codes:
    0 — success (metrics emitted or gracefully skipped)
    2 — blocking error (stderr fed back to Claude)
"""

import json
import logging
import os
import sys
import warnings

# Ensure project root is on sys.path so ``lib`` is importable regardless
# of the working directory from which the hook is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.config import Config
from lib.otel_metrics import create_counter, create_histogram, flush_metrics, init_meter

logger = logging.getLogger(__name__)


def main() -> None:
    """Parse stdin JSON, emit tool completion metrics, and flush."""
    try:
        event_data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        warnings.warn(
            f"PostToolUse hook: invalid JSON on stdin: {exc}",
            stacklevel=2,
        )
        sys.exit(0)

    session_id = event_data.get("session_id", "unknown")
    tool_name = event_data.get("tool_name", "unknown")
    duration_ms = event_data.get("duration_ms", 0)
    status = event_data.get("status", "unknown")
    project = event_data.get("project", "unknown")

    config = Config()
    user = config.safe_user_name

    logger.debug(
        "PostToolUse hook: session_id=%s tool=%s duration=%sms status=%s",
        session_id, tool_name, duration_ms, status,
    )

    try:
        meter = init_meter("claude-code-hooks")

        # --- Counter: tool invocations with completion status ---
        counter = create_counter(
            meter,
            name="claude.tool.invocations",
            description="Total tool invocations by tool type",
        )
        counter.add(
            1,
            attributes={
                "tool": tool_name,
                "project": project,
                "user": user,
                "status": status,
                "session_id": session_id,
            },
        )

        # --- Histogram: tool invocation duration ---
        histogram = create_histogram(
            meter,
            name="claude.tool.duration",
            description="Duration of each tool invocation",
        )
        histogram.record(
            duration_ms,
            attributes={
                "tool": tool_name,
                "project": project,
                "user": user,
            },
        )
    except Exception as exc:
        warnings.warn(
            f"PostToolUse hook: failed to emit tool metrics: {exc}",
            stacklevel=2,
        )
        sys.exit(0)

    flush_metrics()
    sys.exit(0)


if __name__ == "__main__":
    main()
