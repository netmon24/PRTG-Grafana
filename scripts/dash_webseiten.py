"""Dashboard 12: Webseiten-Monitoring (Sensoren mit dem PRTG-Tag `httpsensor`)."""

from typing import Any

import build_dashboards as bd

FILE = "12-webseiten.json"

# Kanalnamen deutsch und englisch (die Sprache der PRTG-Oberfläche bestimmt sie).
LOAD = "Ladezeit|Loading Time"
CODE = "Antwortcode|Response Code"

# [Annahme] Schwellen für die Ladezeit in Millisekunden.
WARN_MS, CRIT_MS = 1000, 3000

_HTTP = r"|> filter(fn: (r) => r.prtg_tags =~ /(^|\|)httpsensor(\||$)/)"
_FILTERS = f"{bd.filters(sensor=True)}\n  {_HTTP}"


def _status(rng: str = bd.NOW) -> str:
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {rng})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  {_FILTERS}"""


def _count(cond: str = "") -> str:
    return f"""{_status()}
  {bd.LAST_SENSOR}
  {cond}
  |> group()
  |> count()"""


def _channel(names: str, rng: str) -> str:
    return f"""from(bucket: v.defaultBucket)
  |> range({rng})
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value")
  |> filter(fn: (r) => r.channel =~ /^({names})$/)
  {_FILTERS}"""


def availability() -> str:
    """Anteil der Abrufe ohne Ausfall im gewählten Zeitraum; Pausiert und Unbekannt zählen nicht."""
    return f"""{_status("v.timeRangeStart, stop: v.timeRangeStop")}
{bd._FLAGS}
  |> group()
  |> reduce(identity: {{down: 0.0, counted: 0.0}}, fn: (r, accumulator) => ({{down: accumulator.down + r.down, counted: accumulator.counted + r.counted}}))
  |> filter(fn: (r) => r.counted > 0.0)
  |> map(fn: (r) => ({{_value: 100.0 * (1.0 - r.down / r.counted)}}))"""


def _availability_by_sensor() -> str:
    return f"""{_status("v.timeRangeStart, stop: v.timeRangeStop")}
{bd._FLAGS}
  |> group(columns: ["core", "sensor_id"])
  |> reduce(identity: {{down: 0.0, counted: 0.0}}, fn: (r, accumulator) => ({{down: accumulator.down + r.down, counted: accumulator.counted + r.counted}}))
  |> filter(fn: (r) => r.counted > 0.0)
  |> map(fn: (r) => ({{key: r.core + "/" + r.sensor_id, Verfügbarkeit: 100.0 * (1.0 - r.down / r.counted)}}))"""


def _load_history() -> str:
    return f"""{_channel(LOAD, "start: v.timeRangeStart, stop: v.timeRangeStop")}
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{r with reihe: r.device + " / " + r.sensor}}))
  |> keep(columns: ["_time", "_value", "reihe"])
  |> group(columns: ["reihe"])"""


def _bad_codes() -> str:
    """Webseiten mit einem Antwortcode außerhalb von 200 bis 299, je Fenster als Anzahl."""
    return f"""{_channel(CODE, "start: v.timeRangeStart, stop: v.timeRangeStop")}
  |> aggregateWindow(every: v.windowPeriod, fn: max, createEmpty: false)
  |> map(fn: (r) => ({{r with _value: if r._value >= 200.0 and r._value < 300.0 then 0 else 1}}))
  |> group(columns: ["_time"])
  |> sum()
  |> group()
  |> rename(columns: {{_value: "Webseiten mit Fehlercode"}})
  |> sort(columns: ["_time"])"""


def _site_master() -> str:
    return f"""{_status()}
  {bd.LAST_SENSOR}
  |> group()
  |> map(fn: (r) => ({{r with key: r.core + "/" + r.sensor_id}}))
  |> keep(columns: ["key", "device", "host", "sensor", "_value"])
  |> rename(columns: {{_value: "Status"}})"""


def _last_channel(names: str, column: str) -> str:
    return f"""{_channel(names, f"start: {bd.NOW}")}
  {bd.LAST_CHANNEL}
  |> group()
  |> map(fn: (r) => ({{r with key: r.core + "/" + r.sensor_id}}))
  |> keep(columns: ["key", "_value"])
  |> rename(columns: {{_value: "{column}"}})"""


def dashboard() -> dict[str, Any]:
    tiles = [
        bd.stat(1, "Webseiten gesamt", _count(), (0, 0, 4, 4), base=bd.PETROL),
        bd.stat(2, "Erreichbar (OK)", _count("|> filter(fn: (r) => r._value == 0)"), (4, 0, 4, 4)),
        bd.stat(
            3,
            "Warnung",
            _count("|> filter(fn: (r) => r._value == 1)"),
            (8, 0, 4, 4),
            base=bd.GREY,
            step=(1, bd.YELLOW),
        ),
        bd.stat(
            4,
            "Ausfall",
            _count("|> filter(fn: (r) => r._value == 2)"),
            (12, 0, 4, 4),
            base=bd.GREY,
            step=(1, bd.RED),
        ),
        bd.stat(
            5,
            "Verfügbarkeit im Zeitraum",
            availability(),
            (16, 0, 4, 4),
            unit="percent",
            decimals=1,
            no_value="keine Daten",
        ),
        bd.freshness_tile(6, (20, 0, 4, 4)),
    ]
    tiles[4]["fieldConfig"]["defaults"]["thresholds"]["steps"] = [
        {"color": bd.RED, "value": None},
        {"color": bd.YELLOW, "value": 95},
        {"color": bd.GREEN, "value": 99},
    ]

    load = bd.timeseries(
        7,
        "Ladezeit je Webseite in ms (Schwellen 1000 und 3000 ms: Annahme)",
        [(_load_history(), "Ladezeit", bd.GREEN)],
        (0, 4, 12, 9),
        unit="ms",
    )
    load["description"] = (
        f"Gestrichelte Linien: Warnschwelle {WARN_MS} ms (gelb) und Alarmschwelle {CRIT_MS} ms (rot). "
        "Die Werte sind eine Annahme und je Panel anpassbar."
    )
    d = load["fieldConfig"]["defaults"]
    d["displayName"] = "${__field.labels.reihe}"
    d["color"] = {"mode": "palette-classic"}
    d["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    d["thresholds"] = {
        "mode": "absolute",
        "steps": [
            {"color": bd.GREEN, "value": None},
            {"color": bd.YELLOW, "value": WARN_MS},
            {"color": bd.RED, "value": CRIT_MS},
        ],
    }
    load["fieldConfig"]["overrides"] = []

    codes = bd.timeseries(
        8,
        "Webseiten mit Antwortcode außerhalb von 200 bis 299",
        [(_bad_codes(), "Webseiten mit Fehlercode", bd.RED)],
        (12, 4, 12, 9),
    )
    codes["fieldConfig"]["defaults"]["custom"]["drawStyle"] = "bars"
    codes["fieldConfig"]["defaults"]["custom"]["fillOpacity"] = 60

    sites = bd.table(
        9,
        "Webseiten im Überblick",
        [
            _site_master(),
            _last_channel(CODE, "Antwortcode"),
            _last_channel(LOAD, "Ladezeit aktuell"),
            _availability_by_sensor(),
        ],
        (0, 13, 24, 10),
        status_columns=("Status",),
        transformations=[
            {"id": "joinByField", "options": {"byField": "key", "mode": "outer"}},
            {
                "id": "organize",
                "options": {
                    "excludeByName": {"key": True},
                    "renameByName": {"device": "Gerät", "host": "Host", "sensor": "Sensor"},
                    "indexByName": {
                        "device": 0,
                        "host": 1,
                        "sensor": 2,
                        "Status": 3,
                        "Antwortcode": 4,
                        "Ladezeit aktuell": 5,
                        "Verfügbarkeit": 6,
                    },
                },
            },
            {"id": "sortBy", "options": {"sort": [{"field": "Status", "desc": True}]}},
        ],
        overrides=[
            {
                "matcher": {"id": "byName", "options": "Antwortcode"},
                "properties": [{"id": "decimals", "value": 0}],
            },
            {
                "matcher": {"id": "byName", "options": "Ladezeit aktuell"},
                "properties": [{"id": "unit", "value": "ms"}, {"id": "decimals", "value": 0}],
            },
            {
                "matcher": {"id": "byName", "options": "Verfügbarkeit"},
                "properties": [
                    {"id": "unit", "value": "percent"},
                    {"id": "decimals", "value": 1},
                    {
                        "id": "thresholds",
                        "value": {
                            "mode": "absolute",
                            "steps": [
                                {"color": bd.RED, "value": None},
                                {"color": bd.YELLOW, "value": 95},
                                {"color": bd.GREEN, "value": 99},
                            ],
                        },
                    },
                    {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                ],
            },
            bd.device_link("Gerät"),
        ],
    )
    note = bd.text(
        10,
        "Hinweis",
        "Gezeigt werden Sensoren mit dem PRTG-Tag `httpsensor` (HTTP, HTTP v2, HTTP XML-/REST-Wert) und, "
        "soweit sie dieses Tag tragen, REST-Sensoren. Die Verfügbarkeit zählt Abrufe mit Status OK, Warnung "
        "und Ausfall; pausierte und unbekannte Zeiten zählen nicht. Sie ist eine Näherung und kann von PRTG "
        "abweichen. Die Schwellen der Ladezeit sind eine Annahme.",
        (0, 23, 24, 3),
    )
    return bd._dashboard(
        "prtg-webseiten",
        "Webseiten-Monitoring",
        [*tiles, load, codes, sites, note],
        ("core", "tag", "group", "device", "sensor"),
        ["webseiten"],
    )
