"""Identity Store API wrapper.

The methods in this module use documented Identity Store list/get/create/update/delete
APIs only. Local search and name resolution are built from list operations.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from botocore.exceptions import ClientError

from .exceptions import DuplicateResourceError, ResourceNotFoundError, ValidationError
from .models import GroupSummary, UserSummary


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email(email: str) -> None:
    if not EMAIL_PATTERN.match(email):
        raise ValidationError(f"Invalid email address: {email}")


def _first_email(user: dict[str, Any]) -> str:
    emails = user.get("Emails") or []
    if not emails:
        return ""
    primary = next((email for email in emails if email.get("Primary")), emails[0])
    return str(primary.get("Value", ""))


def user_summary(user: dict[str, Any]) -> UserSummary:
    return UserSummary(
        user_id=str(user.get("UserId", "")),
        username=str(user.get("UserName", "")),
        display_name=str(user.get("DisplayName", "")),
        email=_first_email(user),
    )


def group_summary(group: dict[str, Any]) -> GroupSummary:
    return GroupSummary(
        group_id=str(group.get("GroupId", "")),
        display_name=str(group.get("DisplayName", "")),
        description=str(group.get("Description", "")),
    )


class IdentityStoreService:
    """High-level operations for users, groups, and memberships."""

    def __init__(self, client: Any, identity_store_id: str):
        self.client = client
        self.identity_store_id = identity_store_id

    def _paginate(self, method_name: str, result_key: str, **kwargs: Any) -> Iterable[dict[str, Any]]:
        method = getattr(self.client, method_name)
        token: str | None = None
        while True:
            params = {"IdentityStoreId": self.identity_store_id, "MaxResults": 50, **kwargs}
            if token:
                params["NextToken"] = token
            response = method(**params)
            yield from response.get(result_key, [])
            token = response.get("NextToken")
            if not token:
                break

    def list_users(self) -> list[UserSummary]:
        users = [user_summary(user) for user in self._paginate("list_users", "Users")]
        return sorted(users, key=lambda item: item.username.lower())

    def list_groups(self) -> list[GroupSummary]:
        groups = [group_summary(group) for group in self._paginate("list_groups", "Groups")]
        return sorted(groups, key=lambda item: item.display_name.lower())

    def get_user(self, user_id: str) -> dict[str, Any]:
        try:
            return self.client.describe_user(
                IdentityStoreId=self.identity_store_id,
                UserId=user_id,
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                raise ResourceNotFoundError(f"User not found: {user_id}") from error
            raise

    def get_group(self, group_id: str) -> dict[str, Any]:
        try:
            return self.client.describe_group(
                IdentityStoreId=self.identity_store_id,
                GroupId=group_id,
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                raise ResourceNotFoundError(f"Group not found: {group_id}") from error
            raise

    def find_user(self, user: str) -> UserSummary:
        for candidate in self.list_users():
            if candidate.user_id == user or candidate.username.lower() == user.lower():
                return candidate
        raise ResourceNotFoundError(f"User not found: {user}")

    def find_group(self, group: str) -> GroupSummary:
        for candidate in self.list_groups():
            if candidate.group_id == group or candidate.display_name.lower() == group.lower():
                return candidate
        raise ResourceNotFoundError(f"Group not found: {group}")

    def search_users(self, query: str) -> list[UserSummary]:
        normalized = query.lower()
        return [
            user
            for user in self.list_users()
            if normalized in user.user_id.lower()
            or normalized in user.username.lower()
            or normalized in user.display_name.lower()
            or normalized in user.email.lower()
        ]

    def search_groups(self, query: str) -> list[GroupSummary]:
        normalized = query.lower()
        return [
            group
            for group in self.list_groups()
            if normalized in group.group_id.lower()
            or normalized in group.display_name.lower()
            or normalized in group.description.lower()
        ]

    def ensure_unique_username(self, username: str) -> None:
        for user in self.list_users():
            if user.username.lower() == username.lower():
                raise DuplicateResourceError(f"Username already exists: {username}")

    def ensure_unique_group_name(self, name: str) -> None:
        for group in self.list_groups():
            if group.display_name.lower() == name.lower():
                raise DuplicateResourceError(f"Group already exists: {name}")

    def create_user(
        self,
        *,
        username: str,
        email: str,
        first_name: str,
        last_name: str,
        display_name: str | None = None,
    ) -> str:
        validate_email(email)
        self.ensure_unique_username(username)
        resolved_display_name = display_name or f"{first_name} {last_name}".strip() or username
        response = self.client.create_user(
            IdentityStoreId=self.identity_store_id,
            UserName=username,
            DisplayName=resolved_display_name,
            Name={"GivenName": first_name, "FamilyName": last_name},
            Emails=[{"Value": email, "Type": "work", "Primary": True}],
        )
        return str(response["UserId"])

    def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        operations: list[dict[str, Any]] = []
        if display_name is not None:
            operations.append({"AttributePath": "displayName", "AttributeValue": display_name})
        if first_name is not None:
            operations.append({"AttributePath": "name.givenName", "AttributeValue": first_name})
        if last_name is not None:
            operations.append({"AttributePath": "name.familyName", "AttributeValue": last_name})
        if email is not None:
            validate_email(email)
            operations.append(
                {
                    "AttributePath": "emails",
                    "AttributeValue": [{"Value": email, "Type": "work", "Primary": True}],
                }
            )
        if not operations:
            raise ValidationError("No user attributes were supplied for update.")
        self.client.update_user(
            IdentityStoreId=self.identity_store_id,
            UserId=user_id,
            Operations=operations,
        )

    def delete_user(self, user_id: str) -> None:
        self.client.delete_user(IdentityStoreId=self.identity_store_id, UserId=user_id)

    def create_group(self, *, name: str, description: str | None = None) -> str:
        self.ensure_unique_group_name(name)
        params: dict[str, Any] = {"IdentityStoreId": self.identity_store_id, "DisplayName": name}
        if description:
            params["Description"] = description
        response = self.client.create_group(**params)
        return str(response["GroupId"])

    def rename_group(self, group_id: str, new_name: str) -> None:
        self.ensure_unique_group_name(new_name)
        self.client.update_group(
            IdentityStoreId=self.identity_store_id,
            GroupId=group_id,
            Operations=[{"AttributePath": "displayName", "AttributeValue": new_name}],
        )

    def delete_group(self, group_id: str) -> None:
        self.client.delete_group(IdentityStoreId=self.identity_store_id, GroupId=group_id)

    def list_group_memberships(self, group: str) -> list[UserSummary]:
        resolved_group = self.find_group(group)
        memberships = list(
            self._paginate("list_group_memberships", "GroupMemberships", GroupId=resolved_group.group_id)
        )
        users: list[UserSummary] = []
        for membership in memberships:
            user_id = membership.get("MemberId", {}).get("UserId")
            if user_id:
                users.append(user_summary(self.get_user(str(user_id))))
        return sorted(users, key=lambda item: item.username.lower())

    def list_user_memberships(self, user: str) -> list[GroupSummary]:
        resolved_user = self.find_user(user)
        memberships = list(
            self._paginate(
                "list_group_memberships_for_member",
                "GroupMemberships",
                MemberId={"UserId": resolved_user.user_id},
            )
        )
        groups: list[GroupSummary] = []
        for membership in memberships:
            group_id = membership.get("GroupId")
            if group_id:
                groups.append(group_summary(self.get_group(str(group_id))))
        return sorted(groups, key=lambda item: item.display_name.lower())

    def get_membership_id(self, group_id: str, user_id: str) -> str | None:
        memberships = self._paginate("list_group_memberships", "GroupMemberships", GroupId=group_id)
        for membership in memberships:
            member_user_id = membership.get("MemberId", {}).get("UserId")
            if member_user_id == user_id:
                return str(membership.get("MembershipId"))
        return None

    def add_user_to_group(self, *, group: str, user: str) -> str:
        resolved_group = self.find_group(group)
        resolved_user = self.find_user(user)
        existing = self.get_membership_id(resolved_group.group_id, resolved_user.user_id)
        if existing:
            raise DuplicateResourceError(
                f"{resolved_user.username} is already a member of {resolved_group.display_name}."
            )
        response = self.client.create_group_membership(
            IdentityStoreId=self.identity_store_id,
            GroupId=resolved_group.group_id,
            MemberId={"UserId": resolved_user.user_id},
        )
        return str(response["MembershipId"])

    def remove_user_from_group(self, *, group: str, user: str) -> None:
        resolved_group = self.find_group(group)
        resolved_user = self.find_user(user)
        membership_id = self.get_membership_id(resolved_group.group_id, resolved_user.user_id)
        if not membership_id:
            raise ResourceNotFoundError(
                f"{resolved_user.username} is not a member of {resolved_group.display_name}."
            )
        self.client.delete_group_membership(
            IdentityStoreId=self.identity_store_id,
            MembershipId=membership_id,
        )
