"""Typed models used by the CLI layer and report generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserSummary:
    user_id: str
    username: str
    display_name: str
    email: str


@dataclass(frozen=True)
class GroupSummary:
    group_id: str
    display_name: str
    description: str = ""


@dataclass(frozen=True)
class AccountSummary:
    account_id: str
    name: str
    email: str = ""
    status: str = ""


@dataclass(frozen=True)
class PermissionSetSummary:
    arn: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class AssignmentSummary:
    account_id: str
    permission_set_arn: str
    principal_type: str
    principal_id: str

