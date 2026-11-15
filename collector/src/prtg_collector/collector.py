"""Abrufzyklus: je Core ein eigener Lauf, Fehler eines Cores stoppen die anderen nie."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from influxdb_client import Point, WritePrecision

from .config import AppConfig, ConfigError, CoreConfig
from .dns import DnsCache
from .filters import apply_filters
from .models import Channel, Inventory
from .points import (
    PointStats,
    channel_points,
    device_points,
    status_points,
    tag_catalog_points,
)
from .prtg_client import PrtgError
from .writer import Writer

SCHEMA_VERSION = 1
DNS_PER_RUN = (
    8  # gleichzeitige Auflösungen je Lauf, damit ein großer Core den Pool nicht allein belegt
)
RUN_LIMIT_FACTOR = 3  # Gesamtfrist je Lauf in Abrufintervallen
# Eigener Pool für DNS: langsame Auflösungen sollen weder das Schreiben noch andere Cores aufhalten,
# die im Standard-Executor laufen.
DNS_TIMEOUT_S = 5.0  # je Auflösung
_DNS_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="dns")
log = logging.getLogger("prtg_collector")


class _FailedClient:
    """Platzhalter, wenn sich der Client nicht bauen lässt: der Lauf meldet den Fehler und versucht es erneut."""

    def __init__(self, err: Exception) -> None:
        self._err = err

    async def fetch_inventory(self) -> Inventory:
        raise self._err

    async def fetch_channels(self) -> list[Channel]:
        raise self._err

    async def aclose(self) -> None:
        return None


class InventorySource(Protocol):
    async def fetch_inventory(self) -> Inventory: ...
    async def fetch_channels(self) -> list[Channel]: ...
    async def aclose(self) -> None: ...


ClientFactory = Callable[[CoreConfig, str], InventorySource]


def _make_client(factory: ClientFactory, core: CoreConfig, token: str) -> InventorySource:
    try:
        return factory(core, token)
    except Exception as err:  # noqa: BLE001 - z. B. unbrauchbare CA-Datei
        return _FailedClient(err)


@dataclass
class RunResult:
    points_written: int = 0
    errors: int = 0
    last_error: str | None = None


def _run_point(
    customer: str,
    core: str,
    now: datetime,
    duration_ms: int,
    objects: int,
    written: int,
    errors: int,
    last_error: str | None,
) -> Point:
    p = (
        Point("collector_run")
        .tag("customer", customer)
        .tag("core", core)
        .field("duration_ms", duration_ms)
        .field("objects_read", objects)
        .field("points_written", written)
        .field("errors", errors)
        .field("schema_version", SCHEMA_VERSION)
    )
    if last_error:
        p = p.field("last_error", last_error[:200])
    return p.time(now.replace(microsecond=0), WritePrecision.S)


async def _resolve_dns(inv: Inventory, dns: DnsCache) -> dict[int, str]:
    hosts = {d.host for d in inv.devices if d.host}
    loop = asyncio.get_running_loop()
    gate = asyncio.Semaphore(DNS_PER_RUN)

    async def one(host: str) -> str:
        async with gate:
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(_DNS_POOL, dns.resolve, host), DNS_TIMEOUT_S
                )
            except TimeoutError:
                return ""  # hängender Resolver: ohne Namen weiter, der Lauf wartet nicht

    names = dict(
        zip(
            hosts,
            await asyncio.gather(*(one(h) for h in hosts)),
            strict=True,
        )
    )
    return {d.id: names.get(d.host, "") for d in inv.devices}


async def _channel_points(
    customer: str,
    core: CoreConfig,
    client: InventorySource,
    inv: Inventory,
    names: Mapping[int, str],
    now: datetime,
    stats: PointStats,
    result: RunResult,
) -> list[Point]:
    """Kanalwerte der gefilterten Sensoren. Fällt der Abruf aus, bleiben die Statusdaten erhalten."""
    try:
        channels = await client.fetch_channels()
    except PrtgError as err:
        result.errors += 1
        result.last_error = f"Kanalwerte nicht lesbar: {err.message}"
        return []
    wanted = {s.id for s in inv.sensors}
    kept = [c for c in channels if c.sensor_id in wanted]
    return channel_points(customer, core.id, inv, kept, names, now, stats)


async def run_core(
    customer: str,
    core: CoreConfig,
    client: InventorySource,
    writer: Writer,
    dns: DnsCache,
    now: datetime,
    time_limit_s: float | None = None,
) -> RunResult:
    started = time.monotonic()
    result, points, objects = RunResult(), [], 0
    limit = time_limit_s if time_limit_s is not None else core.interval_s * RUN_LIMIT_FACTOR
    try:
        # Gesamtfrist: eine tröpfelnde Antwort darf den Lauf nicht unbegrenzt festhalten
        async with asyncio.timeout(limit):
            fetched = await client.fetch_inventory()
            if not fetched.devices and not fetched.sensors and not fetched.skipped:
                # Kein Fehler: es gibt keine Daten, die Dashboards zeigen die Objekte nach 15 min als Unbekannt
                result.last_error = (
                    "PRTG lieferte keine Geräte und Sensoren (Lizenz und Rechte prüfen)."
                )
                log.warning("%s/%s: %s", customer, core.id, result.last_error)
            inv = apply_filters(fetched, core.filters)
            objects = len(inv.sensors)
            names = await _resolve_dns(inv, dns)
            stats = PointStats()
            points = [
                *status_points(customer, core.id, inv, names, now, stats),
                *device_points(customer, core.id, inv, names, now),
                *tag_catalog_points(customer, core.id, inv, now),
            ]
            points.extend(
                await _channel_points(customer, core, client, inv, names, now, stats, result)
            )
        result.errors += stats.dropped
        if stats.dropped:
            result.last_error = f"{stats.dropped} Datenpunkte ohne bekanntes Gerät verworfen."
        if fetched.skipped:
            result.errors += 1
            result.last_error = f"{fetched.skipped} Objekte mit unbrauchbaren Angaben übersprungen."
    except PrtgError as err:
        result.errors, result.last_error = 1, err.message
    except TimeoutError:
        points = []
        result.errors = 1
        result.last_error = f"Abruf nach {limit:g} s abgebrochen (Core antwortet zu langsam)."
    except Exception as err:
        result.errors, result.last_error = 1, f"Unerwarteter Fehler ({type(err).__name__})."
        log.exception("Lauf für %s/%s fehlgeschlagen", customer, core.id)
    result.points_written = len(points)
    duration_ms = int((time.monotonic() - started) * 1000)
    points.append(
        _run_point(
            customer,
            core.id,
            now,
            duration_ms,
            objects,
            result.points_written,
            result.errors,
            result.last_error,
        )
    )
    try:
        await asyncio.to_thread(writer.write, customer, points)
    except Exception as err:  # noqa: BLE001
        result.errors += 1
        status = getattr(err, "status", None)
        if status:
            result.last_error = (
                f"Schreiben in InfluxDB abgelehnt (HTTP {status}). "
                "Bucket und Schreib-Token prüfen, Einrichtung ausführen."
            )
        else:
            result.last_error = (
                f"InfluxDB nicht erreichbar ({type(err).__name__}). "
                "Dieser Abruf geht verloren, der nächste läuft von selbst weiter."
            )
        result.points_written = 0
        log.error("%s/%s: %s", customer, core.id, result.last_error)
    return result


async def run_all(
    cfg: AppConfig,
    env: Mapping[str, str],
    client_factory: ClientFactory,
    writer: Writer,
    dns: DnsCache,
    now: datetime | None = None,
) -> dict[tuple[str, str], RunResult]:
    """Ein Durchlauf über alle Cores, gleichzeitig. Ergebnis je (Kunde, Core)."""
    now = now or datetime.now(UTC)

    async def one(customer: str, core: CoreConfig) -> tuple[tuple[str, str], RunResult]:
        client = _make_client(client_factory, core, core.api_key(env))
        try:
            return (customer, core.id), await run_core(customer, core, client, writer, dns, now)
        finally:
            await client.aclose()

    pairs = await asyncio.gather(*(one(c.id, core) for c in cfg.customers for core in c.cores))
    return dict(pairs)


async def serve(
    cfg: AppConfig,
    env: Mapping[str, str],
    client_factory: ClientFactory,
    writer: Writer,
    dns: DnsCache,
    stop: asyncio.Event,
) -> None:
    """Je Core ein eigener Takt, bis `stop` gesetzt wird."""

    async def loop(customer: str, core: CoreConfig) -> None:
        while not stop.is_set():
            client = _make_client(client_factory, core, core.api_key(env))
            try:
                res = await run_core(customer, core, client, writer, dns, datetime.now(UTC))
                log.info(
                    "%s/%s: %d Punkte, %d Fehler", customer, core.id, res.points_written, res.errors
                )
            finally:
                await client.aclose()
            try:
                await asyncio.wait_for(stop.wait(), timeout=core.interval_s)
            except TimeoutError:
                pass

    await asyncio.gather(*(loop(c.id, core) for c in cfg.customers for core in c.cores))


async def serve_dynamic(
    load: Callable[[], AppConfig],
    env: Mapping[str, str],
    client_factory: ClientFactory,
    writer: Writer,
    dns: DnsCache,
    stop: asyncio.Event,
    poll_s: float = 5.0,
    interval_scale: float = 1.0,
) -> None:
    """Wie `serve`, liest die Konfiguration aber laufend neu: neue, geänderte und entfernte Cores
    werden ohne Neustart übernommen. Ein Core ohne API-Key oder eine unlesbare Konfiguration wird übersprungen."""
    running: dict[tuple[str, str], tuple[str, asyncio.Task[None]]] = {}

    async def core_loop(customer: str, core: CoreConfig, token: str) -> None:
        while True:
            client = _make_client(client_factory, core, token)
            try:
                res = await run_core(customer, core, client, writer, dns, datetime.now(UTC))
                log.info(
                    "%s/%s: %d Punkte, %d Fehler", customer, core.id, res.points_written, res.errors
                )
            finally:
                await client.aclose()
            await asyncio.sleep(core.interval_s * interval_scale)

    async def drop(key: tuple[str, str]) -> None:
        task = running.pop(key)[1]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    try:
        while not stop.is_set():
            try:
                cfg: AppConfig | None = load()
            except Exception as err:  # noqa: BLE001 - laufende Cores nie wegen eines Lesefehlers stoppen
                log.error(
                    "Konfiguration nicht lesbar (%s), es gilt der letzte Stand.", type(err).__name__
                )
                cfg = None
            if cfg is not None:
                wanted: dict[tuple[str, str], tuple[str, CoreConfig, str]] = {}
                for cust in cfg.customers:
                    for core in cust.cores:
                        try:
                            token = core.api_key(env)
                        except ConfigError as err:
                            log.error("%s", err)
                            continue
                        fingerprint = hashlib.sha256(
                            f"{core.model_dump_json()}|{token}".encode()
                        ).hexdigest()
                        wanted[(cust.id, core.id)] = (fingerprint, core, token)
                for key in [k for k in running if k not in wanted or running[k][0] != wanted[k][0]]:
                    await drop(key)
                for key, (fingerprint, core, token) in wanted.items():
                    if key not in running:
                        running[key] = (
                            fingerprint,
                            asyncio.create_task(core_loop(key[0], core, token)),
                        )
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_s)
            except TimeoutError:
                pass
    finally:
        for key in list(running):
            await drop(key)
