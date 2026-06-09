"""Standard-library output helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def _serializable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serializable(item) for item in value]
    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}
    return value


def print_json(value: Any) -> None:
    print(json.dumps(_serializable(value), indent=2, sort_keys=True))


def normalize_output(output: str) -> str:
    normalized = output.lower()
    if normalized not in {"text", "json"}:
        raise ValueError("--output must be text or json")
    return normalized


def print_rows(title: str, headers: list[str], rows: list[list[str]], *, output: str = "text") -> None:
    if normalize_output(output) == "json":
        data = [dict(zip(headers, row, strict=True)) for row in rows]
        print_json(data)
        return

    print(title)
    if not rows:
        print("No results.")
        return

    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    header_line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    print(header_line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_users(users: list[Any], *, output: str = "text") -> None:
    if normalize_output(output) == "json":
        print_json(users)
        return
    print_rows(
        "Users",
        ["User ID", "Username", "Display Name", "Email"],
        [[user.user_id, user.username, user.display_name, user.email] for user in users],
    )


def print_groups(groups: list[Any], *, output: str = "text") -> None:
    if normalize_output(output) == "json":
        print_json(groups)
        return
    print_rows(
        "Groups",
        ["Group ID", "Display Name", "Description"],
        [[group.group_id, group.display_name, group.description] for group in groups],
    )


def print_accounts(accounts: list[Any], *, output: str = "text") -> None:
    if normalize_output(output) == "json":
        print_json(accounts)
        return
    print_rows(
        "AWS Accounts",
        ["Account ID", "Name", "Email", "Status"],
        [[account.account_id, account.name, account.email, account.status] for account in accounts],
    )


def print_permission_sets(permission_sets: list[Any], *, output: str = "text") -> None:
    if normalize_output(output) == "json":
        print_json(permission_sets)
        return
    print_rows(
        "Permission Sets",
        ["Name", "ARN", "Description"],
        [[item.name, item.arn, item.description] for item in permission_sets],
    )


def print_assignments(assignments: list[Any], *, output: str = "text") -> None:
    if normalize_output(output) == "json":
        print_json(assignments)
        return
    print_rows(
        "Account Assignments",
        ["Account ID", "Permission Set ARN", "Principal Type", "Principal ID"],
        [
            [
                item.account_id,
                item.permission_set_arn,
                item.principal_type,
                item.principal_id,
            ]
            for item in assignments
        ],
    )
