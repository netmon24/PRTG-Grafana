"""Führt die Einrichtung (Buckets, Tokens, Grafana) im Hintergrund aus, wenn sich im Portal etwas ändert."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence

from ..setup_tenants import SetupReport

log = logging.getLogger("prtg_collector.portal")


class SetupRunner:
    """Schlägt ein Lauf fehl (zum Beispiel weil InfluxDB oder Grafana nach einer TLS-Umschaltung noch neu starten),
    folgt er nach den Wartezeiten `retry_delays` erneut. Ein neuer `trigger` beginnt die Zählung von vorn."""

    def __init__(
        self,
        run: Callable[[], SetupReport],
        retry_delays: Sequence[float] = (5, 15, 30, 60, 60),
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        self._run = run
        self._delays = tuple(retry_delays)
        self._sleep = sleep
        self._wake = threading.Event()  # ein neuer Auslöser unterbricht die Wartezeit
        self._lock = threading.Lock()
        self._pending = False
        self._running = False
        self._generation = 0
        self.last: SetupReport | None = None
        self.crashed: str | None = None

    def trigger(self) -> None:
        """Startet die Einrichtung; Änderungen während eines Laufs lösen danach einen weiteren Lauf aus."""
        with self._lock:
            self._pending = True
            self._generation += 1
            self._wake.set()
            if self._running:
                return
            self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _attempt(self) -> bool:
        try:
            self.last, self.crashed = self._run(), None
            for line in self.last.created:
                log.info("Einrichtung: angelegt %s", line)
            for line in self.last.problems:
                log.error("Einrichtung: %s", line)
            return not self.last.problems
        except Exception as err:
            self.crashed = f"{type(err).__name__}: {str(err)[:150]}"
            log.exception("Einrichtung fehlgeschlagen")
            return False

    def _wait(self, delay: float) -> None:
        if self._sleep is not None:
            self._sleep(delay)
            return
        self._wake.wait(delay)

    def _loop(self) -> None:
        attempt, generation = 0, -1
        while True:
            with self._lock:
                if not self._pending:
                    self._running = False
                    return
                self._pending = False
                if self._generation != generation:
                    attempt, generation = 0, self._generation  # neuer Auslöser: Zählung von vorn
            self._wake.clear()
            if self._attempt() or attempt >= len(self._delays):
                continue
            self._wait(self._delays[attempt])
            attempt += 1
            with self._lock:
                self._pending = True

    def problems(self) -> list[str]:
        out = [self.crashed] if self.crashed else []
        return out + (self.last.problems if self.last else [])
