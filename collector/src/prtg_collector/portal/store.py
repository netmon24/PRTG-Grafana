"""Konfigurationsspeicher des Portals (SQLite). API-Keys liegen nur verschlüsselt darin."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography import x509
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from ..config import (
    AppConfig,
    ConfigError,
    CoreConfig,
    CustomerConfig,
    Filters,
    check_core_url,
)
from .crypto import Crypto
from .tlsmodes import MODES, SERVICES, AcmePrefs, TlsSetting

log = logging.getLogger(__name__)

_ID = re.compile(r"^[a-z0-9_-]{1,32}$")
VerifyMode = Literal["system", "ca", "pinned", "off"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customer(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS core(
  id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customer(id),
  name TEXT NOT NULL, url TEXT NOT NULL, api_key_enc TEXT,
  verify_mode TEXT NOT NULL DEFAULT 'system', ca_pem TEXT, pinned_sha256 TEXT,
  interval_s INTEGER NOT NULL DEFAULT 60, channel_interval_s INTEGER, events_interval_s INTEGER,
  filters_json TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
  last_test_at TEXT, last_test_ok INTEGER, last_test_message TEXT);
CREATE TABLE IF NOT EXISTS tls_asset(kind TEXT PRIMARY KEY, cert_pem TEXT NOT NULL, key_enc TEXT NOT NULL,
  uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS tls_setting(kind TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'http', domain TEXT,
  not_after TEXT, last_ok_at TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS acme_prefs(id INTEGER PRIMARY KEY CHECK (id = 1), email TEXT NOT NULL DEFAULT '',
  staging INTEGER NOT NULL DEFAULT 1, terms_accepted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS acme_account(directory TEXT PRIMARY KEY, key_enc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admin(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL);
INSERT OR IGNORE INTO meta(key, value) VALUES('version', '0');
"""


