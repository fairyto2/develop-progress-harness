"""Tests for Claude Code hook scripts (hooks/*.py).

Verifies:
    - Each hook script correctly parses expected JSON fields from stdin.
    - Exit codes are 0 for both success and graceful-error cases.
    - flush_metrics() is called before every exit path.
    - Graceful handling of missing/invalid JSON input (exit 0, no crash).
"""

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stdin(data: dict) -> StringIO:
    """Create a StringIO object that behaves like stdin with JSON data.

    Args:
        data: Dictionary to serialize as JSON for the mock stdin.

    Returns:
        A StringIO instance containing the JSON-encoded data.
    """
    return StringIO(json.dumps(data))


def _make_invalid_stdin() -> StringIO:
    """Create a StringIO with invalid JSON content.

    Returns:
        A StringIO instance containing non-JSON text.
    """
    return StringIO("this is not valid json!!!")


def _make_empty_stdin() -> StringIO:
    """Create an empty StringIO to simulate empty stdin.

    Returns:
        An empty StringIO instance.
    """
    return StringIO("")


# ---------------------------------------------------------------------------
# Tests: SessionStart hook — JSON parsing
# ---------------------------------------------------------------------------


class TestSessionStartParsing:
    """Tests for hooks/session_start.py JSON field parsing."""

    def test_parses_session_id(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionStart should extract session_id from stdin JSON."""
        from hooks.session_start import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-20260509-143022", "project": "test-project"},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_parses_project_from_config(
        self,
        env_vars: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionStart should use Config.safe_project for project label."""
        from hooks.session_start import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-20260509-143022"},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_start_with_full_data(
        self,
        env_vars: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionStart should handle all standard JSON fields."""
        from hooks.session_start import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-20260509-143022",
                "project": "test-project",
                "user": "dev",
            },
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Tests: SessionEnd hook — JSON parsing
# ---------------------------------------------------------------------------


class TestSessionEndParsing:
    """Tests for hooks/session_end.py JSON field parsing."""

    def test_parses_session_id(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionEnd should extract session_id from stdin JSON."""
        from hooks.session_end import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-20260509-143022", "duration_seconds": 300.5},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_parses_explicit_duration_seconds(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionEnd should use duration_seconds field when present."""
        from hooks.session_end import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "duration_seconds": 120.0},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_parses_start_time_field(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionEnd should compute duration from start_time field."""
        from hooks.session_end import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "start_time": 1715253600.0},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_end_with_full_data(
        self,
        env_vars: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionEnd should handle all standard JSON fields."""
        from hooks.session_end import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-20260509-143022",
                "project": "test-project",
                "user": "dev",
                "duration_seconds": 300.5,
                "start_time": 1715253600.0,
            },
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Tests: PreToolUse hook — JSON parsing
# ---------------------------------------------------------------------------


class TestPreToolUseParsing:
    """Tests for hooks/pre_tool_use.py JSON field parsing."""

    def test_parses_tool_name(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PreToolUse should extract tool_name from stdin JSON."""
        from hooks.pre_tool_use import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "tool_name": "Read", "project": "test-project"},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_parses_session_id(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PreToolUse should extract session_id from stdin JSON."""
        from hooks.pre_tool_use import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-20260509-143022", "tool_name": "Write"},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_pre_tool_use_with_full_data(
        self,
        env_vars: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PreToolUse should handle all standard JSON fields."""
        from hooks.pre_tool_use import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-20260509-143022",
                "tool_name": "Edit",
                "project": "test-project",
            },
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Tests: PostToolUse hook — JSON parsing
# ---------------------------------------------------------------------------


class TestPostToolUseParsing:
    """Tests for hooks/post_tool_use.py JSON field parsing."""

    def test_parses_tool_name_and_status(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PostToolUse should extract tool_name and status from stdin JSON."""
        from hooks.post_tool_use import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-123",
                "tool_name": "Write",
                "duration_ms": 150,
                "status": "success",
                "project": "test-project",
            },
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_parses_duration_ms(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PostToolUse should extract duration_ms from stdin JSON."""
        from hooks.post_tool_use import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-123",
                "tool_name": "Read",
                "duration_ms": 250,
                "status": "success",
            },
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_post_tool_use_with_full_data(
        self,
        env_vars: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PostToolUse should handle all standard JSON fields."""
        from hooks.post_tool_use import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-20260509-143022",
                "tool_name": "Write",
                "duration_ms": 150,
                "status": "success",
                "project": "test-project",
            },
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Tests: Stop hook — JSON parsing
# ---------------------------------------------------------------------------


