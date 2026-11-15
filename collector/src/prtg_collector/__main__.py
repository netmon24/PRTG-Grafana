"""Einstieg des Collectors: python -m prtg_collector"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

from .collector import serve_dynamic
from .config import ConfigError
from .config_source import load_from_env
from .dns import DnsCache
from .netenv import internal_verify, service_url
from .prtg_client import PrtgClient
from .writer import InfluxWriter

log = logging.getLogger("prtg_collector")


def _token_provider(env: dict[str, str]):  # type: ignore[no-untyped-def]
    path = env.get("INFLUX_TOKEN_FILE")

    def provider() -> str:
        if path and Path(path).is_file():
            return Path(path).read_text(encoding="utf-8").strip()
        token = env.get("INFLUX_WRITE_TOKEN", "")
        if not token:
            raise ConfigError(
                "Kein Schreib-Token gefunden. Im Portal einen Kunden samt Core anlegen oder `docker compose run --rm setup` ausführen."
            )
        return token

    return provider


def _wait_for_token(provider) -> None:  # type: ignore[no-untyped-def]
    """Wartet, bis die Einrichtung das Schreib-Token abgelegt hat (Portal oder setup)."""
    warned = False
    while True:
        try:
            provider()
            return
        except ConfigError as err:
            if not warned:
                log.warning("Warte auf Einrichtung: %s", err)
                warned = True
            time.sleep(5)


async def _main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(
        logging.WARNING
    )  # nicht jede Abfrage einzeln protokollieren
    env = dict(os.environ)
    provider = _token_provider(env)
    await asyncio.to_thread(_wait_for_token, provider)
    writer = InfluxWriter(
        lambda: service_url(env, "influxdb"),
        provider,
        env.get("INFLUX_ORG", "netmon24"),
        verify_ssl=lambda: internal_verify(env),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("Collector gestartet, Konfiguration wird laufend neu gelesen.")
    await serve_dynamic(
        lambda: load_from_env(env),
        env,
        lambda core, token: PrtgClient(core, token),
        writer,
        DnsCache(),
        stop,
    )
    writer.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
