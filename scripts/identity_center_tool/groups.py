"""Typer commands for Identity Store groups."""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from .identitystore import IdentityStoreService, group_summary
from .output import print_groups, print_users


app = typer.Typer(help="Manage IAM Identity Center groups.")


def _store(ctx: typer.Context) -> IdentityStoreService:
    return ctx.obj["identitystore"]


def _log(operation: str, user: str, result: str) -> None:
    logging.info("operation=%s user=%s result=%s", operation, user, result)


@app.command("list")
def list_groups(
    ctx: typer.Context,
    output: Annotated[str, typer.Option("--output", help="text or json.")] = "text",
) -> None:
    groups = _store(ctx).list_groups()
    print_groups(groups, output=output)
    _log("groups.list", "-", f"{len(groups)} groups")


@app.command("search")
def search_groups(ctx: typer.Context, query: Annotated[str, typer.Argument(help="Local search query.")]) -> None:
    groups = _store(ctx).search_groups(query)
    print_groups(groups)
    _log("groups.search", query, f"{len(groups)} groups")


@app.command("get")
def get_group(ctx: typer.Context, group_id: Annotated[str, typer.Argument(help="Identity Store group ID.")]) -> None:
    group = group_summary(_store(ctx).get_group(group_id))
    print_groups([group])
    _log("groups.get", group_id, "ok")


@app.command("create")
def create_group(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Group display name.")],
    description: Annotated[str | None, typer.Option("--description", help="Group description.")] = None,
) -> None:
    group_id = _store(ctx).create_group(name=name, description=description)
    print(f"Group created successfully: {name} ({group_id})")
    _log("groups.create", name, group_id)


@app.command("rename")
def rename_group(
    ctx: typer.Context,
    group_id: Annotated[str, typer.Argument(help="Identity Store group ID.")],
    name: Annotated[str, typer.Option("--name", help="New group display name.")],
) -> None:
    _store(ctx).rename_group(group_id, name)
    print(f"Group renamed successfully: {group_id} -> {name}")
    _log("groups.rename", group_id, name)


@app.command("delete")
def delete_group(
    ctx: typer.Context,
    group_id: Annotated[str, typer.Argument(help="Identity Store group ID.")],
    force: Annotated[bool, typer.Option("--force", help="Delete without confirmation.")] = False,
) -> None:
    if not force:
        typer.confirm(f"Delete group {group_id}?", abort=True)
    _store(ctx).delete_group(group_id)
    print(f"Group deleted successfully: {group_id}")
    _log("groups.delete", group_id, "ok")


@app.command("add-user")
def add_user(
    ctx: typer.Context,
    group: Annotated[str, typer.Option("--group", help="Group ID or display name.")],
    user: Annotated[str, typer.Option("--user", help="User ID or username.")],
) -> None:
    membership_id = _store(ctx).add_user_to_group(group=group, user=user)
    print(f"User added to group successfully: membership={membership_id}")
    _log("groups.add-user", user, membership_id)


@app.command("remove-user")
def remove_user(
    ctx: typer.Context,
    group: Annotated[str, typer.Option("--group", help="Group ID or display name.")],
    user: Annotated[str, typer.Option("--user", help="User ID or username.")],
) -> None:
    _store(ctx).remove_user_from_group(group=group, user=user)
    print("User removed from group successfully")
    _log("groups.remove-user", user, group)


@app.command("members")
def members(ctx: typer.Context, group: Annotated[str, typer.Argument(help="Group ID or display name.")]) -> None:
    users = _store(ctx).list_group_memberships(group)
    print_users(users)
    _log("groups.members", group, f"{len(users)} users")
