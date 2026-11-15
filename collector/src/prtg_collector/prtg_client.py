"""Lesender Client für die PRTG API v2. Sendet ausschließlich GET-Anfragen."""

from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
from collections.abc import Mapping
from typing import Any

import httpx

from .config import CoreConfig, display_url
from .models import Channel, Device, Inventory, Sensor, Status

PAGE_LIMIT = 3000  # Höchstwert der API je Antwort
MAX_PAGES = 1000  # harte Obergrenze gegen endloses Blättern (3 Mio. Objekte je Liste)
RETRIES = 3
_BASE = "/api/v2/experimental"


class PrtgError(Exception):
    """Fehler beim Abruf. Die Meldung ist deutsch und enthält nie den API-Key."""

    def __init__(self, core_id: str, status: int | None, message: str, kind: str = "other") -> None:
        super().__init__(f"Core {core_id}: {message}")
        self.core_id, self.status, self.message, self.kind = core_id, status, message, kind


LICENSE_MESSAGE = (
    "Die PRTG-Lizenz ist inaktiv (LICENSE_INACTIVE): PRTG sperrt dann die API. "
    "Lizenz im PRTG-Core aktivieren oder erneuern, der Collector holt danach von selbst wieder Daten."
)


def _describe(status: int) -> str:
    if status == 401:
        return "API-Key abgelehnt (401). Der Key muss aus der neuen Oberfläche (Port 1616) stammen."
    if status == 403:
        return "Keine Berechtigung (403). Der Benutzer des Keys braucht Leserechte."
    if status == 404:
        return "Pfad nicht gefunden (404). Läuft die API v2 auf diesem Port (üblich 1616)?"
    if status >= 500:
        return f"PRTG meldet einen Serverfehler ({status})."
    return f"Unerwartete Antwort ({status})."


def _s(value: Any) -> str:
    """Text aus einem API-Wert; null wird zu leerem Text, nicht zu \"None\"."""
    text = "" if value is None else str(value)
    # "${" würde Grafana in Variablen und Abfragen als Platzhalter lesen
    return text.replace("${", "$ {")


def _ids(ref: Mapping[str, Any] | None) -> tuple[str, str]:
    ref = ref or {}
    return _s(ref.get("id")), _s(ref.get("type"))


class _PinMismatchError(Exception):
    """Interner Abbruch bei abweichendem Zertifikat-Fingerabdruck."""


