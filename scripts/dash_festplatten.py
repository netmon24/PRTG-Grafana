"""Dashboard 9: Festplatten und Speicherplatz (Tags diskspacesensor und diskfreesensor).

Aus den Kanälen je Laufwerk (freie Bytes, freier Platz in Prozent) wird die Gesamtgröße
berechnet: gesamt = frei / (Prozent frei / 100). Laufwerke ohne Prozentwert oder mit 0 %
(Gesamtgröße nicht ableitbar) bleiben außen vor. Die Werte kommen als Byte-Zahl, auch wenn
PRTG die Einheit "GB" nennt.
"""

from __future__ import annotations

from typing import Any

import build_dashboards as bd

FILE = "09-festplatten.json"

# Feste Auswahl der Festplattensensoren über die Paessler-Tags, zusätzlich zu den Variablenfiltern.
F_DISK = r"|> filter(fn: (r) => r.prtg_tags =~ /(^|\|)(diskspacesensor|diskfreesensor)(\||$)/)"
# Kanäle je Laufwerk; "Summe", "Total Space" und "... gesamt" sind Sammelwerte und zählen nicht.
F_DRIVE_CHANNEL = (
    r"|> filter(fn: (r) => r.channel =~ /^(Freie Bytes|Free Bytes|Freier Platz|Free Space|Freier Speicher)( %)?( [^ ].*)?$/"
    r" and not (r.channel =~ /gesamt/))"
)
_FILTERS = f"{bd.filters(sensor=True)}\n  {F_DISK}\n  {F_DRIVE_CHANNEL}"
# Laufwerk = Kanalname ohne Präfix; fehlt es (ein Laufwerk je Sensor), steht der Sensorname dafür.
_DRIVE = """  |> map(fn: (r) => {
      d = regexp.replaceAllString(r: /^(Freie Bytes|Free Bytes|Freier Platz|Free Space|Freier Speicher)( %)?\\s*/, v: r.channel, t: "")
      return {r with kind: if r.unit == "%" then "pct" else "free", drive: if d == "" then r.sensor else d}
    })"""


def _drives_now() -> str:
    """Eine Zeile je Laufwerk: aktuell frei in Bytes, frei in Prozent, abgeleitet gesamt in Bytes."""
    return f"""import "regexp"

from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value")
  {_FILTERS}
  {bd.LAST_CHANNEL}
{_DRIVE}
  |> keep(columns: ["core", "device", "sensor", "drive", "kind", "_value"])
  |> group()
  |> pivot(rowKey: ["core", "device", "sensor", "drive"], columnKey: ["kind"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.free and exists r.pct and r.pct > 0.0)
  |> map(fn: (r) => ({{r with total: r.free / r.pct * 100.0}}))"""


def total_query() -> str:
    return (
        _drives_now()
        + """
  |> group()
  |> sum(column: "total")
  |> map(fn: (r) => ({_time: now(), _value: r.total}))"""
    )


def free_query() -> str:
    return (
        _drives_now()
        + """
  |> group()
  |> sum(column: "free")
  |> map(fn: (r) => ({_time: now(), _value: r.free}))"""
    )


def free_pct_overall_query() -> str:
    return (
        _drives_now()
        + """
  |> group()
  |> reduce(identity: {free: 0.0, total: 0.0}, fn: (r, accumulator) => ({free: accumulator.free + r.free, total: accumulator.total + r.total}))
  |> map(fn: (r) => ({_time: now(), _value: if r.total > 0.0 then r.free / r.total * 100.0 else 0.0}))"""
    )


def bars_query() -> str:
    return (
        _drives_now()
        + """
  |> map(fn: (r) => ({Laufwerk: r.device + " " + r.drive, "Frei in Prozent": r.pct}))
  |> sort(columns: ["Frei in Prozent"])"""
    )


def history_query() -> str:
    return f"""import "regexp"

from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value" and r.unit == "%")
  {_FILTERS}
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
{_DRIVE}
  |> map(fn: (r) => ({{r with reihe: r.device + " " + r.drive}}))
  |> keep(columns: ["_time", "_value", "reihe"])
  |> group(columns: ["reihe"])"""


def table_query() -> str:
    return (
        _drives_now()
        + """
  |> sort(columns: ["pct"])"""
    )


def _thresholds_pct(panel: dict[str, Any]) -> dict[str, Any]:
    """Ampel nach Prozent frei: unter 10 rot, unter 20 gelb, sonst grün."""
    steps = [
        {"color": bd.RED, "value": None},
        {"color": bd.YELLOW, "value": 10},
        {"color": bd.GREEN, "value": 20},
    ]
    panel["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": steps}
    return panel


