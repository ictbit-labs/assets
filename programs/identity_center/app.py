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


class IdentityCenterResolutionError(RuntimeError):
    """Raised when memberships or assignments cannot be resolved safely."""


@dataclass(frozen=True)
class MemberConfig:
    """Desired group member reference from config.yaml."""

    user_id: str | None = None
    username: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class AssignmentConfig:
    """Desired account assignment from config.yaml."""

    account_id: str
    permission_set_arn: str | None = None
    permission_set_name: str | None = None


@dataclass(frozen=True)
class GroupConfig:
    """Desired Identity Center group from config.yaml."""

    name: str
    description: str
    members: list[MemberConfig]
    assignments: list[AssignmentConfig]


@dataclass(frozen=True)
class ResolvedMembershipConfig:
    """Desired group membership annotated with create/existing resolution."""

    user_id: str
    source: str
    membership_id: str | None = None
    username: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class ResolvedAssignmentConfig:
    """Desired account assignment annotated with create/existing resolution."""

    account_id: str
    permission_set_arn: str
    source: str
    permission_set_name: str | None = None


@dataclass(frozen=True)
class ResolvedGroupConfig:
    """Desired group annotated with create/existing resolution."""

    name: str
    description: str
    source: str
    group_id: str | None = None
    members: list[ResolvedMembershipConfig] | None = None
    assignments: list[ResolvedAssignmentConfig] | None = None


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

        unsupported_fields = sorted(set(group) - {"name", "description", "members", "assignments"})
        if unsupported_fields:
            raise ConfigError(
                f"Group entry #{index} has unsupported field(s): {', '.join(unsupported_fields)}. "
                "Use only name, description, members, and assignments; mode and group_id are not supported."
            )

        name = str(group.get("name") or "").strip()
        if not name:
            raise ConfigError(f"Group entry #{index} must include a non-empty name")

        duplicate_key = name.casefold()
        if duplicate_key in seen_names:
            raise ConfigError(f"Duplicate group name in config.yaml: {name}")
        seen_names.add(duplicate_key)

        description = str(group.get("description") or "").strip()
        members = validate_member_configs(group.get("members", []), group_name=name)
        assignments = validate_assignment_configs(group.get("assignments", []), group_name=name)
        group_configs.append(
            GroupConfig(
                name=name,
                description=description,
                members=members,
                assignments=assignments,
            )
        )

    return group_configs


def validate_member_configs(members: Any, *, group_name: str) -> list[MemberConfig]:
    """Validate desired group member declarations."""

    if members is None:
        return []
    if not isinstance(members, list):
        raise ConfigError(f"Group '{group_name}' field 'members' must be a list")

    member_configs: list[MemberConfig] = []
    seen_members: set[str] = set()
    allowed_fields = {"user_id", "username", "email"}
    for index, member in enumerate(members, start=1):
        if not isinstance(member, dict):
            raise ConfigError(f"Group '{group_name}' member #{index} must be a mapping")

        unsupported_fields = sorted(set(member) - allowed_fields)
        if unsupported_fields:
            raise ConfigError(
                f"Group '{group_name}' member #{index} has unsupported field(s): "
                f"{', '.join(unsupported_fields)}"
            )

        values = {field: str(member.get(field) or "").strip() for field in allowed_fields}
        provided = [field for field, value in values.items() if value]
        if len(provided) != 1:
            raise ConfigError(
                f"Group '{group_name}' member #{index} must include exactly one of "
                "username, email, or user_id"
            )

        duplicate_key = f"{provided[0]}:{values[provided[0]].casefold()}"
        if duplicate_key in seen_members:
            raise ConfigError(f"Duplicate member in group '{group_name}': {values[provided[0]]}")
        seen_members.add(duplicate_key)

        member_configs.append(
            MemberConfig(
                user_id=values["user_id"] or None,
                username=values["username"] or None,
                email=values["email"] or None,
            )
        )

    return member_configs


