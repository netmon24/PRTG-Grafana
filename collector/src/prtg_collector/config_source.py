"""Woher die Konfiguration kommt: Portal-Datenbank (bevorzugt) oder cores.yaml."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path

from .config import AppConfig, ConfigError, load_config


def database_path(env: Mapping[str, str]) -> Path:
    return Path(env.get("PORTAL_DATA_DIR", "/data")) / "portal.db"


def load_from_env(env: Mapping[str, str]) -> AppConfig:
    """Portal-Datenbank, wenn sie Kunden enthält, sonst cores.yaml, sonst eine leere Konfiguration."""
    db = database_path(env)
    if db.is_file():
        from .portal.store import (
            ConfigStore,  # erst hier, damit der Collector ohne Portal-Abhängigkeiten startet
        )

        secret = env.get("PORTAL_SECRET_KEY", "")
        if not secret:
            raise ConfigError(
                "PORTAL_SECRET_KEY fehlt, die Portal-Datenbank lässt sich nicht lesen."
            )
        ca_dir = Path(env.get("CA_DIR") or tempfile.gettempdir()) / "prtg-ca"
        cfg = ConfigStore(db, secret, readonly=True, ca_dir=ca_dir).to_app_config()
        if cfg.customers:
            return cfg
    yaml_path = Path(env.get("CORES_FILE", "/config/cores.yaml"))
    if yaml_path.is_file():
        return load_config(yaml_path, env)
    return AppConfig.model_construct(retention_days=365, customers=[])
