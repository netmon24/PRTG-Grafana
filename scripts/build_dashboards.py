#!/usr/bin/env python3
"""Erzeugt die Grafana-Dashboards (JSON) aus Python, damit Abfragen und Gestaltung an einer Stelle stehen.

Aufruf: python3 scripts/build_dashboards.py [Ausgabeordner, Standard grafana/dashboards]
Gestaltung: Statusfarben nur zusammen mit Text, höchstens 12 Panels je Dashboard, feste Reihenfolge der Variablen.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DS = {"type": "influxdb", "uid": "prtg-influx"}
GREEN, YELLOW, RED, BLUE, GREY, PETROL = (
    "#35d763",
    "#f2c94c",
    "#e5484d",
    "#4aa8ff",
    "#8a9ba0",
    "#0b4a59",
)
STATUS_MAP = {
    "0": ("OK", GREEN),
    "1": ("Warnung", YELLOW),
    "2": ("Ausfall", RED),
    "3": ("Pausiert", BLUE),
    "4": ("Unbekannt", GREY),
}


def worst_first(*then: str) -> str:
    """Flux-Schritte, die nach schlechtestem Status zuerst sortieren: Ausfall, Warnung, Unbekannt, Pausiert, OK.

    Die Statuszahl allein taugt nicht, weil Unbekannt (4) und Pausiert (3) größer sind als Ausfall (2)."""
    cols = ", ".join(f'"{c}"' for c in ("rank", *then))
    return (
        "  |> map(fn: (r) => ({r with rank: if r.status_code == 2 then 0 else if r.status_code == 1 then 1 "
        "else if r.status_code == 4 then 2 else if r.status_code == 3 then 3 else 4}))\n"
        f"  |> sort(columns: [{cols}])\n"
        '  |> drop(columns: ["rank"])'
    )
NOW = "-15m"  # aktueller Zustand: jüngster Wert der letzten 15 Minuten

# Wiederverwendete Filterzeilen. Die Grafana-Variablen werden von Grafana ersetzt.
F_CORE = r"|> filter(fn: (r) => r.core =~ /^${core:regex}$/)"
# Drei Tag-Felder: innerhalb eines Feldes gilt „oder“, zwischen den Feldern „und“ (Filter hintereinander).
F_TAGS = [
    rf"|> filter(fn: (r) => r.prtg_tags =~ /(^|\|)${{{name}:regex}}(\||$)/)" for name in ("tag", "tag2", "tag3")
]
F_TAG = "\n  ".join(F_TAGS)
F_GROUP = r"|> filter(fn: (r) => r.group =~ /^${group:regex}$/)"
F_DEVICE = r"|> filter(fn: (r) => r.device =~ /^${device:regex}$/)"
F_SENSOR = r"|> filter(fn: (r) => r.sensor =~ /^${sensor:regex}$/)"
F_CHANNEL = r"|> filter(fn: (r) => r.channel =~ /^${channel:regex}$/)"
F_STYPE = r"|> filter(fn: (r) => r.sensor_type =~ /^${stype:regex}$/)"


def _latest(*keys: str) -> str:
    """Letzter Wert je Objekt. Ändert sich ein Tag (z. B. dns_name), gibt es zwei Reihen je Objekt;
    last() allein würde beide zählen. Darum je Objekt (und Feld) den jüngsten der Reihen nehmen."""
    cols = ", ".join(f'"{k}"' for k in (*keys, "_field"))
    return f'|> last()\n  |> group(columns: [{cols}])\n  |> top(n: 1, columns: ["_time"])'


LAST_SENSOR = _latest("core", "sensor_id")
LAST_DEVICE = _latest("core", "device_id")
LAST_CHANNEL = _latest("core", "sensor_id", "channel_id")
# Status steht als Zahl (status_code) im Wert; die Variable liefert die Zahlen 0 bis 4 als Text.
F_STATUS = r"|> filter(fn: (r) => string(v: r._value) =~ /^(${status:regex})$/)"


def filters(tag: bool = True, group: bool = True, device: bool = True, sensor: bool = False) -> str:
    parts = (
        [F_CORE]
        + ([F_TAG] if tag else [])
        + ([F_GROUP] if group else [])
        + ([F_DEVICE] if device else [])
        + ([F_SENSOR] if sensor else [])
    )
    return "\n  ".join(parts)


# --- Flux-Bausteine ---


def sensors_now(cond: str = "") -> str:
    """Aktueller Status je Sensor, optional eingeschränkt, als Anzahl."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {filters()}
  {LAST_SENSOR}
  {cond}
  |> group()
  |> count()"""


def problems_table() -> str:
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and (r._field == "status_code" or r._field == "message"))
  {filters()}
  {LAST_SENSOR}
  |> map(fn: (r) => ({{r with _value: string(v: r._value)}}))
  |> group()
  |> pivot(rowKey: ["core", "device", "dns_name", "host", "sensor", "sensor_id", "sensor_type"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.status_code != "0" and r.status_code != "3")
  |> map(fn: (r) => ({{r with status_code: int(v: r.status_code)}}))
  |> keep(columns: ["device", "dns_name", "sensor", "sensor_type", "status_code", "message", "core"])
{worst_first("device")}"""


def status_history(code: int, field_name: str) -> str:
    """Anzahl Sensoren, die im jeweiligen Zeitfenster mindestens einen Punkt mit diesem Status hatten.

    Zuerst auf den Status filtern: Es bleiben nur die wenigen Punkte mit Ausfall oder Warnung übrig, und
    nur für diese wird je Sensor (Core und Id, nicht je Reihe) in Fenster gezählt. Das Fenstern aller
    Rohdaten je Reihe brauchte bei 24 Stunden und 1300 Sensoren über 20 Sekunden."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {filters()}
  |> filter(fn: (r) => r._value == {code})
  |> group(columns: ["core", "sensor_id"])
  |> aggregateWindow(every: v.windowPeriod, fn: count, createEmpty: false)
  |> map(fn: (r) => ({{r with _value: 1}}))
  |> group(columns: ["_time"])
  |> sum()
  |> group()
  |> rename(columns: {{_value: "{field_name}"}})
  |> sort(columns: ["_time"])"""