def validate_assignment_configs(assignments: Any, *, group_name: str) -> list[AssignmentConfig]:
    """Validate desired account assignment declarations."""

    if assignments is None:
        return []
    if not isinstance(assignments, list):
        raise ConfigError(f"Group '{group_name}' field 'assignments' must be a list")

    assignment_configs: list[AssignmentConfig] = []
    seen_assignments: set[str] = set()
    allowed_fields = {"account_id", "permission_set_arn", "permission_set_name"}
    for index, assignment in enumerate(assignments, start=1):
        if not isinstance(assignment, dict):
            raise ConfigError(f"Group '{group_name}' assignment #{index} must be a mapping")

        unsupported_fields = sorted(set(assignment) - allowed_fields)
        if unsupported_fields:
            raise ConfigError(
                f"Group '{group_name}' assignment #{index} has unsupported field(s): "
                f"{', '.join(unsupported_fields)}"
            )

        account_id = str(assignment.get("account_id") or "").strip()
        if not account_id.isdigit() or len(account_id) != 12:
            raise ConfigError(
                f"Group '{group_name}' assignment #{index} account_id must be a 12-digit AWS account ID"
            )

        permission_set_arn = str(assignment.get("permission_set_arn") or "").strip()
        permission_set_name = str(assignment.get("permission_set_name") or "").strip()
        if bool(permission_set_arn) == bool(permission_set_name):
            raise ConfigError(
                f"Group '{group_name}' assignment #{index} must include exactly one of "
                "permission_set_arn or permission_set_name"
            )

        permission_key = permission_set_arn or permission_set_name
        duplicate_key = f"{account_id}:{permission_key.casefold()}"
        if duplicate_key in seen_assignments:
            raise ConfigError(
                f"Duplicate assignment in group '{group_name}' for account {account_id} "
                f"and permission set {permission_key}"
            )
        seen_assignments.add(duplicate_key)

        assignment_configs.append(
            AssignmentConfig(
                account_id=account_id,
                permission_set_arn=permission_set_arn or None,
                permission_set_name=permission_set_name or None,
            )
        )

    return assignment_configs


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
    """Resolve groups, memberships, and assignments before stack resource creation."""

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
        ssoadmin = session.client("sso-admin")
        resolved_groups = [
            resolve_identity_center_group(
                identitystore,
                ssoadmin,
                identity_store_id=config.identity_store_id,
                sso_instance_arn=config.sso_instance_arn,
                group=group,
            )
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
    ssoadmin_client: Any,
    *,
    identity_store_id: str,
    sso_instance_arn: str,
    group: GroupConfig | ResolvedGroupConfig,
) -> ResolvedGroupConfig:
    """Resolve a desired group and its child resources."""

    matches = list_identity_center_groups_by_display_name(
        identitystore_client,
        identity_store_id=identity_store_id,
        display_name=group.name,
    )

    if not matches:
        LOGGER.info("Identity Center group will be created", extra={"group_name": group.name})
        resolved_members = [
            resolve_group_membership(
                identitystore_client,
                identity_store_id=identity_store_id,
                group_name=group.name,
                group_id=None,
                member=member,
            )
            for member in group.members
        ]
        resolved_assignments = [
            resolve_group_assignment(
                ssoadmin_client,
                instance_arn=sso_instance_arn,
                group_name=group.name,
                group_id=None,
                assignment=assignment,
            )
            for assignment in group.assignments
        ]
        return ResolvedGroupConfig(
            name=group.name,
            description=group.description,
            source="created",
            members=resolved_members,
            assignments=resolved_assignments,
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
    resolved_members = [
        resolve_group_membership(
            identitystore_client,
            identity_store_id=identity_store_id,
            group_name=group.name,
            group_id=group_id,
            member=member,
        )
        for member in group.members
    ]
    resolved_assignments = [
        resolve_group_assignment(
            ssoadmin_client,
            instance_arn=sso_instance_arn,
            group_name=group.name,
            group_id=group_id,
            assignment=assignment,
        )
        for assignment in group.assignments
    ]
    return ResolvedGroupConfig(
        name=group.name,
        description=group.description,
        source="existing",
        group_id=group_id,
        members=resolved_members,
        assignments=resolved_assignments,
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


def resolve_group_membership(
    identitystore_client: Any,
    *,
    identity_store_id: str,
    group_name: str,
    group_id: str | None,
    member: MemberConfig,
) -> ResolvedMembershipConfig:
    """Resolve a desired group membership to create or existing."""

    user = resolve_identity_store_user(identitystore_client, identity_store_id, member)
    user_id = str(user.get("UserId") or "")
    if not user_id:
        raise IdentityCenterResolutionError(
            "ERROR: Resolved Identity Store user did not include a UserId.\n\n"
            f"Group name: {group_name}\n\n"
            "Deployment aborted."
        )

    if group_id is None:
        LOGGER.info(
            "Identity Center group membership will be created",
            extra={"group_name": group_name, "user_id": user_id},
        )
        return ResolvedMembershipConfig(
            user_id=user_id,
            source="created",
            username=member.username,
            email=member.email,
        )

    matches = list_group_memberships_for_user(
        identitystore_client,
        identity_store_id=identity_store_id,
        group_id=group_id,
        user_id=user_id,
    )
    if not matches:
        LOGGER.info(
            "Identity Center group membership will be created",
            extra={"group_name": group_name, "group_id": group_id, "user_id": user_id},
        )
        return ResolvedMembershipConfig(
            user_id=user_id,
            source="created",
            username=member.username,
            email=member.email,
        )

    if len(matches) > 1:
        membership_ids = ", ".join(str(match.get("MembershipId", "")) for match in matches)
        raise IdentityCenterResolutionError(
            "ERROR: Duplicate Identity Center group memberships found.\n\n"
            f"Group name: {group_name}\n"
            f"Group ID: {group_id}\n"
            f"User ID: {user_id}\n"
            f"Matching membership IDs: {membership_ids}\n\n"
            "Deployment aborted."
        )

    membership_id = str(matches[0].get("MembershipId") or "")
    if not membership_id:
        raise IdentityCenterResolutionError(
            "ERROR: Existing Identity Center group membership did not include a MembershipId.\n\n"
            f"Group name: {group_name}\n"
            f"Group ID: {group_id}\n"
            f"User ID: {user_id}\n\n"
            "Deployment aborted."
        )

    LOGGER.info(
        "Identity Center group membership already exists",
        extra={"group_name": group_name, "group_id": group_id, "user_id": user_id},
    )
    return ResolvedMembershipConfig(
        user_id=user_id,
        source="existing",
        membership_id=membership_id,
        username=member.username,
        email=member.email,
    )


def resolve_identity_store_user(
    identitystore_client: Any,
    identity_store_id: str,
    member: MemberConfig,
) -> dict[str, Any]:
    """Resolve a user by user_id, username, or email."""

    if member.user_id:
        try:
            return identitystore_client.describe_user(
                IdentityStoreId=identity_store_id,
                UserId=member.user_id,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                raise IdentityCenterResolutionError(
                    "ERROR: Identity Store user was not found.\n\n"
                    f"User ID: {member.user_id}\n"
                    f"Identity Store ID: {identity_store_id}\n\n"
                    "Deployment aborted."
                ) from exc
            raise

    if member.username:
        users = list_identity_store_users(
            identitystore_client,
            identity_store_id=identity_store_id,
            filters=[{"AttributePath": "UserName", "AttributeValue": member.username}],
        )
        matches = [user for user in users if user.get("UserName") == member.username]
        return select_single_user(matches, "username", member.username, identity_store_id)

    if member.email:
        users = list_identity_store_users(identitystore_client, identity_store_id=identity_store_id)
        matches = [
            user
            for user in users
            if member.email.casefold() in {email.casefold() for email in user_email_values(user)}
        ]
        return select_single_user(matches, "email", member.email, identity_store_id)

    raise IdentityCenterResolutionError("ERROR: Member did not include username, email, or user_id")


def list_identity_store_users(
    identitystore_client: Any,
    *,
    identity_store_id: str,
    filters: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """List Identity Store users, optionally with API filters."""

    users: list[dict[str, Any]] = []
    paginator = identitystore_client.get_paginator("list_users")
    paginate_args: dict[str, Any] = {"IdentityStoreId": identity_store_id}
    if filters:
        paginate_args["Filters"] = filters
    for page in paginator.paginate(**paginate_args):
        users.extend(page.get("Users", []))
    return users


def select_single_user(
    matches: list[dict[str, Any]],
    lookup_type: str,
    lookup_value: str,
    identity_store_id: str,
) -> dict[str, Any]:
    """Select exactly one user lookup result."""

    if not matches:
        raise IdentityCenterResolutionError(
            "ERROR: Identity Store user was not found.\n\n"
            f"Lookup type: {lookup_type}\n"
            f"Lookup value: {lookup_value}\n"
            f"Identity Store ID: {identity_store_id}\n\n"
            "Deployment aborted."
        )
    if len(matches) > 1:
        user_ids = ", ".join(str(user.get("UserId", "")) for user in matches)
        raise IdentityCenterResolutionError(
            "ERROR: Multiple Identity Store users matched lookup.\n\n"
            f"Lookup type: {lookup_type}\n"
            f"Lookup value: {lookup_value}\n"
            f"Matching user IDs: {user_ids}\n\n"
            "Deployment aborted."
        )
    return matches[0]


def user_email_values(user: dict[str, Any]) -> list[str]:
    """Return email values from an Identity Store user response."""

    return [
        str(email.get("Value") or "")
        for email in user.get("Emails", [])
        if isinstance(email, dict) and email.get("Value")
    ]


def list_group_memberships_for_user(
    identitystore_client: Any,
    *,
    identity_store_id: str,
    group_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """List memberships for a group/user pair."""

    matches: list[dict[str, Any]] = []
    paginator = identitystore_client.get_paginator("list_group_memberships")
    for page in paginator.paginate(IdentityStoreId=identity_store_id, GroupId=group_id):
        for membership in page.get("GroupMemberships", []):
            member_id = membership.get("MemberId", {})
            if isinstance(member_id, dict) and member_id.get("UserId") == user_id:
                matches.append(membership)
    return matches


def resolve_group_assignment(
    ssoadmin_client: Any,
    *,
    instance_arn: str,
    group_name: str,
    group_id: str | None,
    assignment: AssignmentConfig,
) -> ResolvedAssignmentConfig:
    """Resolve a desired account assignment to create or existing."""

    permission_set_arn, permission_set_name = resolve_permission_set(
        ssoadmin_client,
        instance_arn=instance_arn,
        assignment=assignment,
    )

    if group_id is None:
        LOGGER.info(
            "Identity Center account assignment will be created",
            extra={"group_name": group_name, "account_id": assignment.account_id},
        )
        return ResolvedAssignmentConfig(
            account_id=assignment.account_id,
            permission_set_arn=permission_set_arn,
            permission_set_name=permission_set_name,
            source="created",
        )

    matches = list_account_assignments_for_group(
        ssoadmin_client,
        instance_arn=instance_arn,
        account_id=assignment.account_id,
        permission_set_arn=permission_set_arn,
        group_id=group_id,
    )
    if not matches:
        LOGGER.info(
            "Identity Center account assignment will be created",
            extra={
                "group_name": group_name,
                "group_id": group_id,
                "account_id": assignment.account_id,
                "permission_set_arn": permission_set_arn,
            },
        )
        return ResolvedAssignmentConfig(
            account_id=assignment.account_id,
            permission_set_arn=permission_set_arn,
            permission_set_name=permission_set_name,
            source="created",
        )

    if len(matches) > 1:
        raise IdentityCenterResolutionError(
            "ERROR: Duplicate Identity Center account assignments found.\n\n"
            f"Group name: {group_name}\n"
            f"Group ID: {group_id}\n"
            f"Account ID: {assignment.account_id}\n"
            f"Permission set ARN: {permission_set_arn}\n\n"
            "Deployment aborted."
        )

    LOGGER.info(
        "Identity Center account assignment already exists",
        extra={
            "group_name": group_name,
            "group_id": group_id,
            "account_id": assignment.account_id,
            "permission_set_arn": permission_set_arn,
        },
    )
    return ResolvedAssignmentConfig(
        account_id=assignment.account_id,
        permission_set_arn=permission_set_arn,
        permission_set_name=permission_set_name,
        source="existing",
    )


def resolve_permission_set(
    ssoadmin_client: Any,
    *,
    instance_arn: str,
    assignment: AssignmentConfig,
) -> tuple[str, str | None]:
    """Resolve and validate a permission set ARN."""

    if assignment.permission_set_arn:
        try:
            response = ssoadmin_client.describe_permission_set(
                InstanceArn=instance_arn,
                PermissionSetArn=assignment.permission_set_arn,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                raise IdentityCenterResolutionError(
                    "ERROR: Identity Center permission set was not found.\n\n"
                    f"Permission set ARN: {assignment.permission_set_arn}\n"
                    f"Instance ARN: {instance_arn}\n\n"
                    "Deployment aborted."
                ) from exc
            raise
        permission_set = response.get("PermissionSet", {})
        return assignment.permission_set_arn, permission_set.get("Name")

    permission_sets = list_permission_sets_by_name(
        ssoadmin_client,
        instance_arn=instance_arn,
        permission_set_name=str(assignment.permission_set_name),
    )
    if not permission_sets:
        raise IdentityCenterResolutionError(
            "ERROR: Identity Center permission set was not found.\n\n"
            f"Permission set name: {assignment.permission_set_name}\n"
            f"Instance ARN: {instance_arn}\n\n"
            "Deployment aborted."
        )
    if len(permission_sets) > 1:
        permission_set_arns = ", ".join(item["arn"] for item in permission_sets)
        raise IdentityCenterResolutionError(
            "ERROR: Multiple Identity Center permission sets matched lookup.\n\n"
            f"Permission set name: {assignment.permission_set_name}\n"
            f"Matching permission set ARNs: {permission_set_arns}\n\n"
            "Deployment aborted."
        )
    return permission_sets[0]["arn"], permission_sets[0]["name"]


def list_permission_sets_by_name(
    ssoadmin_client: Any,
    *,
    instance_arn: str,
    permission_set_name: str,
) -> list[dict[str, str]]:
    """List permission sets matching a name exactly."""

    matches: list[dict[str, str]] = []
    paginator = ssoadmin_client.get_paginator("list_permission_sets")
    for page in paginator.paginate(InstanceArn=instance_arn):
        for permission_set_arn in page.get("PermissionSets", []):
            response = ssoadmin_client.describe_permission_set(
                InstanceArn=instance_arn,
                PermissionSetArn=permission_set_arn,
            )
            permission_set = response.get("PermissionSet", {})
            if permission_set.get("Name") == permission_set_name:
                matches.append({"arn": permission_set_arn, "name": permission_set_name})
    return matches


def list_account_assignments_for_group(
    ssoadmin_client: Any,
    *,
    instance_arn: str,
    account_id: str,
    permission_set_arn: str,
    group_id: str,
) -> list[dict[str, Any]]:
    """List account assignments for a group/principal tuple."""

    matches: list[dict[str, Any]] = []
    paginator = ssoadmin_client.get_paginator("list_account_assignments")
    for page in paginator.paginate(
        InstanceArn=instance_arn,
        AccountId=account_id,
        PermissionSetArn=permission_set_arn,
    ):
        for assignment in page.get("AccountAssignments", []):
            if assignment.get("PrincipalType") == "GROUP" and assignment.get("PrincipalId") == group_id:
                matches.append(assignment)
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
    except (
        ConfigError,
        AwsAccountValidationError,
        IdentityStoreGroupResolutionError,
        IdentityCenterResolutionError,
    ) as exc:
        LOGGER.error("%s", exc)
        sys.exit(1)
