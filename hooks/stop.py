#!/usr/bin/env python3
"""Stop hook: aggregates session metrics and updates GitLab progress issue.

Parses the session summary JSON from stdin, emits final OTel metrics
(e.g. ``claude.files.modified`` counter), creates or updates a GitLab
issue with a human-readable progress summary via the ``glab`` CLI, and
force-flushes metrics before the short-lived process exits.

Input JSON fields (all optional with sensible defaults):
    session_id      – unique session identifier
    project         – project name for metric labels
    tools_used      – number of tool invocations in the session
    files_modified  – number of files created/edited/deleted
    duration_seconds – total session duration in seconds
    tokens_estimated – estimated token usage
    stop_reason     – reason the agent stopped

Exit codes:
    0 – success (best-effort: errors are logged, not fatal)
    2 – (never used; for spec compatibility)
"""

import json
import logging
import os
import sys
import warnings
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path setup – allow imports from the project ``lib/`` directory regardless
# of the working directory from which the hook is invoked.
# ---------------------------------------------------------------------------
_project_dir = os.environ.get(
    "CLAUDE_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)

from lib.config import Config  # noqa: E402
from lib.gitlab_integration import GitLabClient  # noqa: E402
from lib.otel_metrics import (  # noqa: E402
    create_counter,
    create_histogram,
    flush_metrics,
    init_meter,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERVICE_NAME = "claude-code-hooks.stop"


def _build_human_summary(data: dict) -> str:
    """Build a human-readable progress summary for the GitLab issue note.

    The spec requires: "Issue notes should be human-readable summaries,
    not JSON dumps".  This function formats the session data into a
    Markdown-friendly summary.

    Args:
        data: Parsed session summary dictionary from stdin.

    Returns:
        A multi-line Markdown string summarising the session.
    """
    session_id = data.get("session_id", "unknown")
    project = data.get("project", "unknown")
    tools_used = data.get("tools_used", 0)
    files_modified = data.get("files_modified", 0)
    duration_seconds = data.get("duration_seconds", 0)
    tokens_estimated = data.get("tokens_estimated", 0)
    stop_reason = data.get("stop_reason", "completed")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"**AI Coding Session Complete** — {now}",
        "",
        f"- **Session ID**: `{session_id}`",
        f"- **Project**: {project}",
        f"- **Tools Used**: {tools_used}",
        f"- **Files Modified**: {files_modified}",
        f"- **Duration**: {duration_seconds:.1f}s"
        if isinstance(duration_seconds, (int, float))
        else f"- **Duration**: {duration_seconds}",
        f"- **Estimated Tokens**: {tokens_estimated:,}"
        if isinstance(tokens_estimated, int)
        else f"- **Estimated Tokens**: {tokens_estimated}",
        f"- **Stop Reason**: {stop_reason}",
    ]

    return "\n".join(lines)


def _emit_metrics(data: dict, config: Config) -> None:
    """Emit final aggregated OTel metrics for the session.

    Records ``claude.files.modified`` counter and
    ``claude.tokens.estimated`` histogram with project/user labels.

    Args:
        data: Parsed session summary dictionary from stdin.
        config: Configuration instance for label values.
    """
    meter = init_meter(_SERVICE_NAME)

    project = data.get("project", config.safe_project)
    user = config.safe_user_name

    # --- claude.files.modified counter ---
    files_counter = create_counter(
        meter,
        "claude.files.modified",
        "Files created/edited/deleted per session",
        unit="count",
    )
    files_modified = data.get("files_modified", 0)
    if isinstance(files_modified, (int, float)) and files_modified > 0:
        files_counter.add(
            int(files_modified),
            attributes={"project": project, "user": user, "operation": "total"},
        )

    # --- claude.tokens.estimated histogram ---
    tokens_histogram = create_histogram(
        meter,
        "claude.tokens.estimated",
        "Estimated token usage per session",
        unit="tokens",
    )
    tokens_estimated = data.get("tokens_estimated", 0)
    if isinstance(tokens_estimated, (int, float)) and tokens_estimated > 0:
        tokens_histogram.record(
            float(tokens_estimated),
            attributes={"project": project, "user": user},
        )

    logger.debug(
        "Stop hook metrics emitted: files_modified=%s, tokens_estimated=%s",
        files_modified,
        tokens_estimated,
    )


def _update_gitlab(data: dict, config: Config) -> None:
    """Create or update a GitLab progress-tracking issue.

    Creates a new issue for the session and adds a human-readable note.
    If issue creation fails (e.g. issue already exists), the note is
    silently skipped.  All operations are best-effort: missing
    configuration causes the call to be skipped without raising.

    Args:
        data: Parsed session summary dictionary from stdin.
        config: Configuration instance for GitLab credentials.
    """
    client = GitLabClient(config)

    session_id = data.get("session_id", "unknown")
    project = data.get("project", config.safe_project)

    # Build a descriptive issue title.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    title = f"[AI Coding] Session {now}"

    description = (
        f"Automated AI coding progress tracking for session `{session_id}` "
        f"on project `{project}`."
    )

    # Create a new progress-tracking issue.
    result = client.create_issue(
        title=title,
        description=description,
        labels="ai-coding,progress",
    )

    # If issue creation succeeded, add a human-readable progress note.
    if result:
        note = _build_human_summary(data)
        issue_id = _extract_issue_id(result)
        if issue_id:
            client.add_note(issue_id, note)
            client.update_issue(issue_id, labels="ai-coding,progress,completed")
    else:
        logger.debug("GitLab issue creation skipped or failed; note not added.")


def _extract_issue_id(glab_output: str) -> str | None:
    """Extract the issue IID from ``glab issue create`` output.

    The ``glab`` CLI outputs the issue URL on success.  This function
    parses the trailing numeric segment as the issue IID.

    Args:
        glab_output: The stdout from ``glab issue create``.

    Returns:
        The issue IID as a string, or ``None`` if parsing fails.
    """
    if not glab_output:
        return None

    # Look for a URL ending in /issues/<number>
    for part in glab_output.strip().split():
        if "/issues/" in part:
            segments = part.rstrip("/").split("/")
            for i, seg in enumerate(segments):
                if seg == "issues" and i + 1 < len(segments):
                    candidate = segments[i + 1]
                    if candidate.isdigit():
                        return candidate

    # Fallback: try the last token if it is purely numeric.
    tokens = glab_output.strip().split()
    if tokens and tokens[-1].strip("#").isdigit():
        return tokens[-1].strip("#")

    return None


def main() -> None:
    """Entry point for the Stop hook script.

    Reads session summary JSON from stdin, emits final metrics, updates
    GitLab, and flushes OTel metrics before exit.  All errors are caught
    and logged so that telemetry infrastructure issues never block the
    hook script.
    """
    config = Config()

    # ------------------------------------------------------------------
    # 1. Parse session summary from stdin
    # ------------------------------------------------------------------
    try:
        event_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        warnings.warn(
            "Stop hook: invalid JSON on stdin — skipping metric emission",
            stacklevel=2,
        )
        # Best-effort: still flush and exit cleanly.
        flush_metrics()
        sys.exit(0)
    except Exception:
        warnings.warn(
            "Stop hook: failed to read stdin — skipping metric emission",
            stacklevel=2,
        )
        flush_metrics()
        sys.exit(0)

    # ------------------------------------------------------------------
    # 2. Emit aggregated OTel metrics
    # ------------------------------------------------------------------
    try:
        _emit_metrics(event_data, config)
    except Exception:
        warnings.warn(
            "Stop hook: OTel metric emission failed",
            stacklevel=2,
        )

    # ------------------------------------------------------------------
    # 3. Update GitLab issue with human-readable progress summary
    # ------------------------------------------------------------------
    try:
        _update_gitlab(event_data, config)
    except Exception:
        warnings.warn(
            "Stop hook: GitLab integration failed",
            stacklevel=2,
        )

    # ------------------------------------------------------------------
    # 4. CRITICAL: Force-flush metrics before process exits
    # ------------------------------------------------------------------
    flush_metrics()

    sys.exit(0)


if __name__ == "__main__":
    main()
