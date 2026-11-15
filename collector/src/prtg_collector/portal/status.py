"""Zustand der Collector-Läufe für die Übersicht (aus InfluxDB oder leer)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RunStatus:
    at: str
    errors: int
    last_error: str | None


class StatusProvider(Protocol):
    def last_runs(self) -> dict[str, RunStatus]: ...


class NullStatus:
    def last_runs(self) -> dict[str, RunStatus]:
        return {}


log = logging.getLogger("prtg_collector.portal")


def _fmt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value).replace("T", " ")[:16]


class InfluxStatus:
    """Letzter Lauf je Core aus `collector_run`. `query` liefert Zeilen mit core, time, errors, last_error."""

    def __init__(self, query: Callable[[], list[dict[str, Any]]]) -> None:
        self._query = query

    def last_runs(self) -> dict[str, RunStatus]:
        try:
            rows = self._query()
        except Exception as err:  # noqa: BLE001 - die Übersicht muss ohne Status weiter funktionieren
            log.warning("Status nicht abrufbar (%s)", type(err).__name__)
            return {}
        runs: dict[str, RunStatus] = {}
        for row in rows:
            at = _fmt(row.get("time", ""))
            if row["core"] not in runs or at > runs[row["core"]].at:
                runs[row["core"]] = RunStatus(
                    at=at,
                    errors=int(row.get("errors") or 0),
                    last_error=row.get("last_error") or None,
                )
        return runs

    @classmethod
    def from_influx(
        cls,
        url: str | Callable[[], str],
        token: str,
        org: str,
        buckets: Callable[[], list[str]],
        verify_ssl: bool | Callable[[], bool] = True,
    ) -> InfluxStatus:
        from influxdb_client import InfluxDBClient

        def query() -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            with InfluxDBClient(
                url=url() if callable(url) else url,
                token=token,
                org=org,
                verify_ssl=verify_ssl() if callable(verify_ssl) else verify_ssl,
            ) as client:
                for bucket in buckets():
                    flux = (
                        f'from(bucket: "{bucket}") |> range(start: -1h) '
                        '|> filter(fn: (r) => r._measurement == "collector_run") '
                        '|> filter(fn: (r) => r._field == "errors" or r._field == "last_error") |> last() '
                        '|> pivot(rowKey: ["_time", "core"], columnKey: ["_field"], valueColumn: "_value")'
                    )
                    for table in client.query_api().query(flux):
                        rows += [
                            {
                                "core": r.values.get("core"),
                                "time": r.get_time(),
                                "errors": r.values.get("errors"),
                                "last_error": r.values.get("last_error"),
                            }
                            for r in table.records
                        ]
            return rows

        return cls(query)
