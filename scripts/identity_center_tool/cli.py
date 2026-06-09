"""Top-level Typer CLI for IAM Identity Center management."""

from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import assignments, groups, reporting, users
from .config import DEFAULT_CONFIG_PATH, create_session, load_config
from .exceptions import IdcToolError, format_aws_error
from .identitystore import IdentityStoreService
from .permissions import PermissionSetService


app = typer.Typer(
    help="Manage IAM Identity Center users, groups, memberships, and account assignments.",
    no_args_is_help=True,
)
app.add_typer(users.app, name="users")
app.add_typer(groups.app, name="groups")
app.add_typer(assignments.app, name="assignments")
app.add_typer(reporting.app, name="reports")

ACTIVE_PROFILE: str | None = None


def setup_logging(log_path: Path = Path("logs/idc.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s operator=%(operator)s %(message)s",
        force=True,
    )
    old_factory = logging.getLogRecordFactory()
    operator = getpass.getuser()

    def record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = old_factory(*args, **kwargs)
        record.operator = operator
        return record

    logging.setLogRecordFactory(record_factory)


@app.callback()
def callback(
    ctx: typer.Context,
    config: Annotated[Path, typer.Option("--config", help="Path to config.yaml.")] = DEFAULT_CONFIG_PATH,
    profile: Annotated[str | None, typer.Option("--profile", help="AWS profile override.")] = None,
    region: Annotated[str | None, typer.Option("--region", help="AWS region override.")] = None,
    identity_store_id: Annotated[
        str | None, typer.Option("--identity-store-id", help="Identity Store ID override.")
    ] = None,
    sso_instance_arn: Annotated[
        str | None, typer.Option("--sso-instance-arn", help="IAM Identity Center instance ARN override.")
    ] = None,
) -> None:
    global ACTIVE_PROFILE

    if ctx.resilient_parsing or "--help" in sys.argv or "-h" in sys.argv:
        return

    setup_logging()
    app_config = load_config(
        config,
        profile=profile,
        region=region,
        identity_store_id=identity_store_id,
        sso_instance_arn=sso_instance_arn,
    )
    ACTIVE_PROFILE = app_config.profile
    session = create_session(app_config)
    identitystore_client = session.client("identitystore")
    sso_admin_client = session.client("sso-admin")
    organizations_client = session.client("organizations")
    ctx.obj = {
        "config": app_config,
        "identitystore": IdentityStoreService(identitystore_client, app_config.identity_store_id),
        "permissions": PermissionSetService(
            sso_admin_client=sso_admin_client,
            organizations_client=organizations_client,
            identity_store_id=app_config.identity_store_id,
            configured_instance_arn=app_config.sso_instance_arn,
        ),
    }


def main() -> None:
    try:
        app()
    except IdcToolError as error:
        print(error)
        raise SystemExit(1) from None
    except Exception as error:
        print(format_aws_error(error, profile=ACTIVE_PROFILE))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
