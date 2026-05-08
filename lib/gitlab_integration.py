"""GitLab integration module for Claude Code hook scripts.

Wraps the ``glab`` CLI to create progress-tracking issues, add development
notes, and update issue labels.  All commands use ``--yes`` to skip
interactive prompts, and each subprocess call has a 30-second timeout to
prevent indefinite hangs inside hook scripts.

If ``GITLAB_TOKEN`` is not configured, every public method returns silently
so that telemetry infrastructure issues never block hook script execution.
"""

import logging
import subprocess
import warnings
from typing import Any

from lib.config import Config

logger = logging.getLogger(__name__)

# Default timeout (seconds) for glab subprocess calls.
_GLAB_TIMEOUT = 30


class GitLabClient:
    """Lightweight wrapper around the ``glab`` CLI for issue management.

    All operations are best-effort: missing configuration (``GITLAB_TOKEN``,
    ``GITLAB_PROJECT``) causes the call to be skipped silently with a debug
    log message, matching the graceful-degradation principle described in the
    spec.

    Args:
        config: Optional ``Config`` instance.  When *None*, a new one is
            created from environment variables.
    """

    def __init__(self, config: Config | None = None) -> None:
        """Initialize the GitLab client.

        Args:
            config: Optional pre-built Config.  A fresh Config is created
                when not supplied.
        """
        self._config = config or Config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_issue(
        self,
        title: str,
        description: str,
        labels: str | None = None,
    ) -> str | None:
        """Create a GitLab issue via ``glab issue create``.

        Args:
            title: Issue title.
            description: Issue body / description.
            labels: Comma-separated label names (e.g. ``"ai-coding,progress"``).
                Labels must already exist in the GitLab project.

        Returns:
            The output of the ``glab`` command on success (typically includes
            the issue URL), or ``None`` if GitLab is not configured or the
            command fails.
        """
        if not self._ready():
            return None

        cmd: list[str] = [
            "glab", "issue", "create",
            "--title", title,
            "--description", description,
            "--yes",
        ]

        if labels:
            cmd.extend(["--label", labels])

        repo_flag = self._repo_flag()
        if repo_flag:
            cmd.extend(repo_flag)

        return self._run(cmd)

    def add_note(
        self,
        issue_id: str | int,
        message: str,
    ) -> str | None:
        """Add a progress note to an existing GitLab issue.

        Args:
            issue_id: The issue IID (per-project identifier, not global ID).
            message: Note body text.  Should be a human-readable summary,
                not raw JSON (per spec: "Issue notes should be human-readable
                summaries, not JSON dumps").

        Returns:
            The command output on success, or ``None`` if GitLab is not
            configured or the command fails.
        """
        if not self._ready():
            return None

        cmd: list[str] = [
            "glab", "issue", "note",
            str(issue_id),
            "--message", message,
        ]

        repo_flag = self._repo_flag()
        if repo_flag:
            cmd.extend(repo_flag)

        return self._run(cmd)

    def update_issue(
        self,
        issue_id: str | int,
        labels: str | None = None,
    ) -> str | None:
        """Update labels on an existing GitLab issue.

        Args:
            issue_id: The issue IID (per-project identifier).
            labels: Comma-separated label names to set on the issue.

        Returns:
            The command output on success, or ``None`` if GitLab is not
            configured or the command fails.
        """
        if not self._ready():
            return None

        cmd: list[str] = [
            "glab", "issue", "update",
            str(issue_id),
        ]

        if labels:
            cmd.extend(["--label", labels])

        repo_flag = self._repo_flag()
        if repo_flag:
            cmd.extend(repo_flag)

        return self._run(cmd)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ready(self) -> bool:
        """Check whether GitLab integration is configured.

        Returns:
            ``True`` when both ``GITLAB_TOKEN`` and ``GITLAB_PROJECT`` are
            set; ``False`` otherwise (with a debug log emitted).
        """
        if not self._config.has_gitlab_config:
            logger.debug(
                "GitLab integration skipped: GITLAB_TOKEN or "
                "GITLAB_PROJECT not configured"
            )
            return False
        return True

    def _repo_flag(self) -> list[str]:
        """Build the ``-R OWNER/REPO`` flag if a project is configured.

        Returns:
            A list containing the ``-R`` flag and project path, or an empty
            list when no project is set (glab falls back to the git remote).
        """
        if self._config.gitlab_project:
            return ["-R", self._config.gitlab_project]
        return []

    def _run(self, cmd: list[str]) -> str | None:
        """Execute a ``glab`` CLI command with timeout and error handling.

        Uses ``subprocess.run`` with a 30-second timeout.  ``TimeoutExpired``
        is caught and logged as a warning so the hook script continues
        uninterrupted.

        Args:
            cmd: Command and arguments ready for ``subprocess.run``.

        Returns:
            ``stdout`` text on success, or ``None`` on any failure.
        """
        logger.debug("Running glab command: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_GLAB_TIMEOUT,
            )
        except FileNotFoundError:
            warnings.warn(
                "glab CLI not found — is it installed and on PATH?",
                stacklevel=3,
            )
            return None
        except subprocess.TimeoutExpired:
            warnings.warn(
                f"glab command timed out after {_GLAB_TIMEOUT}s: "
                f"{' '.join(cmd)}",
                stacklevel=3,
            )
            return None

        if result.returncode != 0:
            warnings.warn(
                f"glab command failed (exit {result.returncode}): "
                f"{result.stderr.strip()}",
                stacklevel=3,
            )
            return None

        return result.stdout.strip() if result.stdout else None
