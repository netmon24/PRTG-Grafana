"""Dashboard 13 Gerätestatus: Filter-Vorlage (Core, Tag, Gruppe, Gerät, Sensortyp, Status)."""

from typing import Any

import build_dashboards as bd

FILE = "13-geraetestatus.json"


def _devices_now(cond: str = "") -> str:
    """Anzahl Geräte (aktueller Status), optional auf einen Status eingeschränkt."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_device" and r._field == "status_code")
  {bd.filters()}
  {bd.LAST_DEVICE}
  {cond}
  |> group()
  |> count()"""


def _device_table() -> str:
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_device")
  |> filter(fn: (r) => r._field == "status_code" or r._field == "sensor_count" or r._field == "sensors_down" or r._field == "sensors_warning")
  {bd.filters()}
  {bd.LAST_DEVICE}
  |> group()
  |> pivot(rowKey: ["core", "device_id", "device", "dns_name", "host", "group"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["device", "dns_name", "host", "group", "status_code", "sensor_count", "sensors_down", "sensors_warning"])
{bd.worst_first("device")}"""


def _sensor_table() -> str:
    return f"""import "strings"

from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status")
  |> filter(fn: (r) => r._field == "status_code" or r._field == "message")
  {bd.filters()}
  {bd.F_STYPE}
  {bd.LAST_SENSOR}
  |> map(fn: (r) => ({{r with _value: string(v: r._value)}}))
  |> group()
  |> pivot(rowKey: ["core", "device", "sensor", "sensor_id", "sensor_type"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.status_code =~ /^(${{status:regex}})$/)
  |> map(fn: (r) => ({{r with status_code: int(v: r.status_code), message: if exists r.message then strings.substring(v: r.message, start: 0, end: 80) else ""}}))
  |> keep(columns: ["device", "sensor", "sensor_type", "status_code", "message"])
{bd.worst_first("device", "sensor")}"""


def _problem_history(code: int, field_name: str) -> str:
    """Sensoren im Status über die Zeit, zusätzlich nach Sensortyp gefiltert."""
    q = bd.status_history(code, field_name)
    marker = "  |> aggregateWindow"
    return q.replace(marker, f"  {bd.F_STYPE}\n{marker}", 1)


HINT = (
    "**So bauen Sie eine eigene Ansicht:** Wählen Sie oben Core, Tag, Gruppe, Gerät, Sensortyp und Status. "
    "Kacheln, Geräteliste, Sensorliste und Verlauf passen sich an. Über Speichern unter (Dashboard-Einstellungen) "
    "legen Sie die Auswahl als eigenes Dashboard im Ordner Eigene ab. Kein Code nötig.\n\n"
    "Die Kacheln und die Geräteliste zählen Geräte (Filter: Core, Tag, Gruppe, Gerät). Sensorliste und Verlauf "
    "zusätzlich nach Sensortyp, die Sensorliste auch nach Status. Ein Klick auf ein Gerät öffnet den Kanalverlauf."
)


def dashboard() -> dict[str, Any]:
    tile_specs = [
        ("Geräte gesamt", "", bd.PETROL, None),
        ("Geräte mit Status OK", "|> filter(fn: (r) => r._value == 0)", bd.GREEN, None),
        (
            "Geräte mit Status Warnung",
            "|> filter(fn: (r) => r._value == 1)",
            bd.GREEN,
            (1, bd.YELLOW),
        ),
        ("Geräte mit Status Ausfall", "|> filter(fn: (r) => r._value == 2)", bd.GREEN, (1, bd.RED)),
        ("Geräte mit Status Pausiert", "|> filter(fn: (r) => r._value == 3)", bd.BLUE, None),
        ("Geräte mit Status Unbekannt", "|> filter(fn: (r) => r._value == 4)", bd.GREY, None),
    ]
    panels: list[dict[str, Any]] = [
        bd.stat(i + 1, title, _devices_now(cond), (i * 3, 0, 3, 4), base=base, step=step)
        for i, (title, cond, base, step) in enumerate(tile_specs)
    ]
    fresh = bd.freshness_tile(7, (18, 0, 6, 4))
    panels.append(fresh)

    device_overrides = [
        bd.status_cell("status_code", "Status (schlechtester Sensor)"),
        bd.device_link("device"),
        *[
            {
                "matcher": {"id": "byName", "options": col},
                "properties": [{"id": "displayName", "value": name}],
            }
            for col, name in [
                ("device", "Gerät"),
                ("dns_name", "DNS-Name"),
                ("host", "Host"),
                ("group", "Gruppe"),
                ("sensor_count", "Sensoren"),
                ("sensors_down", "davon Ausfall"),
                ("sensors_warning", "davon Warnung"),
            ]
        ],
    ]
    devices = bd.table(
        8,
        "Geräte mit Status",
        [_device_table()],
        (0, 4, 24, 9),
        overrides=device_overrides,
        transformations=[
            {
                "id": "organize",
                "options": {
                    "indexByName": {
                        "device": 0,
                        "dns_name": 1,
                        "host": 2,
                        "group": 3,
                        "status_code": 4,
                        "sensor_count": 5,
                        "sensors_down": 6,
                        "sensors_warning": 7,
                    }
                },
            }
        ],
    )
    sensors = bd.table(
        9,
        "Sensoren nach Auswahl",
        [_sensor_table()],
        (0, 13, 24, 10),
        overrides=[
            bd.status_cell("status_code", "Status"),
            bd.device_link("device"),
            *[
                {
                    "matcher": {"id": "byName", "options": col},
                    "properties": [{"id": "displayName", "value": name}],
                }
                for col, name in [
                    ("device", "Gerät"),
                    ("sensor", "Sensor"),
                    ("sensor_type", "Typ"),
                    ("message", "Meldung"),
                ]
            ],
        ],
    )
    history = bd.timeseries(
        10,
        "Sensoren im Status Ausfall und Warnung im Verlauf",
        [
            (_problem_history(2, "Ausfall"), "Ausfall", bd.RED),
            (_problem_history(1, "Warnung"), "Warnung", bd.YELLOW),
        ],
        (0, 23, 16, 8),
    )
    hint = bd.text(11, "So bauen Sie eine eigene Ansicht", HINT, (16, 23, 8, 8))
    panels += [devices, sensors, history, hint]
    return bd._dashboard(
        "prtg-geraetestatus",
        "Gerätestatus",
        panels,
        ("core", "tag", "group", "device", "stype", "status"),
        ["geraete", "vorlage"],
    )
