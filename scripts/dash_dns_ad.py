"""Dashboard 10: Wie geht es DNS und Active Directory? (Vorlage Grafana „CoreDNS“)

Die Auswahl läuft über Paessler-Tags: `dnssensor` (Sensortyp „DNS v2“) und `ldapsensor` (Sensortyp „LDAP“).
"""

from __future__ import annotations

from typing import Any

import build_dashboards as bd

FILE = "10-dns-ad.json"

DNS = r"/(^|\|)dnssensor(\||$)/"
LDAP = r"/(^|\|)ldapsensor(\||$)/"
BOTH = r"/(^|\|)(dnssensor|ldapsensor)(\||$)/"

_SELECT = bd.filters(sensor=True)


def _tagged(tag_regex: str) -> str:
    return f"|> filter(fn: (r) => r.prtg_tags =~ {tag_regex})\n  {_SELECT}"


def count_status(tag_regex: str, cond: str) -> str:
    """Anzahl der Sensoren mit dem Tag, deren aktueller Status die Bedingung erfüllt."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {_tagged(tag_regex)}
  {bd.LAST_SENSOR}
  |> filter(fn: (r) => {cond})
  |> group()
  |> count()"""


def response_time(tag_regex: str) -> str:
    """Antwortzeit je Sensor im gewählten Zeitraum."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value" and r.channel == "Antwortzeit")
  {_tagged(tag_regex)}
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{r with reihe: r.device + " / " + r.sensor}}))
  |> keep(columns: ["_time", "_value", "reihe"])
  |> group(columns: ["reihe"])"""


def sensors_table() -> str:
    """Status und aktuelle Antwortzeit je Sensor, schlechtester Status zuerst."""
    return f"""import "join"

status = from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {_tagged(BOTH)}
  {bd.LAST_SENSOR}
  |> group()
  |> keep(columns: ["core", "sensor_id", "device", "dns_name", "sensor", "sensor_type", "_value"])
  |> rename(columns: {{_value: "status_code"}})

answer = from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value" and r.channel == "Antwortzeit")
  {_tagged(BOTH)}
  {bd.LAST_CHANNEL}
  |> group()
  |> keep(columns: ["core", "sensor_id", "_value"])
  |> rename(columns: {{_value: "antwortzeit"}})

join.left(
  left: status,
  right: answer,
  on: (l, r) => l.core == r.core and l.sensor_id == r.sensor_id,
  as: (l, r) => ({{l with antwortzeit: r.antwortzeit}}),
)
  |> keep(columns: ["device", "dns_name", "sensor", "sensor_type", "status_code", "antwortzeit"])
{bd.worst_first("device")}"""


def availability_by_service() -> str:
    """Verfügbarkeit je Dienstgruppe in Prozent; gewertet werden Abrufe mit Status 0 bis 2 (Pausiert und Unbekannt zählen nicht)."""
    return f"""from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {_tagged(BOTH)}
  |> map(fn: (r) => ({{r with dienst: if r.prtg_tags =~ {LDAP} then "Active Directory (LDAP)" else "DNS", down: if r._value == 2 then 1.0 else 0.0, counted: if r._value <= 2 then 1.0 else 0.0}}))
  |> group(columns: ["dienst"])
  |> reduce(identity: {{down: 0.0, counted: 0.0}}, fn: (r, accumulator) => ({{down: accumulator.down + r.down, counted: accumulator.counted + r.counted}}))
  |> filter(fn: (r) => r.counted > 0.0)
  |> map(fn: (r) => ({{dienst: r.dienst, verfuegbarkeit: 100.0 * (1.0 - r.down / r.counted), abrufe: r.counted}}))
  |> group()
  |> sort(columns: ["verfuegbarkeit"])"""


def _response_chart(
    pid: int, title: str, tag_regex: str, pos: tuple[int, int, int, int], yellow: int, red: int
) -> dict[str, Any]:
    panel = bd.timeseries(
        pid, title, [(response_time(tag_regex), "Antwortzeit", bd.GREEN)], pos, unit="ms"
    )
    d = panel["fieldConfig"]["defaults"]
    d["displayName"] = "${__field.labels.reihe}"
    d["color"] = {"mode": "palette-classic"}
    d["noValue"] = "Keine Daten im gewählten Zeitraum"
    d["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    d["thresholds"] = {
        "mode": "absolute",
        "steps": [
            {"color": bd.GREEN, "value": None},
            {"color": bd.YELLOW, "value": yellow},
            {"color": bd.RED, "value": red},
        ],
    }
    panel["fieldConfig"]["overrides"] = []
    return panel


