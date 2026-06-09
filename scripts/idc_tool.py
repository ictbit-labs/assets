#!/usr/bin/env python3
"""Minimal IAM Identity Center management tool.

Dependencies: boto3, pyyaml.
CLI: argparse only.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import boto3
import yaml


DEFAULT_CONFIG = Path(__file__).resolve().parent / "idc_tool" / "config.yaml"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class IdcError(Exception):
    """User-facing error."""


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise IdcError(f"Config file must contain a YAML mapping: {path}")
    return raw


def resolve_config(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(getattr(args, "config", DEFAULT_CONFIG))
    data = load_yaml(path)
    profile = getattr(args, "profile", None) or data.get("profile")
    region = getattr(args, "region", None) or data.get("region")
    identity_store_id = getattr(args, "identity_store_id", None) or data.get("identity_store_id")
    sso_instance_arn = getattr(args, "sso_instance_arn", None) or data.get("sso_instance_arn")

    if not profile:
        raise IdcError(
            "No AWS profile specified.\n"
            "Provide --profile <name> or set profile in config.yaml."
        )
    if not region:
        raise IdcError("No AWS region specified. Provide --region <region> or set region in config.yaml.")
    if not identity_store_id:
        raise IdcError(
            "No identity store ID specified. Provide --identity-store-id <id> or set identity_store_id in config.yaml."
        )

    return {
        "profile": str(profile),
        "region": str(region),
        "identity_store_id": str(identity_store_id),
        "sso_instance_arn": str(sso_instance_arn) if sso_instance_arn else None,
    }


def create_clients(config: dict[str, Any]) -> dict[str, Any]:
    session = boto3.Session(
        profile_name=config["profile"],
        region_name=config["region"],
    )
    return {
        "identitystore": session.client("identitystore"),
        "sso_admin": session.client("sso-admin"),
        "organizations": session.client("organizations"),
    }


def error_message(error: Exception, profile: str | None = None) -> str:
    name = error.__class__.__name__
    if name in {"SSOTokenLoadError", "UnauthorizedSSOTokenError", "NoAuthTokenError", "TokenRetrievalError"}:
        if profile:
            return f"AWS SSO session expired. Run:\naws sso login --profile {profile}"
        return "AWS SSO session expired. Run aws sso login for the selected profile."
    if name == "ProfileNotFound":
        return f"AWS profile not found: {profile}" if profile else "AWS profile not found."
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        detail = response.get("Error", {})
        code = detail.get("Code", name)
        message = detail.get("Message", str(error))
        return f"{code}: {message}"
    return str(error)


def page(client: Any, method_name: str, key: str, **kwargs: Any) -> list[dict[str, Any]]:
    method = getattr(client, method_name)
    items: list[dict[str, Any]] = []
    token = None
    while True:
        params = dict(kwargs)
        if token:
            params["NextToken"] = token
        try:
            response = method(**params)
        except TypeError:
            params.pop("MaxResults", None)
            response = method(**params)
        items.extend(response.get(key, []))
        token = response.get("NextToken")
        if not token:
            return items


def first_email(user: dict[str, Any]) -> str:
    emails = user.get("Emails") or []
    if not emails:
        return ""
    primary = next((item for item in emails if item.get("Primary")), emails[0])
    return str(primary.get("Value", ""))


def user_row(user: dict[str, Any]) -> dict[str, str]:
    return {
        "user_id": str(user.get("UserId", "")),
        "username": str(user.get("UserName", "")),
        "display_name": str(user.get("DisplayName", "")),
        "email": first_email(user),
    }


def group_row(group: dict[str, Any]) -> dict[str, str]:
    return {
        "group_id": str(group.get("GroupId", "")),
        "display_name": str(group.get("DisplayName", "")),
        "description": str(group.get("Description", "")),
    }


def list_users(ctx: dict[str, Any]) -> list[dict[str, str]]:
    users = page(
        ctx["identitystore"],
        "list_users",
        "Users",
        IdentityStoreId=ctx["identity_store_id"],
        MaxResults=50,
    )
    return sorted((user_row(user) for user in users), key=lambda item: item["username"].lower())


def list_groups(ctx: dict[str, Any]) -> list[dict[str, str]]:
    groups = page(
        ctx["identitystore"],
        "list_groups",
        "Groups",
        IdentityStoreId=ctx["identity_store_id"],
        MaxResults=50,
    )
    return sorted((group_row(group) for group in groups), key=lambda item: item["display_name"].lower())


def find_user(ctx: dict[str, Any], value: str) -> dict[str, str]:
    for user in list_users(ctx):
        if user["user_id"] == value or user["username"].lower() == value.lower():
            return user
    raise IdcError(f"User not found: {value}")


def find_group(ctx: dict[str, Any], value: str) -> dict[str, str]:
    for group in list_groups(ctx):
        if group["group_id"] == value or group["display_name"].lower() == value.lower():
            return group
    raise IdcError(f"Group not found: {value}")


def ensure_unique_user(ctx: dict[str, Any], username: str) -> None:
    if any(user["username"].lower() == username.lower() for user in list_users(ctx)):
        raise IdcError(f"Username already exists: {username}")


def ensure_unique_group(ctx: dict[str, Any], name: str) -> None:
    if any(group["display_name"].lower() == name.lower() for group in list_groups(ctx)):
        raise IdcError(f"Group already exists: {name}")


def describe_user(ctx: dict[str, Any], user_id: str) -> dict[str, Any]:
    return ctx["identitystore"].describe_user(
        IdentityStoreId=ctx["identity_store_id"],
        UserId=user_id,
    )


def describe_group(ctx: dict[str, Any], group_id: str) -> dict[str, Any]:
    return ctx["identitystore"].describe_group(
        IdentityStoreId=ctx["identity_store_id"],
        GroupId=group_id,
    )


def membership_id(ctx: dict[str, Any], group_id: str, user_id: str) -> str | None:
    memberships = page(
        ctx["identitystore"],
        "list_group_memberships",
        "GroupMemberships",
        IdentityStoreId=ctx["identity_store_id"],
        GroupId=group_id,
        MaxResults=50,
    )
    for membership in memberships:
        if membership.get("MemberId", {}).get("UserId") == user_id:
            return str(membership.get("MembershipId"))
    return None


def group_members(ctx: dict[str, Any], group: str) -> list[dict[str, str]]:
    resolved_group = find_group(ctx, group)
    memberships = page(
        ctx["identitystore"],
        "list_group_memberships",
        "GroupMemberships",
        IdentityStoreId=ctx["identity_store_id"],
        GroupId=resolved_group["group_id"],
        MaxResults=50,
    )
    users = [
        user_row(describe_user(ctx, str(item["MemberId"]["UserId"])))
        for item in memberships
        if item.get("MemberId", {}).get("UserId")
    ]
    return sorted(users, key=lambda item: item["username"].lower())


def user_memberships(ctx: dict[str, Any], user: str) -> list[dict[str, str]]:
    resolved_user = find_user(ctx, user)
    memberships = page(
        ctx["identitystore"],
        "list_group_memberships_for_member",
        "GroupMemberships",
        IdentityStoreId=ctx["identity_store_id"],
        MemberId={"UserId": resolved_user["user_id"]},
        MaxResults=50,
    )
    groups = [
        group_row(describe_group(ctx, str(item["GroupId"])))
        for item in memberships
        if item.get("GroupId")
    ]
    return sorted(groups, key=lambda item: item["display_name"].lower())


def sso_instance_arn(ctx: dict[str, Any]) -> str:
    if ctx.get("sso_instance_arn"):
        return str(ctx["sso_instance_arn"])
    instances = ctx["sso_admin"].list_instances().get("Instances", [])
    for instance in instances:
        if instance.get("IdentityStoreId") == ctx["identity_store_id"]:
            return str(instance["InstanceArn"])
    raise IdcError(f"No IAM Identity Center instance found for identity store {ctx['identity_store_id']}.")


def list_accounts(ctx: dict[str, Any]) -> list[dict[str, str]]:
    accounts = page(ctx["organizations"], "list_accounts", "Accounts", MaxResults=20)
    return sorted(
        (
            {
                "account_id": str(account.get("Id", "")),
                "name": str(account.get("Name", "")),
                "email": str(account.get("Email", "")),
                "status": str(account.get("Status", "")),
            }
            for account in accounts
        ),
        key=lambda item: item["name"].lower(),
    )


def resolve_account_id(ctx: dict[str, Any], account: str) -> str:
    for item in list_accounts(ctx):
        if item["account_id"] == account or item["name"].lower() == account.lower():
            return item["account_id"]
    raise IdcError(f"Account not found: {account}")


def list_permission_sets(ctx: dict[str, Any]) -> list[dict[str, str]]:
    instance = sso_instance_arn(ctx)
    arns = page(
        ctx["sso_admin"],
        "list_permission_sets",
        "PermissionSets",
        InstanceArn=instance,
        MaxResults=50,
    )
    rows = []
    for arn in arns:
        response = ctx["sso_admin"].describe_permission_set(
            InstanceArn=instance,
            PermissionSetArn=arn,
        )
        item = response.get("PermissionSet", {})
        rows.append(
            {
                "name": str(item.get("Name", "")),
                "arn": str(arn),
                "description": str(item.get("Description", "")),
            }
        )
    return sorted(rows, key=lambda item: item["name"].lower())


def resolve_permission_set(ctx: dict[str, Any], value: str) -> str:
    for item in list_permission_sets(ctx):
        if item["arn"] == value or item["name"].lower() == value.lower():
            return item["arn"]
    raise IdcError(f"Permission set not found: {value}")


def list_assignments(ctx: dict[str, Any], account: str | None = None, permission_set: str | None = None) -> list[dict[str, str]]:
    account_ids = [resolve_account_id(ctx, account)] if account else [item["account_id"] for item in list_accounts(ctx)]
    permission_set_arns = [resolve_permission_set(ctx, permission_set)] if permission_set else [
        item["arn"] for item in list_permission_sets(ctx)
    ]
    instance = sso_instance_arn(ctx)
    rows = []
    for account_id in account_ids:
        for permission_set_arn in permission_set_arns:
            assignments = page(
                ctx["sso_admin"],
                "list_account_assignments",
                "AccountAssignments",
                InstanceArn=instance,
                AccountId=account_id,
                PermissionSetArn=permission_set_arn,
                MaxResults=50,
            )
            for item in assignments:
                rows.append(
                    {
                        "account_id": account_id,
                        "permission_set_arn": permission_set_arn,
                        "principal_type": str(item.get("PrincipalType", "")),
                        "principal_id": str(item.get("PrincipalId", "")),
                    }
                )
    return rows


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def print_table(title: str, rows: list[dict[str, Any]], columns: list[str]) -> None:
    print(title)
    if not rows:
        print("No results.")
        return
    widths = [max(len(column), *(len(str(row.get(column, ""))) for row in rows)) for column in columns]
    print("  ".join(column.ljust(widths[index]) for index, column in enumerate(columns)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(row.get(column, "")).ljust(widths[index]) for index, column in enumerate(columns)))


def emit(title: str, rows: Any, output: str, columns: list[str] | None = None) -> None:
    if output == "json":
        print_json(rows)
        return
    if isinstance(rows, list) and columns:
        print_table(title, rows, columns)
        return
    print(rows)


def handle_users(args: argparse.Namespace, ctx: dict[str, Any]) -> None:
    if args.action == "list":
        emit("Users", list_users(ctx), args.output, ["user_id", "username", "display_name", "email"])
    elif args.action == "get":
        emit("User", user_row(describe_user(ctx, args.user_id)), args.output)
    elif args.action == "search":
        query = args.query.lower()
        rows = [
            user for user in list_users(ctx)
            if query in user["user_id"].lower()
            or query in user["username"].lower()
            or query in user["display_name"].lower()
            or query in user["email"].lower()
        ]
        emit("Users", rows, args.output, ["user_id", "username", "display_name", "email"])
    elif args.action == "create":
        if not EMAIL_RE.match(args.email):
            raise IdcError(f"Invalid email address: {args.email}")
        ensure_unique_user(ctx, args.username)
        display_name = args.display_name or f"{args.first_name} {args.last_name}".strip() or args.username
        response = ctx["identitystore"].create_user(
            IdentityStoreId=ctx["identity_store_id"],
            UserName=args.username,
            DisplayName=display_name,
            Name={"GivenName": args.first_name, "FamilyName": args.last_name},
            Emails=[{"Value": args.email, "Type": "work", "Primary": True}],
        )
        emit("User", {"message": "User created successfully", "user_id": response["UserId"]}, args.output)
    elif args.action == "update":
        operations = []
        if args.display_name:
            operations.append({"AttributePath": "displayName", "AttributeValue": args.display_name})
        if args.email:
            if not EMAIL_RE.match(args.email):
                raise IdcError(f"Invalid email address: {args.email}")
            operations.append({"AttributePath": "emails", "AttributeValue": [{"Value": args.email, "Type": "work", "Primary": True}]})
        if args.first_name:
            operations.append({"AttributePath": "name.givenName", "AttributeValue": args.first_name})
        if args.last_name:
            operations.append({"AttributePath": "name.familyName", "AttributeValue": args.last_name})
        if not operations:
            raise IdcError("No user attributes supplied for update.")
        ctx["identitystore"].update_user(
            IdentityStoreId=ctx["identity_store_id"],
            UserId=args.user_id,
            Operations=operations,
        )
        emit("User", {"message": "User updated successfully", "user_id": args.user_id}, args.output)
    elif args.action == "delete":
        ctx["identitystore"].delete_user(IdentityStoreId=ctx["identity_store_id"], UserId=args.user_id)
        emit("User", {"message": "User deleted successfully", "user_id": args.user_id}, args.output)


def handle_groups(args: argparse.Namespace, ctx: dict[str, Any]) -> None:
    if args.action == "list":
        emit("Groups", list_groups(ctx), args.output, ["group_id", "display_name", "description"])
    elif args.action == "get":
        emit("Group", group_row(describe_group(ctx, args.group_id)), args.output)
    elif args.action == "search":
        query = args.query.lower()
        rows = [
            group for group in list_groups(ctx)
            if query in group["group_id"].lower()
            or query in group["display_name"].lower()
            or query in group["description"].lower()
        ]
        emit("Groups", rows, args.output, ["group_id", "display_name", "description"])
    elif args.action == "create":
        ensure_unique_group(ctx, args.name)
        params = {"IdentityStoreId": ctx["identity_store_id"], "DisplayName": args.name}
        if args.description:
            params["Description"] = args.description
        response = ctx["identitystore"].create_group(**params)
        emit("Group", {"message": "Group created successfully", "group_id": response["GroupId"]}, args.output)
    elif args.action == "rename":
        ensure_unique_group(ctx, args.name)
        ctx["identitystore"].update_group(
            IdentityStoreId=ctx["identity_store_id"],
            GroupId=args.group_id,
            Operations=[{"AttributePath": "displayName", "AttributeValue": args.name}],
        )
        emit("Group", {"message": "Group renamed successfully", "group_id": args.group_id}, args.output)
    elif args.action == "delete":
        ctx["identitystore"].delete_group(IdentityStoreId=ctx["identity_store_id"], GroupId=args.group_id)
        emit("Group", {"message": "Group deleted successfully", "group_id": args.group_id}, args.output)


def handle_memberships(args: argparse.Namespace, ctx: dict[str, Any]) -> None:
    if args.action == "add":
        user = find_user(ctx, args.user)
        group = find_group(ctx, args.group)
        if membership_id(ctx, group["group_id"], user["user_id"]):
            raise IdcError(f"{user['username']} is already a member of {group['display_name']}.")
        response = ctx["identitystore"].create_group_membership(
            IdentityStoreId=ctx["identity_store_id"],
            GroupId=group["group_id"],
            MemberId={"UserId": user["user_id"]},
        )
        emit("Membership", {"message": "Membership created successfully", "membership_id": response["MembershipId"]}, args.output)
    elif args.action == "remove":
        user = find_user(ctx, args.user)
        group = find_group(ctx, args.group)
        found = membership_id(ctx, group["group_id"], user["user_id"])
        if not found:
            raise IdcError(f"{user['username']} is not a member of {group['display_name']}.")
        ctx["identitystore"].delete_group_membership(IdentityStoreId=ctx["identity_store_id"], MembershipId=found)
        emit("Membership", {"message": "Membership deleted successfully", "membership_id": found}, args.output)
    elif args.action == "list":
        if args.group:
            emit("Group Members", group_members(ctx, args.group), args.output, ["user_id", "username", "display_name", "email"])
        elif args.user:
            emit("User Memberships", user_memberships(ctx, args.user), args.output, ["group_id", "display_name", "description"])
        else:
            raise IdcError("Provide --group or --user.")


def handle_permission_sets(args: argparse.Namespace, ctx: dict[str, Any]) -> None:
    emit("Permission Sets", list_permission_sets(ctx), args.output, ["name", "arn", "description"])


def handle_accounts(args: argparse.Namespace, ctx: dict[str, Any]) -> None:
    emit("Accounts", list_accounts(ctx), args.output, ["account_id", "name", "email", "status"])


def handle_assignments(args: argparse.Namespace, ctx: dict[str, Any]) -> None:
    if args.action == "list":
        emit(
            "Assignments",
            list_assignments(ctx, account=args.account_id, permission_set=args.permission_set),
            args.output,
            ["account_id", "permission_set_arn", "principal_type", "principal_id"],
        )
    elif args.action == "create":
        if args.user:
            principal = find_user(ctx, args.user)
            principal_type = "USER"
            principal_id = principal["user_id"]
        elif args.group:
            principal = find_group(ctx, args.group)
            principal_type = "GROUP"
            principal_id = principal["group_id"]
        else:
            raise IdcError("Provide --user or --group.")
        ctx["sso_admin"].create_account_assignment(
            InstanceArn=sso_instance_arn(ctx),
            TargetId=resolve_account_id(ctx, args.account_id),
            TargetType="AWS_ACCOUNT",
            PermissionSetArn=resolve_permission_set(ctx, args.permission_set),
            PrincipalType=principal_type,
            PrincipalId=principal_id,
        )
        emit("Assignment", {"message": "Assignment created successfully", "principal_id": principal_id}, args.output)
    elif args.action == "delete":
        if args.user:
            principal = find_user(ctx, args.user)
            principal_type = "USER"
            principal_id = principal["user_id"]
        elif args.group:
            principal = find_group(ctx, args.group)
            principal_type = "GROUP"
            principal_id = principal["group_id"]
        else:
            raise IdcError("Provide --user or --group.")
        ctx["sso_admin"].delete_account_assignment(
            InstanceArn=sso_instance_arn(ctx),
            TargetId=resolve_account_id(ctx, args.account_id),
            TargetType="AWS_ACCOUNT",
            PermissionSetArn=resolve_permission_set(ctx, args.permission_set),
            PrincipalType=principal_type,
            PrincipalId=principal_id,
        )
        emit("Assignment", {"message": "Assignment deleted successfully", "principal_id": principal_id}, args.output)


def handle_reports(args: argparse.Namespace, ctx: dict[str, Any]) -> None:
    if args.report == "memberships":
        rows = []
        for group in list_groups(ctx):
            for user in group_members(ctx, group["group_id"]):
                rows.append(
                    {
                        "group_id": group["group_id"],
                        "group": group["display_name"],
                        "user_id": user["user_id"],
                        "username": user["username"],
                        "email": user["email"],
                    }
                )
        emit("Membership Report", rows, args.output, ["group_id", "group", "user_id", "username", "email"])
    elif args.report == "inventory":
        report = {
            "users": list_users(ctx),
            "groups": list_groups(ctx),
            "accounts": list_accounts(ctx),
            "permission_sets": list_permission_sets(ctx),
        }
        if args.output_file:
            path = Path(args.output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() == ".csv":
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["section", "id", "name", "detail"])
                    for user in report["users"]:
                        writer.writerow(["user", user["user_id"], user["username"], user["email"]])
                    for group in report["groups"]:
                        writer.writerow(["group", group["group_id"], group["display_name"], group["description"]])
                    for account in report["accounts"]:
                        writer.writerow(["account", account["account_id"], account["name"], account["status"]])
                    for permission_set in report["permission_sets"]:
                        writer.writerow(["permission_set", permission_set["arn"], permission_set["name"], permission_set["description"]])
            else:
                path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"Report written successfully: {path}")
        else:
            print_json(report)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=argparse.SUPPRESS, help="Path to config.yaml.")
    parser.add_argument("--profile", default=argparse.SUPPRESS, help="Local AWS profile name.")
    parser.add_argument("--region", default=argparse.SUPPRESS, help="AWS region override.")
    parser.add_argument("--identity-store-id", default=argparse.SUPPRESS, help="Identity Store ID override.")
    parser.add_argument("--sso-instance-arn", default=argparse.SUPPRESS, help="IAM Identity Center instance ARN override.")


def add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", choices=["text", "json"], default="text")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal IAM Identity Center management tool.")
    add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    users = sub.add_parser("users")
    users_sub = users.add_subparsers(dest="action", required=True)
    users_list = users_sub.add_parser("list")
    add_common(users_list)
    add_output(users_list)
    users_get = users_sub.add_parser("get")
    users_get.add_argument("user_id")
    add_common(users_get)
    add_output(users_get)
    users_search = users_sub.add_parser("search")
    users_search.add_argument("query")
    add_common(users_search)
    add_output(users_search)
    users_create = users_sub.add_parser("create")
    users_create.add_argument("--username", required=True)
    users_create.add_argument("--email", required=True)
    users_create.add_argument("--first-name", required=True)
    users_create.add_argument("--last-name", required=True)
    users_create.add_argument("--display-name")
    add_common(users_create)
    add_output(users_create)
    users_update = users_sub.add_parser("update")
    users_update.add_argument("user_id")
    users_update.add_argument("--display-name")
    users_update.add_argument("--email")
    users_update.add_argument("--first-name")
    users_update.add_argument("--last-name")
    add_common(users_update)
    add_output(users_update)
    users_delete = users_sub.add_parser("delete")
    users_delete.add_argument("user_id")
    add_common(users_delete)
    add_output(users_delete)
    users.set_defaults(handler=handle_users)

    groups = sub.add_parser("groups")
    groups_sub = groups.add_subparsers(dest="action", required=True)
    groups_list = groups_sub.add_parser("list")
    add_common(groups_list)
    add_output(groups_list)
    groups_get = groups_sub.add_parser("get")
    groups_get.add_argument("group_id")
    add_common(groups_get)
    add_output(groups_get)
    groups_search = groups_sub.add_parser("search")
    groups_search.add_argument("query")
    add_common(groups_search)
    add_output(groups_search)
    groups_create = groups_sub.add_parser("create")
    groups_create.add_argument("--name", required=True)
    groups_create.add_argument("--description")
    add_common(groups_create)
    add_output(groups_create)
    groups_rename = groups_sub.add_parser("rename")
    groups_rename.add_argument("group_id")
    groups_rename.add_argument("--name", required=True)
    add_common(groups_rename)
    add_output(groups_rename)
    groups_delete = groups_sub.add_parser("delete")
    groups_delete.add_argument("group_id")
    add_common(groups_delete)
    add_output(groups_delete)
    groups.set_defaults(handler=handle_groups)

    memberships = sub.add_parser("memberships")
    memberships_sub = memberships.add_subparsers(dest="action", required=True)
    memberships_add = memberships_sub.add_parser("add")
    memberships_add.add_argument("--user", required=True)
    memberships_add.add_argument("--group", required=True)
    add_common(memberships_add)
    add_output(memberships_add)
    memberships_remove = memberships_sub.add_parser("remove")
    memberships_remove.add_argument("--user", required=True)
    memberships_remove.add_argument("--group", required=True)
    add_common(memberships_remove)
    add_output(memberships_remove)
    memberships_list = memberships_sub.add_parser("list")
    memberships_list.add_argument("--user")
    memberships_list.add_argument("--group")
    add_common(memberships_list)
    add_output(memberships_list)
    memberships.set_defaults(handler=handle_memberships)

    permission_sets = sub.add_parser("permission-sets")
    permission_sets_sub = permission_sets.add_subparsers(dest="action", required=True)
    permission_sets_list = permission_sets_sub.add_parser("list")
    add_common(permission_sets_list)
    add_output(permission_sets_list)
    permission_sets.set_defaults(handler=handle_permission_sets)

    accounts = sub.add_parser("accounts")
    accounts_sub = accounts.add_subparsers(dest="action", required=True)
    accounts_list = accounts_sub.add_parser("list")
    add_common(accounts_list)
    add_output(accounts_list)
    accounts.set_defaults(handler=handle_accounts)

    assignments = sub.add_parser("assignments")
    assignments_sub = assignments.add_subparsers(dest="action", required=True)
    assignments_list = assignments_sub.add_parser("list")
    assignments_list.add_argument("--account-id")
    assignments_list.add_argument("--permission-set")
    add_common(assignments_list)
    add_output(assignments_list)
    assignments_create = assignments_sub.add_parser("create")
    assignments_create.add_argument("--account-id", required=True)
    assignments_create.add_argument("--permission-set", required=True)
    assignments_create.add_argument("--user")
    assignments_create.add_argument("--group")
    add_common(assignments_create)
    add_output(assignments_create)
    assignments_delete = assignments_sub.add_parser("delete")
    assignments_delete.add_argument("--account-id", required=True)
    assignments_delete.add_argument("--permission-set", required=True)
    assignments_delete.add_argument("--user")
    assignments_delete.add_argument("--group")
    add_common(assignments_delete)
    add_output(assignments_delete)
    assignments.set_defaults(handler=handle_assignments)

    reports = sub.add_parser("reports")
    reports_sub = reports.add_subparsers(dest="report", required=True)
    memberships_report = reports_sub.add_parser("memberships")
    add_common(memberships_report)
    add_output(memberships_report)
    inventory_report = reports_sub.add_parser("inventory")
    inventory_report.add_argument("--output-file")
    add_common(inventory_report)
    add_output(inventory_report)
    reports.set_defaults(handler=handle_reports)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    profile: str | None = None
    try:
        config = resolve_config(args)
        profile = config["profile"]
        clients = create_clients(config)
        ctx = {**config, **clients}
        args.handler(args, ctx)
        return 0
    except IdcError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"ERROR: {error_message(error, profile)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
