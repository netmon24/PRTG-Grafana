"""Zertifikate besorgen und erneuern: verbindet ACME-Client, Ablage im Volume und Betriebsart im Portal."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import ConfigError
from .acme import (
    PRODUCTION,
    STAGING,
    AcmeBackend,
    AcmeError,
    ChallengeStore,
    generate_key_and_csr,
    validate_domain,
)
from .store import ConfigStore
from .tlscheck import validate_pair
from .tlsmodes import SERVICES, install_pair, sync_markers

log = logging.getLogger("prtg_collector.portal")
RENEW_BEFORE_DAYS = 30


class TlsManager:
    def __init__(
        self,
        store: ConfigStore,
        certs_dir: Path,
        backend: AcmeBackend,
        answers: ChallengeStore,
        key_gid: int | None = None,
        on_change: Callable[[], None] | None = None,
        after_install: Callable[[str], None] | None = None,
        directory_url: str | None = None,
    ) -> None:
        self._store, self._certs, self._backend, self._answers = store, certs_dir, backend, answers
        self._gid, self._on_change, self._after = key_gid, on_change, after_install
        self._directory_override = directory_url
        self._lock = (
            threading.Lock()
        )  # ein Zertifikat nach dem anderen, nie zwei Bestellungen gleichzeitig

    def _directory(self, staging: bool) -> str:
        return self._directory_override or (STAGING if staging else PRODUCTION)

    def obtain(self, kind: str, domain: str | None = None) -> datetime:
        """Besorgt ein Zertifikat und legt es wie ein Upload ab. Bei einem Fehler bleibt der alte Stand erhalten."""
        if kind not in SERVICES:
            raise AcmeError("Unbekannter Dienst.")
        prefs = self._store.get_acme_prefs()
        if not prefs.email or "@" not in prefs.email:
            raise AcmeError("Bitte zuerst eine E-Mail-Adresse für Let's Encrypt eintragen.")
        if not prefs.terms_accepted:
            raise AcmeError("Bitte zuerst den Nutzungsbedingungen von Let's Encrypt zustimmen.")
        name = validate_domain(
            domain if domain is not None else self._store.get_tls_setting(kind).domain or ""
        )
        with self._lock:
            self._store.set_tls_domain(kind, name)
            directory = self._directory(prefs.staging)
            try:
                key_pem, csr_pem = generate_key_and_csr(name)
                chain, account = self._backend.obtain(
                    domain=name,
                    email=prefs.email,
                    directory_url=directory,
                    account_key_pem=self._store.get_acme_account_key(directory),
                    answers=self._answers,
                    csr_pem=csr_pem,
                )
                info = validate_pair(chain, key_pem.decode())
            except (AcmeError, ConfigError) as err:
                message = err.message if isinstance(err, AcmeError) else str(err)
                self._store.record_tls_result(kind, error=message)
                raise AcmeError(message) from None
            self._store.set_acme_account_key(directory, account)
            self._install(kind, chain, key_pem.decode())
            self._store.set_tls_setting(kind, "acme", name)
            self._store.record_tls_result(kind, not_after=info.not_after.isoformat())
            sync_markers(self._certs, self._store)
        log.info("Zertifikat für %s besorgt (gültig bis %s).", kind, info.not_after.date())
        if self._on_change:
            self._on_change()
        if self._after:
            self._after(kind)
        return info.not_after

    def _install(self, kind: str, cert_pem: str, key_pem: str) -> None:
        install_pair(self._certs, kind, cert_pem, key_pem, self._gid)
        self._store.save_tls_asset(kind, cert_pem, key_pem)

    def renew_due(self, days: int = RENEW_BEFORE_DAYS) -> list[str]:
        """Erneuert Zertifikate im Modus Let's Encrypt, die in weniger als `days` Tagen ablaufen."""
        renewed = []
        limit = datetime.now(UTC) + timedelta(days=days)
        for kind in SERVICES:
            setting = self._store.get_tls_setting(kind)
            if setting.mode != "acme" or not setting.not_after or not setting.domain:
                continue
            if datetime.fromisoformat(setting.not_after) > limit:
                continue
            try:
                self.obtain(kind, setting.domain)
                renewed.append(kind)
            except AcmeError as err:
                log.warning("Erneuerung für %s fehlgeschlagen: %s", kind, err.message)
        return renewed

    def start_loop(self, interval_s: float = 86400, first_after_s: float = 120) -> threading.Thread:
        """Prüft täglich, ob ein Zertifikat erneuert werden muss."""
        stop = threading.Event()

        def loop() -> None:
            wait = first_after_s
            while not stop.wait(wait):
                try:
                    self.renew_due()
                except Exception:
                    log.exception("Erneuerungsprüfung fehlgeschlagen")
                wait = interval_s

        thread = threading.Thread(target=loop, daemon=True, name="tls-renewal")
        thread.start()
        return thread