class CoreInput(BaseModel):
    """Eingabe aus dem Portal. `api_key=None` oder leer heißt beim Ändern: unverändert."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8)
    api_key: str | None = None
    verify_mode: VerifyMode = "system"
    ca_pem: str | None = None
    pinned_sha256: str | None = None
    interval_s: int = Field(default=60, ge=10, le=300)
    channel_interval_s: int | None = Field(default=None, ge=10)
    events_interval_s: int | None = Field(default=None, ge=10)
    filters: Filters = Filters()
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _url_ok(cls, value: str) -> str:
        return check_core_url(value)

    @field_validator("ca_pem")
    @classmethod
    def _ca_must_be_pem(cls, value: str | None) -> str | None:
        if value and value.strip():
            try:
                x509.load_pem_x509_certificates(value.encode())
            except ValueError:
                raise ValueError("CA-Zertifikat nicht lesbar") from None
        return value or None


@dataclass(frozen=True)
class CoreView:
    """Lesesicht ohne Geheimnisse."""

    id: str
    customer_id: str
    name: str
    url: str
    has_key: bool
    verify_mode: str
    pinned_sha256: str | None
    interval_s: int
    channel_interval_s: int | None
    events_interval_s: int | None
    filters: Filters
    enabled: bool
    last_test_ok: bool | None
    last_test_message: str | None
    last_test_at: str | None


class ConfigStore:
    def __init__(
        self, path: Path, secret_key: str, readonly: bool = False, ca_dir: Path | None = None
    ) -> None:
        self._path = Path(path)
        self._ca_dir = Path(ca_dir) if ca_dir else self._path.parent / "ca"
        self._crypto = Crypto(secret_key)
        self._lock = threading.RLock()
        if readonly:  # z. B. der Collector mit schreibgeschütztem Volume
            self._db = sqlite3.connect(
                f"file:{self._path}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        if not readonly:
            with self._lock:
                self._db.executescript(_SCHEMA)
                self._db.commit()
        self._check_secret_key(readonly)

    def _check_secret_key(self, readonly: bool) -> None:
        """Ein falscher PORTAL_SECRET_KEY fällt beim Start auf, nicht erst beim ersten Abruf."""
        probe = self._db.execute("SELECT value FROM meta WHERE key='key_check'").fetchone()
        if probe is None:
            probe = self._db.execute(
                "SELECT api_key_enc FROM core WHERE api_key_enc IS NOT NULL "
                "UNION ALL SELECT key_enc FROM tls_asset UNION ALL SELECT key_enc FROM acme_account"
            ).fetchone()  # Stand vor dieser Prüfung: an vorhandenen Geheimnissen prüfen
            if probe is not None:
                self._crypto.decrypt(probe[0])
            if not readonly:
                self._db.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('key_check', ?)",
                    (self._crypto.encrypt("ok"),),
                )
                self._db.commit()
            return
        self._crypto.decrypt(probe[0])

    # --- Version ---
    def version(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])

    def _bump(self) -> None:
        self._db.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key='version'")
        self._db.commit()

    # --- Kunden ---
    def add_customer(self, customer_id: str, name: str) -> None:
        if not _ID.match(customer_id):
            raise ConfigError("Kennung ungültig: nur a-z, 0-9, _ und -, höchstens 32 Zeichen.")
        with self._lock:
            try:
                self._db.execute("INSERT INTO customer(id, name) VALUES(?, ?)", (customer_id, name))
            except sqlite3.IntegrityError:
                raise ConfigError(f"Kunde {customer_id} gibt es schon.") from None
            self._bump()

    def customers(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT id, name FROM customer ORDER BY name"))

    # --- Cores ---
    def add_core(self, customer_id: str, core: CoreInput) -> str:
        with self._lock:
            if not self._db.execute("SELECT 1 FROM customer WHERE id=?", (customer_id,)).fetchone():
                raise ConfigError(f"Kunde {customer_id} gibt es nicht.")
            if self._db.execute("SELECT 1 FROM core WHERE id=?", (core.id,)).fetchone():
                raise ConfigError(f"Core {core.id} gibt es schon.")
            self._db.execute(
                "INSERT INTO core(id, customer_id, name, url, api_key_enc, verify_mode, ca_pem, pinned_sha256,"
                " interval_s, channel_interval_s, events_interval_s, filters_json, enabled)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    core.id,
                    customer_id,
                    core.name,
                    core.url,
                    self._crypto.encrypt(core.api_key) if core.api_key else None,
                    core.verify_mode,
                    core.ca_pem,
                    core.pinned_sha256,
                    core.interval_s,
                    core.channel_interval_s,
                    core.events_interval_s,
                    core.filters.model_dump_json(),
                    int(core.enabled),
                ),
            )
            self._bump()
            return core.id

    def update_core(self, core_id: str, core: CoreInput) -> None:
        with self._lock:
            row = self._db.execute(
                "SELECT api_key_enc, ca_pem, pinned_sha256 FROM core WHERE id=?", (core_id,)
            ).fetchone()
            if row is None:
                raise ConfigError(f"Core {core_id} gibt es nicht.")
            if core.id != core_id:
                raise ConfigError("Die Kennung eines Cores lässt sich nicht ändern.")
            key_enc = self._crypto.encrypt(core.api_key) if core.api_key else row["api_key_enc"]
            # Leere Eingabe heißt "unverändert", wie beim API-Key; beim Wechsel der Art wird gelöscht
            ca_pem = (core.ca_pem or row["ca_pem"]) if core.verify_mode == "ca" else None
            pinned = (
                (core.pinned_sha256 or row["pinned_sha256"])
                if core.verify_mode == "pinned"
                else None
            )
            self._db.execute(
                "UPDATE core SET name=?, url=?, api_key_enc=?, verify_mode=?, ca_pem=?, pinned_sha256=?,"
                " interval_s=?, channel_interval_s=?, events_interval_s=?, filters_json=?, enabled=? WHERE id=?",
                (
                    core.name,
                    core.url,
                    key_enc,
                    core.verify_mode,
                    ca_pem,
                    pinned,
                    core.interval_s,
                    core.channel_interval_s,
                    core.events_interval_s,
                    core.filters.model_dump_json(),
                    int(core.enabled),
                    core_id,
                ),
            )
            self._bump()

    def customer_name(self, customer_id: str) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT name FROM customer WHERE id=?", (customer_id,)).fetchone()
        return r[0] if r else None

    def delete_customer(self, customer_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM core WHERE customer_id=?", (customer_id,))
            self._db.execute("DELETE FROM customer WHERE id=?", (customer_id,))
            self._bump()

    def delete_core(self, core_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM core WHERE id=?", (core_id,))
            self._bump()

    @staticmethod
    def _view(r: sqlite3.Row) -> CoreView:
        return CoreView(
            id=r["id"],
            customer_id=r["customer_id"],
            name=r["name"],
            url=r["url"],
            has_key=bool(r["api_key_enc"]),
            verify_mode=r["verify_mode"],
            pinned_sha256=r["pinned_sha256"],
            interval_s=r["interval_s"],
            channel_interval_s=r["channel_interval_s"],
            events_interval_s=r["events_interval_s"],
            filters=Filters(**json.loads(r["filters_json"])),
            enabled=bool(r["enabled"]),
            last_test_ok=None if r["last_test_ok"] is None else bool(r["last_test_ok"]),
            last_test_message=r["last_test_message"],
            last_test_at=r["last_test_at"],
        )

    def get_core(self, core_id: str) -> CoreView:
        with self._lock:
            r = self._db.execute("SELECT * FROM core WHERE id=?", (core_id,)).fetchone()
        if r is None:
            raise ConfigError(f"Core {core_id} gibt es nicht.")
        return self._view(r)

    def cores(self, customer_id: str | None = None) -> list[CoreView]:
        with self._lock:
            q, args = (
                ("SELECT * FROM core WHERE customer_id=? ORDER BY name", (customer_id,))
                if customer_id
                else ("SELECT * FROM core ORDER BY customer_id, name", ())
            )
            return [self._view(r) for r in self._db.execute(q, args)]

    def core_config(self, core_id: str) -> CoreConfig:
        """Ein einzelner Core als Konfiguration, auch wenn er deaktiviert ist (für den Verbindungstest)."""
        for cust in self._to_customers(only_enabled=False):
            for core in cust.cores:
                if core.id == core_id:
                    return core
        raise ConfigError(f"Core {core_id} gibt es nicht.")

    def set_test_result(self, core_id: str, ok: bool, message: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE core SET last_test_ok=?, last_test_message=?, last_test_at=datetime('now') WHERE id=?",
                (int(ok), message[:300], core_id),
            )
            self._db.commit()

    def set_pinned(self, core_id: str, sha256: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE core SET verify_mode='pinned', pinned_sha256=? WHERE id=?",
                (sha256, core_id),
            )
            self._bump()

    def save_tls_asset(self, kind: str, cert_pem: str, key_pem: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO tls_asset(kind, cert_pem, key_enc) VALUES(?,?,?)",
                (kind, cert_pem, self._crypto.encrypt(key_pem)),
            )
            self._db.commit()

    def get_tls_asset(self, kind: str) -> tuple[str, str] | None:
        with self._lock:
            r = self._db.execute(
                "SELECT cert_pem, key_enc FROM tls_asset WHERE kind=?", (kind,)
            ).fetchone()
        return (r["cert_pem"], self._crypto.decrypt(r["key_enc"])) if r else None

    # --- Betriebsart je Dienst (http, eigenes Zertifikat, Let's Encrypt) ---
    @staticmethod
    def _service(kind: str) -> str:
        if kind not in SERVICES:
            raise ConfigError(f"Unbekannter Dienst: {kind}.")
        return kind

    def get_tls_setting(self, kind: str) -> TlsSetting:
        self._service(kind)
        with self._lock:
            r = self._db.execute(
                "SELECT mode, domain, not_after, last_ok_at, last_error FROM tls_setting WHERE kind=?",
                (kind,),
            ).fetchone()
        return TlsSetting(*tuple(r)) if r else TlsSetting()

    def set_tls_setting(self, kind: str, mode: str, domain: str | None = None) -> None:
        self._service(kind)
        if mode not in MODES:
            raise ConfigError(f"Unbekannte Betriebsart: {mode}.")
        if mode != "http" and self.get_tls_asset(kind) is None:
            raise ConfigError(
                "Kein Zertifikat vorhanden. Bitte zuerst eines hochladen oder bei Let's Encrypt besorgen."
            )
        with self._lock:
            self._db.execute(
                "INSERT INTO tls_setting(kind, mode, domain) VALUES(?,?,?) "
                "ON CONFLICT(kind) DO UPDATE SET mode=excluded.mode, "
                "domain=COALESCE(excluded.domain, tls_setting.domain)",
                (kind, mode, domain),
            )
            self._db.commit()

    def record_tls_result(
        self, kind: str, not_after: str | None = None, error: str | None = None
    ) -> None:
        """Merkt sich das Ergebnis des letzten Versuchs. Ein Fehler lässt Gültigkeit und letzten Erfolg stehen."""
        self._service(kind)
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO tls_setting(kind) VALUES(?)", (kind,))
            if error is None:
                self._db.execute(
                    "UPDATE tls_setting SET not_after=COALESCE(?, not_after), "
                    "last_ok_at=CURRENT_TIMESTAMP, last_error=NULL WHERE kind=?",
                    (not_after, kind),
                )
            else:
                self._db.execute("UPDATE tls_setting SET last_error=? WHERE kind=?", (error, kind))
            self._db.commit()

    def set_tls_domain(self, kind: str, domain: str) -> None:
        self._service(kind)
        with self._lock:
            self._db.execute(
                "INSERT INTO tls_setting(kind, domain) VALUES(?, ?) "
                "ON CONFLICT(kind) DO UPDATE SET domain=excluded.domain",
                (kind, domain),
            )
            self._db.commit()

    # --- Let's Encrypt: gemeinsame Angaben und Kontoschlüssel ---
    def get_acme_prefs(self) -> AcmePrefs:
        with self._lock:
            r = self._db.execute(
                "SELECT email, staging, terms_accepted FROM acme_prefs WHERE id=1"
            ).fetchone()
        return (
            AcmePrefs(r["email"], bool(r["staging"]), bool(r["terms_accepted"]))
            if r
            else AcmePrefs()
        )

    def set_acme_prefs(self, email: str, staging: bool, terms_accepted: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO acme_prefs(id, email, staging, terms_accepted) VALUES(1,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET email=excluded.email, staging=excluded.staging, "
                "terms_accepted=excluded.terms_accepted",
                (email.strip(), int(staging), int(terms_accepted)),
            )
            self._db.commit()

    def get_acme_account_key(self, directory: str) -> bytes | None:
        with self._lock:
            r = self._db.execute(
                "SELECT key_enc FROM acme_account WHERE directory=?", (directory,)
            ).fetchone()
        return self._crypto.decrypt(r["key_enc"]).encode() if r else None

    def set_acme_account_key(self, directory: str, pem: bytes) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO acme_account(directory, key_enc) VALUES(?, ?)",
                (directory, self._crypto.encrypt(pem.decode())),
            )
            self._db.commit()

    # --- Übergabe an den Collector ---
    def to_app_config(self) -> AppConfig:
        customers = [c for c in self._to_customers(only_enabled=True) if c.cores]
        return AppConfig.model_construct(retention_days=365, customers=customers)

    def _to_customers(self, only_enabled: bool) -> list[CustomerConfig]:
        ca_dir = self._ca_dir
        customers: list[CustomerConfig] = []
        with self._lock:
            for c in self._db.execute("SELECT id FROM customer ORDER BY id").fetchall():
                cores = []
                q = (
                    "SELECT * FROM core WHERE customer_id=?"
                    + (" AND enabled=1" if only_enabled else "")
                    + " ORDER BY id"
                )
                for r in self._db.execute(q, (c["id"],)):
                    ca_file = None
                    if r["verify_mode"] == "ca" and r["ca_pem"]:
                        ca_dir.mkdir(parents=True, exist_ok=True)
                        ca_path = ca_dir / f"{r['id']}.pem"
                        ca_path.write_text(r["ca_pem"], encoding="utf-8")
                        ca_file = str(ca_path)
                    try:
                        cores.append(
                            CoreConfig(
                                id=r["id"],
                                url=r["url"],
                                verify_ssl=r["verify_mode"] != "off",
                                ca_file=ca_file,
                                pinned_sha256=r["pinned_sha256"]
                                if r["verify_mode"] == "pinned"
                                else None,
                                api_key_value=SecretStr(self._crypto.decrypt(r["api_key_enc"]))
                                if r["api_key_enc"]
                                else None,
                                interval_s=r["interval_s"],
                                channel_interval_s=r["channel_interval_s"],
                                events_interval_s=r["events_interval_s"],
                                filters=Filters(**json.loads(r["filters_json"])),
                            )
                        )
                    except ValidationError as err:
                        if only_enabled:  # Collector: ein kaputter Core stoppt die anderen nicht
                            log.warning(
                                "Core %s übersprungen: %d Fehler.", r["id"], err.error_count()
                            )
                            continue
                        raise ConfigError(
                            f"Core {r['id']} ungültig: {err.error_count()} Fehler."
                        ) from None
                customers.append(CustomerConfig.model_construct(id=c["id"], cores=cores))
        return customers

    # --- Anmeldedaten ---
    def admin_hash(self, username: str) -> str | None:
        with self._lock:
            r = self._db.execute(
                "SELECT password_hash FROM admin WHERE username=?", (username,)
            ).fetchone()
        return r[0] if r else None

    def set_admin_hash(self, username: str, password_hash: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO admin(username, password_hash) VALUES(?, ?)",
                (username, password_hash),
            )
            self._db.commit()

    def admin_names(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT username FROM admin ORDER BY username")]

    def delete_admin(self, username: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM admin WHERE username=?", (username,))
            self._db.commit()