def freshness() -> str:
    return """from(bucket: v.defaultBucket)
  |> range(start: -1d)
  |> filter(fn: (r) => r._measurement == "collector_run" and r._field == "errors")
  |> filter(fn: (r) => r.core =~ /^${core:regex}$/)
  |> last()
  |> group()
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 1)
  |> map(fn: (r) => ({_time: r._time, _value: float(v: int(v: now()) - int(v: r._time)) / 1000000000.0}))"""


def collector_table() -> str:
    """Zahlenfelder je Core (Läufe, Dauer, Objekte). Der Text `last_error` steht in `collector_errors()`:
    ein Feld mit Text und Zahlen in einer Tabelle ließe pivot mit einer Schemakollision scheitern."""
    return """from(bucket: v.defaultBucket)
  |> range(start: -1d)
  |> filter(fn: (r) => r._measurement == "collector_run" and r._field != "last_error")
  |> filter(fn: (r) => r.core =~ /^${core:regex}$/)
  |> last()
  |> group()
  |> pivot(rowKey: ["_time", "core"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "core", "errors", "duration_ms", "objects_read"])
  |> sort(columns: ["core"])"""


def collector_errors() -> str:
    """Letzte Fehlermeldung je Core; Grafana verbindet sie mit der Zahlentabelle über den Core."""
    return """from(bucket: v.defaultBucket)
  |> range(start: -1d)
  |> filter(fn: (r) => r._measurement == "collector_run" and r._field == "last_error")
  |> filter(fn: (r) => r.core =~ /^${core:regex}$/)
  |> last()
  |> group()
  |> keep(columns: ["core", "_value"])
  |> rename(columns: {_value: "last_error"})"""


_FLAGS = """  |> map(fn: (r) => ({r with down: if r._value == 2 then 1.0 else 0.0, counted: if r._value <= 2 then 1.0 else 0.0}))"""


def availability_by_device(period: str, hourly: bool, label: str = "") -> str:
    """Verfügbarkeit in Prozent je Gerät. Gewertet werden Abrufe mit Status 0 bis 2; Pausiert und Unbekannt zählen nicht.

    Kurze Zeiträume lesen die 60-Sekunden-Daten, lange die Stundenverdichtung (`_1h`)."""
    name = label or period
    if hourly:
        return f"""from(bucket: v.defaultBucket + "_1h")
  |> range(start: {period})
  |> filter(fn: (r) => r._measurement == "prtg_device_1h")
  {filters(device=True)}
  |> group(columns: ["core", "device_id", "_field"])
  |> sum()
  |> pivot(rowKey: ["core", "device_id"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.counted_n > 0)
  |> map(fn: (r) => ({{key: r.core + "/" + r.device_id, "{name}": 100.0 * (1.0 - float(v: r.down_n) / float(v: r.counted_n))}}))"""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {period})
  |> filter(fn: (r) => r._measurement == "prtg_device" and r._field == "status_code")
  {filters(device=True)}
{_FLAGS}
  |> group(columns: ["core", "device_id"])
  |> reduce(identity: {{down: 0.0, counted: 0.0}}, fn: (r, accumulator) => ({{down: accumulator.down + r.down, counted: accumulator.counted + r.counted}}))
  |> filter(fn: (r) => r.counted > 0.0)
  |> map(fn: (r) => ({{key: r.core + "/" + r.device_id, "{name}": 100.0 * (1.0 - r.down / r.counted)}}))"""


def availability_overall(period: str, hourly: bool) -> str:
    """Gesamtverfügbarkeit aller gewählten Geräte in Prozent."""
    if hourly:
        return f"""from(bucket: v.defaultBucket + "_1h")
  |> range(start: {period})
  |> filter(fn: (r) => r._measurement == "prtg_device_1h")
  {filters()}
  |> group(columns: ["_field"])
  |> sum()
  |> pivot(rowKey: [], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.counted_n > 0)
  |> map(fn: (r) => ({{_value: 100.0 * (1.0 - float(v: r.down_n) / float(v: r.counted_n))}}))"""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {period})
  |> filter(fn: (r) => r._measurement == "prtg_device" and r._field == "status_code")
  {filters()}
{_FLAGS}
  |> group()
  |> reduce(identity: {{down: 0.0, counted: 0.0}}, fn: (r, accumulator) => ({{down: accumulator.down + r.down, counted: accumulator.counted + r.counted}}))
  |> filter(fn: (r) => r.counted > 0.0)
  |> map(fn: (r) => ({{_value: 100.0 * (1.0 - r.down / r.counted)}}))"""


def device_master() -> str:
    """Stammdaten je Gerät (Name, DNS-Name, Host, Gruppe), Grundlage der Verfügbarkeitstabelle.

    Gelesen wird der jüngste Punkt je Gerät aus dem ganzen Zeitraum der längsten Spalte (365 Tage), damit auch
    nach einem Collectorausfall oder für aus PRTG gelöschte Geräte der Name neben dem Prozentwert steht. Ist
    der Punkt älter als 15 Minuten, gilt der Status als unbekannt statt eines veralteten Werts."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "prtg_device" and r._field == "status_code")
  {filters()}
  {LAST_DEVICE}
  |> group()
  |> map(fn: (r) => ({{r with key: r.core + "/" + r.device_id, _value: if int(v: r._time) > int(v: now()) - 900000000000 then r._value else 4}}))
  |> keep(columns: ["key", "device", "dns_name", "host", "group", "_value"])
  |> rename(columns: {{_value: "Status"}})"""


