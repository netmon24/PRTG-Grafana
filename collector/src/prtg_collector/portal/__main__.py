"""Einstieg des Portals: python -m prtg_collector.portal"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from ..config import ConfigError
from ..netenv import internal_verify, service_url
from ..setup_tenants import SetupReport
from ..setup_tenants import run as run_setup
from ..writer import bucket_name
from .acme import AcmeLibBackend, ChallengeServer, ChallengeStore
from .app import create_app
from .auth import MIN_PASSWORD_LEN, Auth
from .purge import TenantPurger
from .setup_runner import SetupRunner
from .status import InfluxStatus
from .store import ConfigStore
from .testing import ConnectionTester
from .tlsmanager import TlsManager

log = logging.getLogger("prtg_collector.portal")


def schedule_restart(delay_s: float = 3.0) -> None:
    """Beendet das Portal kurz nach der Antwort an den Browser; Docker startet es neu (restart: unless-stopped)."""
    threading.Timer(delay_s, os.kill, [os.getpid(), signal.SIGTERM]).start()


def ssl_files(env: Mapping[str, str]) -> tuple[str, str] | None:
    """Zertifikat und Schlüssel des Portals, wenn seine Betriebsart TLS verlangt und beide Dateien vorhanden sind."""
    certs = Path(env.get("CERTS_DIR", "/certs"))
    cert, key = certs / "portal.crt", certs / "portal.key"
    usable = all(p.is_file() and p.stat().st_size for p in (cert, key))
    return (str(cert), str(key)) if usable and (certs / "portal.enabled").is_file() else None


def _gid(text: str | None) -> int | None:
    return int(text) if text and text.strip().isdigit() else None


def start_challenge_server(env: Mapping[str, str], app: FastAPI) -> ChallengeServer:
    """Antwortserver für die HTTP-01-Prüfung von Let's Encrypt (Host-Port 80 zeigt auf diesen Port)."""
    port = int(env.get("ACME_HTTP_INTERNAL_PORT", "8081"))
    server = ChallengeServer(app.state.challenge_store, "0.0.0.0", port)
    server.start()
    return server


def build_app(
    env: Mapping[str, str],
    run_setup: Callable[[Mapping[str, str]], SetupReport] = run_setup,
    restart: Callable[[], None] | None = None,
) -> FastAPI:
    """Baut das Portal aus der Umgebung. Wirft ConfigError bei fehlenden oder unbrauchbaren Angaben."""
    secret = env.get("PORTAL_SECRET_KEY", "")
    if not secret:
        raise ConfigError(
            "PORTAL_SECRET_KEY fehlt in der .env (mindestens 16 Zeichen, zufällig wählen)."
        )
    user = env.get("PORTAL_ADMIN_USER", "admin")
    password = env.get("PORTAL_ADMIN_PASSWORD", "")
    data_dir = Path(env.get("PORTAL_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    store = ConfigStore(data_dir / "portal.db", secret)
    if not store.admin_names() and len(password) < MIN_PASSWORD_LEN:
        raise ConfigError(
            f"PORTAL_ADMIN_PASSWORD fehlt oder ist zu kurz (mindestens {MIN_PASSWORD_LEN} Zeichen)."
        )
    auth = Auth(store, user, password)
    runner = SetupRunner(lambda: run_setup(env))
    status = InfluxStatus.from_influx(
        lambda: service_url(env, "influxdb"),
        env.get("INFLUX_ADMIN_TOKEN", ""),
        env.get("INFLUX_ORG", "netmon24"),
        lambda: [bucket_name(c["id"]) for c in store.customers()],
        verify_ssl=lambda: internal_verify(env),
    )
    certs_dir = Path(env.get("CERTS_DIR", "/certs"))
    answers = ChallengeStore()
    manager = TlsManager(
        store,
        certs_dir,
        AcmeLibBackend(verify_ssl=env.get("ACME_VERIFY_TLS", "true").lower() != "false"),
        answers,
        key_gid=_gid(env.get("TLS_KEY_GID")),
        on_change=runner.trigger,
        after_install=lambda kind: restart() if kind == "portal" and restart else None,
        directory_url=env.get("ACME_DIRECTORY_URL", "").strip() or None,
    )
    app = create_app(
        store,
        auth,
        ConnectionTester(),
        status=status,
        certs_dir=certs_dir,
        key_gid=_gid(env.get("TLS_KEY_GID")),
        tls=manager,
        portal_restart=restart,
        on_change=runner.trigger,
        setup_problems=runner.problems,
        purger=TenantPurger(env),
        trust_forwarded=env.get("PORTAL_TRUST_PROXY", "").lower() == "true",
    )
    app.state.tls_manager, app.state.challenge_store = manager, answers
    runner.trigger()  # beim Start einmal abgleichen
    return app


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        app = build_app(os.environ, restart=schedule_restart)
    except ConfigError as err:
        log.error("%s", err)
        return 2
    start_challenge_server(os.environ, app)
    app.state.tls_manager.start_loop()
    tls = ssl_files(os.environ)
    if tls:
        log.info("Portal läuft mit TLS.")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORTAL_INTERNAL_PORT", "8080")),
        log_level="info",
        ssl_certfile=tls[0] if tls else None,
        ssl_keyfile=tls[1] if tls else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
