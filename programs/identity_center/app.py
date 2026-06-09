#!/usr/bin/env python3
"""Identity Center CDK application entry point."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import yaml

from stacks.groups import IdentityCenterGroupsStack


LOGGER = logging.getLogger("identity_center.app")
APP_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = APP_ROOT / "config" / "config.yaml"
REQUIRED_CONFIG_FIELDS = ("region", "identity_store_id", "sso_instance_arn")


class ConfigError(ValueError):
    """Raised when Identity Center configuration is invalid."""


@dataclass(frozen=True)
class IdentityCenterConfig:
    """Validated Identity Center deployment configuration."""

    region: str
    identity_store_id: str
    sso_instance_arn: str
    groups: list[dict[str, Any]]


def configure_logging() -> None:
    """Configure structured, concise logging for synth/deploy output."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def context_value(app: cdk.App, key: str) -> Any:
    """Read a CDK context value."""

    return app.node.try_get_context(key)


def parse_context_override(key: str, value: Any) -> Any:
    """Parse context values that may arrive as strings from CDK -c flags."""

    if key == "groups" and isinstance(value, str):
        parsed_value = yaml.safe_load(value)
        return [] if parsed_value is None else parsed_value

    return value


def load_config(config_path: Path) -> dict[str, Any]:
    """Load raw YAML configuration from disk."""

    LOGGER.info("Loading configuration", extra={"config_path": str(config_path)})
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")

    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        raise ConfigError(f"Config file must contain a YAML mapping: {config_path}")

    LOGGER.info("Configuration loaded", extra={"config_path": str(config_path)})
    return raw_config


def apply_context_overrides(app: cdk.App, config: dict[str, Any]) -> dict[str, Any]:
    """Apply deploy_cdk.py/CDK -c context values over config.yaml values."""

    resolved = dict(config)
    for key in ("region", "identity_store_id", "sso_instance_arn", "groups"):
        value = context_value(app, key)
        if value is not None:
            resolved[key] = parse_context_override(key, value)

    LOGGER.info("Context overrides applied")
    return resolved


def validate_config(config: dict[str, Any]) -> IdentityCenterConfig:
    """Validate required configuration structure."""

    missing_fields = [field for field in REQUIRED_CONFIG_FIELDS if field not in config]
    if missing_fields:
        raise ConfigError(f"Missing required config field(s): {', '.join(missing_fields)}")

    region = config.get("region")
    if region in (None, ""):
        raise ConfigError("Config field 'region' must not be empty")

    groups = config.get("groups", [])
    if not isinstance(groups, list):
        raise ConfigError("Config field 'groups' must be a list")

    LOGGER.info(
        "Configuration validation successful",
        extra={
            "has_identity_store_id": bool(config.get("identity_store_id")),
            "has_sso_instance_arn": bool(config.get("sso_instance_arn")),
            "group_count": len(groups),
        },
    )
    return IdentityCenterConfig(
        region=str(region),
        identity_store_id=str(config.get("identity_store_id") or ""),
        sso_instance_arn=str(config.get("sso_instance_arn") or ""),
        groups=groups,
    )


def main() -> None:
    """Create and synthesize the Identity Center CDK app."""

    configure_logging()
    app = cdk.App()

    config_path_context = context_value(app, "config_path")
    config_path = Path(config_path_context) if config_path_context else DEFAULT_CONFIG_PATH
    if not config_path.is_absolute():
        config_path = APP_ROOT / config_path

    raw_config = load_config(config_path)
    config = validate_config(apply_context_overrides(app, raw_config))

    stack_name = context_value(app, "stack_name") or "IdentityCenterGroupsStack"
    IdentityCenterGroupsStack(
        app,
        stack_name,
        config=config,
        env=cdk.Environment(region=config.region),
        synthesizer=cdk.BootstraplessSynthesizer(),
    )
    LOGGER.info("Stack instantiated", extra={"stack_name": stack_name})

    app.synth()
    LOGGER.info("CDK synth successful")


if __name__ == "__main__":
    main()
