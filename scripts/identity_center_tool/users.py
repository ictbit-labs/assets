"""Typer commands for Identity Store users."""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from .identitystore import IdentityStoreService, user_summary
from .output import print_groups, print_users


app = typer.Typer(help="Manage IAM Identity Center users.")


def _store(ctx: typer.Context) -> IdentityStoreService:
    return ctx.obj["identitystore"]


def _log(operation: str, user: str, result: str) -> None:
    logging.info("operation=%s user=%s result=%s", operation, user, result)


@app.command("list")
def list_users(
    ctx: typer.Context,
    output: Annotated[str, typer.Option("--output", help="text or json.")] = "text",
) -> None:
    users = _store(ctx).list_users()
    print_users(users, output=output)
    _log("users.list", "-", f"{len(users)} users")


@app.command("search")
def search_users(ctx: typer.Context, query: Annotated[str, typer.Argument(help="Local search query.")]) -> None:
    users = _store(ctx).search_users(query)
    print_users(users)
    _log("users.search", query, f"{len(users)} users")


@app.command("get")
def get_user(ctx: typer.Context, user_id: Annotated[str, typer.Argument(help="Identity Store user ID.")]) -> None:
    user = user_summary(_store(ctx).get_user(user_id))
    print_users([user])
    _log("users.get", user_id, "ok")


@app.command("create")
def create_user(
    ctx: typer.Context,
    username: Annotated[str, typer.Option("--username", help="UserName value.")],
    email: Annotated[str, typer.Option("--email", help="Primary work email.")],
    first_name: Annotated[str, typer.Option("--first-name", help="Given name.")],
    last_name: Annotated[str, typer.Option("--last-name", help="Family name.")],
    display_name: Annotated[str | None, typer.Option("--display-name", help="Display name.")] = None,
) -> None:
    user_id = _store(ctx).create_user(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        display_name=display_name,
    )
    print(f"User created successfully: {username} ({user_id})")
    _log("users.create", username, user_id)


@app.command("update")
def update_user(
    ctx: typer.Context,
    user_id: Annotated[str, typer.Argument(help="Identity Store user ID.")],
    display_name: Annotated[str | None, typer.Option("--display-name", help="New display name.")] = None,
    email: Annotated[str | None, typer.Option("--email", help="New primary work email.")] = None,
    first_name: Annotated[str | None, typer.Option("--first-name", help="New given name.")] = None,
    last_name: Annotated[str | None, typer.Option("--last-name", help="New family name.")] = None,
) -> None:
    _store(ctx).update_user(
        user_id,
        display_name=display_name,
        email=email,
        first_name=first_name,
        last_name=last_name,
    )
    print(f"User updated successfully: {user_id}")
    _log("users.update", user_id, "ok")


@app.command("delete")
def delete_user(
    ctx: typer.Context,
    user_id: Annotated[str, typer.Argument(help="Identity Store user ID.")],
    force: Annotated[bool, typer.Option("--force", help="Delete without confirmation.")] = False,
) -> None:
    if not force:
        typer.confirm(f"Delete user {user_id}?", abort=True)
    _store(ctx).delete_user(user_id)
    print(f"User deleted successfully: {user_id}")
    _log("users.delete", user_id, "ok")


@app.command("memberships")
def memberships(ctx: typer.Context, user: Annotated[str, typer.Argument(help="User ID or username.")]) -> None:
    groups = _store(ctx).list_user_memberships(user)
    print_groups(groups)
    _log("users.memberships", user, f"{len(groups)} groups")
