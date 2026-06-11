"""Identity Center group stack framework."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import aws_cdk as cdk
from aws_cdk import CfnOutput
from aws_cdk import aws_identitystore as identitystore
from aws_cdk import aws_sso as sso
from constructs import Construct

if TYPE_CHECKING:
    from app import (
        IdentityCenterConfig,
        ResolvedAssignmentConfig,
        ResolvedGroupConfig,
        ResolvedMembershipConfig,
    )


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
        self.identity_store_group_memberships: list[identitystore.CfnGroupMembership] = []
        self.permission_set_assignments: list[sso.CfnAssignment] = []
        self.group_ids_by_name: dict[str, str] = {}

        for group in self.config.groups:
            self._create_or_reference_group(group)
            self._create_or_reference_group_memberships(group)
            self._create_or_reference_group_assignments(group)

        LOGGER.info(
            "Resource preparation complete",
            extra={
                "identity_store_groups": len(self.identity_store_groups),
                "identity_store_group_memberships": len(self.identity_store_group_memberships),
                "permission_set_assignments": len(self.permission_set_assignments),
            },
        )

    def _create_or_reference_group(self, group: "ResolvedGroupConfig") -> None:
        """Create stack-owned groups and reference external groups resolved by app.py."""

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
        self.identity_store_groups.append(cfn_group)
        LOGGER.info("Creating Identity Center group", extra={"group_name": group.name})
        return cfn_group.attr_group_id

    def _create_or_reference_group_memberships(self, group: "ResolvedGroupConfig") -> None:
        """Create stack-owned memberships and reference external memberships."""

        group_id = self.group_ids_by_name[group.name]
        for membership in group.members or []:
            membership_source = getattr(membership, "source", "")
            if membership_source == "created":
                membership_id = self._create_group_membership(group, group_id, membership)
            elif membership_source == "existing":
                membership_id = str(membership.membership_id or "")
                LOGGER.info(
                    "Referencing existing Identity Center group membership",
                    extra={
                        "group_name": group.name,
                        "group_id": group_id,
                        "user_id": membership.user_id,
                        "membership_id": membership_id,
                    },
                )
            else:
                raise ValueError(
                    f"Membership for user '{membership.user_id}' in group '{group.name}' "
                    "was not resolved before stack creation"
                )

            if not membership_id:
                raise ValueError(
                    f"Membership for user '{membership.user_id}' in group '{group.name}' "
                    "does not have a usable membership ID"
                )

            self.add_group_membership_outputs(
                group_name=group.name,
                group_id=group_id,
                user_id=membership.user_id,
                membership_id=membership_id,
                membership_source=membership_source,
                user_name=membership.username or membership.email,
            )

    def _create_group_membership(
        self,
        group: "ResolvedGroupConfig",
        group_id: str,
        membership: "ResolvedMembershipConfig",
    ) -> str:
        """Create an Identity Store group membership with CloudFormation."""

        resource_id = (
            f"{sanitize_output_part(group.name, 'Group')}"
            f"{sanitize_output_part(membership.username or membership.email or membership.user_id, 'User')}"
            "Membership"
        )
        cfn_membership = identitystore.CfnGroupMembership(
            self,
            resource_id,
            group_id=group_id,
            identity_store_id=self.config.identity_store_id,
            member_id=identitystore.CfnGroupMembership.MemberIdProperty(user_id=membership.user_id),
        )
        self.identity_store_group_memberships.append(cfn_membership)
        LOGGER.info(
            "Creating Identity Center group membership",
            extra={"group_name": group.name, "group_id": group_id, "user_id": membership.user_id},
        )
        return cfn_membership.attr_membership_id

    def _create_or_reference_group_assignments(self, group: "ResolvedGroupConfig") -> None:
        """Create stack-owned account assignments and reference external assignments."""

        group_id = self.group_ids_by_name[group.name]
        for assignment in group.assignments or []:
            assignment_source = getattr(assignment, "source", "")
            if assignment_source == "created":
                self._create_account_assignment(group, group_id, assignment)
            elif assignment_source == "existing":
                LOGGER.info(
                    "Referencing existing Identity Center account assignment",
                    extra={
                        "group_name": group.name,
                        "group_id": group_id,
                        "account_id": assignment.account_id,
                        "permission_set_arn": assignment.permission_set_arn,
                    },
                )
            else:
                raise ValueError(
                    f"Assignment for group '{group.name}' account '{assignment.account_id}' "
                    "was not resolved before stack creation"
                )

            self.add_account_assignment_outputs(
                group_name=group.name,
                group_id=group_id,
                account_id=assignment.account_id,
                permission_set_arn=assignment.permission_set_arn,
                assignment_source=assignment_source,
                permission_set_name=assignment.permission_set_name,
            )

    def _create_account_assignment(
        self,
        group: "ResolvedGroupConfig",
        group_id: str,
        assignment: "ResolvedAssignmentConfig",
    ) -> None:
        """Create an Identity Center account assignment with CloudFormation."""

        resource_id = (
            f"{sanitize_output_part(group.name, 'Group')}Assignment"
            f"{sanitize_output_part(assignment.permission_set_name or assignment.permission_set_arn, 'PermissionSet')}"
            f"Account{sanitize_output_part(assignment.account_id, 'Account')}"
        )
        cfn_assignment = sso.CfnAssignment(
            self,
            resource_id,
            instance_arn=self.config.sso_instance_arn,
            permission_set_arn=assignment.permission_set_arn,
            principal_id=group_id,
            principal_type="GROUP",
            target_id=assignment.account_id,
            target_type="AWS_ACCOUNT",
        )
        self.permission_set_assignments.append(cfn_assignment)
        LOGGER.info(
            "Creating Identity Center account assignment",
            extra={
                "group_name": group.name,
                "group_id": group_id,
                "account_id": assignment.account_id,
                "permission_set_arn": assignment.permission_set_arn,
            },
        )

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
        membership_source: str,
        user_name: str | None = None,
    ) -> None:
        """Expose CloudFormation outputs for a group membership."""

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
        self._add_output(
            f"{base_name}MembershipSource",
            value=membership_source,
            description="Identity Store group membership source",
        )

    def add_account_assignment_outputs(
        self,
        *,
        group_name: str,
        group_id: str,
        account_id: str,
        permission_set_arn: str,
        assignment_source: str,
        permission_set_name: str | None = None,
    ) -> None:
        """Expose CloudFormation outputs for an account assignment."""

        group_part = sanitize_output_part(group_name, "Group")
        permission_set_part = sanitize_output_part(permission_set_name or permission_set_arn, "PermissionSet")
        account_part = sanitize_output_part(account_id, "Account")
        base_name = f"IdentityCenter{group_part}Assignment{permission_set_part}Account{account_part}"

        self._add_output(
            f"{base_name}GroupName",
            value=group_name,
            description="Identity Center account assignment group name",
        )
        self._add_output(
            f"{base_name}GroupId",
            value=group_id,
            description="Identity Center account assignment group ID",
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
        self._add_output(
            f"{base_name}AssignmentSource",
            value=assignment_source,
            description="Identity Center account assignment source",
        )

    def _add_output(self, logical_id: str, *, value: str, description: str) -> None:
        """Create a CloudFormation output with a sanitized logical ID."""

        CfnOutput(
            self,
            sanitize_output_part(logical_id, "IdentityCenterOutput"),
            value=str(value),
            description=description,
        )
