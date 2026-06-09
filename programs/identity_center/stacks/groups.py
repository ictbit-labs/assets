"""Identity Center group stack framework."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aws_cdk as cdk
from constructs import Construct

if TYPE_CHECKING:
    from app import IdentityCenterConfig


LOGGER = logging.getLogger("identity_center.stacks.groups")


class IdentityCenterGroupsStack(cdk.Stack):
    """Initial Identity Center stack scaffold.

    This stack intentionally creates no Identity Center resources yet. Future
    tasks will add Identity Store groups, group memberships, permission sets,
    and account assignments here.
    """

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
        """Reserve extension points for future Identity Center resources."""

        self.identity_store_groups: list[Construct] = []
        self.identity_store_group_memberships: list[Construct] = []
        self.permission_set_assignments: list[Construct] = []

        LOGGER.info(
            "Resource preparation complete",
            extra={
                "identity_store_groups": len(self.identity_store_groups),
                "identity_store_group_memberships": len(self.identity_store_group_memberships),
                "permission_set_assignments": len(self.permission_set_assignments),
            },
        )
