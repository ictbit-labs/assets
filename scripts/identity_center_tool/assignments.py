"""Typer commands for IAM Identity Center account assignments."""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from .identitystore import IdentityStoreService
from .output import print_accounts, print_assignments, print_permission_sets
from .permissions import PermissionSetService


app = typer.Typer(help="Manage IAM Identity Center account assignments.")


def _identitystore(ctx: typer.Context) -> IdentityStoreService:
    return ctx.obj["identitystore"]


def _permissions(ctx: typer.Context) -> PermissionSetService:
    return ctx.obj["permissions"]


def _log(operation: str, user: str, result: str) -> None:
    logging.info("operation=%s user=%s result=%s", operation, user, result)


@app.command("accounts")
def accounts(ctx: typer.Context) -> None:
    rows = _permissions(ctx).list_accounts()
    print_accounts(rows)
    _log("assignments.accounts", "-", f"{len(rows)} accounts")


@app.command("permission-sets")
def permission_sets(ctx: typer.Context) -> None:
    rows = _permissions(ctx).list_permission_sets()
    print_permission_sets(rows)
    _log("assignments.permission-sets", "-", f"{len(rows)} permission sets")


@app.command("list")
def list_assignments(
    ctx: typer.Context,
    account: Annotated[str | None, typer.Option("--account", help="Account ID or name.")] = None,
    permission_set: Annotated[
        str | None, typer.Option("--permission-set", help="Permission set name or ARN.")
    ] = None,
    output: Annotated[str, typer.Option("--output", help="text or json.")] = "text",
) -> None:
    rows = _permissions(ctx).list_assignments(account=account, permission_set=permission_set)
    print_assignments(rows, output=output)
    _log("assignments.list", account or "-", f"{len(rows)} assignments")


@app.command("assign-user")
def assign_user(
    ctx: typer.Context,
    account: Annotated[str, typer.Option("--account", help="Account ID or name.")],
    permission_set: Annotated[str, typer.Option("--permission-set", help="Permission set name or ARN.")],
    user: Annotated[str, typer.Option("--user", help="User ID or username.")],
) -> None:
    resolved_user = _identitystore(ctx).find_user(user)
    _permissions(ctx).create_assignment(
        account=account,
        permission_set=permission_set,
        principal_type="USER",
        principal_id=resolved_user.user_id,
    )
    print(f"Permission set assigned to user successfully: {resolved_user.username}")
    _log("assignments.assign-user", resolved_user.username, account)


@app.command("assign-group")
def assign_group(
    ctx: typer.Context,
    account: Annotated[str, typer.Option("--account", help="Account ID or name.")],
    permission_set: Annotated[str, typer.Option("--permission-set", help="Permission set name or ARN.")],
    group: Annotated[str, typer.Option("--group", help="Group ID or display name.")],
) -> None:
    resolved_group = _identitystore(ctx).find_group(group)
    _permissions(ctx).create_assignment(
        account=account,
        permission_set=permission_set,
        principal_type="GROUP",
        principal_id=resolved_group.group_id,
    )
    print(f"Permission set assigned to group successfully: {resolved_group.display_name}")
    _log("assignments.assign-group", resolved_group.display_name, account)


@app.command("remove-user")
def remove_user(
    ctx: typer.Context,
    account: Annotated[str, typer.Option("--account", help="Account ID or name.")],
    permission_set: Annotated[str, typer.Option("--permission-set", help="Permission set name or ARN.")],
    user: Annotated[str, typer.Option("--user", help="User ID or username.")],
) -> None:
    resolved_user = _identitystore(ctx).find_user(user)
    _permissions(ctx).delete_assignment(
        account=account,
        permission_set=permission_set,
        principal_type="USER",
        principal_id=resolved_user.user_id,
    )
    print(f"User assignment removed successfully: {resolved_user.username}")
    _log("assignments.remove-user", resolved_user.username, account)


@app.command("remove-group")
def remove_group(
    ctx: typer.Context,
    account: Annotated[str, typer.Option("--account", help="Account ID or name.")],
    permission_set: Annotated[str, typer.Option("--permission-set", help="Permission set name or ARN.")],
    group: Annotated[str, typer.Option("--group", help="Group ID or display name.")],
) -> None:
    resolved_group = _identitystore(ctx).find_group(group)
    _permissions(ctx).delete_assignment(
        account=account,
        permission_set=permission_set,
        principal_type="GROUP",
        principal_id=resolved_group.group_id,
    )
    print(f"Group assignment removed successfully: {resolved_group.display_name}")
    _log("assignments.remove-group", resolved_group.display_name, account)
