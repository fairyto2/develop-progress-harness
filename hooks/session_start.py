#\!/usr/bin/env python3
"""SessionStart hook: records session start with project/user metadata.

Emits a ``claude.session.count`` counter incremented by 1 for each new
Claude Code session.  The counter carries ``project`` and ``user`` labels
derived from environment configuration, enabling three-tier dashboard
filtering (global, per-project, per-user).

Hook data arrives as JSON on stdin.  This script extracts the
``session_id`` field for logging and calls ``flush_metrics()`` before
exit to guarantee metric export from the short-lived hook process.

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
    """Parse stdin JSON, emit session counter, flush metrics."""
    try:
        event_data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        warnings.warn(f"SessionStart hook: invalid JSON on stdin: {exc}", stacklevel=2)
        sys.exit(0)

    session_id = event_data.get("session_id", "unknown")

    config = Config()
    project = config.safe_project
    user = config.safe_user_name

    logger.debug(
        "SessionStart hook: session_id=%s project=%s user=%s",
        session_id, project, user,
    )

    try:
        meter = init_meter("claude-code-hooks")
        counter = create_counter(
            meter,
            name="claude.session.count",
            description="Number of Claude Code sessions started",
            unit="count",
        )
        counter.add(
            1,
            attributes={"project": project, "user": user, "session_id": session_id},
        )
    except Exception as exc:
        warnings.warn(
            f"SessionStart hook: failed to emit session counter: {exc}",
            stacklevel=2,
        )
        sys.exit(0)

    flush_metrics()
    sys.exit(0)


if __name__ == "__main__":
    main()
