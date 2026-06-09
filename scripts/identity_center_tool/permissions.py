"""SSO Admin and Organizations operations for account assignments."""

from __future__ import annotations

from typing import Any

from .exceptions import ResourceNotFoundError, ValidationError
from .models import AccountSummary, AssignmentSummary, PermissionSetSummary


class PermissionSetService:
    """Manage IAM Identity Center permission set assignments."""

    def __init__(
        self,
        *,
        sso_admin_client: Any,
        organizations_client: Any,
        identity_store_id: str,
        configured_instance_arn: str | None = None,
    ):
        self.sso_admin = sso_admin_client
        self.organizations = organizations_client
        self.identity_store_id = identity_store_id
        self.configured_instance_arn = configured_instance_arn

    def instance_arn(self) -> str:
        if self.configured_instance_arn:
            return self.configured_instance_arn
        response = self.sso_admin.list_instances()
        for instance in response.get("Instances", []):
            if instance.get("IdentityStoreId") == self.identity_store_id:
                return str(instance["InstanceArn"])
        raise ResourceNotFoundError(
            f"No IAM Identity Center instance found for identity store {self.identity_store_id}."
        )

    def _paginate(self, client: Any, method_name: str, result_key: str, **kwargs: Any) -> list[dict[str, Any]]:
        method = getattr(client, method_name)
        items: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            params = {"MaxResults": 50, **kwargs}
            if token:
                params["NextToken"] = token
            response = method(**params)
            items.extend(response.get(result_key, []))
            token = response.get("NextToken")
            if not token:
                return items

    def list_accounts(self) -> list[AccountSummary]:
        accounts = self._paginate(self.organizations, "list_accounts", "Accounts")
        return sorted(
            [
                AccountSummary(
                    account_id=str(account.get("Id", "")),
                    name=str(account.get("Name", "")),
                    email=str(account.get("Email", "")),
                    status=str(account.get("Status", "")),
                )
                for account in accounts
            ],
            key=lambda item: item.name.lower(),
        )

    def resolve_account_id(self, account: str) -> str:
        for candidate in self.list_accounts():
            if candidate.account_id == account or candidate.name.lower() == account.lower():
                return candidate.account_id
        raise ResourceNotFoundError(f"Account not found: {account}")

    def list_permission_sets(self) -> list[PermissionSetSummary]:
        instance_arn = self.instance_arn()
        arns = self._paginate(
            self.sso_admin,
            "list_permission_sets",
            "PermissionSets",
            InstanceArn=instance_arn,
        )
        permission_sets: list[PermissionSetSummary] = []
        for arn in arns:
            response = self.sso_admin.describe_permission_set(
                InstanceArn=instance_arn,
                PermissionSetArn=arn,
            )
            permission_set = response.get("PermissionSet", {})
            permission_sets.append(
                PermissionSetSummary(
                    arn=str(arn),
                    name=str(permission_set.get("Name", "")),
                    description=str(permission_set.get("Description", "")),
                )
            )
        return sorted(permission_sets, key=lambda item: item.name.lower())

    def resolve_permission_set_arn(self, permission_set: str) -> str:
        for candidate in self.list_permission_sets():
            if candidate.arn == permission_set or candidate.name.lower() == permission_set.lower():
                return candidate.arn
        raise ResourceNotFoundError(f"Permission set not found: {permission_set}")

    def _principal_type(self, principal_type: str) -> str:
        normalized = principal_type.upper()
        if normalized not in {"USER", "GROUP"}:
            raise ValidationError("Principal type must be USER or GROUP.")
        return normalized

    def create_assignment(
        self,
        *,
        account: str,
        permission_set: str,
        principal_type: str,
        principal_id: str,
    ) -> None:
        self.sso_admin.create_account_assignment(
            InstanceArn=self.instance_arn(),
            TargetId=self.resolve_account_id(account),
            TargetType="AWS_ACCOUNT",
            PermissionSetArn=self.resolve_permission_set_arn(permission_set),
            PrincipalType=self._principal_type(principal_type),
            PrincipalId=principal_id,
        )

    def delete_assignment(
        self,
        *,
        account: str,
        permission_set: str,
        principal_type: str,
        principal_id: str,
    ) -> None:
        self.sso_admin.delete_account_assignment(
            InstanceArn=self.instance_arn(),
            TargetId=self.resolve_account_id(account),
            TargetType="AWS_ACCOUNT",
            PermissionSetArn=self.resolve_permission_set_arn(permission_set),
            PrincipalType=self._principal_type(principal_type),
            PrincipalId=principal_id,
        )

    def list_assignments(
        self,
        *,
        account: str | None = None,
        permission_set: str | None = None,
    ) -> list[AssignmentSummary]:
        accounts = [self.resolve_account_id(account)] if account else [item.account_id for item in self.list_accounts()]
        permission_sets = (
            [self.resolve_permission_set_arn(permission_set)]
            if permission_set
            else [item.arn for item in self.list_permission_sets()]
        )
        instance_arn = self.instance_arn()
        assignments: list[AssignmentSummary] = []
        for account_id in accounts:
            for permission_set_arn in permission_sets:
                page = self._paginate(
                    self.sso_admin,
                    "list_account_assignments",
                    "AccountAssignments",
                    InstanceArn=instance_arn,
                    AccountId=account_id,
                    PermissionSetArn=permission_set_arn,
                )
                assignments.extend(
                    AssignmentSummary(
                        account_id=account_id,
                        permission_set_arn=permission_set_arn,
                        principal_type=str(item.get("PrincipalType", "")),
                        principal_id=str(item.get("PrincipalId", "")),
                    )
                    for item in page
                )
        return assignments
