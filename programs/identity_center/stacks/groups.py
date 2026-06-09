"""Identity Center group stack framework."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import aws_cdk as cdk
from aws_cdk import CfnOutput, RemovalPolicy
from aws_cdk import aws_identitystore as identitystore
from constructs import Construct

if TYPE_CHECKING:
    from app import IdentityCenterConfig, ResolvedGroupConfig


LOGGER = logging.getLogger("identity_center.stacks.groups")


def sanitize_output_part(value: object, fallback: str) -> str:
    """Convert a value into a CloudFormation logical ID compatible part."""

    sanitized = "".join(
        part[:1].upper() + part[1:] for part in re.findall(r"[A-Za-z0-9]+", str(value))
    )
    return sanitized or fallback


class IdentityCenterGroupsStack(cdk.Stack):
    """Identity Center group stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: "IdentityCenterConfig",
        **kwargs: object,
    ) -> None:
        LOGGER.info("Initializing Identity Center groups stack")
        super().__init__(scope, construct_id, **kwargs)

        self.config = config
        self._validate_config()
        self._prepare_future_resources()
        LOGGER.info("Identity Center groups stack initialized")

    def _validate_config(self) -> None:
        """Validate stack-level configuration assumptions."""

        if not self.config.region:
            raise ValueError("Identity Center stack requires a region")

        LOGGER.info(
            "Stack configuration validation successful",
            extra={
                "has_identity_store_id": bool(self.config.identity_store_id),
                "has_sso_instance_arn": bool(self.config.sso_instance_arn),
                "group_count": len(self.config.groups),
            },
        )

    def _prepare_future_resources(self) -> None:
        """Create or reference groups and reserve future resource extension points."""

        self.identity_store_groups: list[identitystore.CfnGroup] = []
        self.identity_store_group_memberships: list[Construct] = []
        self.permission_set_assignments: list[Construct] = []
        self.group_ids_by_name: dict[str, str] = {}

        for group in self.config.groups:
            self._create_or_reference_group(group)

        LOGGER.info(
            "Resource preparation complete",
            extra={
                "identity_store_groups": len(self.identity_store_groups),
                "identity_store_group_memberships": len(self.identity_store_group_memberships),
                "permission_set_assignments": len(self.permission_set_assignments),
            },
        )

    def _create_or_reference_group(self, group: "ResolvedGroupConfig") -> None:
        """Create new groups and reference existing groups resolved by app.py."""

        group_source = getattr(group, "source", "")
        if group_source == "created":
            group_id = self._create_group(group)
        elif group_source == "existing":
            group_id = str(group.group_id or "")
            LOGGER.info(
                "Referencing existing Identity Center group",
                extra={"group_name": group.name, "group_id": group_id},
            )
        else:
            raise ValueError(f"Group '{group.name}' was not resolved before stack creation")

        if not group_id:
            raise ValueError(f"Group '{group.name}' does not have a usable group ID")

        self.group_ids_by_name[group.name] = group_id
        self.add_group_outputs(
            group_name=group.name,
            group_id=group_id,
            group_source=group_source,
            identity_store_id=self.config.identity_store_id,
        )

    def _create_group(self, group: "ResolvedGroupConfig") -> str:
        """Create an Identity Store group with CloudFormation."""

        resource_id = f"{sanitize_output_part(group.name, 'Group')}Group"
        group_properties: dict[str, str] = {
            "display_name": group.name,
            "identity_store_id": self.config.identity_store_id,
        }
        if group.description:
            group_properties["description"] = group.description

        cfn_group = identitystore.CfnGroup(
            self,
            resource_id,
            **group_properties,
        )
        cfn_group.apply_removal_policy(RemovalPolicy.RETAIN)
        self.identity_store_groups.append(cfn_group)
        LOGGER.info("Creating Identity Center group", extra={"group_name": group.name})
        return cfn_group.attr_group_id

    def add_group_outputs(
        self,
        *,
        group_name: str,
        group_id: str,
        group_source: str,
        identity_store_id: str | None = None,
    ) -> None:
        """Expose CloudFormation outputs for a created Identity Store group."""

        base_name = f"IdentityCenter{sanitize_output_part(group_name, 'Group')}"
        resolved_identity_store_id = identity_store_id or self.config.identity_store_id

        self._add_output(
            f"{base_name}GroupName",
            value=group_name,
            description=f"Identity Store group name for {group_name}",
        )
        self._add_output(
            f"{base_name}GroupId",
            value=group_id,
            description=f"Identity Store group ID for {group_name}",
        )
        self._add_output(
            f"{base_name}GroupSource",
            value=group_source,
            description=f"Identity Store group source for {group_name}",
        )
        self._add_output(
            f"{base_name}IdentityStoreId",
            value=resolved_identity_store_id,
            description=f"Identity Store ID for {group_name}",
        )

    def add_group_membership_outputs(
        self,
        *,
        group_name: str,
        group_id: str,
        user_id: str,
        membership_id: str,
        user_name: str | None = None,
    ) -> None:
        """Expose CloudFormation outputs for a created group membership."""

        group_part = sanitize_output_part(group_name, "Group")
        user_part = sanitize_output_part(user_name or user_id, "User")
        base_name = f"IdentityCenter{group_part}Membership{user_part}"

        self._add_output(
            f"{base_name}GroupName",
            value=group_name,
            description=f"Identity Store group name for {group_name} membership",
        )
        self._add_output(
            f"{base_name}GroupId",
            value=group_id,
            description=f"Identity Store group ID for {group_name} membership",
        )
        self._add_output(
            f"{base_name}UserId",
            value=user_id,
            description=f"Identity Store user ID for {group_name} membership",
        )
        self._add_output(
            f"{base_name}MembershipId",
            value=membership_id,
            description="Identity Store group membership ID",
        )

    def add_account_assignment_outputs(
        self,
        *,
        principal_type: str,
        principal_id: str,
        permission_set_arn: str,
        account_id: str,
        group_name: str | None = None,
        permission_set_name: str | None = None,
    ) -> None:
        """Expose CloudFormation outputs for a created account assignment."""

        principal_part = sanitize_output_part(group_name or principal_id, "Principal")
        permission_set_part = sanitize_output_part(permission_set_name or permission_set_arn, "PermissionSet")
        account_part = sanitize_output_part(account_id, "Account")
        base_name = f"IdentityCenter{principal_part}Assignment{permission_set_part}Account{account_part}"

        self._add_output(
            f"{base_name}PrincipalType",
            value=principal_type,
            description="Identity Center account assignment principal type",
        )
        self._add_output(
            f"{base_name}PrincipalId",
            value=principal_id,
            description="Identity Center account assignment principal ID",
        )
        self._add_output(
            f"{base_name}PermissionSetArn",
            value=permission_set_arn,
            description="Identity Center account assignment permission set ARN",
        )
        self._add_output(
            f"{base_name}AccountId",
            value=account_id,
            description="Identity Center account assignment AWS account ID",
        )

    def _add_output(self, logical_id: str, *, value: str, description: str) -> None:
        """Create a CloudFormation output with a sanitized logical ID."""

        CfnOutput(
            self,
            sanitize_output_part(logical_id, "IdentityCenterOutput"),
            value=str(value),
            description=description,
        )