class PrtgClient:
    def __init__(
        self, cfg: CoreConfig, token: str, backoff_s: float = 1.0, timeout_s: float = 25.0
    ) -> None:
        self._cfg, self._backoff, self._timeout = cfg, backoff_s, timeout_s
        self._pin_verified = False
        verify: bool | ssl.SSLContext | str = cfg.verify_ssl and not cfg.pinned_sha256
        if cfg.verify_ssl and cfg.ca_file and not cfg.pinned_sha256:
            verify = ssl.create_default_context(cafile=cfg.ca_file)
        self._http = httpx.AsyncClient(
            base_url=cfg.url.rstrip("/"),
            verify=verify,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: Mapping[str, Any]) -> httpx.Response:
        core = self._cfg.id
        for attempt in range(1, RETRIES + 1):
            try:
                resp = await self._http.get(
                    path, params=params, extensions={"trace": self._on_trace}
                )
            except _PinMismatchError:
                raise PrtgError(
                    core, None, "Zertifikat hat sich geändert (Fingerabdruck passt nicht).", "tls"
                ) from None
            except httpx.TimeoutException:
                raise PrtgError(
                    core, None, f"Keine Antwort innerhalb von {self._timeout:g} s."
                ) from None
            except httpx.ConnectError as err:
                if isinstance(err.__cause__, ssl.SSLError) or "CERTIFICATE" in str(err).upper():
                    raise PrtgError(
                        core, None, "Zertifikat nicht vertrauenswürdig.", "tls"
                    ) from None
                raise PrtgError(
                    core, None, f"Keine Verbindung zu {display_url(self._cfg.url)}.", "net"
                ) from None
            except httpx.HTTPError as err:
                raise PrtgError(core, None, f"Verbindungsfehler ({type(err).__name__}).") from None
            self._check_pin()
            license_inactive = resp.status_code >= 500 and "LICENSE_INACTIVE" in resp.text
            if resp.status_code >= 500 and attempt < RETRIES and not license_inactive:
                await asyncio.sleep(self._backoff * attempt)
                continue
            if resp.status_code >= 400:
                raise PrtgError(
                    core,
                    resp.status_code,
                    LICENSE_MESSAGE if license_inactive else _describe(resp.status_code),
                    "auth" if resp.status_code in (401, 403) else "server",
                )
            return resp
        raise AssertionError("unerreichbar")  # pragma: no cover

    async def _on_trace(self, name: str, info: Mapping[str, Any]) -> None:
        """Prüft den Fingerabdruck direkt nach dem TLS-Handshake, bevor Daten gesendet werden."""
        if name != "connection.start_tls.complete" or not self._cfg.pinned_sha256:
            return  # ohne Fingerabdruck prüft allein die TLS-Einstellung von httpx
        stream = info.get("return_value")
        ssl_obj = stream.get_extra_info("ssl_object") if stream is not None else None
        der = ssl_obj.getpeercert(binary_form=True) if ssl_obj is not None else None
        pin = (self._cfg.pinned_sha256 or "").lower().replace(":", "")
        if not der or hashlib.sha256(der).hexdigest() != pin:
            raise _PinMismatchError
        self._pin_verified = True

    def _check_pin(self) -> None:
        if self._cfg.pinned_sha256 and not self._pin_verified:
            raise PrtgError(
                self._cfg.id,
                None,
                "Verbindung ist nicht verschlüsselt, Fingerabdruck nicht prüfbar.",
                "tls",
            )

    async def count_probes(self) -> int:
        """Kleinste sinnvolle Abfrage für den Verbindungstest: Anzahl der Probes."""
        resp = await self._get(f"{_BASE}/probes", {"limit": 1})
        total = resp.headers.get("X-Total-Count")
        if total is not None:
            return int(total)
        return len(json.loads(resp.text)) if resp.text.strip() else 0

    async def _list(self, kind: str) -> list[dict[str, Any]]:
        """Liest eine Liste vollständig, blättert über `offset` bis `X-Total-Count` erreicht ist."""
        items: list[dict[str, Any]] = []
        seen_first: set[str] = set()
        for _ in range(MAX_PAGES):
            resp = await self._get(f"{_BASE}/{kind}", {"limit": PAGE_LIMIT, "offset": len(items)})
            try:
                page = json.loads(resp.text) if resp.text.strip() else []
            except ValueError:
                raise PrtgError(
                    self._cfg.id, resp.status_code, "Antwort ist kein gültiges JSON."
                ) from None
            if isinstance(page, dict):
                page = page.get("items", [])
            if not page:
                return items
            # Ignoriert die Gegenstelle `offset`, käme dieselbe Seite wieder: dann abbrechen statt endlos sammeln.
            first = json.dumps(page[0], sort_keys=True, default=str)
            if first in seen_first:
                raise PrtgError(
                    self._cfg.id,
                    resp.status_code,
                    "Die Antwort blättert nicht weiter (gleiche Seite erneut geliefert). "
                    "Proxy oder API-Version prüfen.",
                )
            seen_first.add(first)
            items.extend(page)
            total = resp.headers.get("X-Total-Count")
            if total is not None:
                try:
                    limit = int(total)
                except ValueError:
                    raise PrtgError(
                        self._cfg.id, resp.status_code, "Ungültiger Header X-Total-Count."
                    ) from None
                if len(items) >= limit:
                    return items
        raise PrtgError(
            self._cfg.id, None, f"Mehr als {MAX_PAGES} Seiten für {kind}, Abruf abgebrochen."
        )

    async def fetch_inventory(self) -> Inventory:
        probes, groups, devices, sensors = (
            await self._list("probes"),
            await self._list("groups"),
            await self._list("devices"),
            await self._list("sensors"),
        )
        probe_names = {str(p["id"]): str(p.get("name", "")) for p in probes}
        group_info = {
            str(g["id"]): (str(g.get("name", "")), _ids(g.get("parent"))[0]) for g in groups
        }

        def probe_of(node_id: str) -> str:
            for _ in range(64):  # Schutz gegen Schleifen
                if node_id in probe_names:
                    return probe_names[node_id]
                if node_id not in group_info:
                    return ""
                node_id = group_info[node_id][1]
            return ""

        skipped = 0
        out_devices = []
        for d in devices:
            try:
                parent_id, _ = _ids(d.get("parent"))
                group = group_info.get(parent_id, (_s((d.get("parent") or {}).get("name")), ""))[0]
                basic = d.get("basic") or {}
                out_devices.append(
                    Device(
                        id=int(d["id"]),
                        name=_s(d.get("name")),
                        host=_s(basic.get("host")),
                        probe=probe_of(parent_id),
                        group=group,
                        tags=_tags(basic),
                    )
                )
            except (KeyError, ValueError, TypeError, AttributeError):
                skipped += 1  # ein fehlerhaftes Objekt darf nicht den ganzen Abruf verwerfen
        out_sensors = []
        for s in sensors:
            try:
                basic = s.get("basic") or {}
                out_sensors.append(
                    Sensor(
                        id=int(s["id"]),
                        device_id=int(_ids(s.get("parent"))[0] or 0),
                        name=_s(s.get("name")),
                        type=_s(s.get("kind_name") or s.get("kind")),
                        status=Status.from_prtg(s.get("status")),
                        message=_s(s.get("message")),
                        tags=_tags(basic),
                    )
                )
            except (KeyError, ValueError, TypeError, AttributeError):
                skipped += 1
        return Inventory(devices=out_devices, sensors=out_sensors, skipped=skipped)

    async def fetch_channels(self) -> list[Channel]:
        """Aktuelle Kanalwerte aller Sensoren (`last_measurement`). Nur Zahlenwerte erhalten einen Wert."""
        out = []
        for c in await self._list("channels"):
            try:
                sensor_id, _ = _ids(c.get("parent"))
                index = _s(c.get("id")).split(".")[-1]
                basic = c.get("basic") or {}
                out.append(
                    Channel(
                        sensor_id=int(sensor_id),
                        id=int(index),
                        name=_s(c.get("name")),
                        unit=_s(basic.get("displayunit")),
                        value=_number((c.get("last_measurement") or {}).get("value")),
                    )
                )
            except (ValueError, TypeError, AttributeError):
                continue  # ein fehlerhafter Kanal darf die übrigen nicht verwerfen
        return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _tags(basic: Mapping[str, Any]) -> tuple[str, ...]:
    """Wirksame Tags: eigene vor geerbten, ohne Dubletten."""
    seen: dict[str, None] = {}
    for t in [*(basic.get("tags") or []), *(basic.get("parenttags") or [])]:
        if t is not None:
            seen.setdefault(str(t), None)
    return tuple(seen)
