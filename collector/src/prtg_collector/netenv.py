"""Einstellungen für die Verbindungen zwischen den Diensten (Collector, Portal, Setup, Grafana, InfluxDB).

Ob ein Dienst TLS spricht, steht in der Marker-Datei `<dienst>.enabled` im Volume `tls-certs`, die das Portal anlegt."""

from __future__ import annotations

import logging
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

log = logging.getLogger("prtg_collector")

_OFF = {"false", "0", "no", "off"}
_HOSTS = {"influxdb": "influxdb:8086", "grafana": "grafana:3000"}
_OVERRIDES = {
    "influxdb": ("INFLUX_INTERNAL_URL", "INFLUX_URL"),
    "grafana": ("GRAFANA_INTERNAL_URL",),
}
Service = Literal["influxdb", "grafana"]


def _certs_dir(env: Mapping[str, str]) -> Path:
    return Path(env.get("CERTS_DIR", "/certs"))


def tls_enabled(env: Mapping[str, str], service: str) -> bool:
    return (_certs_dir(env) / f"{service}.enabled").is_file()


def service_url(env: Mapping[str, str], service: Service) -> str:
    """Adresse eines Dienstes im Compose-Netz: ausdrücklich gesetzte Adresse, sonst nach Marker http oder https."""
    for name in _OVERRIDES[service]:
        if env.get(name, "").strip():
            return env[name].strip()
    scheme = "https" if tls_enabled(env, service) else "http"
    return f"{scheme}://{_HOSTS[service]}"


def internal_verify(env: Mapping[str, str]) -> bool:
    """Zertifikate interner Verbindungen prüfen?

    `INTERNAL_TLS_VERIFY` entscheidet, wenn gesetzt (nur ein ausdrückliches „false“ schaltet ab). Ohne Angabe wird
    nur dann nicht geprüft, wenn ein Dienst im Compose-Netz TLS spricht: das Zertifikat lautet auf den öffentlichen
    Namen, die Container sprechen sich aber mit `influxdb` und `grafana` an. Eine ausdrücklich gesetzte Adresse
    (`*_INTERNAL_URL`, `INFLUX_URL`) wird immer geprüft, außer `INTERNAL_TLS_VERIFY=false` verlangt es."""
    value = env.get("INTERNAL_TLS_VERIFY", "").strip().lower()
    explicit_address = any(env.get(n, "").strip() for names in _OVERRIDES.values() for n in names)
    if value:
        verify = value not in _OFF
    else:
        verify = explicit_address or not any(tls_enabled(env, s) for s in _HOSTS)
    if not verify:
        _quiet_insecure_warnings()
    return verify


def _quiet_insecure_warnings() -> None:
    """Die Warnung je Verbindung wäre Rauschen: die abgeschaltete Prüfung wird einmal beim Start gemeldet."""
    from urllib3.exceptions import InsecureRequestWarning

    warnings.simplefilter("ignore", InsecureRequestWarning)


def internal_tls(env: Mapping[str, str]) -> bool:
    """Wie `internal_verify`, meldet eine abgeschaltete Prüfung aber einmal im Protokoll statt bei jeder Verbindung."""
    verify = internal_verify(env)
    if not verify:
        log.warning("Zertifikatsprüfung für interne Verbindungen ist abgeschaltet.")
    return verify
