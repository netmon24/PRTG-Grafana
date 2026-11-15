"""Schreiben in InfluxDB 2.x. Je Kunde ein Bucket `prtg_<kunde>`."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

BATCH = 5000


def bucket_name(customer: str) -> str:
    return f"prtg_{customer}"


class Writer(Protocol):
    def write(self, customer: str, points: Sequence[Point]) -> None: ...


class InfluxWriter:
    """Schreibt in InfluxDB. `token` darf eine Funktion sein: ändert sich ihr Wert, wird neu verbunden."""

    def __init__(
        self,
        url: str | Callable[[], str],
        token: str | Callable[[], str],
        org: str,
        verify_ssl: bool | Callable[[], bool] = True,
    ) -> None:
        """`url`, `token` und `verify_ssl` dürfen Funktionen sein: ändert sich ein Wert (zum Beispiel weil im
        Portal auf https umgeschaltet wurde), wird neu verbunden."""
        self._org = org
        self._lock = threading.Lock()
        self._url_of: Callable[[], str] = url if callable(url) else (lambda: url)
        self._verify_of: Callable[[], bool] = (
            verify_ssl if callable(verify_ssl) else (lambda: verify_ssl)
        )
        self._provider: Callable[[], str] = token if callable(token) else (lambda: token)
        self._state: tuple[str, str, bool] | None = None
        self._client: InfluxDBClient | None = None
        self._write: Any = None
        self._retired: list[InfluxDBClient] = []
        self._connect()

    def _connect(self) -> Any:
        """Liefert die aktuelle Schreib-Schnittstelle; neu verbunden wird unter Sperre, damit
        kein paralleler Core in einen gerade geschlossenen Client schreibt."""
        state = (self._url_of(), self._provider(), self._verify_of())
        with self._lock:
            if self._client is not None and state == self._state:
                return self._write
            old = self._client
            self._client = InfluxDBClient(
                url=state[0], token=state[1], org=self._org, verify_ssl=state[2]
            )
            self._write = self._client.write_api(write_options=SYNCHRONOUS)
            self._state = state
            if old is not None:
                # nicht sofort schließen: laufende Schreibvorgänge anderer Threads nutzen ihn noch
                self._retired.append(old)
                for stale in self._retired[:-1]:
                    stale.close()
                del self._retired[:-1]
            return self._write

    def write(self, customer: str, points: Sequence[Point]) -> None:
        write_api = self._connect()
        for i in range(0, len(points), BATCH):
            write_api.write(
                bucket=bucket_name(customer), org=self._org, record=list(points[i : i + BATCH])
            )

    def close(self) -> None:
        with self._lock:
            for client in [*self._retired, self._client]:
                if client is not None:
                    client.close()
            self._retired.clear()