def tag_sensors_table() -> str:
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and (r._field == "status_code" or r._field == "message"))
  {filters()}
  {LAST_SENSOR}
  |> map(fn: (r) => ({{r with _value: string(v: r._value)}}))
  |> group()
  |> pivot(rowKey: ["core", "device", "dns_name", "host", "sensor", "sensor_id", "sensor_type"], columnKey: ["_field"], valueColumn: "_value")
  |> map(fn: (r) => ({{r with status_code: int(v: r.status_code)}}))
  |> keep(columns: ["device", "dns_name", "sensor", "sensor_type", "status_code", "message"])
{worst_first("device")}"""


_CHANNEL_FILTERS = "\n  ".join([F_CORE, F_TAG, F_GROUP, F_DEVICE, F_SENSOR, F_CHANNEL])
_CHANNEL_SERIES = 'r.device + " / " + r.sensor + " / " + r.channel + " (" + r.unit + ")"'  # unit ist immer gesetzt


def channels_now() -> str:
    """Anzahl der Kanäle mit einem Wert in den letzten Minuten."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {NOW})
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value")
  {_CHANNEL_FILTERS}
  {LAST_CHANNEL}
  |> group()
  |> count()"""


def channel_history(shift: str | None = None, suffix: str = "") -> str:
    """Kanalwerte im gewählten Zeitraum; mit `shift` der um diese Dauer frühere Zeitraum, auf heute gelegt."""
    if shift:
        head = 'import "experimental"\n\n'
        start = f"experimental.subDuration(d: {shift}, from: v.timeRangeStart)"
        stop = f"experimental.subDuration(d: {shift}, from: v.timeRangeStop)"
        tail = f"\n  |> timeShift(duration: {shift})"
    else:
        head, start, stop, tail = "", "v.timeRangeStart", "v.timeRangeStop", ""
    return f"""{head}from(bucket: v.defaultBucket)
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value")
  {_CHANNEL_FILTERS}
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false){tail}
  |> map(fn: (r) => ({{r with reihe: {_CHANNEL_SERIES}{' + " (' + suffix + ')"' if suffix else ""}}}))
  |> keep(columns: ["_time", "_value", "reihe"])
  |> group(columns: ["reihe"])"""


def channel_table() -> str:
    """Aktueller Wert sowie Minimum, Maximum und Mittelwert im gewählten Zeitraum je Kanal."""
    keys = '["device", "dns_name", "sensor", "channel", "unit"]'
    parts = ",\n  ".join(
        f'data |> {fn}() |> drop(columns: ["_time", "_start", "_stop"]) |> map(fn: (r) => ({{r with stat: "{name}"}}))'
        for fn, name in (
            ("last", "Aktuell"),
            ("min", "Minimum"),
            ("max", "Maximum"),
            ("mean", "Mittelwert"),
        )
    )
    return f"""data = from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value")
  {_CHANNEL_FILTERS}
  |> keep(columns: ["_time", "_value", "device", "dns_name", "sensor", "channel", "unit"])
  |> group(columns: {keys})