def dashboard() -> dict[str, Any]:
    def tile(
        pid: int, title: str, tag: str, cond: str, x: int, step: bool, none: str
    ) -> dict[str, Any]:
        return bd.stat(
            pid,
            title,
            count_status(tag, cond),
            (x, 0, 5, 4),
            base=bd.GREEN,
            step=(1, bd.RED) if step else None,
            no_value=none,
        )

    panels = [
        tile(1, "DNS-Sensoren in Ordnung", DNS, "r._value == 0", 0, False, "Keine DNS-Sensoren"),
        tile(
            2,
            "DNS-Sensoren mit Warnung oder Ausfall",
            DNS,
            "r._value == 1 or r._value == 2",
            5,
            True,
            "0",
        ),
        tile(
            3,
            "AD/LDAP-Sensoren in Ordnung",
            LDAP,
            "r._value == 0",
            10,
            False,
            "Keine LDAP-Sensoren",
        ),
        tile(
            4,
            "AD/LDAP-Sensoren mit Warnung oder Ausfall",
            LDAP,
            "r._value == 1 or r._value == 2",
            15,
            True,
            "0",
        ),
        bd.freshness_tile(5, (20, 0, 4, 4)),
        _response_chart(6, "Antwortzeit DNS je Sensor", DNS, (0, 4, 12, 8), 100, 500),
        _response_chart(7, "Antwortzeit LDAP je Sensor", LDAP, (12, 4, 12, 8), 200, 1000),
        bd.table(
            8,
            "DNS- und AD/LDAP-Sensoren",
            [sensors_table()],
            (0, 12, 24, 8),
            transformations=[
                {
                    "id": "organize",
                    "options": {
                        "renameByName": {
                            "device": "Gerät",
                            "dns_name": "DNS-Name",
                            "sensor": "Sensor",
                            "sensor_type": "Typ",
                            "antwortzeit": "Antwortzeit aktuell",
                        },
                        "indexByName": {
                            "device": 0,
                            "dns_name": 1,
                            "sensor": 2,
                            "sensor_type": 3,
                            "status_code": 4,
                            "antwortzeit": 5,
                        },
                    },
                }
            ],
            status_columns=("status_code",),
            overrides=[
                bd.device_link("Gerät"),
                {
                    "matcher": {"id": "byName", "options": "Antwortzeit aktuell"},
                    "properties": [
                        {"id": "unit", "value": "ms"},
                        {"id": "decimals", "value": 0},
                    ],
                },
            ],
        ),
        bd.table(
            9,
            "Verfügbarkeit je Dienstgruppe im gewählten Zeitraum",
            [availability_by_service()],
            (0, 20, 14, 5),
            transformations=[
                {
                    "id": "organize",
                    "options": {
                        "renameByName": {
                            "dienst": "Dienstgruppe",
                            "verfuegbarkeit": "Verfügbarkeit",
                            "abrufe": "Gewertete Abrufe",
                        },
                        "indexByName": {"dienst": 0, "verfuegbarkeit": 1, "abrufe": 2},
                    },
                }
            ],
            overrides=[
                {
                    "matcher": {"id": "byName", "options": "Verfügbarkeit"},
                    "properties": [
                        {"id": "unit", "value": "percent"},
                        {"id": "decimals", "value": 1},
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Gewertete Abrufe"},
                    "properties": [{"id": "decimals", "value": 0}],
                },
            ],
        ),
        bd.text(
            10,
            "Hinweise",
            "Ausgewählt werden Sensoren mit den PRTG-Tags `dnssensor` (DNS) und `ldapsensor` (Active Directory/LDAP). "
            "Die Verfügbarkeit ist eine Näherung aus den Abrufen im 60-Sekunden-Takt; pausierte und unbekannte "
            "Zeiträume zählen nicht. Gestrichelte Linien markieren die Schwellen: DNS gelb ab 100 ms, rot ab 500 ms; "
            "LDAP gelb ab 200 ms, rot ab 1000 ms.",
            (14, 20, 10, 5),
        ),
    ]
    return bd._dashboard(
        "prtg-dns-ad",
        "DNS und Active Directory",
        panels,
        ("core", "tag", "group", "device", "sensor"),
        ["dns", "active-directory"],
        time_from="now-6h",
    )