class TestStopParsing:
    """Tests for hooks/stop.py JSON field parsing."""

    def test_parses_session_summary(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop hook should extract session summary fields from stdin JSON."""
        from hooks.stop import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-20260509-143022",
                "project": "test-project",
                "tools_used": 15,
                "files_modified": 3,
                "duration_seconds": 120.0,
                "tokens_estimated": 5000,
                "stop_reason": "completed",
            },
        )
        with (
            patch.object(sys, "stdin", mock_stdin),
            patch("hooks.stop.GitLabClient"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stop_with_minimal_data(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop hook should handle minimal JSON with only session_id."""
        from hooks.stop import main

        mock_stdin = _make_stdin({"session_id": "sess-123"})
        with (
            patch.object(sys, "stdin", mock_stdin),
            patch("hooks.stop.GitLabClient"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stop_with_full_data(
        self,
        env_vars: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop hook should handle all standard JSON fields."""
        from hooks.stop import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-20260509-143022",
                "project": "test-project",
                "tools_used": 15,
                "files_modified": 3,
                "duration_seconds": 120.0,
                "tokens_estimated": 5000,
                "stop_reason": "completed",
            },
        )
        with (
            patch.object(sys, "stdin", mock_stdin),
            patch("hooks.stop.GitLabClient"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Tests: Exit codes — all hooks return 0
# ---------------------------------------------------------------------------


class TestExitCodes:
    """Tests that all hook scripts exit with code 0 on success."""

    def test_session_start_exits_zero(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionStart should exit with code 0 on success."""
        from hooks.session_start import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "project": "test-project"},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_end_exits_zero(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionEnd should exit with code 0 on success."""
        from hooks.session_end import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "duration_seconds": 60.0},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_pre_tool_use_exits_zero(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PreToolUse should exit with code 0 on success."""
        from hooks.pre_tool_use import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "tool_name": "Read"},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_post_tool_use_exits_zero(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PostToolUse should exit with code 0 on success."""
        from hooks.post_tool_use import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-123",
                "tool_name": "Write",
                "duration_ms": 150,
                "status": "success",
            },
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stop_exits_zero(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop should exit with code 0 on success."""
        from hooks.stop import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-123",
                "tools_used": 5,
                "files_modified": 2,
            },
        )
        with (
            patch.object(sys, "stdin", mock_stdin),
            patch("hooks.stop.GitLabClient"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Tests: flush_metrics is called
# ---------------------------------------------------------------------------


class TestFlushMetricsCalled:
    """Tests that flush_metrics() is called before each hook exits."""

    def test_session_start_calls_flush(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionStart must call flush_metrics() before exit."""
        from hooks.session_start import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "project": "test-project"},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        mock_meter_provider.force_flush.assert_called()

    def test_session_end_calls_flush(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionEnd must call flush_metrics() before exit."""
        from hooks.session_end import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "duration_seconds": 60.0},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        mock_meter_provider.force_flush.assert_called()

    def test_pre_tool_use_calls_flush(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PreToolUse must call flush_metrics() before exit."""
        from hooks.pre_tool_use import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "tool_name": "Read"},
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        mock_meter_provider.force_flush.assert_called()

    def test_post_tool_use_calls_flush(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PostToolUse must call flush_metrics() before exit."""
        from hooks.post_tool_use import main

        mock_stdin = _make_stdin(
            {
                "session_id": "sess-123",
                "tool_name": "Write",
                "duration_ms": 150,
                "status": "success",
            },
        )
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        mock_meter_provider.force_flush.assert_called()

    def test_stop_calls_flush(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop must call flush_metrics() before exit."""
        from hooks.stop import main

        mock_stdin = _make_stdin(
            {"session_id": "sess-123", "tools_used": 5},
        )
        with (
            patch.object(sys, "stdin", mock_stdin),
            patch("hooks.stop.GitLabClient"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        mock_meter_provider.force_flush.assert_called()


# ---------------------------------------------------------------------------
# Tests: Graceful handling of invalid JSON
# ---------------------------------------------------------------------------


class TestInvalidJsonHandling:
    """Tests for graceful handling of missing/invalid JSON input."""

    def test_session_start_handles_invalid_json(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionStart should exit 0 gracefully when stdin is not valid JSON."""
        from hooks.session_start import main

        mock_stdin = _make_invalid_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_end_handles_invalid_json(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionEnd should exit 0 gracefully when stdin is not valid JSON."""
        from hooks.session_end import main

        mock_stdin = _make_invalid_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_pre_tool_use_handles_invalid_json(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PreToolUse should exit 0 gracefully when stdin is not valid JSON."""
        from hooks.pre_tool_use import main

        mock_stdin = _make_invalid_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_post_tool_use_handles_invalid_json(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PostToolUse should exit 0 gracefully when stdin is not valid JSON."""
        from hooks.post_tool_use import main

        mock_stdin = _make_invalid_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stop_handles_invalid_json(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop should exit 0 gracefully when stdin is not valid JSON."""
        from hooks.stop import main

        mock_stdin = _make_invalid_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Tests: Graceful handling of empty JSON
# ---------------------------------------------------------------------------


class TestEmptyJsonHandling:
    """Tests for graceful handling of empty JSON objects ({}) and empty stdin."""

    def test_session_start_handles_empty_object(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionStart should exit 0 when stdin is an empty JSON object."""
        from hooks.session_start import main

        mock_stdin = _make_stdin({})
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_end_handles_empty_object(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionEnd should exit 0 when stdin is an empty JSON object."""
        from hooks.session_end import main

        mock_stdin = _make_stdin({})
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_pre_tool_use_handles_empty_object(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PreToolUse should exit 0 when stdin is an empty JSON object."""
        from hooks.pre_tool_use import main

        mock_stdin = _make_stdin({})
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_post_tool_use_handles_empty_object(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """PostToolUse should exit 0 when stdin is an empty JSON object."""
        from hooks.post_tool_use import main

        mock_stdin = _make_stdin({})
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stop_handles_empty_object(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop should exit 0 when stdin is an empty JSON object."""
        from hooks.stop import main

        mock_stdin = _make_stdin({})
        with (
            patch.object(sys, "stdin", mock_stdin),
            patch("hooks.stop.GitLabClient"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_start_handles_empty_stdin(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """SessionStart should exit 0 when stdin is completely empty."""
        from hooks.session_start import main

        mock_stdin = _make_empty_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stop_handles_empty_stdin(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop should exit 0 when stdin is completely empty."""
        from hooks.stop import main

        mock_stdin = _make_empty_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Tests: flush_metrics still called on invalid input
# ---------------------------------------------------------------------------


class TestFlushOnInvalidInput:
    """Tests that flush_metrics() is called even when input is invalid."""

    def test_stop_flushes_on_invalid_json(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop should still call flush_metrics() when JSON parsing fails."""
        from hooks.stop import main

        mock_stdin = _make_invalid_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        # Stop hook calls flush_metrics() even on JSON parse failure.
        mock_meter_provider.force_flush.assert_called()

    def test_stop_flushes_on_empty_stdin(
        self,
        minimal_env: dict[str, str],
        mock_meter_provider: MagicMock,
    ) -> None:
        """Stop should still call flush_metrics() when stdin is empty."""
        from hooks.stop import main

        mock_stdin = _make_empty_stdin()
        with patch.object(sys, "stdin", mock_stdin):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        # Stop hook calls flush_metrics() even on empty stdin.
        mock_meter_provider.force_flush.assert_called()
