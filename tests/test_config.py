"""Tests for the configuration module (lib.config).

Verifies:
    - Environment variables load correctly with valid values.
    - Default values are applied when environment variables are missing.
    - GITLAB_TOKEN presence controls has_gitlab_config.
    - OTEL_EXPORTER_OTLP_ENDPOINT falls back to default.
    - validate() returns appropriate warning messages.
    - safe_user_name / safe_project provide fallback values.
"""

import warnings

import pytest

from lib.config import Config


# ---------------------------------------------------------------------------
# Tests: Environment variable loading with valid values
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Tests for Config loading environment variables correctly."""

    def test_loads_otel_endpoint_from_env(self, env_vars: dict[str, str]) -> None:
        """Config should read OTEL_EXPORTER_OTLP_ENDPOINT from environment."""
        config = Config()
        assert config.otel_endpoint == "http://localhost:4317"

    def test_loads_gitlab_token_from_env(self, env_vars: dict[str, str]) -> None:
        """Config should read GITLAB_TOKEN from environment."""
        config = Config()
        assert config.gitlab_token == "glpat-test-token-12345"

    def test_loads_gitlab_host_from_env(self, env_vars: dict[str, str]) -> None:
        """Config should read GITLAB_HOST from environment."""
        config = Config()
        assert config.gitlab_host == "https://gitlab.example.com"

    def test_loads_gitlab_project_from_env(self, env_vars: dict[str, str]) -> None:
        """Config should read GITLAB_PROJECT from environment."""
        config = Config()
        assert config.gitlab_project == "testorg/testproject"

    def test_loads_user_name_from_env(self, env_vars: dict[str, str]) -> None:
        """Config should read CLAUDE_USER_NAME from environment."""
        config = Config()
        assert config.user_name == "test-developer"


# ---------------------------------------------------------------------------
# Tests: Default values when environment variables are missing
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """Tests for default values when environment variables are not set."""

    def test_otel_endpoint_default(self, minimal_env: dict[str, str]) -> None:
        """OTEL_EXPORTER_OTLP_ENDPOINT should default to http://localhost:4317."""
        config = Config()
        assert config.otel_endpoint == "http://localhost:4317"

    def test_gitlab_token_default_empty(self, minimal_env: dict[str, str]) -> None:
        """GITLAB_TOKEN should default to empty string when not set."""
        config = Config()
        assert config.gitlab_token == ""

    def test_gitlab_host_default(self, minimal_env: dict[str, str]) -> None:
        """GITLAB_HOST should default to https://gitlab.com when not set."""
        config = Config()
        assert config.gitlab_host == "https://gitlab.com"

    def test_gitlab_project_default_empty(self, minimal_env: dict[str, str]) -> None:
        """GITLAB_PROJECT should default to empty string when not set."""
        config = Config()
        assert config.gitlab_project == ""

    def test_user_name_default_empty(self, minimal_env: dict[str, str]) -> None:
        """CLAUDE_USER_NAME should default to empty string when not set."""
        config = Config()
        assert config.user_name == ""


# ---------------------------------------------------------------------------
# Tests: GITLAB_TOKEN check (has_gitlab_config)
# ---------------------------------------------------------------------------


