"""Konfiguration (cores.yaml). Enthält nur Namen von Umgebungsvariablen, nie Tokens."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

MAX_REGEX_LEN = 200
# Gruppe mit Wiederholung innen und außen, z. B. (a+)+ oder (.*)* (Katastrophen-Backtracking)
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")
Ident = Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,32}$")]


class ConfigError(Exception):
    """Fehler in der Konfiguration. Die Meldung enthält nie Geheimnisse."""


def check_core_url(url: str) -> str:
    """Prüft die Core-Adresse: nur http/https, Host nötig, keine Zugangsdaten, Port 1-65535.

    Die Fehlermeldung enthält die Eingabe nie, damit eingetippte Zugangsdaten nicht weiterwandern."""
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise ValueError("Adresse unbrauchbar") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("Adresse muss mit https:// beginnen")
    if not host:
        raise ValueError("Adresse ohne Rechnernamen")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("Zugangsdaten gehören nicht in die Adresse")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Port ungültig")
    return url


def display_url(url: str) -> str:
    """Adresse ohne Zugangsdaten, Pfad und Query, für Meldungen und Protokolle."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "(Adresse unbrauchbar)"
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}{port}"


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] = []
    groups: list[str] = []
    sensor_types: list[str] = []
    name_regex: str | None = None

    @field_validator("name_regex")
    @classmethod
    def _valid_regex(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value) > MAX_REGEX_LEN:
            raise ValueError(f"Namensmuster ist länger als {MAX_REGEX_LEN} Zeichen")
        if _NESTED_QUANTIFIER.search(value):
            raise ValueError(
                "Namensmuster enthält verschachtelte Wiederholungen wie (a+)+ und könnte den Abruf blockieren"
            )
        try:
            re.compile(value)
        except re.error as err:
            raise ValueError(f"Namensmuster ist kein gültiger regulärer Ausdruck: {err}") from None
        return value


class CoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Ident
    url: str
    api_key_env: str = ""
    api_key_value: SecretStr | None = Field(
        default=None, repr=False
    )  # aus dem Portal, nie aus der YAML
    verify_ssl: bool = True
    ca_file: str | None = None
    pinned_sha256: str | None = None
    interval_s: int = Field(default=60, ge=10, le=300)  # Dashboards lesen die letzten 15 min
    # Reserviert, ohne Wirkung: alle Daten eines Cores werden im Takt `interval_s` abgerufen.
    channel_interval_s: int | None = Field(default=None, ge=10)
    events_interval_s: int | None = Field(default=None, ge=10)
    filters: Filters = Filters()

    @field_validator("url")
    @classmethod
    def _url_ok(cls, value: str) -> str:
        return check_core_url(value)

    def api_key(self, env: Mapping[str, str]) -> str:
        if self.api_key_value is not None and self.api_key_value.get_secret_value():
            return self.api_key_value.get_secret_value()
        value = env.get(self.api_key_env) if self.api_key_env else None
        if not value:
            raise ConfigError(
                f"Umgebungsvariable {self.api_key_env} für Core {self.id} fehlt oder ist leer."
            )
        return value


class CustomerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Ident
    cores: list[CoreConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_core_ids(self) -> CustomerConfig:
        ids = [c.id for c in self.cores]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Core-Kennungen bei Kunde {self.id} sind nicht eindeutig")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Ohne Wirkung: die Aufbewahrung steuert INFLUX_RETENTION_DAYS in der .env (Einrichtungsdienst).
    retention_days: int = Field(default=365, ge=1)
    customers: list[CustomerConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_customer_ids(self) -> AppConfig:
        ids = [c.id for c in self.customers]
        if len(ids) != len(set(ids)):
            raise ValueError("Kunden-Kennungen sind nicht eindeutig")
        return self


def load_config(path: Path, env: Mapping[str, str]) -> AppConfig:
    """Lädt und prüft cores.yaml. `env` ist für spätere Prüfungen vorgesehen und wird nicht gelesen."""
    del env
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as err:
        raise ConfigError(f"Konfiguration {path} nicht lesbar: {type(err).__name__}") from None
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as err:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()
        )
        raise ConfigError(f"Konfiguration ungültig: {problems}") from None
