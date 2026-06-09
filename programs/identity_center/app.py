#!/usr/bin/env python3
"""Identity Center CDK application entry point."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import boto3
import yaml
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ProfileNotFound

from stacks.groups import IdentityCenterGroupsStack


LOGGER = logging.getLogger("identity_center.app")
APP_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = APP_ROOT / "config" / "config.yaml"
REQUIRED_CONFIG_FIELDS = (
    "aws_profile",
    "expected_account_id",
    "region",
    "identity_store_id",
    "sso_instance_arn",
)


class ConfigError(ValueError):
    """Raised when Identity Center configuration is invalid."""


class AwsAccountValidationError(RuntimeError):
    """Raised when active AWS identity does not match deployment config."""


@dataclass(frozen=True)
class IdentityCenterConfig:
    """Validated Identity Center deployment configuration."""

    aws_profile: str
    expected_account_id: str
    region: str
    identity_store_id: str
    sso_instance_arn: str
    groups: list[dict[str, Any]]


def configure_logging() -> None:
    """Configure structured, concise logging for synth/deploy output."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def context_value(app: cdk.App, key: str) -> Any:
    """Read a CDK context value."""

    return app.node.try_get_context(key)


def parse_context_override(key: str, value: Any) -> Any:
    """Parse context values that may arrive as strings from CDK -c flags."""

    if key == "groups" and isinstance(value, str):
        parsed_value = yaml.safe_load(value)
        return [] if parsed_value is None else parsed_value

    return value


def load_config(config_path: Path) -> dict[str, Any]:
    """Load raw YAML configuration from disk."""

    LOGGER.info("Loading configuration", extra={"config_path": str(config_path)})
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")

    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        raise ConfigError(f"Config file must contain a YAML mapping: {config_path}")

    LOGGER.info("Configuration loaded", extra={"config_path": str(config_path)})
    return raw_config


def apply_context_overrides(app: cdk.App, config: dict[str, Any]) -> dict[str, Any]:
    """Apply deploy_cdk.py/CDK -c context values over config.yaml values."""

    resolved = dict(config)
    for key in (
        "aws_profile",
        "profile",
        "expected_account_id",
        "region",
        "identity_store_id",
        "sso_instance_arn",
        "groups",
    ):
        value = context_value(app, key)
        if value is not None:
            resolved_key = "aws_profile" if key == "profile" else key
            resolved[resolved_key] = parse_context_override(key, value)

    LOGGER.info("Context overrides applied")
    return resolved


def validate_config(config: dict[str, Any]) -> IdentityCenterConfig:
    """Validate required configuration structure."""

    if "profile" in config:
        config["aws_profile"] = config["profile"]

    aws_profile = str(config.get("aws_profile") or "").strip()
    if not aws_profile:
        raise ConfigError(
            "Missing AWS profile. Pass a profile through deploy_cdk.py/CDK context "
            "or set aws_profile in config/config.yaml."
        )

    missing_fields = [
        field for field in REQUIRED_CONFIG_FIELDS if field != "aws_profile" and field not in config
    ]
    if missing_fields:
        raise ConfigError(f"Missing required config field(s): {', '.join(missing_fields)}")
    if aws_profile == "default":
        raise ConfigError(
            "Refusing to use AWS profile 'default'. Set aws_profile to an explicit "
            "deployment profile in config/config.yaml or pass one through deploy_cdk.py."
        )

    expected_account_id = str(config.get("expected_account_id") or "").strip()
    if not expected_account_id:
        raise ConfigError("Config field 'expected_account_id' must not be empty")
    if not expected_account_id.isdigit() or len(expected_account_id) != 12:
        raise ConfigError("Config field 'expected_account_id' must be a 12-digit AWS account ID")

    region = config.get("region")
    if region in (None, ""):
        raise ConfigError("Config field 'region' must not be empty")

    groups = config.get("groups", [])
    if not isinstance(groups, list):
        raise ConfigError("Config field 'groups' must be a list")

    LOGGER.info(
        "Configuration validation successful",
        extra={
            "aws_profile": aws_profile,
            "expected_account_id": expected_account_id,
            "has_identity_store_id": bool(config.get("identity_store_id")),
            "has_sso_instance_arn": bool(config.get("sso_instance_arn")),
            "group_count": len(groups),
        },
    )
    return IdentityCenterConfig(
        aws_profile=aws_profile,
        expected_account_id=expected_account_id,
        region=str(region),
        identity_store_id=str(config.get("identity_store_id") or ""),
        sso_instance_arn=str(config.get("sso_instance_arn") or ""),
        groups=groups,
    )