class TestGitlabConfigCheck:
    """Tests for has_gitlab_config property."""

    def test_has_gitlab_config_true_when_both_set(
        self, env_vars: dict[str, str]
    ) -> None:
        """has_gitlab_config should be True when GITLAB_TOKEN and GITLAB_PROJECT are set."""
        config = Config()
        assert config.has_gitlab_config is True

    def test_has_gitlab_config_false_when_token_missing(
        self, env_vars_no_gitlab: dict[str, str]
    ) -> None:
        """has_gitlab_config should be False when GITLAB_TOKEN is not set."""
        config = Config()
        assert config.has_gitlab_config is False

    def test_has_gitlab_config_false_when_project_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """has_gitlab_config should be False when GITLAB_PROJECT is not set."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-some-token")
        monkeypatch.delenv("GITLAB_PROJECT", raising=False)

        config = Config()
        assert config.has_gitlab_config is False

    def test_has_gitlab_config_false_when_both_missing(
        self, minimal_env: dict[str, str]
    ) -> None:
        """has_gitlab_config should be False when neither token nor project is set."""
        config = Config()
        assert config.has_gitlab_config is False


# ---------------------------------------------------------------------------
# Tests: Safe fallback properties
# ---------------------------------------------------------------------------


class TestSafeFallbacks:
    """Tests for safe_user_name and safe_project fallback properties."""

    def test_safe_user_name_returns_configured_name(
        self, env_vars: dict[str, str]
    ) -> None:
        """safe_user_name should return the configured CLAUDE_USER_NAME."""
        config = Config()
        assert config.safe_user_name == "test-developer"

    def test_safe_user_name_returns_unknown_when_empty(
        self, minimal_env: dict[str, str]
    ) -> None:
        """safe_user_name should return 'unknown' when CLAUDE_USER_NAME is not set."""
        config = Config()
        assert config.safe_user_name == "unknown"

    def test_safe_project_returns_configured_project(
        self, env_vars: dict[str, str]
    ) -> None:
        """safe_project should return the configured GITLAB_PROJECT."""
        config = Config()
        assert config.safe_project == "testorg/testproject"

    def test_safe_project_returns_unknown_when_empty(
        self, minimal_env: dict[str, str]
    ) -> None:
        """safe_project should return 'unknown' when GITLAB_PROJECT is not set."""
        config = Config()
        assert config.safe_project == "unknown"


# ---------------------------------------------------------------------------
# Tests: validate()
# ---------------------------------------------------------------------------


class TestValidate:
    """Tests for Config.validate() warning message generation."""

    def test_validate_no_warnings_with_full_config(
        self, env_vars: dict[str, str]
    ) -> None:
        """validate() should return an empty list when all variables are set."""
        config = Config()
        warnings_list = config.validate()
        assert warnings_list == []

    def test_validate_warns_missing_gitlab_token(
        self, env_vars_no_gitlab: dict[str, str]
    ) -> None:
        """validate() should warn when GITLAB_TOKEN is not set."""
        config = Config()
        warnings_list = config.validate()
        assert any("GITLAB_TOKEN not set" in w for w in warnings_list)

    def test_validate_warns_missing_gitlab_project(
        self, env_vars_no_gitlab: dict[str, str]
    ) -> None:
        """validate() should warn when GITLAB_PROJECT is not set."""
        config = Config()
        warnings_list = config.validate()
        assert any("GITLAB_PROJECT not set" in w for w in warnings_list)

    def test_validate_warns_missing_user_name(
        self, minimal_env: dict[str, str]
    ) -> None:
        """validate() should warn when CLAUDE_USER_NAME is not set."""
        config = Config()
        warnings_list = config.validate()
        assert any("CLAUDE_USER_NAME not set" in w for w in warnings_list)

    def test_validate_warns_incomplete_gitlab(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """validate() should warn when GITLAB_TOKEN is set but GITLAB_PROJECT is not."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-some-token")
        monkeypatch.delenv("GITLAB_PROJECT", raising=False)
        monkeypatch.delenv("CLAUDE_USER_NAME", raising=False)

        config = Config()
        warnings_list = config.validate()
        assert any("GITLAB_TOKEN is set but GITLAB_PROJECT is not" in w for w in warnings_list)

    def test_validate_warns_invalid_otel_endpoint(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """validate() should warn when OTEL_EXPORTER_OTLP_ENDPOINT lacks http(s) prefix."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "grpc://bad-endpoint:4317")
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.delenv("GITLAB_PROJECT", raising=False)
        monkeypatch.delenv("CLAUDE_USER_NAME", raising=False)

        config = Config()
        warnings_list = config.validate()
        assert any("does not start with http://" in w for w in warnings_list)


# ---------------------------------------------------------------------------
# Tests: warn_missing()
# ---------------------------------------------------------------------------


class TestWarnMissing:
    """Tests for Config.warn_missing() emitting warnings."""

    def test_warn_missing_emits_warnings_for_missing_config(
        self, minimal_env: dict[str, str]
    ) -> None:
        """warn_missing() should emit warnings via the warnings module."""
        config = Config()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config.warn_missing()

        messages = [str(w.message) for w in caught]
        assert any("GITLAB_TOKEN not set" in m for m in messages)
        assert any("GITLAB_PROJECT not set" in m for m in messages)
        assert any("CLAUDE_USER_NAME not set" in m for m in messages)

    def test_warn_missing_no_warnings_with_full_config(
        self, env_vars: dict[str, str]
    ) -> None:
        """warn_missing() should not emit warnings when all variables are set."""
        config = Config()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config.warn_missing()

        assert len(caught) == 0
