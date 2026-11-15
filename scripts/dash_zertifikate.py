"""Dashboard 11: Zertifikate, Laufzeit (Vorlage: cert-manager)."""

from typing import Any

import build_dashboards as bd

FILE = "11-zertifikate.json"

# Kanalnamen je nach PRTG-Sprache (deutsch / englisch)
DAYS = "Tage bis zum Ablauf|Days to Expiration"
SELF = "Selbstsigniert|Self-Signed"
REVOKED = "Widerrufen|Revoked"

_FILTERS = bd.filters(sensor=True)
_PICK = '|> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value")'


def _days(cond: str = "") -> str:
    """Restlaufzeit in Tagen, jüngster Wert je Zertifikatssensor (Zeilen: ein Wert je Sensor)."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  {_PICK}
  {_FILTERS}
  |> filter(fn: (r) => r.channel =~ /^({DAYS})$/)
  {bd.LAST_CHANNEL}
  {cond}
  |> group()"""


def _count(cond: str) -> str:
    return _days(cond) + "\n  |> count()"


def _remaining() -> str:
    return (
        _days()
        + """
  |> map(fn: (r) => ({r with reihe: r.device + " / " + r.sensor}))
  |> keep(columns: ["reihe", "_value"])
  |> sort(columns: ["_value"])
  |> limit(n: 30)"""
    )


def _history() -> str:
    """Verlauf der Restlaufzeit der zehn Zertifikate mit der kürzesten aktuellen Laufzeit."""
    return f"""ids = {_days().replace(chr(10) + "  |> group()", "")}
  |> group()
  |> sort(columns: ["_value"])
  |> limit(n: 10)
  |> map(fn: (r) => ({{r with key: r.core + "/" + r.sensor_id}}))
  |> findColumn(fn: (key) => true, column: "key")

from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  {_PICK}
  {_FILTERS}
  |> filter(fn: (r) => r.channel =~ /^({DAYS})$/)
  |> filter(fn: (r) => contains(value: r.core + "/" + r.sensor_id, set: ids))
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false)
  |> map(fn: (r) => ({{r with reihe: r.device + " / " + r.sensor}}))
  |> keep(columns: ["_time", "_value", "reihe"])
  |> group(columns: ["reihe"])"""


def _details() -> str:
    """Eine Zeile je Zertifikat: Restlaufzeit, selbstsigniert, widerrufen."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  {_PICK}
  {_FILTERS}
  |> filter(fn: (r) => r.channel =~ /^({DAYS}|{SELF}|{REVOKED})$/)
  {bd.LAST_CHANNEL}
  |> map(fn: (r) => ({{r with k: if r.channel =~ /^({DAYS})$/ then "tage" else if r.channel =~ /^({SELF})$/ then "selbst" else "widerrufen"}}))
  |> map(fn: (r) => ({{r with key: r.core + "/" + r.sensor_id}}))
  |> keep(columns: ["device", "host", "sensor", "key", "k", "_value"])
  |> group()
  |> pivot(rowKey: ["device", "host", "sensor", "key"], columnKey: ["k"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.tage)"""


def _status() -> str:
    """Status der Zertifikatssensoren (nur die Sensoren mit Restlaufzeit-Kanal)."""
    return f"""ids = {_days().replace(chr(10) + "  |> group()", "")}
  |> group()
  |> map(fn: (r) => ({{r with key: r.core + "/" + r.sensor_id}}))
  |> findColumn(fn: (key) => true, column: "key")

from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  |> filter(fn: (r) => contains(value: r.core + "/" + r.sensor_id, set: ids))
  {bd.LAST_SENSOR}"""


def _status_table() -> str:
    return (
        _status()
        + """
  |> group()
  |> map(fn: (r) => ({r with key: r.core + "/" + r.sensor_id}))
  |> keep(columns: ["key", "_value"])
  |> rename(columns: {_value: "status"})"""
    )


def _status_problems() -> str:
    return (
        _status()
        + """
  |> filter(fn: (r) => r._value >= 1 and r._value <= 2)
  |> group()
  |> count()"""
    )


def _bargauge() -> dict[str, Any]:
    panel = bd.table(
        5,
        "Restlaufzeit in Tagen je Zertifikat",
        [_remaining()],
        (0, 4, 12, 10),
    )
    panel["type"] = "bargauge"
    panel.pop("transformations", None)
    panel["fieldConfig"] = {
        "defaults": {
            "unit": "suffix: Tage",
            "min": 0,
            "max": 400,
            "decimals": 0,
            "noValue": "Keine Daten im gewählten Zeitraum",
            "color": {"mode": "thresholds"},
            "thresholds": {
                "mode": "absolute",
                "steps": [
                    {"color": bd.RED, "value": None},
                    {"color": bd.YELLOW, "value": 14},
                    {"color": bd.GREEN, "value": 30},
                ],
            },
        },
        "overrides": [],
    }
    panel["options"] = {
        "orientation": "horizontal",
        "displayMode": "gradient",
        "valueMode": "text",
        "showUnfilled": True,
        "reduceOptions": {"calcs": [], "fields": "/^_value$/", "values": True, "limit": 30},
    }
    return panel


