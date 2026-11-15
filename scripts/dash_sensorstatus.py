"""Dashboard 7: Sensorstatus (Gesamtstatus aller Sensoren, je Core, mit Sensortabelle)."""

from typing import Any

import build_dashboards as bd

FILE = "07-sensorstatus.json"

_SCOPE = f"{bd.filters()}\n  {bd.F_STYPE}\n  {bd.F_STATUS}"


def _status_text() -> str:
    """Flux-Ausdruck: Statuszahl in Text (aus bd.STATUS_MAP)."""
    parts = [
        f'if r._value == {k} then "{text}"' for k, (text, _) in list(bd.STATUS_MAP.items())[:-1]
    ]
    return " else ".join(parts) + f' else "{bd.STATUS_MAP["4"][0]}"'


def _current(tail: str) -> str:
    """Jüngster Status je Sensor im gewählten Umfang, danach `tail`."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {_SCOPE}
  {bd.LAST_SENSOR}
  |> group()
{tail}"""


def status_split() -> str:
    """Eine Zeile mit einer Spalte je Status: Anzahl der Sensoren (Kreisdiagramm)."""
    return _current(f"""  |> map(fn: (r) => ({{r with st: {_status_text()}}}))
  |> keep(columns: ["st", "_value"])
  |> group(columns: ["st"])
  |> count()
  |> group()
  |> map(fn: (r) => ({{r with k: "x"}}))
  |> pivot(rowKey: ["k"], columnKey: ["st"], valueColumn: "_value")
  |> drop(columns: ["k"])""")


def status_by_core() -> str:
    """Eine Zeile je Core mit einer Spalte je Status (gestapeltes Balkendiagramm)."""
    return _current(f"""  |> map(fn: (r) => ({{r with st: {_status_text()}}}))
  |> keep(columns: ["core", "st", "_value"])
  |> group(columns: ["core", "st"])
  |> count()
  |> group()
  |> pivot(rowKey: ["core"], columnKey: ["st"], valueColumn: "_value")""")


def distinct_count(expr: str) -> str:
    """Anzahl verschiedener Werte von `expr` (Ausdruck über r) unter den aktuellen Sensoren."""
    return _current(f"""  |> map(fn: (r) => ({{r with key: {expr}}}))
  |> keep(columns: ["key"])
  |> unique(column: "key")
  |> count(column: "key")""")


def sensors_table() -> str:
    """Sensoren mit Pfad, Typ, Status, Statuswechsel und letzter Prüfung.

    Statuswechsel: letzter Wert-Wechsel von status_code in 30 Tagen; gab es keinen,
    gilt der erste Datenpunkt. Die letzte Prüfung folgt aus last_check_age_s, falls
    geschrieben, sonst aus dem Zeitpunkt des Abrufs."""
    cols = '"core", "probe", "group", "device", "sensor", "sensor_id", "sensor_type", "st", "since", "first", "tm", "age"'
    keys = '["core", "probe", "group", "device", "sensor", "sensor_id", "sensor_type"]'

    def part(st: str, since: str, first: str, tm: str, age: str) -> str:
        return f"""  |> map(fn: (r) => ({{r with st: {st}, since: {since}, first: {first}, tm: {tm}, age: {age}}}))
  |> keep(columns: [{cols}])
  |> group(columns: {keys})"""

    return f"""base = from(bucket: v.defaultBucket)
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {bd.filters()}
  {bd.F_STYPE}
cur = base
  {bd.LAST_SENSOR}
{part("int(v: r._value)", "0", "0", "int(v: r._time)", "-1.0")}
chg = base
  |> difference()
  |> filter(fn: (r) => r._value != 0)
  |> last()
{part("-1", "int(v: r._time)", "0", "0", "-1.0")}
fst = base
  |> first()
{part("-1", "0", "int(v: r._time)", "0", "-1.0")}
age = from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "last_check_age_s")
  {bd.F_CORE}
  |> last()
{part("-1", "0", "0", "0", "r._value")}
union(tables: [cur, chg, fst, age])
  |> reduce(
    fn: (r, accumulator) => ({{
      st: if r.st >= 0 then r.st else accumulator.st,
      since: if r.since > accumulator.since then r.since else accumulator.since,
      first: if accumulator.first == 0 or (r.first > 0 and r.first < accumulator.first) then r.first else accumulator.first,
      tm: if r.tm > accumulator.tm then r.tm else accumulator.tm,
      age: if r.age > accumulator.age then r.age else accumulator.age,
    }}),
    identity: {{st: -1, since: 0, first: 0, tm: 0, age: -1.0}},
  )
  |> group()
  |> filter(fn: (r) => r.st >= 0 and r.tm > int(v: now()) - 900000000000)
  |> filter(fn: (r) => string(v: r.st) =~ /^(${{status:regex}})$/)
  |> map(fn: (r) => ({{r with rank: if r.st == 2 then 0 else if r.st == 1 then 1 else if r.st == 4 then 2 else if r.st == 3 then 3 else 4, path: r.probe + " > " + r.group + " > " + r.device, since: time(v: if r.since > 0 then r.since else r.first), checked: time(v: if r.age >= 0.0 then r.tm - int(v: r.age) * 1000000000 else r.tm)}}))
  |> keep(columns: ["path", "sensor", "sensor_type", "st", "since", "checked", "rank"])
  |> sort(columns: ["rank", "path", "sensor"])
  |> drop(columns: ["rank"])"""