def dashboard() -> dict[str, Any]:
    total = bd.stat(
        1,
        "Speicher gesamt",
        total_query(),
        (0, 0, 6, 4),
        base=bd.PETROL,
        unit="bytes",
        decimals=1,
        no_value="keine Daten",
    )
    free = bd.stat(
        2,
        "Speicher offen",
        free_query(),
        (6, 0, 6, 4),
        base=bd.PETROL,
        unit="bytes",
        decimals=1,
        no_value="keine Daten",
    )
    pct = _thresholds_pct(
        bd.stat(
            3,
            "Frei insgesamt in Prozent",
            free_pct_overall_query(),
            (12, 0, 6, 4),
            unit="percent",
            decimals=1,
            no_value="keine Daten",
        )
    )
    fresh = bd.freshness_tile(4, (18, 0, 6, 4))
    bars = {
        "id": 5,
        "type": "bargauge",
        "title": "Freier Platz je Laufwerk in Prozent",
        "datasource": bd.DS,
        "gridPos": {"x": 0, "y": 4, "w": 24, "h": 9},
        "targets": [{"refId": "A", "datasource": bd.DS, "query": bars_query()}],
        "fieldConfig": {
            "defaults": {
                "unit": "percent",
                "decimals": 1,
                "min": 0,
                "max": 100,
                "noValue": "Keine Daten im gewählten Zeitraum",
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": bd.RED, "value": None},
                        {"color": bd.YELLOW, "value": 10},
                        {"color": bd.GREEN, "value": 20},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "orientation": "horizontal",
            "displayMode": "basic",
            "valueMode": "color",
            "showUnfilled": True,
            "namePlacement": "left",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
        },
    }
    history = bd.timeseries(
        6,
        "Freier Platz in Prozent",
        [(history_query(), "Frei", bd.GREEN)],
        (0, 13, 24, 8),
        unit="percent",
    )
    history["fieldConfig"]["defaults"]["displayName"] = "${__field.labels.reihe}"
    history["fieldConfig"]["defaults"]["decimals"] = 1
    history["fieldConfig"]["defaults"]["min"] = 0
    history["fieldConfig"]["defaults"]["max"] = 100
    history["fieldConfig"]["defaults"]["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    history["fieldConfig"]["defaults"]["color"] = {"mode": "palette-classic"}
    history["fieldConfig"]["defaults"]["thresholds"] = {
        "mode": "absolute",
        "steps": [
            {"color": "transparent", "value": None},
            {"color": bd.RED, "value": 10},
            {"color": bd.YELLOW, "value": 20},
        ],
    }
    history["fieldConfig"]["overrides"] = []
    drives = bd.table(
        7,
        "Laufwerke, schlechteste zuerst",
        [table_query()],
        (0, 21, 24, 9),
        transformations=[
            {
                "id": "organize",
                "options": {
                    "renameByName": {
                        "core": "Core",
                        "device": "Gerät",
                        "sensor": "Sensor",
                        "drive": "Laufwerk",
                        "pct": "Frei in Prozent",
                        "free": "Frei",
                        "total": "Gesamt",
                    },
                    "indexByName": {
                        "device": 0,
                        "sensor": 1,
                        "drive": 2,
                        "pct": 3,
                        "free": 4,
                        "total": 5,
                        "core": 6,
                    },
                },
            }
        ],
        overrides=[
            bd.device_link("Gerät"),
            {
                "matcher": {"id": "byName", "options": "Frei in Prozent"},
                "properties": [
                    {"id": "unit", "value": "percent"},
                    {"id": "decimals", "value": 1},
                    {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
                    {"id": "color", "value": {"mode": "thresholds"}},
                    {
                        "id": "thresholds",
                        "value": {
                            "mode": "absolute",
                            "steps": [
                                {"color": bd.RED, "value": None},
                                {"color": bd.YELLOW, "value": 10},
                                {"color": bd.GREEN, "value": 20},
                            ],
                        },
                    },
                ],
            },
            {
                "matcher": {"id": "byRegexp", "options": "^(Frei|Gesamt)$"},
                "properties": [
                    {"id": "unit", "value": "bytes"},
                    {"id": "decimals", "value": 1},
                ],
            },
        ],
        sort_by="Frei in Prozent",
    )
    panels = [total, free, pct, fresh, bars, history, drives]
    return bd._dashboard(
        "prtg-festplatten",
        "Festplatten und Speicherplatz",
        panels,
        ("core", "tag", "group", "device", "sensor"),
        ["festplatten"],
    )