union(tables: [
  {parts}
])
  |> group()
  |> pivot(rowKey: {keys}, columnKey: ["stat"], valueColumn: "_value")"""


def status_changes(to_code: int | None = None, listing: bool = True) -> str:
    """Statuswechsel je Sensor, abgeleitet aus den Abrufen: `von` und `neu` sind Statuscodes.

    Ein Wechsel zwischen zwei Abrufen ist sichtbar, der erste Wert im Zeitraum nicht (kein Vorgänger)."""
    cond = f"\n  |> filter(fn: (r) => r.neu == {to_code})" if to_code is not None else ""
    tail = (
        """
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 200)
  |> keep(columns: ["_time", "core", "device", "dns_name", "sensor", "von", "neu"])"""
        if listing
        else ""
    )
    return f"""from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {filters()}
  |> duplicate(column: "_value", as: "neu")
  |> difference(columns: ["_value"])
  |> filter(fn: (r) => r._value != 0)
  |> map(fn: (r) => ({{r with von: r.neu - r._value}})){cond}
  |> group(){tail}"""


def changes_count(to_code: int | None = None) -> str:
    return status_changes(to_code, listing=False) + "\n  |> count()"


def changes_history(to_code: int, field_name: str) -> str:
    return (
        status_changes(to_code, listing=False)
        + f"""
  |> aggregateWindow(every: v.windowPeriod, fn: count, createEmpty: false)
  |> rename(columns: {{_value: "{field_name}"}})"""
    )


def render_flux(
    query: str,
    bucket: str,
    core: str = "core-a",
    group: str = "G",
    device: str = "A",
    tag: str = ".*",
    sensor: str = "S10",
    channel: str = "Antwortzeit",
    stype: str = "Ping",
) -> str:
    """Ersetzt die Grafana-Variablen, damit eine Abfrage direkt gegen InfluxDB geprüft werden kann."""
    subst = {
        "v.defaultBucket": f'"{bucket}"',
        "v.windowPeriod": "1m",
        "v.timeRangeStart": "time(v: int(v: now()) - 86400000000000)",  # 24 Stunden zurück, als Zeitpunkt
        "v.timeRangeStop": "now()",
        "${core:regex}": re.escape(core),
        "${group:regex}": re.escape(group),
        "${device:regex}": re.escape(device),
        "${sensor:regex}": re.escape(sensor),
        "${channel:regex}": re.escape(channel),
        "${stype:regex}": re.escape(stype),
        "${status:regex}": "0|1|2|3|4",
        "${tag:regex}": tag,
        "${tag2:regex}": ".*",
        "${tag3:regex}": ".*",
    }
    for key, value in subst.items():
        query = query.replace(key, value)
    return query


# --- Panels ---


def _gridpos(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


def stat(
    pid: int,
    title: str,
    query: str,
    pos: tuple[int, int, int, int],
    base: str = GREEN,
    step: tuple[int, str] | None = None,
    unit: str = "none",
    decimals: int | None = 0,
    no_value: str = "0",
) -> dict[str, Any]:
    steps = [{"color": base, "value": None}] + (
        [{"color": step[1], "value": step[0]}] if step else []
    )
    defaults: dict[str, Any] = {
        "unit": unit,
        "noValue": no_value,
        "color": {"mode": "thresholds"},
        "thresholds": {"mode": "absolute", "steps": steps},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "datasource": DS,
        "gridPos": _gridpos(*pos),
        "targets": [{"refId": "A", "datasource": DS, "query": query}],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "textMode": "value",
            "justifyMode": "center",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def status_mapping() -> list[dict[str, Any]]:
    options = {
        k: {"text": text, "color": color, "index": int(k)}
        for k, (text, color) in STATUS_MAP.items()
    }
    return [{"type": "value", "options": options}]


def table(
    pid: int,
    title: str,
    targets: list[str],
    pos: tuple[int, int, int, int],
    status_columns: tuple[str, ...] = (),
    transformations: list[dict[str, Any]] | None = None,
    overrides: list[dict[str, Any]] | None = None,
    sort_by: str | None = None,
) -> dict[str, Any]:
    ov = []
    for col in status_columns:
        ov.append(
            {
                "matcher": {"id": "byName", "options": col},
                "properties": [
                    {"id": "mappings", "value": status_mapping()},
                    {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                    {"id": "displayName", "value": "Status"},
                ],
            }
        )
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "datasource": DS,
        "gridPos": _gridpos(*pos),
        "targets": [
            {"refId": chr(65 + i), "datasource": DS, "query": q} for i, q in enumerate(targets)
        ],
        "transformations": transformations or [],
        "fieldConfig": {
            "defaults": {
                "noValue": "Keine Daten im gewählten Zeitraum",
                "custom": {"align": "auto"},
            },
            "overrides": ov + (overrides or []),
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "sortBy": [{"displayName": sort_by, "desc": False}] if sort_by else [],
        },
    }


def device_link(column: str) -> dict[str, Any]:
    """Klick auf ein Gerät öffnet Dashboard 4 mit gesetztem Gerät."""
    return {
        "matcher": {"id": "byName", "options": column},
        "properties": [
            {
                "id": "links",
                "value": [
                    {
                        "title": "Kanalverlauf öffnen",
                        "url": "/d/prtg-kanalverlauf?var-device=${__value.text}&${__url_time_range}",
                    }
                ],
            }
        ],
    }


def status_cell(column: str, display: str) -> dict[str, Any]:
    return {
        "matcher": {"id": "byName", "options": column},
        "properties": [
            {"id": "mappings", "value": status_mapping()},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
            {"id": "displayName", "value": display},
        ],
    }


def timeseries(
    pid: int,
    title: str,
    targets: list[tuple[str, str, str]],
    pos: tuple[int, int, int, int],
    unit: str = "none",
) -> dict[str, Any]:
    """targets: (Abfrage, Reihenname, Farbe)"""
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "datasource": DS,
        "gridPos": _gridpos(*pos),
        "targets": [
            {"refId": chr(65 + i), "datasource": DS, "query": q}
            for i, (q, _, _) in enumerate(targets)
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": 0,
                "custom": {"lineWidth": 1, "fillOpacity": 12, "showPoints": "never"},
                "noValue": "Keine Daten im gewählten Zeitraum",
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": name},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": color}}
                    ],
                }
                for _, name, color in targets
            ],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
    }


def text(pid: int, title: str, content: str, pos: tuple[int, int, int, int]) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "text",
        "title": title,
        "gridPos": _gridpos(*pos),
        "options": {"mode": "markdown", "content": content},
    }


# --- Variablen ---


def _var(
    name: str, label: str, query: str, include_all: bool = True, all_value: str | None = None
) -> dict[str, Any]:
    v: dict[str, Any] = {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DS,
        "query": query,
        "refresh": 2,
        "multi": True,
        "includeAll": include_all,
        "sort": 5,
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
    }
    if all_value:
        v["allValue"] = all_value
    return v


def _tag_values(tag: str, measurement: str, field: str) -> str:
    """Werte eines Tags über den Tag-Index (schema.tagValues) statt über einen Vollscan der Rohdaten.

    Ein Scan von sieben Tagen (range, keep, group, distinct) braucht bei 10 000 Sensoren Minuten und
    läuft in Grafanas Zeitgrenze von 30 s. Der Index liefert dieselben Werte in Bruchteilen einer Sekunde.
    Das Prädikat nennt nur Messreihe und Feld, weil nur solche einfachen Bedingungen im Index ausgewertet
    werden; ein contains() auf die übergeordnete Variable würde wieder die Rohdaten lesen. Darum
    schränken die Listen nicht mehr nach Core, Gruppe oder Gerät ein; die Panels filtern weiterhin."""
    return (
        'import "influxdata/influxdb/schema"\n'
        f'schema.tagValues(bucket: v.defaultBucket, tag: "{tag}", start: -7d, '
        f'predicate: (r) => r._measurement == "{measurement}" and r._field == "{field}")'
    )


def variables(*names: str) -> dict[str, Any]:
    allowed = {
        "core": _var(
            "core",
            "Core",
            'import "influxdata/influxdb/schema"\nschema.tagValues(bucket: v.defaultBucket, tag: "core")',
            all_value=".*",
        ),
        "tag": _var(
            "tag",
            "Tag",
            _tag_values("prtg_tag", "prtg_tag_catalog", "count"),
            all_value=".*",
        ),
        "group": _var("group", "Gruppe", _tag_values("group", "prtg_device", "sensor_count"), all_value=".*"),
        "device": _var(
            "device",
            "Gerät",
            _tag_values("device", "prtg_device", "sensor_count"),
            all_value=".*",
        ),
    }
    for n, label in (("tag2", "Tag 2 (und)"), ("tag3", "Tag 3 (und)")):
        allowed[n] = _var(n, label, _tag_values("prtg_tag", "prtg_tag_catalog", "count"), all_value=".*")
    allowed["sensor"] = _var(
        "sensor",
        "Sensor",
        _tag_values("sensor", "prtg_channel", "value"),
        all_value=".*",
    )
    allowed["channel"] = _var(
        "channel",
        "Kanal",
        _tag_values("channel", "prtg_channel", "value"),
        all_value=".*",
    )
    allowed["stype"] = _var(
        "stype",
        "Sensortyp",
        _tag_values("sensor_type", "prtg_sensor_status", "status_code"),
        all_value=".*",
    )
    allowed["status"] = {
        "name": "status",
        "label": "Status",
        "type": "custom",
        "query": "OK : 0,Warnung : 1,Ausfall : 2,Pausiert : 3,Unbekannt : 4",
        "multi": True,
        "includeAll": True,
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
        "allValue": "0|1|2|3|4",
    }
    order = ("core", "group", "device", "sensor", "tag", "tag2", "tag3", "channel", "stype", "status")
    wanted = set(names) | ({"tag2", "tag3"} if "tag" in names else set())  # Tag-Felder gehören zusammen
    return {"list": [allowed[n] for n in order if n in wanted]}


# --- Dashboards ---


FOOTER_NOTE = (
    "**Datenstand:** Alter des letzten Abrufs der PRTG-Daten, gelb nach 3, rot nach 10 Abrufintervallen. "
    "Die Ansicht aktualisiert sich alle 5 Minuten. Zeigt ein Feld einen Fehler oder bleibt es leer, ist die "
    "Datenbank gerade nicht erreichbar oder der Collector liefert nichts; die Felder füllen sich nach der "
    "Wiederkehr von selbst."
)


def _without_old_freshness(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entfernt die einzeln eingebauten Datenstand-Kacheln und schließt die Lücke in ihrer Zeile."""
    kept = [p for p in panels if not p.get("title", "").startswith("Datenstand (Alter")]
    for old in (p for p in panels if p not in kept):
        g = old["gridPos"]
        row = [
            q["gridPos"] for q in kept if q["gridPos"]["y"] == g["y"] and q["gridPos"]["h"] == g["h"]
        ]
        if not row:
            continue
        for grid in row:
            if grid["x"] > g["x"]:
                grid["x"] -= g["w"]
        max(row, key=lambda grid: grid["x"])["w"] += g["w"]
    return kept


