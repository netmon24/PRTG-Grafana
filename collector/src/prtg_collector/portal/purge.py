"""Löscht Daten, wenn im Portal ein Kunde oder ein Core entfernt wird."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Protocol

from ..netenv import internal_tls, service_url
from ..setup_tenants import GrafanaHttp, InfluxHttp
from ..writer import bucket_name

log = logging.getLogger("prtg_collector.portal")
RECHECK_S = (
    90  # der Collector übernimmt die Änderung erst im nächsten Abruf; danach nochmals löschen
)


class Purger(Protocol):
    def delete_core(self, customer_id: str, core_id: str) -> None: ...
    def delete_customer(self, customer_id: str) -> None: ...


class TenantPurger:
    """Löscht in InfluxDB und Grafana. Wirft bei einem Fehler, damit das Portal den Eintrag stehen lässt."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._env = env

    def _influx(self) -> InfluxHttp:
        return InfluxHttp(
            service_url(self._env, "influxdb"),
            self._env.get("INFLUX_ADMIN_TOKEN", ""),
            self._env.get("INFLUX_ORG", "netmon24"),
            verify_ssl=internal_tls(self._env),
        )

    def _delete_core_points(self, customer_id: str, core_id: str) -> None:
        influx = self._influx()
        for bucket in (bucket_name(customer_id), f"{bucket_name(customer_id)}_1h"):
            influx.delete_series(bucket, f'core="{core_id}"')

    def delete_core(self, customer_id: str, core_id: str) -> None:
        self._delete_core_points(customer_id, core_id)
        timer = threading.Timer(RECHECK_S, self._recheck, (customer_id, core_id))
        timer.daemon = True
        timer.start()

    def _recheck(self, customer_id: str, core_id: str) -> None:
        try:
            self._delete_core_points(customer_id, core_id)
        except Exception:
            log.exception("Nachlöschen der Daten von %s fehlgeschlagen", core_id)

    def delete_customer(self, customer_id: str) -> None:
        grafana = GrafanaHttp(
            service_url(self._env, "grafana"),
            self._env.get("GRAFANA_ADMIN_PASSWORD", ""),
            verify_ssl=internal_tls(self._env),
        )
        influx = self._influx()
        grafana.delete_org(customer_id)
        influx.delete_tokens(f"grafana-read-{customer_id}")
        influx.delete_task(f"prtg_{customer_id}_rollup_1h")
        influx.delete_bucket(bucket_name(customer_id))
        influx.delete_bucket(f"{bucket_name(customer_id)}_1h")
