"""Dashboard 8: Gruppenstatus (eine Kachel je PRTG-Gruppe, gefärbt nach dem schlechtesten Status)."""

from typing import Any

import build_dashboards as bd

FILE = "08-gruppenstatus.json"

# Rangfolge für "schlechtester Status" (wie beim Gerätestatus im Collector): Ausfall vor Warnung vor OK;
# Unbekannt und Pausiert zählen nur, wenn in der Gruppe nichts anderes gewertet ist.
_PRIO = """  |> map(fn: (r) => ({r with prio: if r._value == 2 then 4 else if r._value == 1 then 3 else if r._value == 0 then 2 else if r._value == 4 then 1 else 0}))"""


def group_status_query(tail: str = "") -> str:
    """Je Gruppe: schlechtester Status (status_code), Zahl der Sensoren in genau diesem Status (nicht aller Sensoren der Gruppe) und Pfad Probe > Gruppe."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {bd.filters(device=False)}
  {bd.LAST_SENSOR}
  |> group()
{_PRIO}
  |> group(columns: ["core", "probe", "group", "prio"])
  |> count()
  |> group(columns: ["core", "probe", "group"])
  |> sort(columns: ["prio"], desc: true)
  |> limit(n: 1)
  |> group()
  |> map(fn: (r) => ({{core: r.core, path: r.probe + " > " + r.group, sensors: r._value, status_code: if r.prio == 4 then 2 else if r.prio == 3 then 1 else if r.prio == 2 then 0 else if r.prio == 1 then 4 else 3}}))
  {bd.F_STATUS.replace("r._value", "r.status_code")}
  {tail}"""


def tiles_query() -> str:
    """Eine Reihe je Gruppe: Wert = Status, Name = Pfad und Zahl der Sensoren in diesem Status."""
    return group_status_query(
        """|> map(fn: (r) => ({name: r.path + " (Sensoren in diesem Status: " + string(v: r.sensors) + ")", status_code: r.status_code}))
""" + bd.worst_first("name")
    )


def count_query(cond: str = "") -> str:
    return group_status_query(f'{cond}\n  |> count(column: "status_code")')


def problems_query() -> str:
    return group_status_query(
        """|> filter(fn: (r) => r.status_code == 1 or r.status_code == 2)
  |> keep(columns: ["path", "core", "status_code", "sensors"])
  |> sort(columns: ["status_code", "sensors"], desc: true)"""
    )


def _group_tiles() -> dict[str, Any]:
    p = bd.stat(5, "Gruppen nach schlechtestem Status", tiles_query(), (0, 8, 24, 12))
    defaults = p["fieldConfig"]["defaults"]
    defaults["mappings"] = bd.status_mapping()
    defaults["noValue"] = "Keine Daten im gewählten Zeitraum"
    defaults["color"] = {"mode": "fixed", "fixedColor": bd.GREY}
    p["options"]["textMode"] = "value_and_name"
    p["options"]["reduceOptions"] = {
        "calcs": ["lastNotNull"],
        "fields": "/^status_code$/",
        "values": True,
    }
    p["options"]["orientation"] = "horizontal"
    p["options"]["wideLayout"] = True
    p["options"]["text"] = {"titleSize": 14, "valueSize": 22}
    return p


def dashboard() -> dict[str, Any]:
    panels = [
        bd.stat(1, "Gruppen gesamt", count_query(), (0, 0, 6, 4), base=bd.PETROL),
        bd.stat(
            2,
            "Gruppen mit Ausfall",
            count_query("|> filter(fn: (r) => r.status_code == 2)"),
            (6, 0, 6, 4),
            step=(1, bd.RED),
        ),
        bd.stat(
            3,
            "Gruppen mit Warnung",
            count_query("|> filter(fn: (r) => r.status_code == 1)"),
            (12, 0, 6, 4),
            step=(1, bd.YELLOW),
        ),
        bd.freshness_tile(4, (18, 0, 6, 4)),
        bd.table(
            6,
            "Gruppen mit Problemen",
            [problems_query()],
            (0, 4, 24, 4),
            status_columns=("status_code",),
            transformations=[
                {
                    "id": "organize",
                    "options": {
                        "renameByName": {
                            "path": "Gruppe (Probe > Gruppe)",
                            "sensors": "Sensoren im schlechtesten Status",
                            "core": "Core",
                        }
                    },
                }
            ],
        ),
        _group_tiles(),
    ]
    return bd._dashboard(
        "prtg-gruppenstatus",
        "Gruppenstatus",
        panels,
        ("core", "tag", "group", "status"),
        ["gruppenstatus"],
    )
