"""Report generation for Identity Center users, groups, and assignments."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import typer

from .identitystore import IdentityStoreService
from .permissions import PermissionSetService


app = typer.Typer(help="Generate operational reports.")


def generate_report(
    identitystore: IdentityStoreService,
    permissions: PermissionSetService | None = None,
    *,
    include_assignments: bool = False,
) -> dict[str, Any]:
    users = identitystore.list_users()
    groups = identitystore.list_groups()
    memberships = {
        group.display_name: [user.username for user in identitystore.list_group_memberships(group.group_id)]
        for group in groups
    }
    report: dict[str, Any] = {
        "users": [user.__dict__ for user in users],
        "groups": [group.__dict__ for group in groups],
        "memberships": memberships,
    }
    if include_assignments:
        if permissions is None:
            report["assignments"] = []
        else:
            report["accounts"] = [account.__dict__ for account in permissions.list_accounts()]
            report["permission_sets"] = [
                permission_set.__dict__ for permission_set in permissions.list_permission_sets()
            ]
            report["assignments"] = [
                assignment.__dict__ for assignment in permissions.list_assignments()
            ]
    return report


def write_report(report: dict[str, Any], path: Path, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    if fmt == "csv":
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["section", "id", "name", "detail"])
            for user in report["users"]:
                writer.writerow(["user", user["user_id"], user["username"], user["email"]])
            for group in report["groups"]:
                writer.writerow(["group", group["group_id"], group["display_name"], group["description"]])
            for group_name, users in report["memberships"].items():
                for username in users:
                    writer.writerow(["membership", group_name, username, ""])
            for assignment in report.get("assignments", []):
                writer.writerow(
                    [
                        "assignment",
                        assignment["account_id"],
                        assignment["principal_id"],
                        assignment["permission_set_arn"],
                    ]
                )
        return
    raise ValueError(f"Unsupported report format: {fmt}")


@app.command("generate")
def generate(
    ctx: typer.Context,
    output: Path = typer.Option(Path("reports/idc-report.json"), "--output", "-o", help="Output path."),
    fmt: str = typer.Option("json", "--format", help="json or csv."),
    include_assignments: bool = typer.Option(False, "--include-assignments", help="Include account assignments."),
) -> None:
    report = generate_report(
        ctx.obj["identitystore"],
        ctx.obj.get("permissions"),
        include_assignments=include_assignments,
    )
    write_report(report, output, fmt)
    print(f"Report written successfully: {output}")
