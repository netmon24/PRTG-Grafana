"""DNS-Namen der Geräte mit Zwischenspeicher. Löst nie eine Ausnahme aus."""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from typing import Any


class DnsCache:
    def __init__(
        self,
        ttl_s: int = 3600,
        reverse: Callable[[str], Any] = socket.gethostbyaddr,
        forward: Callable[[str], Any] = socket.gethostbyname,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl, self._reverse, self._forward, self._clock = ttl_s, reverse, forward, clock
        self._cache: dict[str, tuple[float, str]] = {}

    def resolve(self, host: str) -> str:
        """IP: Rückwärtseintrag. Name: gilt, wenn er sich auflösen lässt. Sonst leer."""
        host = (host or "").strip()
        if not host:
            return ""
        now = self._clock()
        hit = self._cache.get(host)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        name = self._lookup(host)
        self._cache[host] = (now, name)
        return name

    def _lookup(self, host: str) -> str:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            try:
                self._forward(host)
            except (OSError, UnicodeError, ValueError, TypeError):
                return ""
            return host
        try:
            name, aliases, _ = self._reverse(host)
        except (OSError, UnicodeError, ValueError, TypeError):
            return ""
        # Mehrere Rückwärtseinträge kommen in wechselnder Reihenfolge; ein fester Name hält die Reihe stabil
        return str(min([name, *aliases]))
