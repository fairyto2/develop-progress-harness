"""Tests for the GitLab integration module (lib.gitlab_integration).

Verifies:
    - Issue create command builds correct glab CLI arguments
      (--title, --description, --label, --yes).
    - Note command includes the correct issue ID and message body.
    - Graceful skip when GITLAB_TOKEN is not configured.
    - Subprocess timeout handling (TimeoutExpired caught, returns None).
    - FileNotFoundError handling when glab CLI is not installed.
"""

import subprocess
import warnings
from unittest.mock import MagicMock, patch

import pytest

from lib.config import Config
from lib.gitlab_integration import GitLabClient


# ---------------------------------------------------------------------------
# Tests: create_issue — CLI argument construction
# ---------------------------------------------------------------------------


class TestCreateIssue:
    """Tests for GitLabClient.create_issue() argument building."""

    def test_issue_create_builds_correct_args(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Issue create command must include --title, --description, --yes."""
        client = GitLabClient()
        client.create_issue(
            title="[AI Coding] Session 2026-05-09",
            description="Automated AI coding progress tracking",
            labels="ai-coding,progress",
        )

        mock_glab_success.assert_called_once()
        call_args = mock_glab_success.call_args

        # Extract the command list (first positional argument to run).
        cmd = call_args[0][0]

        assert cmd[0:3] == ["glab", "issue", "create"]
        assert "--title" in cmd
        assert cmd[cmd.index("--title") + 1] == "[AI Coding] Session 2026-05-09"
        assert "--description" in cmd
        assert cmd[cmd.index("--description") + 1] == "Automated AI coding progress tracking"
        assert "--yes" in cmd

    def test_issue_create_includes_labels(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Issue create with labels must include --label flag."""
        client = GitLabClient()
        client.create_issue(
            title="Test Issue",
            description="Test body",
            labels="ai-coding,progress",
        )

        cmd = mock_glab_success.call_args[0][0]
        assert "--label" in cmd
        assert cmd[cmd.index("--label") + 1] == "ai-coding,progress"

    def test_issue_create_without_labels(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Issue create without labels must omit --label flag."""
        client = GitLabClient()
        client.create_issue(
            title="Test Issue",
            description="Test body",
            labels=None,
        )

        cmd = mock_glab_success.call_args[0][0]
        assert "--label" not in cmd

    def test_issue_create_includes_repo_flag(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Issue create must include -R OWNER/REPO when GITLAB_PROJECT is set."""
        client = GitLabClient()
        client.create_issue(
            title="Test Issue",
            description="Test body",
        )

        cmd = mock_glab_success.call_args[0][0]
        assert "-R" in cmd
        assert cmd[cmd.index("-R") + 1] == "testorg/testproject"

    def test_issue_create_returns_stdout_on_success(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Issue create should return stdout (issue URL) on success."""
        client = GitLabClient()
        result = client.create_issue(
            title="Test Issue",
            description="Test body",
        )

        assert result is not None
        assert "testorg/testproject/-/issues/42" in result

    def test_issue_create_returns_none_on_failure(
        self,
        env_vars: dict[str, str],
        mock_glab_failure: MagicMock,
    ) -> None:
        """Issue create should return None when glab exits non-zero."""
        client = GitLabClient()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = client.create_issue(
                title="Test Issue",
                description="Test body",
            )

        assert result is None


# ---------------------------------------------------------------------------
# Tests: add_note — CLI argument construction
# ---------------------------------------------------------------------------


class TestAddNote:
    """Tests for GitLabClient.add_note() argument building."""

    def test_note_includes_issue_id_and_message(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Note command must include the issue ID and --message flag."""
        client = GitLabClient()
        client.add_note(
            issue_id=42,
            message="Completed: 15 tool invocations, 3 files modified",
        )

        mock_glab_success.assert_called_once()
        cmd = mock_glab_success.call_args[0][0]

        assert cmd[0:3] == ["glab", "issue", "note"]
        assert "42" in cmd
        assert "--message" in cmd
        assert cmd[cmd.index("--message") + 1] == "Completed: 15 tool invocations, 3 files modified"

    def test_note_with_string_issue_id(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Note command should accept string issue IDs."""
        client = GitLabClient()
        client.add_note(issue_id="99", message="Progress update")

        cmd = mock_glab_success.call_args[0][0]
        assert "99" in cmd

    def test_note_includes_repo_flag(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Note command must include -R flag when project is configured."""
        client = GitLabClient()
        client.add_note(issue_id=42, message="Test note")

        cmd = mock_glab_success.call_args[0][0]
        assert "-R" in cmd
        assert cmd[cmd.index("-R") + 1] == "testorg/testproject"

    def test_note_returns_none_on_failure(
        self,
        env_vars: dict[str, str],
        mock_glab_failure: MagicMock,
    ) -> None:
        """Note should return None when glab exits non-zero."""
        client = GitLabClient()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = client.add_note(issue_id=42, message="Test")

        assert result is None


# ---------------------------------------------------------------------------
# Tests: update_issue — CLI argument construction
# ---------------------------------------------------------------------------


class TestUpdateIssue:
    """Tests for GitLabClient.update_issue() argument building."""

    def test_update_issue_includes_labels(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Update issue must include --label flag when labels provided."""
        client = GitLabClient()
        client.update_issue(issue_id=42, labels="ai-coding,completed")

        cmd = mock_glab_success.call_args[0][0]
        assert cmd[0:3] == ["glab", "issue", "update"]
        assert "42" in cmd
        assert "--label" in cmd
        assert cmd[cmd.index("--label") + 1] == "ai-coding,completed"

    def test_update_issue_without_labels(
        self,
        env_vars: dict[str, str],
        mock_glab_success: MagicMock,
    ) -> None:
        """Update issue without labels must omit --label flag."""
        client = GitLabClient()
        client.update_issue(issue_id=42, labels=None)

        cmd = mock_glab_success.call_args[0][0]
        assert "--label" not in cmd


# ---------------------------------------------------------------------------
# Tests: Graceful skip when GITLAB_TOKEN not configured
# ---------------------------------------------------------------------------


class TestGracefulSkip:
    """Tests for graceful degradation when GitLab is not configured."""

    def test_create_issue_skips_without_gitlab_token(
        self,
        env_vars_no_gitlab: dict[str, str],
    ) -> None:
        """create_issue should return None silently when GITLAB_TOKEN is unset."""
        client = GitLabClient()
        result = client.create_issue(
            title="Test Issue",
            description="Test body",
        )

        assert result is None

    def test_add_note_skips_without_gitlab_token(
        self,
        env_vars_no_gitlab: dict[str, str],
    ) -> None:
        """add_note should return None silently when GITLAB_TOKEN is unset."""
        client = GitLabClient()
        result = client.add_note(issue_id=42, message="Test note")

        assert result is None

    def test_update_issue_skips_without_gitlab_token(
        self,
        env_vars_no_gitlab: dict[str, str],
    ) -> None:
        """update_issue should return None silently when GITLAB_TOKEN is unset."""
        client = GitLabClient()
        result = client.update_issue(issue_id=42, labels="completed")

        assert result is None

    def test_no_subprocess_call_when_not_configured(
        self,
        env_vars_no_gitlab: dict[str, str],
    ) -> None:
        """No subprocess.run call should be made when GitLab is not configured."""
        client = GitLabClient()
        with patch("subprocess.run") as mock_run:
            client.create_issue(title="Test", description="Body")
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Subprocess timeout handling
# ---------------------------------------------------------------------------


class TestSubprocessTimeout:
    """Tests for subprocess timeout handling in glab CLI calls."""

    def test_create_issue_handles_timeout(
        self,
        env_vars: dict[str, str],
        mock_glab_timeout: MagicMock,
    ) -> None:
        """create_issue should return None and warn on subprocess timeout."""
        client = GitLabClient()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = client.create_issue(
                title="Test Issue",
                description="Test body",
            )

        assert result is None
        assert any("timed out" in str(w.message) for w in caught)

    def test_add_note_handles_timeout(
        self,
        env_vars: dict[str, str],
        mock_glab_timeout: MagicMock,
    ) -> None:
        """add_note should return None and warn on subprocess timeout."""
        client = GitLabClient()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = client.add_note(issue_id=42, message="Progress")

        assert result is None
        assert any("timed out" in str(w.message) for w in caught)

    def test_update_issue_handles_timeout(
        self,
        env_vars: dict[str, str],
        mock_glab_timeout: MagicMock,
    ) -> None:
        """update_issue should return None and warn on subprocess timeout."""
        client = GitLabClient()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = client.update_issue(issue_id=42, labels="done")

        assert result is None
        assert any("timed out" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# Tests: glab CLI not found (FileNotFoundError)
# ---------------------------------------------------------------------------


class TestGlabNotFound:
    """Tests for handling missing glab CLI installation."""

    def test_create_issue_warns_when_glab_missing(
        self,
        env_vars: dict[str, str],
        mock_glab_not_found: MagicMock,
    ) -> None:
        """create_issue should return None and warn when glab is not installed."""
        client = GitLabClient()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = client.create_issue(title="Test", description="Body")

        assert result is None
        assert any("glab CLI not found" in str(w.message) for w in caught)

    def test_add_note_warns_when_glab_missing(
        self,
        env_vars: dict[str, str],
        mock_glab_not_found: MagicMock,
    ) -> None:
        """add_note should return None and warn when glab is not installed."""
        client = GitLabClient()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = client.add_note(issue_id=42, message="Test")

        assert result is None
        assert any("glab CLI not found" in str(w.message) for w in caught)
