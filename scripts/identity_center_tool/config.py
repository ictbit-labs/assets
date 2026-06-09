"""Configuration and AWS session creation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import yaml

from .exceptions import ConfigError


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


@dataclass(frozen=True)
class AppConfig:
    region: str
    identity_store_id: str
    profile: str
    sso_instance_arn: str | None = None


def load_config(
    config_path: Path | None = None,
    *,
    profile: str | None = None,
    region: str | None = None,
    identity_store_id: str | None = None,
    sso_instance_arn: str | None = None,
) -> AppConfig:
    """Load YAML config and apply CLI overrides."""

    path = config_path or DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is not None:
            if not isinstance(raw, dict):
                raise ConfigError(f"Config file must contain a YAML mapping: {path}")
            data = raw

    resolved_region = region or data.get("region")
    resolved_profile = profile or data.get("profile")
    resolved_identity_store_id = identity_store_id or data.get("identity_store_id")
    resolved_sso_instance_arn = sso_instance_arn or data.get("sso_instance_arn")

    if not resolved_profile:
        raise ConfigError("Missing AWS profile. Set config.yaml profile or pass --profile.")
    if not resolved_region:
        raise ConfigError("Missing region. Set config.yaml region or pass --region.")
    if not resolved_identity_store_id:
        raise ConfigError(
            "Missing identity_store_id. Set config.yaml identity_store_id or pass --identity-store-id."
        )

    return AppConfig(
        region=str(resolved_region),
        identity_store_id=str(resolved_identity_store_id),
        profile=str(resolved_profile),
        sso_instance_arn=str(resolved_sso_instance_arn) if resolved_sso_instance_arn else None,
    )


def create_session(config: AppConfig) -> boto3.Session:
    """Create a boto3 session from resolved CLI/config values."""

    return boto3.Session(profile_name=config.profile, region_name=config.region)