def _yes_no() -> list[dict[str, Any]]:
    mapping = [
        {
            "type": "value",
            "options": {
                "0": {"text": "Nein", "color": bd.GREEN, "index": 0},
                "1": {"text": "Ja", "color": bd.RED, "index": 1},
                "-1": {"text": "unbekannt", "color": bd.GREY, "index": 2},
            },
        }
    ]
    return [
        {
            "matcher": {"id": "byName", "options": col},
            "properties": [
                {"id": "mappings", "value": mapping},
                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            ],
        }
        for col in ("selbst", "widerrufen")
    ]


def _details_table() -> dict[str, Any]:
    days_override = {
        "matcher": {"id": "byName", "options": "tage"},
        "properties": [
            {"id": "unit", "value": "suffix: Tage"},
            {"id": "decimals", "value": 0},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {
                "id": "thresholds",
                "value": {
                    "mode": "absolute",
                    "steps": [
                        {"color": bd.RED, "value": None},
                        {"color": bd.YELLOW, "value": 14},
                        {"color": bd.GREEN, "value": 30},
                    ],
                },
            },
            {"id": "color", "value": {"mode": "thresholds"}},
        ],
    }
    return bd.table(
        8,
        "Zertifikate im Einzelnen",
        [_details(), _status_table()],
        (0, 14, 24, 10),
        transformations=[
            {"id": "joinByField", "options": {"byField": "key", "mode": "inner"}},
            {
                "id": "organize",
                "options": {
                    "excludeByName": {"key": True},
                    "renameByName": {
                        "device": "Gerät",
                        "host": "Host",
                        "sensor": "Sensor",
                        "tage": "Tage bis Ablauf",
                        "selbst": "Selbstsigniert",
                        "widerrufen": "Widerrufen",
                        "status": "Status",
                    },
                    "indexByName": {
                        "device": 0,
                        "host": 1,
                        "sensor": 2,
                        "tage": 3,
                        "selbst": 4,
                        "widerrufen": 5,
                        "status": 6,
                    },
                },
            },
        ],
        overrides=[
            *[
                {**o, "matcher": {"id": "byName", "options": name}}
                for o, name in zip(
                    [days_override, *_yes_no()],
                    ["Tage bis Ablauf", "Selbstsigniert", "Widerrufen"],
                )
            ],
            bd.status_cell("Status", "Status"),
            bd.device_link("Gerät"),
        ],
        sort_by="Tage bis Ablauf",
    )


def dashboard() -> dict[str, Any]:
    fresh = bd.freshness_tile(4, (20, 0, 4, 4))
    panels = [
        bd.stat(
            1,
            "Läuft in weniger als 14 Tagen ab",
            _count("|> filter(fn: (r) => r._value > 0.0 and r._value < 14.0)"),
            (0, 0, 4, 4),
            base=bd.GREEN,
            step=(1, bd.RED),
        ),
        bd.stat(
            2,
            "Läuft in 14 bis 29 Tagen ab",
            _count("|> filter(fn: (r) => r._value >= 14.0 and r._value < 30.0)"),
            (4, 0, 4, 4),
            base=bd.GREEN,
            step=(1, bd.YELLOW),
        ),
        bd.stat(3, "Zertifikate gesamt", _count(""), (8, 0, 4, 4), base=bd.PETROL),
        bd.stat(
            6,
            "Abgelaufen",
            _count("|> filter(fn: (r) => r._value <= 0.0)"),
            (12, 0, 4, 4),
            base=bd.GREEN,
            step=(1, bd.RED),
        ),
        bd.stat(
            7,
            "Zertifikatssensoren mit Warnung oder Ausfall",
            _status_problems(),
            (16, 0, 4, 4),
            base=bd.GREEN,
            step=(1, bd.RED),
        ),
        fresh,
        _bargauge(),
        bd.timeseries(
            9,
            "Restlaufzeit der zehn kürzesten Zertifikate",
            [(_history(), "Tage", bd.GREEN)],
            (12, 4, 12, 10),
            unit="suffix: Tage",
        ),
        _details_table(),
        bd.text(
            10,
            "Hinweis",
            "Gezeigt werden Sensoren mit dem Kanal Tage bis zum Ablauf (englisch: Days to Expiration), "
            "typischerweise mit den PRTG-Tags sslcertificate, tlscertificate oder certificate. "
            "Ampel: rot unter 14 Tagen, gelb unter 30 Tagen, sonst grün. "
            "Selbstsigniert und Widerrufen stammen aus den gleichnamigen Kanälen (unbekannt = Kanal liefert keinen Wert).",
            (0, 24, 24, 3),
        ),
    ]
    panels.sort(key=lambda p: p["id"])
    return bd._dashboard(
        "prtg-zertifikate",
        "Zertifikate: Laufzeit",
        panels,
        ("core", "tag", "group", "device", "sensor"),
        ["zertifikate"],
        time_from="now-30d",
    )
