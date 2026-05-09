#\!/usr/bin/env python3
"""PreToolUse hook: records tool invocation start metrics.

Receives event data as JSON on stdin containing ``session_id``,
``tool_name``, and optional ``project`` fields.  Increments the
``claude.tool.invocations`` counter with ``status="started"`` so that
in-flight / started-vs-completed ratios can be computed in dashboards.

Hook scripts are short-lived processes.  ``flush_metrics()`` is called
before exit to guarantee the PeriodicExportingMetricReader exports the
recorded metric (it only fires every 10 s otherwise).

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
from lib.otel_metrics import create_counter, flush_metrics, init_meter

logger = logging.getLogger(__name__)


def main() -> None:
    """Parse stdin JSON, emit tool invocation start metric, and flush."""
    try:
        event_data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        warnings.warn(
            f"PreToolUse hook: invalid JSON on stdin: {exc}",
            stacklevel=2,
        )
        sys.exit(0)

    session_id = event_data.get("session_id", "unknown")
    tool_name = event_data.get("tool_name", "unknown")
    project = event_data.get("project", "unknown")

    config = Config()
    user = config.safe_user_name

    logger.debug(
        "PreToolUse hook: session_id=%s tool=%s project=%s",
        session_id, tool_name, project,
    )

    try:
        meter = init_meter("claude-code-hooks")
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
                "status": "started",
                "session_id": session_id,
            },
        )
    except Exception as exc:
        warnings.warn(
            f"PreToolUse hook: failed to emit tool invocation metric: {exc}",
            stacklevel=2,
        )
        sys.exit(0)

    flush_metrics()
    sys.exit(0)


if __name__ == "__main__":
    main()