def configure_aws_environment(config: IdentityCenterConfig) -> None:
    """Expose the resolved AWS profile and target environment to CDK."""

    os.environ["AWS_PROFILE"] = config.aws_profile
    os.environ["AWS_REGION"] = config.region
    os.environ["AWS_DEFAULT_REGION"] = config.region
    os.environ["CDK_DEFAULT_ACCOUNT"] = config.expected_account_id
    os.environ["CDK_DEFAULT_REGION"] = config.region
    LOGGER.info(
        "AWS environment configured",
        extra={
            "aws_profile": config.aws_profile,
            "expected_account_id": config.expected_account_id,
            "region": config.region,
        },
    )


def validate_aws_account(config: IdentityCenterConfig) -> dict[str, Any]:
    """Validate that the selected AWS profile resolves to the expected account."""

    LOGGER.info("Validating AWS account with STS", extra={"aws_profile": config.aws_profile})
    try:
        session = boto3.Session(profile_name=config.aws_profile, region_name=config.region)
        caller = session.client("sts").get_caller_identity()
    except ProfileNotFound as exc:
        raise AwsAccountValidationError(
            f"ERROR: AWS profile not found.\n\nConfigured profile: {config.aws_profile}\n\nDeployment aborted."
        ) from exc
    except NoCredentialsError as exc:
        raise AwsAccountValidationError(
            f"ERROR: AWS credentials were not found.\n\nConfigured profile: {config.aws_profile}\n\nDeployment aborted."
        ) from exc
    except (BotoCoreError, ClientError) as exc:
        raise AwsAccountValidationError(
            f"ERROR: Unable to validate AWS account with STS.\n\n"
            f"Configured profile: {config.aws_profile}\n"
            f"Expected account: {config.expected_account_id}\n"
            f"Region: {config.region}\n"
            f"Reason: {exc}\n\n"
            "Deployment aborted."
        ) from exc

    actual_account_id = str(caller.get("Account", ""))
    if actual_account_id != config.expected_account_id:
        raise AwsAccountValidationError(
            "ERROR: AWS account mismatch.\n\n"
            f"Configured profile: {config.aws_profile}\n"
            f"Expected account: {config.expected_account_id}\n"
            f"Actual account: {actual_account_id}\n\n"
            "Deployment aborted."
        )

    LOGGER.info(
        "AWS account validation successful",
        extra={
            "aws_profile": config.aws_profile,
            "expected_account_id": config.expected_account_id,
            "actual_account_id": actual_account_id,
        },
    )
    return caller


def main() -> None:
    """Create and synthesize the Identity Center CDK app."""

    configure_logging()
    app = cdk.App()

    config_path_context = context_value(app, "config_path")
    config_path = Path(config_path_context) if config_path_context else DEFAULT_CONFIG_PATH
    if not config_path.is_absolute():
        config_path = APP_ROOT / config_path

    raw_config = load_config(config_path)
    config = validate_config(apply_context_overrides(app, raw_config))
    configure_aws_environment(config)
    validate_aws_account(config)

    stack_name = context_value(app, "stack_name") or "IdentityCenterGroupsStack"
    IdentityCenterGroupsStack(
        app,
        stack_name,
        config=config,
        env=cdk.Environment(account=config.expected_account_id, region=config.region),
        synthesizer=cdk.BootstraplessSynthesizer(),
    )
    LOGGER.info("Stack instantiated", extra={"stack_name": stack_name})

    app.synth()
    LOGGER.info("CDK synth successful")


if __name__ == "__main__":
    try:
        main()
    except (ConfigError, AwsAccountValidationError) as exc:
        LOGGER.error("%s", exc)
        sys.exit(1)