def _selection_note(vars_: Iterable[dict[str, Any]]) -> str:
    """Erklärt, wie die Auswahlfelder dieses Dashboards zusammenwirken."""
    labels = [v["label"] for v in vars_ if v["name"] not in ("tag2", "tag3")]
    if len(labels) < 2:
        return "**Auswahl:** Mehrere Werte im Feld Core gelten als „oder“."
    fields = ", ".join(labels[:-1]) + " und " + labels[-1]
    note = (
        f"**Auswahl:** Die Felder {fields} wirken zusammen als „und“. "
        "Mehrere Werte innerhalb eines Feldes gelten als „oder“. „All“ filtert nicht."
    )
    if "Tag" in labels:
        note += (
            " Die Felder Tag, Tag 2 und Tag 3 gelten ebenfalls als „und“: ein Objekt muss zu jedem Feld "
            "mit Auswahl passen (Tag `web`, Tag 2 `db`: nur Objekte mit beiden Tags)."
        )
    return note


def _footer(panels: list[dict[str, Any]], vars_: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ein einheitliches kleines Feld unten rechts: Hinweis (auch zur Auswahl) und Alter der Daten."""
    y = max((p["gridPos"]["y"] + p["gridPos"]["h"] for p in panels), default=0)
    pid = max((p["id"] for p in panels), default=0) + 1
    content = FOOTER_NOTE + "\n\n" + _selection_note(vars_)
    note = text(pid, "Hinweis zur Anzeige", content, (4, y, 16, 4))
    fresh = freshness_tile(pid + 1, (20, y, 4, 4))
    fresh["options"]["text"] = {"titleSize": 11, "valueSize": 18}
    fresh["fieldConfig"]["defaults"]["unit"] = "dtdurations"
    return [note, fresh]


def _dashboard(
    uid: str,
    title: str,
    panels: list[dict[str, Any]],
    vars_: tuple[str, ...],
    tags: list[str],
    time_from: str = "now-24h",
) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "tags": ["netmon24", "prtg", *tags],
        "timezone": "browser",
        "editable": True,
        "schemaVersion": 39,
        "version": 1,
        "refresh": "5m",
        "timepicker": {"refresh_intervals": ["5m", "15m", "30m", "1h"]},
        "time": {"from": time_from, "to": "now"},
        "templating": (templating := variables(*vars_)),
        "annotations": {"list": []},
        "links": [
            {
                "type": "dashboards",
                "title": "Weitere Dashboards",
                "tags": ["netmon24"],
                "asDropdown": True,
                "includeVars": True,
                "keepTime": True,
            }
        ],
        "panels": (lambda kept: [*kept, *_footer(kept, templating["list"])])(
            _without_old_freshness(panels)
        ),
    }


def _tiles() -> list[dict[str, Any]]:
    return [
        stat(1, "Sensoren gesamt", sensors_now(), (0, 0, 4, 4), base=PETROL),
        stat(
            2,
            "Sensoren OK",
            sensors_now("|> filter(fn: (r) => r._value == 0)"),
            (4, 0, 4, 4),
            base=GREEN,
        ),
        stat(
            3,
            "Sensoren mit Warnung",
            sensors_now("|> filter(fn: (r) => r._value == 1)"),
            (8, 0, 4, 4),
            base=GREEN,
            step=(1, YELLOW),
        ),
        stat(
            4,
            "Sensoren mit Ausfall",
            sensors_now("|> filter(fn: (r) => r._value == 2)"),
            (12, 0, 4, 4),
            base=GREEN,
            step=(1, RED),
        ),
        stat(
            5,
            "Pausiert oder unbekannt",
            sensors_now("|> filter(fn: (r) => r._value == 3 or r._value == 4)"),
            (16, 0, 4, 4),
            base=BLUE,
        ),
        stat(
            6,
            "Datenstand (Alter des letzten Abrufs)",
            freshness(),
            (20, 0, 4, 4),
            base=GREEN,
            unit="s",
            step=(180, YELLOW),
            no_value="kein Abruf",
        ),
    ]


def overview() -> dict[str, Any]:
    panels = _tiles()
    # Rot ab 600 s (10 Abrufintervalle bei 60 s), Gelb ab 180 s: zweite Schwelle ergänzen
    panels[5]["fieldConfig"]["defaults"]["thresholds"]["steps"].append({"color": RED, "value": 600})
    panels += [
        table(
            7,
            "Offene Probleme",
            [problems_table()],
            (0, 4, 24, 10),
            status_columns=("status_code",),
            overrides=[device_link("device")],
        ),
        timeseries(
            8,
            "Sensoren mit Ausfall und Warnung im Verlauf",
            [
                (status_history(2, "Ausfall"), "Ausfall", RED),
                (status_history(1, "Warnung"), "Warnung", YELLOW),
            ],
            (0, 14, 14, 8),
        ),
        table(
            9,
            "Collector-Gesundheit je Core",
            [collector_table(), collector_errors()],
            (14, 14, 10, 8),
            transformations=[
                {"id": "joinByField", "options": {"byField": "core", "mode": "outer"}},
                {
                    "id": "organize",
                    "options": {
                        "indexByName": {
                            "_time": 0,
                            "core": 1,
                            "errors": 2,
                            "last_error": 3,
                            "duration_ms": 4,
                            "objects_read": 5,
                        }
                    },
                },
            ],
        ),
    ]
    return _dashboard(
        "prtg-uebersicht",
        "1 Gesamtübersicht",
        panels,
        ("core", "tag", "group", "device"),
        ["uebersicht"],
    )


def availability() -> dict[str, Any]:
    periods = [
        ("-24h", False, "24 Stunden"),
        ("-7d", False, "7 Tage"),
        ("-30d", True, "30 Tage"),
        ("-365d", True, "365 Tage"),
    ]
    tiles = [
        stat(
            i + 1,
            f"Verfügbarkeit {label}",
            availability_overall(p, h),
            (i * 6, 0, 6, 4),
            base=GREEN,
            unit="percent",
            decimals=1,
            no_value="keine Daten",
        )
        for i, (p, h, label) in enumerate(periods)
    ]
    for t in tiles:  # unter 99 Prozent gelb, unter 95 rot
        t["fieldConfig"]["defaults"]["thresholds"]["steps"] = [
            {"color": RED, "value": None},
            {"color": YELLOW, "value": 95},
            {"color": GREEN, "value": 99},
        ]
    targets = [device_master()] + [availability_by_device(p, h, label) for p, h, label in periods]
    transformations = [
        {"id": "joinByField", "options": {"byField": "key", "mode": "outer"}},
        {
            "id": "organize",
            "options": {
                "excludeByName": {"key": True},
                "renameByName": {
                    "device": "Gerät",
                    "dns_name": "DNS-Name",
                    "host": "Host",
                    "group": "Gruppe",
                },
                "indexByName": {"device": 0, "dns_name": 1, "host": 2, "group": 3, "Status": 4},
            },
        },
    ]
    percent = [
        {
            "matcher": {"id": "byName", "options": label},
            "properties": [
                {"id": "unit", "value": "percent"},
                {"id": "decimals", "value": 1},
                {
                    "id": "thresholds",
                    "value": {
                        "mode": "absolute",
                        "steps": [
                            {"color": RED, "value": None},
                            {"color": YELLOW, "value": 95},
                            {"color": GREEN, "value": 99},
                        ],
                    },
                },
                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            ],
        }
        for _, _, label in periods
    ]
    panels = tiles + [
        table(
            5,
            "Verfügbarkeit je Maschine und Gerät",
            targets,
            (0, 4, 24, 14),
            status_columns=("Status",),
            transformations=transformations,
            overrides=[*percent, device_link("Gerät")],
            sort_by="Gerät",
        ),
        text(
            6,
            "Hinweis zur Berechnung",
            "Die Verfügbarkeit wird aus den Abrufen im Takt von 60 Sekunden berechnet (Status OK, Warnung und Ausfall "
            "zählen; pausierte und unbekannte Zeiten zählen nicht). Sie ist eine **Näherung** und kann von der "
            "Berechnung in PRTG abweichen. Die Zeiträume 30 und 365 Tage lesen die Stundenverdichtung und füllen sich "
            "mit jeder vollen Stunde.",
            (0, 18, 24, 3),
        ),
        stat(
            7,
            "Datenstand (Alter des letzten Abrufs)",
            freshness(),
            (0, 21, 6, 3),
            unit="s",
            step=(180, YELLOW),
            no_value="kein Abruf",
        ),
    ]
    panels[6]["fieldConfig"]["defaults"]["thresholds"]["steps"].append({"color": RED, "value": 600})
    return _dashboard(
        "prtg-verfuegbarkeit",
        "2 Verfügbarkeit Maschinen und Geräte",
        panels,
        ("core", "tag", "group", "device"),
        ["verfuegbarkeit"],
        time_from="now-7d",
    )


def tag_view() -> dict[str, Any]:
    panels = _tiles()[:5]
    panels[0]["title"] = "Sensoren mit diesen Tags"
    panels.append(
        stat(
            6,
            "Datenstand (Alter des letzten Abrufs)",
            freshness(),
            (20, 0, 4, 4),
            unit="s",
            step=(180, YELLOW),
            no_value="kein Abruf",
        )
    )
    panels[5]["fieldConfig"]["defaults"]["thresholds"]["steps"].append({"color": RED, "value": 600})
    panels += [
        table(
            7,
            "Sensoren mit den gewählten Tags",
            [tag_sensors_table()],
            (0, 4, 24, 10),
            status_columns=("status_code",),
        ),
        timeseries(
            8,
            "Ausfälle und Warnungen der gewählten Tags im Verlauf",
            [
                (status_history(2, "Ausfall"), "Ausfall", RED),
                (status_history(1, "Warnung"), "Warnung", YELLOW),
            ],
            (0, 14, 12, 8),
        ),
        stat(
            9,
            "Verfügbarkeit der gewählten Tags (24 Stunden)",
            availability_overall("-24h", False),
            (12, 14, 6, 8),
            unit="percent",
            decimals=1,
            no_value="keine Daten",
        ),
        stat(
            10,
            "Verfügbarkeit der gewählten Tags (30 Tage)",
            availability_overall("-30d", True),
            (18, 14, 6, 8),
            unit="percent",
            decimals=1,
            no_value="keine Daten",
        ),
    ]
    for p in panels[8:]:
        p["fieldConfig"]["defaults"]["thresholds"]["steps"] = [
            {"color": RED, "value": None},
            {"color": YELLOW, "value": 95},
            {"color": GREEN, "value": 99},
        ]
    return _dashboard(
        "prtg-tag-ansicht",
        "3 Tag-Ansicht (Vorlage für eigene Ansichten)",
        panels,
        ("core", "tag", "group", "device"),
        ["tags", "vorlage"],
    )


def _staleness(panel: dict[str, Any]) -> dict[str, Any]:
    panel["fieldConfig"]["defaults"]["thresholds"]["steps"].append({"color": RED, "value": 600})
    return panel


def freshness_tile(pid: int, pos: tuple[int, int, int, int]) -> dict[str, Any]:
    """Kachel „Datenstand“: gelb nach 3, rot nach 10 Abrufintervallen."""
    return _staleness(
        stat(
            pid,
            "Datenstand (Alter des letzten Abrufs)",
            freshness(),
            pos,
            unit="s",
            step=(180, YELLOW),
            no_value="kein Abruf",
        )
    )


def channel_view() -> dict[str, Any]:
    """Dashboard 4: Verlauf der Kanalwerte eines Sensors, mit Vergleich zum Vortag und zur Vorwoche."""
    fresh = stat(
        3,
        "Datenstand (Alter des letzten Abrufs)",
        freshness(),
        (6, 0, 6, 4),
        unit="s",
        step=(180, YELLOW),
        no_value="kein Abruf",
    )
    compare = timeseries(
        6,
        "Vergleich mit Vortag und Vorwoche",
        [
            (channel_history(), "Heute", GREEN),
            (channel_history("1d", "Vortag"), "Vortag", GREY),
            (channel_history("7d", "Vorwoche"), "Vorwoche", BLUE),
        ],
        (0, 13, 24, 8),
    )
    compare["fieldConfig"]["defaults"]["displayName"] = "${__field.labels.reihe}"
    compare["fieldConfig"]["defaults"]["custom"]["spanNulls"] = True
    compare["fieldConfig"]["overrides"] = [
        {
            "matcher": {"id": "byRegexp", "options": f".*\\({label}\\)$"},
            "properties": [
                {"id": "color", "value": {"mode": "fixed", "fixedColor": color}},
                {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
            ],
        }
        for label, color in (("Vortag", GREY), ("Vorwoche", BLUE))
    ]
    history = timeseries(
        5,
        "Kanalwerte im Verlauf",
        [(channel_history(), "Wert", GREEN)],
        (0, 4, 24, 9),
    )
    history["fieldConfig"]["defaults"]["displayName"] = "${__field.labels.reihe}"
    history["fieldConfig"]["defaults"]["decimals"] = 2
    history["fieldConfig"]["overrides"] = []
    compare["fieldConfig"]["defaults"]["decimals"] = 2
    values = table(
        7,
        "Aktuelle Werte der gewählten Kanäle",
        [channel_table()],
        (0, 21, 24, 9),
        transformations=[
            {
                "id": "organize",
                "options": {
                    "renameByName": {
                        "device": "Gerät",
                        "dns_name": "DNS-Name",
                        "sensor": "Sensor",
                        "channel": "Kanal",
                        "unit": "Einheit",
                    },
                    "indexByName": {
                        "device": 0,
                        "dns_name": 1,
                        "sensor": 2,
                        "channel": 3,
                        "unit": 4,
                        "Aktuell": 5,
                        "Minimum": 6,
                        "Maximum": 7,
                        "Mittelwert": 8,
                    },
                },
            }
        ],
        overrides=[
            {
                "matcher": {"id": "byRegexp", "options": "Aktuell|Minimum|Maximum|Mittelwert"},
                "properties": [{"id": "decimals", "value": 2}],
            }
        ],
        sort_by="Gerät",
    )
    panels = [
        stat(1, "Kanäle mit aktuellem Wert", channels_now(), (0, 0, 6, 4), base=PETROL),
        _staleness(fresh),
        text(
            4,
            "Bedienung",
            "Wählen Sie oben Gruppe, Gerät, Sensor, Tag und Kanal. Mehrere Tags in einem Feld gelten als „oder“, die Felder Tag, Tag 2 und Tag 3 als „und“. Die Einheit steht in Klammern hinter dem Kanalnamen. "
            "Gestrichelte Linien zeigen denselben Zeitraum vor einem Tag und vor einer Woche. "
            "Die Werte werden im Takt des Collectors (Standard 60 Sekunden) erfasst. "
            "Bei „Alle“ über mehr als 5000 Reihen bricht Grafana ab: dann Gerät oder Sensor eingrenzen.",
            (12, 0, 12, 4),
        ),
        history,
        compare,
        values,
    ]
    # Reihenfolge nach Id, damit die Kopfzeile links oben steht
    panels.sort(key=lambda p: p["id"])
    return _dashboard(
        "prtg-kanalverlauf",
        "4 Sensor- und Kanalverlauf",
        panels,
        ("core", "tag", "group", "device", "sensor", "channel"),
        ["kanaele"],
        time_from="now-6h",
    )


def events_view() -> dict[str, Any]:
    """Dashboard 5: Ereignisse, abgeleitet aus Statuswechseln der Sensoren (die API v2 liefert keine Meldungen)."""
    tiles = [
        stat(1, "Statuswechsel im Zeitraum", changes_count(), (0, 0, 5, 4), base=PETROL),
        stat(
            2,
            "Wechsel auf Ausfall",
            changes_count(2),
            (5, 0, 5, 4),
            base=GREEN,
            step=(1, RED),
        ),
        stat(
            3,
            "Wechsel auf Warnung",
            changes_count(1),
            (10, 0, 5, 4),
            base=GREEN,
            step=(1, YELLOW),
        ),
        stat(4, "Wechsel auf OK (wiederhergestellt)", changes_count(0), (15, 0, 5, 4), base=GREEN),
        _staleness(
            stat(
                5,
                "Datenstand (Alter des letzten Abrufs)",
                freshness(),
                (20, 0, 4, 4),
                unit="s",
                step=(180, YELLOW),
                no_value="kein Abruf",
            )
        ),
    ]
    panels = [
        *tiles,
        timeseries(
            6,
            "Statuswechsel im Verlauf",
            [
                (changes_history(2, "Ausfall"), "Ausfall", RED),
                (changes_history(1, "Warnung"), "Warnung", YELLOW),
                (changes_history(0, "OK"), "OK", GREEN),
            ],
            (0, 4, 24, 8),
        ),
        table(
            7,
            "Ereignisse (Statuswechsel)",
            [status_changes()],
            (0, 12, 24, 12),
            transformations=[
                {
                    "id": "organize",
                    "options": {
                        "renameByName": {
                            "_time": "Zeit",
                            "device": "Gerät",
                            "dns_name": "DNS-Name",
                            "sensor": "Sensor",
                            "core": "Core",
                        },
                        "indexByName": {
                            "_time": 0,
                            "device": 1,
                            "dns_name": 2,
                            "sensor": 3,
                            "von": 4,
                            "neu": 5,
                            "core": 6,
                        },
                    },
                }
            ],
            overrides=[status_cell("von", "Von"), status_cell("neu", "Nach"), device_link("Gerät")],
        ),
        text(
            8,
            "Hinweis zur Herkunft der Ereignisse",
            "Diese Ereignisse sind aus den Statuswechseln der Sensoren **abgeleitet**: Der Collector vergleicht "
            "die Abrufe im Takt von 60 Sekunden. Meldungstexte aus dem PRTG-Protokoll liefert die API v2 "
            "derzeit nicht (Stand 11.11.2026). Wechsel, die zwischen zwei Abrufen kommen und wieder gehen, "
            "sind nicht sichtbar. Die Liste zeigt höchstens die letzten 200 Wechsel im gewählten Zeitraum.",
            (0, 24, 24, 3),
        ),
    ]
    return _dashboard(
        "prtg-ereignisse",
        "5 Meldungen und Ereignisse",
        panels,
        ("core", "tag", "group", "device"),
        ["ereignisse"],
    )


def _extra_dashboards() -> dict[str, dict[str, Any]]:
    """Weitere Dashboards: jede Datei scripts/dash_*.py liefert FILE (Dateiname) und dashboard()."""
    import importlib.util

    # Die Dashboard-Dateien importieren build_dashboards; beim Aufruf als Skript ist das dieses Modul.
    sys.modules.setdefault("build_dashboards", sys.modules[__name__])
    found = {}
    for path in sorted(Path(__file__).resolve().parent.glob("dash_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found[module.FILE] = module.dashboard()
    return found


def build_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "01-gesamtuebersicht.json": overview(),
        "02-verfuegbarkeit.json": availability(),
        "03-tag-ansicht.json": tag_view(),
        "04-kanalverlauf.json": channel_view(),
        "05-ereignisse.json": events_view(),
        **_extra_dashboards(),
    }
    paths = []
    for name, dash in files.items():
        path = out_dir / name
        path.write_text(json.dumps(dash, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    target = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[1] / "grafana" / "dashboards"
    )
    for p in build_all(target):
        print(p)
