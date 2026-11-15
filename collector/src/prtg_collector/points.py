"""Baut Influx-Punkte aus Domänenobjekten. Reine Funktionen, kein Netzwerk."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from influxdb_client import Point, WritePrecision

from .models import Channel, Device, Event, Inventory, Sensor, Status

MESSAGE_LIMIT = 200
DNS_PLACEHOLDER = "nicht auflösbar"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class PointStats:
    """Zählt verworfene Punkte, damit der Collector sie als Fehler melden kann."""

    dropped: int = 0


def join_tags(tags: Iterable[str]) -> str:
    items = [t for t in tags if t]
    return "|" + "|".join(items) + "|" if items else "|"


def worst_status(statuses: Iterable[Status]) -> Status:
    """Schlechtester gewerteter Status (Ausfall vor Warnung vor OK). Unbekannt und Pausiert zählen nur,
    wenn kein Sensor gewertet wird: dann Unbekannt, sonst Pausiert."""
    statuses = list(statuses)
    counted = [s for s in statuses if s in (Status.UP, Status.WARNING, Status.DOWN)]
    if counted:  # Unbekannt und Pausiert zählen nicht, solange andere Sensoren gewertet werden
        return max(counted, key=lambda s: s.severity)
    return Status.UNKNOWN if Status.UNKNOWN in statuses else Status.PAUSED


def _clean(text: str) -> str:
    return _CONTROL.sub(" ", text)[:MESSAGE_LIMIT]


def _ts(now: datetime) -> datetime:
    return now.replace(microsecond=0)


def _base(point: Point, customer: str, core: str, dev: Device, dns: Mapping[int, str]) -> Point:
    point = (
        point.tag("customer", customer)
        .tag("core", core)
        .tag("probe", dev.probe or "-")
        .tag("group", dev.group or "-")
        .tag("device", dev.name or "-")
        .tag("device_id", str(dev.id))
        .tag("host", dev.host or "-")
        # Influx lässt leere Tags weg; mit Platzhalter hat jede Abfrage die Spalte
        .tag("dns_name", dns.get(dev.id) or DNS_PLACEHOLDER)
    )
    return point


def _devices(inv: Inventory) -> dict[int, Device]:
    return {d.id: d for d in inv.devices}


def status_points(
    customer: str,
    core: str,
    inv: Inventory,
    dns: Mapping[int, str],
    now: datetime,
    stats: PointStats | None = None,
) -> list[Point]:
    devices, out = _devices(inv), []
    for s in inv.sensors:
        dev = devices.get(s.device_id)
        if dev is None:
            if stats:
                stats.dropped += 1
            continue
        p = _base(Point("prtg_sensor_status"), customer, core, dev, dns)
        p = (
            p.tag("sensor", s.name)
            .tag("sensor_id", str(s.id))
            .tag("sensor_type", s.type)
            .tag("prtg_tags", join_tags(s.tags))
            .field("status", s.status.label)
            .field("status_code", s.status.code)
            .field("message", _clean(s.message))
        )
        if s.last_check_age_s is not None:
            p = p.field("last_check_age_s", float(s.last_check_age_s))
        out.append(p.time(_ts(now), WritePrecision.S))
    return out


def device_points(
    customer: str,
    core: str,
    inv: Inventory,
    dns: Mapping[int, str],
    now: datetime,
) -> list[Point]:
    by_dev: dict[int, list[Sensor]] = {}
    for s in inv.sensors:
        by_dev.setdefault(s.device_id, []).append(s)
    out = []
    for dev in inv.devices:
        sensors = by_dev.get(dev.id, [])
        if not sensors:
            continue
        worst = worst_status(s.status for s in sensors)
        p = _base(Point("prtg_device"), customer, core, dev, dns).tag(
            "prtg_tags", join_tags(dev.tags)
        )
        p = (
            p.field("status_code", worst.code)
            .field("sensor_count", len(sensors))
            .field("sensors_down", sum(s.status is Status.DOWN for s in sensors))
            .field("sensors_warning", sum(s.status is Status.WARNING for s in sensors))
        )
        out.append(p.time(_ts(now), WritePrecision.S))
    return out


def channel_points(
    customer: str,
    core: str,
    inv: Inventory,
    channels: Sequence[Channel],
    dns: Mapping[int, str],
    now: datetime,
    stats: PointStats | None = None,
) -> list[Point]:
    devices = _devices(inv)
    sensors = {s.id: s for s in inv.sensors}
    out = []
    for c in channels:
        if c.value is None:
            continue
        s = sensors.get(c.sensor_id)
        dev = devices.get(s.device_id) if s else None
        if s is None or dev is None:
            if stats:
                stats.dropped += 1
            continue
        p = _base(Point("prtg_channel"), customer, core, dev, dns)
        p = (
            p.tag("sensor", s.name)
            .tag("sensor_id", str(s.id))
            .tag("sensor_type", s.type)
            .tag("prtg_tags", join_tags(s.tags))
            .tag("channel", c.name)
            .tag("channel_id", str(c.id))
        )
        p = p.tag("unit", c.unit or "-")  # leere Tags lässt Influx weg
        out.append(p.field("value", float(c.value)).time(_ts(now), WritePrecision.S))
    return out


def _event_id(e: Event) -> str:
    if e.id:
        return e.id
    raw = f"{e.time.isoformat()}|{e.sensor_id}|{e.device_id}|{e.message}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def event_points(
    customer: str, core: str, events: Sequence[Event], dns: Mapping[int, str]
) -> list[Point]:
    out = []
    for e in events:
        p = (
            Point("prtg_event")
            .tag("customer", customer)
            .tag("core", core)
            .tag("event_type", e.type)
        )
        if e.device_id is not None:
            p = p.tag("device_id", str(e.device_id))
            if dns.get(e.device_id):
                p = p.tag("dns_name", dns[e.device_id])
        if e.sensor_id is not None:
            p = p.tag("sensor_id", str(e.sensor_id))
        p = p.field("message", _clean(e.message)).field("status", e.status.label)
        p = p.field("event_id", _event_id(e))
        out.append(p.time(e.time, WritePrecision.S))
    return out


def tag_catalog_points(customer: str, core: str, inv: Inventory, now: datetime) -> list[Point]:
    counts: Counter[tuple[str, str]] = Counter()
    for d in inv.devices:
        counts.update(("device", t) for t in set(d.tags))
    for s in inv.sensors:
        counts.update(("sensor", t) for t in set(s.tags))
    return [
        Point("prtg_tag_catalog")
        .tag("customer", customer)
        .tag("core", core)
        .tag("prtg_tag", tag)
        .tag("object_kind", kind)
        .field("count", n)
        .time(_ts(now), WritePrecision.S)
        for (kind, tag), n in sorted(counts.items())
    ]
