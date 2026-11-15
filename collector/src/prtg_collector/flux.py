"""Flux-Texte, die an mehreren Stellen gebraucht werden (Verdichtung, Dashboards)."""

from __future__ import annotations


def rollup_flux(customer: str) -> str:
    """Stündliche Verdichtung der Gerätestatus: Anzahl Abrufe mit Ausfall und Anzahl gewerteter Abrufe.

    Gewertet werden Status 0 bis 2 (up, warning, down); pausiert (3) und unbekannt (4) zählen nicht.
    """
    return f"""import "date"

option task = {{name: "prtg_{customer}_rollup_1h", every: 1h, offset: 2m}}

stop = date.truncate(t: now(), unit: 1h)
start = date.sub(d: 1h, from: stop)

data = from(bucket: "prtg_{customer}")
  |> range(start: start, stop: stop)
  |> filter(fn: (r) => r._measurement == "prtg_device" and r._field == "status_code")

down = data
  |> map(fn: (r) => ({{r with _value: if r._value == 2 then 1 else 0, _field: "down_n"}}))
  |> aggregateWindow(every: 1h, fn: sum, createEmpty: false, timeSrc: "_start")

counted = data
  |> map(fn: (r) => ({{r with _value: if r._value <= 2 then 1 else 0, _field: "counted_n"}}))
  |> aggregateWindow(every: 1h, fn: sum, createEmpty: false, timeSrc: "_start")

union(tables: [down, counted])
  |> set(key: "_measurement", value: "prtg_device_1h")
  |> to(bucket: "prtg_{customer}_1h")
"""
