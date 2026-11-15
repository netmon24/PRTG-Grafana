"""Betriebsarten der Dienste (http, eigenes Zertifikat, Let's Encrypt) und ihre Marker im Volume `tls-certs`.

Dienste und Clients lesen nur die Marker `<dienst>.enabled`, nie die Datenbank des Portals."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .store import ConfigStore

SERVICES = ("grafana", "influxdb", "portal")
Mode = Literal["http", "own", "acme"]
MODES: tuple[str, ...] = ("http", "own", "acme")


@dataclass(frozen=True)
class AcmePrefs:
    """Gemeinsame Angaben für Let's Encrypt. Die Testumgebung ist vorbelegt, die Zustimmung nie."""

    email: str = ""
    staging: bool = True
    terms_accepted: bool = False


@dataclass(frozen=True)
class TlsSetting:
    mode: str = "http"
    domain: str | None = None
    not_after: str | None = None
    last_ok_at: str | None = None
    last_error: str | None = None


def sync_markers(certs_dir: Path, store: ConfigStore) -> None:
    """Legt `<dienst>.enabled` für `own` und `acme` an und entfernt sie bei `http`.

    Zertifikat und Schlüssel bleiben liegen, damit Zurückschalten keine Neuausstellung braucht."""
    certs_dir.mkdir(parents=True, exist_ok=True)
    for kind in SERVICES:
        marker = certs_dir / f"{kind}.enabled"
        if store.get_tls_setting(kind).mode == "http":
            marker.unlink(missing_ok=True)
        elif not marker.exists():
            tmp = certs_dir / f".{kind}.enabled.tmp"
            tmp.write_text("1\n", encoding="utf-8")
            os.replace(tmp, marker)  # atomar: ein Beobachter sieht nie eine halbe Datei


def write_key(path: Path, pem: str, gid: int | None) -> None:
    """Schreibt den privaten Schlüssel von Anfang an nur für Besitzer und Gruppe lesbar (0640).

    InfluxDB und Grafana gehören dazu über die gemeinsame Gruppe, nicht über „alle“."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.fchmod(fd, 0o640)  # auch bei bereits vorhandener Datei und fremdem umask
        if gid is not None:
            os.fchown(fd, -1, gid)
        os.write(fd, pem.encode("utf-8"))
    finally:
        os.close(fd)


def install_pair(certs_dir: Path, kind: str, cert_pem: str, key_pem: str, gid: int | None) -> None:
    """Legt Zertifikat und Schlüssel als Paar ab: erst beide als Temp-Datei, dann beide tauschen.

    Schlägt ein Schritt fehl, bleibt das bisherige Paar unberührt (kein Zertifikat A mit Schlüssel B)."""
    certs_dir.mkdir(parents=True, exist_ok=True)
    crt, key = certs_dir / f"{kind}.crt", certs_dir / f"{kind}.key"
    crt_tmp, key_tmp = certs_dir / f".{kind}.crt.tmp", certs_dir / f".{kind}.key.tmp"
    try:
        write_key(key_tmp, key_pem, gid)
        crt_tmp.write_text(cert_pem, encoding="utf-8")
    except OSError:
        key_tmp.unlink(missing_ok=True)
        crt_tmp.unlink(missing_ok=True)
        raise
    os.replace(key_tmp, key)
    os.replace(crt_tmp, crt)
