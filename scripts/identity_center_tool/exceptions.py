"""Application exceptions and AWS error formatting."""

from __future__ import annotations

from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    ProfileNotFound,
    SSOTokenLoadError,
    UnauthorizedSSOTokenError,
)


class IdcToolError(Exception):
    """Base exception for user-facing CLI errors."""


class ConfigError(IdcToolError):
    """Raised when configuration is missing or invalid."""


class DuplicateResourceError(IdcToolError):
    """Raised when a requested create would duplicate an existing resource."""


class ValidationError(IdcToolError):
    """Raised when operator input is invalid."""


class ResourceNotFoundError(IdcToolError):
    """Raised when a user, group, account, or permission set cannot be found."""


def format_aws_error(error: Exception, *, profile: str | None = None) -> str:
    """Return a concise, human-readable message for common AWS failures."""

    if isinstance(error, ProfileNotFound):
        resolved_profile = getattr(error, "profile", None) or getattr(error, "profile_name", None) or profile
        if resolved_profile:
            return f"AWS profile not found: {resolved_profile}"
        return "AWS profile not found."
    if isinstance(error, (SSOTokenLoadError, UnauthorizedSSOTokenError)):
        if profile:
            return f"AWS SSO session expired. Run:\naws sso login --profile {profile}"
        return "AWS SSO session expired. Run aws sso login for the selected profile."
    if isinstance(error, NoCredentialsError):
        return "AWS credentials were not found for the selected profile/session."
    if isinstance(error, ClientError):
        response_error = error.response.get("Error", {})
        code = response_error.get("Code", "AWS Error")
        message = response_error.get("Message", str(error))
        friendly = {
            "AccessDeniedException": "Access denied",
            "ConflictException": "Conflict",
            "ResourceNotFoundException": "Resource not found",
            "ValidationException": "Validation failed",
        }.get(code, code)
        return f"{friendly}: {message}"
    return str(error)