def _pie(pid: int, title: str, pos: tuple[int, int, int, int]) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "piechart",
        "title": title,
        "datasource": bd.DS,
        "gridPos": bd._gridpos(*pos),
        "targets": [{"refId": "A", "datasource": bd.DS, "query": status_split()}],
        "fieldConfig": {
            "defaults": {
                "unit": "none",
                "decimals": 0,
                "noValue": "Keine Daten im gewählten Zeitraum",
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": text},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": color}}
                    ],
                }
                for text, color in bd.STATUS_MAP.values()
            ],
        },
        "options": {
            "pieType": "donut",
            "displayLabels": ["value"],
            "legend": {
                "displayMode": "table",
                "placement": "right",
                "showLegend": True,
                "values": ["value", "percent"],
            },
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "tooltip": {"mode": "single"},
        },
    }


def _bars(pid: int, title: str, pos: tuple[int, int, int, int]) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "barchart",
        "title": title,
        "datasource": bd.DS,
        "gridPos": bd._gridpos(*pos),
        "targets": [
            {"refId": "A", "datasource": bd.DS, "query": status_by_core(), "format": "table"}
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "none",
                "decimals": 0,
                "noValue": "Keine Daten im gewählten Zeitraum",
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": text},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": color}}
                    ],
                }
                for text, color in bd.STATUS_MAP.values()
            ],
        },
        "options": {
            "xField": "core",
            "orientation": "horizontal",
            "stacking": "normal",
            "showValue": "always",
            "legend": {"displayMode": "table", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi"},
        },
    }


def _table() -> dict[str, Any]:
    panel = bd.table(
        9,
        "Sensoren",
        [sensors_table()],
        (0, 16, 24, 12),
        transformations=[
            {
                "id": "organize",
                "options": {
                    "indexByName": {
                        "path": 0,
                        "sensor": 1,
                        "sensor_type": 2,
                        "st": 3,
                        "since": 4,
                        "checked": 5,
                    },
                },
            }
        ],
        overrides=[
            bd.status_cell("st", "Status"),
            *[
                {
                    "matcher": {"id": "byName", "options": col},
                    "properties": [
                        {"id": "displayName", "value": name},
                        *([{"id": "unit", "value": "dateTimeAsLocal"}] if unit else []),
                    ],
                }
                for col, name, unit in (
                    ("path", "Pfad (Probe > Gruppe > Gerät)", False),
                    ("sensor", "Sensor", False),
                    ("sensor_type", "Sensortyp", False),
                    ("since", "Status seit", True),
                    ("checked", "Letzte Prüfung", True),
                )
            ],
        ],
    )
    panel["description"] = (
        "Status seit: letzter Statuswechsel innerhalb von 30 Tagen. Gab es keinen, "
        "gilt der erste gespeicherte Datenpunkt des Sensors."
    )
    return panel


def dashboard() -> dict[str, Any]:
    panels = [
        _pie(1, "Auswahl: Gesamtstatus aller Sensoren", (0, 0, 10, 8)),
        bd.stat(2, "Server (Cores)", distinct_count("r.core"), (10, 0, 7, 4), base=bd.PETROL),
        bd.stat(
            3,
            "Gruppen",
            distinct_count('r.core + "/" + r.probe + "/" + r.group'),
            (17, 0, 7, 4),
            base=bd.PETROL,
        ),
        bd.stat(
            4,
            "Geräte",
            distinct_count('r.core + "/" + r.device_id'),
            (10, 4, 7, 4),
            base=bd.PETROL,
        ),
        bd.stat(
            5,
            "Sensoren",
            distinct_count('r.core + "/" + r.sensor_id'),
            (17, 4, 7, 4),
            base=bd.PETROL,
        ),
        _bars(6, "Gesamtstatus je Core", (0, 8, 24, 8)),
        _table(),
        bd.freshness_tile(8, (16, 28, 8, 4)),
    ]
    return bd._dashboard(
        "prtg-sensorstatus",
        "Sensorstatus",
        panels,
        ("core", "tag", "group", "device", "stype", "status"),
        ["sensorstatus"],
    )
