#!/usr/bin/env python3
"""Identity Center CDK application entry point."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, replace
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


class IdentityStoreGroupResolutionError(RuntimeError):
    """Raised when desired Identity Store groups cannot be resolved safely."""


@dataclass(frozen=True)
class GroupConfig:
    """Desired Identity Center group from config.yaml."""

    name: str
    description: str


@dataclass(frozen=True)
class ResolvedGroupConfig:
    """Desired group annotated with create/existing resolution."""

    name: str
    description: str
    source: str
    group_id: str | None = None


@dataclass(frozen=True)
class IdentityCenterConfig:
    """Validated Identity Center deployment configuration."""

    aws_profile: str
    expected_account_id: str
    region: str
    identity_store_id: str
    sso_instance_arn: str
    groups: list[GroupConfig | ResolvedGroupConfig]


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

    identity_store_id = str(config.get("identity_store_id") or "").strip()
    if not identity_store_id:
        raise ConfigError("Config field 'identity_store_id' must not be empty")

    sso_instance_arn = str(config.get("sso_instance_arn") or "").strip()
    if not sso_instance_arn:
        raise ConfigError("Config field 'sso_instance_arn' must not be empty")

    groups = config.get("groups", [])
    if not isinstance(groups, list):
        raise ConfigError("Config field 'groups' must be a list")
    group_configs = validate_group_configs(groups)

    LOGGER.info(
        "Configuration validation successful",
        extra={
            "aws_profile": aws_profile,
            "expected_account_id": expected_account_id,
            "has_identity_store_id": bool(config.get("identity_store_id")),
            "has_sso_instance_arn": bool(config.get("sso_instance_arn")),
            "group_count": len(group_configs),
        },
    )
    return IdentityCenterConfig(
        aws_profile=aws_profile,
        expected_account_id=expected_account_id,
        region=str(region),
        identity_store_id=identity_store_id,
        sso_instance_arn=sso_instance_arn,
        groups=group_configs,
    )


def validate_group_configs(groups: list[Any]) -> list[GroupConfig]:
    """Validate desired group declarations from config.yaml."""

    group_configs: list[GroupConfig] = []
    seen_names: set[str] = set()

    for index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            raise ConfigError(f"Group entry #{index} must be a mapping with name and description")

        unsupported_fields = sorted(set(group) - {"name", "description"})
        if unsupported_fields:
            raise ConfigError(
                f"Group entry #{index} has unsupported field(s): {', '.join(unsupported_fields)}. "
                "Use only name and description; mode and group_id are not supported."
            )

        name = str(group.get("name") or "").strip()
        if not name:
            raise ConfigError(f"Group entry #{index} must include a non-empty name")

        duplicate_key = name.casefold()
        if duplicate_key in seen_names:
            raise ConfigError(f"Duplicate group name in config.yaml: {name}")
        seen_names.add(duplicate_key)

        description = str(group.get("description") or "").strip()
        group_configs.append(GroupConfig(name=name, description=description))

    return group_configs


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


def resolve_identity_center_groups(config: IdentityCenterConfig) -> IdentityCenterConfig:
    """Resolve desired groups against Identity Store before stack resource creation."""

    if not config.groups:
        LOGGER.info("No Identity Center groups configured")
        return config

    LOGGER.info(
        "Resolving Identity Center groups",
        extra={"identity_store_id": config.identity_store_id, "group_count": len(config.groups)},
    )
    try:
        session = boto3.Session(profile_name=config.aws_profile, region_name=config.region)
        identitystore = session.client("identitystore")
        resolved_groups = [
            resolve_identity_center_group(identitystore, config.identity_store_id, group)
            for group in config.groups
        ]
    except (BotoCoreError, ClientError) as exc:
        raise IdentityStoreGroupResolutionError(
            f"ERROR: Unable to resolve Identity Center groups.\n\n"
            f"Configured profile: {config.aws_profile}\n"
            f"Identity Store ID: {config.identity_store_id}\n"
            f"Reason: {exc}\n\n"
            "Deployment aborted."
        ) from exc

    created_count = sum(1 for group in resolved_groups if group.source == "created")
    existing_count = sum(1 for group in resolved_groups if group.source == "existing")
    LOGGER.info(
        "Identity Center group resolution complete",
        extra={"created_count": created_count, "existing_count": existing_count},
    )
    return replace(config, groups=resolved_groups)


def resolve_identity_center_group(
    identitystore_client: Any,
    identity_store_id: str,
    group: GroupConfig | ResolvedGroupConfig,
) -> ResolvedGroupConfig:
    """Resolve a desired group to either an existing group ID or a create action."""

    matches = list_identity_center_groups_by_display_name(
        identitystore_client,
        identity_store_id=identity_store_id,
        display_name=group.name,
    )

    if not matches:
        LOGGER.info("Identity Center group will be created", extra={"group_name": group.name})
        return ResolvedGroupConfig(
            name=group.name,
            description=group.description,
            source="created",
        )

    if len(matches) > 1:
        group_ids = ", ".join(str(match.get("GroupId", "")) for match in matches)
        raise IdentityStoreGroupResolutionError(
            "ERROR: Duplicate Identity Center groups found.\n\n"
            f"Group name: {group.name}\n"
            f"Identity Store ID: {identity_store_id}\n"
            f"Matching group IDs: {group_ids}\n\n"
            "Deployment aborted."
        )

    group_id = str(matches[0].get("GroupId") or "")
    if not group_id:
        raise IdentityStoreGroupResolutionError(
            "ERROR: Existing Identity Center group did not include a GroupId.\n\n"
            f"Group name: {group.name}\n"
            f"Identity Store ID: {identity_store_id}\n\n"
            "Deployment aborted."
        )

    LOGGER.info(
        "Identity Center group already exists",
        extra={"group_name": group.name, "group_id": group_id},
    )
    return ResolvedGroupConfig(
        name=group.name,
        description=group.description,
        source="existing",
        group_id=group_id,
    )


def list_identity_center_groups_by_display_name(
    identitystore_client: Any,
    *,
    identity_store_id: str,
    display_name: str,
) -> list[dict[str, Any]]:
    """List Identity Store groups matching a display name exactly."""

    matches: list[dict[str, Any]] = []
    paginator = identitystore_client.get_paginator("list_groups")
    for page in paginator.paginate(
        IdentityStoreId=identity_store_id,
        Filters=[{"AttributePath": "DisplayName", "AttributeValue": display_name}],
    ):
        for group in page.get("Groups", []):
            if group.get("DisplayName") == display_name:
                matches.append(group)

    return matches


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
    config = resolve_identity_center_groups(config)

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
    except (ConfigError, AwsAccountValidationError, IdentityStoreGroupResolutionError) as exc:
        LOGGER.error("%s", exc)
        sys.exit(1)
