"""Configuration module for Claude Code hook scripts.

Loads environment variables with sensible defaults and provides validation.
All configuration is read from environment variables to support
containerized and multi-environment deployments.

Required Environment Variables:
    OTEL_EXPORTER_OTLP_ENDPOINT: OTel Collector gRPC endpoint
    GITLAB_TOKEN: GitLab personal access token (optional, skips GitLab if unset)
    GITLAB_HOST: GitLab instance host URL
    GITLAB_PROJECT: Target GitLab project in owner/repo format
    CLAUDE_USER_NAME: User identifier for individual-level metrics
"""

import os
import warnings


class Config:
    """Application configuration loaded from environment variables.

    Attributes:
        otel_endpoint: OTel Collector gRPC endpoint URL.
        gitlab_token: GitLab personal access token (empty string if unset).
        gitlab_host: GitLab instance host URL.
        gitlab_project: Target GitLab project (owner/repo format).
        user_name: User identifier for metrics labeling.
    """

    # Default values for environment variables
    _DEFAULTS = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
        "GITLAB_TOKEN": "",
        "GITLAB_HOST": "https://gitlab.com",
        "GITLAB_PROJECT": "",
        "CLAUDE_USER_NAME": "",
    }

    def __init__(self) -> None:
        """Initialize configuration from environment variables."""
        self.otel_endpoint: str = os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            self._DEFAULTS["OTEL_EXPORTER_OTLP_ENDPOINT"],
        )

        self.gitlab_token: str = os.environ.get(
            "GITLAB_TOKEN",
            self._DEFAULTS["GITLAB_TOKEN"],
        )

        self.gitlab_host: str = os.environ.get(
            "GITLAB_HOST",
            self._DEFAULTS["GITLAB_HOST"],
        )

        self.gitlab_project: str = os.environ.get(
            "GITLAB_PROJECT",
            self._DEFAULTS["GITLAB_PROJECT"],
        )

        self.user_name: str = os.environ.get(
            "CLAUDE_USER_NAME",
            self._DEFAULTS["CLAUDE_USER_NAME"],
        )

    @property
    def has_gitlab_config(self) -> bool:
        """Check if GitLab integration is fully configured.

        Returns:
            True if both GITLAB_TOKEN and GITLAB_PROJECT are set.
        """
        return bool(self.gitlab_token and self.gitlab_project)

    @property
    def safe_user_name(self) -> str:
        """Get user name with fallback for metric labels.

        Returns:
            The configured user name, or 'unknown' if not set.
        """
        return self.user_name or "unknown"

    @property
    def safe_project(self) -> str:
        """Get project name with fallback for metric labels.

        Returns:
            The configured GitLab project, or 'unknown' if not set.
        """
        return self.gitlab_project or "unknown"

    def validate(self) -> list[str]:
        """Validate configuration and return list of warnings.

        Checks for missing or potentially misconfigured values.
        Does not raise exceptions — warnings are returned for the caller
        to handle gracefully, matching the best-effort metric collection
        approach described in the spec.

        Returns:
            List of warning messages for missing or suspicious configuration.
        """
        warnings_list: list[str] = []

        if not self.gitlab_token:
            warnings_list.append(
                "GITLAB_TOKEN not set — GitLab issue integration will be skipped"
            )

        if not self.gitlab_project:
            warnings_list.append(
                "GITLAB_PROJECT not set — GitLab issue integration will be skipped"
            )

        if not self.user_name:
            warnings_list.append(
                "CLAUDE_USER_NAME not set — metrics will use 'unknown' for user label"
            )

        if self.gitlab_token and not self.gitlab_project:
            warnings_list.append(
                "GITLAB_TOKEN is set but GITLAB_PROJECT is not — "
                "GitLab integration incomplete"
            )

        if not self.otel_endpoint.startswith(("http://", "https://")):
            warnings_list.append(
                f"OTEL_EXPORTER_OTLP_ENDPOINT '{self.otel_endpoint}' "
                "does not start with http:// or https://"
            )

        return warnings_list

    def warn_missing(self) -> None:
        """Emit warnings for missing configuration via the warnings module.

        Useful during hook script startup to alert about missing config
        without failing the hook execution.
        """
        for warning_msg in self.validate():
            warnings.warn(warning_msg, stacklevel=2)
