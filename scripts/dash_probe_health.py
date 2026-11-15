"""Dashboard 6: Probe- und Core-Health (lokale Probe jedes Core-Servers)."""

from typing import Any

import build_dashboards as bd

FILE = "06-probe-health.json"

# Die lokale Probe hat in PRTG immer die Geräte-Id 40; der Sensor "Zustand der Probe"
# hat die Id 1003, der Core-Zustand-Sensor die Id 1002.
PROBE_DEVICE = "40"
PROBE_SENSOR = "1003"
CORE_SENSOR = "1002"

_BASE = f'r.device_id == "{PROBE_DEVICE}"'


def _status_now(sensor_id: str) -> str:
    return f"""from(bucket: v.defaultBucket)
  |> range(start: {bd.NOW})
  |> filter(fn: (r) => r._measurement == "prtg_sensor_status" and r._field == "status_code")
  |> filter(fn: (r) => {_BASE} and r.sensor_id == "{sensor_id}")
  {bd.F_CORE}
  {bd.LAST_SENSOR}
  |> keep(columns: ["_time", "_value", "core"])
  |> group(columns: ["core"])"""


def _channels(*names: str, multi: bool = True) -> str:
    """Kanalverlauf der Probe je Core; Legende `Core – Probe` (bei mehreren Kanälen mit Kanalname)."""
    chosen = ", ".join(f'"{n}"' for n in names)
    label = 'r.core + " – Probe – " + r.channel' if multi else 'r.core + " – Probe"'
    return f"""from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "prtg_channel" and r._field == "value")
  |> filter(fn: (r) => {_BASE} and r.sensor_id == "{PROBE_SENSOR}")
  |> filter(fn: (r) => contains(value: r.channel, set: [{chosen}]))
  {bd.F_CORE}
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{r with reihe: {label}}}))
  |> keep(columns: ["_time", "_value", "reihe"])
  |> group(columns: ["reihe"])"""


def _status_tile(pid: int, title: str, sensor_id: str, x: int) -> dict[str, Any]:
    p = bd.stat(pid, title, _status_now(sensor_id), (x, 0, 8, 4), no_value="Keine Daten")
    p["fieldConfig"]["defaults"]["mappings"] = bd.status_mapping()
    p["fieldConfig"]["defaults"]["displayName"] = "${__field.labels.core}"
    p["options"]["textMode"] = "value_and_name"
    return p


def _series(
    pid: int,
    title: str,
    query: str,
    pos: tuple[int, int, int, int],
    unit: str,
    steps: list[tuple[Any, str]] | None = None,
    decimals: int = 1,
) -> dict[str, Any]:
    p = bd.timeseries(pid, title, [(query, "Wert", bd.GREEN)], pos, unit=unit)
    d = p["fieldConfig"]["defaults"]
    d["displayName"] = "${__field.labels.reihe}"
    d["decimals"] = decimals
    d["color"] = {"mode": "palette-classic"}
    p["fieldConfig"]["overrides"] = []
    if steps:
        d["thresholds"] = {
            "mode": "absolute",
            "steps": [{"color": c, "value": v} for v, c in steps],
        }
        d["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    return p


def dashboard() -> dict[str, Any]:
    g, y, r = bd.GREEN, bd.YELLOW, bd.RED
    fresh = bd.freshness_tile(3, (16, 0, 8, 4))
    panels = [
        _status_tile(1, "Zustand der Probe (Sensor 1003)", PROBE_SENSOR, 0),
        _status_tile(2, "Zustand des Core-Servers (Sensor 1002)", CORE_SENSOR, 8),
        fresh,
        _series(
            4,
            "Gesundheit der Probe in Prozent",
            _channels("Zustand", multi=False),
            (0, 4, 12, 8),
            "percent",
            [(None, r), (50, y), (80, g)],
            decimals=0,
        ),
        _series(
            5,
            "Prozessorlast des Probe-Prozesses in Prozent",
            _channels("Prozessorlast Probe-Prozess", multi=False),
            (12, 4, 12, 8),
            "percent",
            [(None, g), (70, y), (90, r)],
        ),
        _series(
            6,
            "Speicher der Probe (Speichernutzung und freier Datenspeicher)",
            _channels("Speichernutzung", "Freier Datenspeicher"),
            (0, 12, 12, 8),
            "bytes",
        ),
        _series(
            7,
            "Warteschlange Probe und Core in Elementen",
            _channels("Warteschlange Probe/Core", multi=False),
            (12, 12, 12, 8),
            "none",
            [(None, g), (50, y), (200, r)],
            decimals=0,
        ),
        _series(
            8,
            "Intervallverzögerungen (WMI, SNMP, Sonstige) in Prozent",
            _channels(
                "WMI Intervallverzögerung",
                "SNMP Intervallverzögerung",
                "Sonstige Intervallverzögerung",
            ),
            (0, 20, 12, 8),
            "percent",
            [(None, g), (20, y), (50, r)],
        ),
        _series(
            9,
            "Threads und Handles der Probe (Anzahl)",
            _channels("Threads", "Handles"),
            (12, 20, 12, 8),
            "none",
            decimals=0,
        ),
    ]
    return bd._dashboard(
        "prtg-probe-health",
        "Probe- und Core-Health",
        panels,
        ("core",),
        ["probe", "health"],
        time_from="now-6h",
    )
