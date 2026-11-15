"""Einrichtung je Kunde: Buckets, Tokens, Verdichtung, Grafana-Organisation, Datenquelle, Dashboards.

Idempotent: ein zweiter Lauf ändert nichts. Tokens und Kennwörter erscheinen nie im Bericht.
Aufruf: python -m prtg_collector.setup_tenants
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import AppConfig, ConfigError
from .config_source import load_from_env
from .flux import rollup_flux
from .netenv import internal_tls, service_url
from .writer import bucket_name

log = logging.getLogger("prtg_collector.setup")
DATASOURCE_UID = "prtg-influx"
# Grafana bricht Flux-Abfragen ab 1000 Reihen ab; Dashboard 4 mit Auswahl "Alle" überschreitet das schnell.
MAX_SERIES = 5000
# Ordner der mitgelieferten Dashboards; was nicht in CORE_HEALTH steht, gehört zu den Sensoren
CORE_HEALTH = ("core-health", "Core Health")
SENSOR_DASHBOARD = ("sensor-dashboard", "Sensor Dashboard")
OWN_FOLDER = ("eigene", "Eigene")  # leer; hier speichern Kunden ihre Kopien
CORE_HEALTH_FILES = {"01-gesamtuebersicht.json", "06-probe-health.json"}
WRITE_TOKEN = "collector-write"


class InfluxAdmin(Protocol):
    def ensure_bucket(self, name: str, retention_days: int) -> tuple[str, bool]: ...
    def ensure_task(self, name: str, flux: str) -> bool: ...
    def ensure_token(
        self, description: str, bucket_ids: list[str], write: bool
    ) -> tuple[str, bool]: ...


class GrafanaAdmin(Protocol):
    def ensure_org(self, name: str) -> tuple[int, bool]: ...
    def ensure_datasource(
        self, org_id: int, bucket: str, token: str, influx_url: str, influx_org: str
    ) -> bool: ...
    def ensure_dashboards(self, org_id: int, directory: Path) -> int: ...


@dataclass
class SetupReport:
    created: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def ensure_tenants(
    cfg: AppConfig,
    influx: InfluxAdmin,
    grafana: GrafanaAdmin,
    *,
    token_dir: Path,
    dashboard_dir: Path,
    influx_url: str,
    influx_org: str,
    retention_days: int,
) -> SetupReport:
    """Richtet alle Kunden ein. Ein Fehler bei einem Kunden stoppt die anderen nicht."""
    report = SetupReport()
    all_bucket_ids: list[str] = []
    for cust in cfg.customers:
        try:
            raw, created = influx.ensure_bucket(bucket_name(cust.id), retention_days)
            hourly, created_h = influx.ensure_bucket(f"{bucket_name(cust.id)}_1h", retention_days)
            ids = [raw, hourly]
            all_bucket_ids += (
                ids  # sofort: ein späterer Fehler darf die Aufnahme dieses Kunden nicht abschneiden
            )
            report.created += [
                f"Bucket {n}"
                for n, c in (
                    (bucket_name(cust.id), created),
                    (f"{bucket_name(cust.id)}_1h", created_h),
                )
                if c
            ]
            if influx.ensure_task(f"prtg_{cust.id}_rollup_1h", rollup_flux(cust.id)):
                report.created.append(f"Verdichtung für {cust.id}")
            token, created_t = influx.ensure_token(f"grafana-read-{cust.id}", ids, write=False)
            if created_t:
                report.created.append(f"Lese-Token für {cust.id}")
            org_id, created_o = grafana.ensure_org(cust.id)
            if created_o:
                report.created.append(f"Grafana-Organisation {cust.id}")
            if grafana.ensure_datasource(
                org_id, bucket_name(cust.id), token, influx_url, influx_org
            ):
                report.created.append(f"Datenquelle für {cust.id}")
            if dashboard_dir.is_dir():
                grafana.ensure_dashboards(org_id, dashboard_dir)
        except Exception as err:
            report.problems.append(f"Kunde {cust.id}: {type(err).__name__}: {str(err)[:150]}")
            log.exception("Einrichtung für %s fehlgeschlagen", cust.id)
    if all_bucket_ids:
        write_token, created_w = influx.ensure_token(WRITE_TOKEN, all_bucket_ids, write=True)
        token_dir.mkdir(parents=True, exist_ok=True)
        path = token_dir / "collector.token"
        path.write_text(write_token, encoding="utf-8")
        path.chmod(0o640)
        if created_w:
            report.created.append("Schreib-Token des Collectors")
    return report


# --- Echte Schnittstellen ---


class InfluxHttp:
    """InfluxDB 2.x über den offiziellen Client."""

    def __init__(self, url: str, token: str, org: str, verify_ssl: bool = True) -> None:
        from influxdb_client import InfluxDBClient

        self._c = InfluxDBClient(url=url, token=token, org=org, verify_ssl=verify_ssl)
        self._org = org
        orgs = self._c.organizations_api().find_organizations(org=org)
        if not orgs:
            raise ConfigError(f"InfluxDB-Organisation {org} nicht gefunden.")
        self._org_id = orgs[0].id

    def ensure_bucket(self, name: str, retention_days: int) -> tuple[str, bool]:
        from influxdb_client import BucketRetentionRules

        api = self._c.buckets_api()
        found = api.find_bucket_by_name(name)
        if found:
            current = found.retention_rules[0].every_seconds if found.retention_rules else 0
            wanted = retention_days * 86400
            if (
                current and current < wanted
            ):  # 0 = unbegrenzt. Verkürzen löscht Daten, das bleibt Handarbeit.
                found.retention_rules = [BucketRetentionRules(type="expire", every_seconds=wanted)]
                api.update_bucket(found)
                log.info("Aufbewahrung von %s auf %d Tage verlängert", name, retention_days)
            elif current > wanted:
                log.warning(
                    "Aufbewahrung von %s bleibt bei %d Tagen (INFLUX_RETENTION_DAYS=%d wirkt nur verlängernd)",
                    name,
                    current // 86400,
                    retention_days,
                )
            return found.id, False
        rules = BucketRetentionRules(type="expire", every_seconds=retention_days * 86400)
        return api.create_bucket(
            bucket_name=name, retention_rules=rules, org_id=self._org_id
        ).id, True

    def ensure_task(self, name: str, flux: str) -> bool:
        from influxdb_client import TaskCreateRequest, TaskUpdateRequest

        api = self._c.tasks_api()
        existing = [t for t in api.find_tasks(name=name) if t.name == name]
        if existing:
            if existing[0].flux.strip() == flux.strip():
                return False
            api.update_task_request(existing[0].id, TaskUpdateRequest(flux=flux))
            return True
        api.create_task(
            task_create_request=TaskCreateRequest(flux=flux, org_id=self._org_id, status="active")
        )
        return True

    def ensure_token(
        self, description: str, bucket_ids: list[str], write: bool
    ) -> tuple[str, bool]:
        from influxdb_client import Authorization, Permission, PermissionResource

        action = "write" if write else "read"
        api = self._c.authorizations_api()
        want = {(action, b) for b in bucket_ids}
        for auth in api.find_authorizations(org_id=self._org_id):
            if auth.description != description:
                continue
            have = {
                (p.action, p.resource.id) for p in auth.permissions if p.resource.type == "buckets"
            }
            if have == want and auth.status == "active":
                return auth.token, False
            api.delete_authorization(auth.id)
        perms = [
            Permission(
                action=action,
                resource=PermissionResource(type="buckets", id=b, org_id=self._org_id),
            )
            for b in bucket_ids
        ]
        created = api.create_authorization(
            authorization=Authorization(
                org_id=self._org_id, permissions=perms, description=description
            )
        )
        return created.token, True

    def delete_series(self, bucket: str, predicate: str) -> None:
        """Löscht alle Punkte des Buckets, auf die das Prädikat passt (zum Beispiel `core="hq"`)."""
        from datetime import UTC, datetime, timedelta

        found = self._c.buckets_api().find_bucket_by_name(bucket)
        if found is None:
            return
        self._c.delete_api().delete(
            start=datetime(1970, 1, 1, tzinfo=UTC),
            stop=datetime.now(UTC) + timedelta(days=1),
            predicate=predicate,
            bucket=bucket,
            org=self._org,
        )

    def delete_bucket(self, name: str) -> None:
        api = self._c.buckets_api()
        found = api.find_bucket_by_name(name)
        if found is not None:
            api.delete_bucket(found)

    def delete_task(self, name: str) -> None:
        api = self._c.tasks_api()
        for task in api.find_tasks(name=name):
            if task.name == name:
                api.delete_task(task.id)

    def delete_tokens(self, description: str) -> None:
        api = self._c.authorizations_api()
        for auth in api.find_authorizations(org_id=self._org_id):
            if auth.description == description:
                api.delete_authorization(auth.id)


class GrafanaHttp:
    """Grafana über die HTTP-Schnittstelle mit dem Administrator-Konto."""

    def __init__(self, url: str, admin_password: str, verify_ssl: bool = True) -> None:
        self._skip_verify = not verify_ssl  # gilt auch für die Datenquelle (Grafana → InfluxDB)
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            auth=("admin", admin_password),
            timeout=30,
            verify=verify_ssl,
        )

    def _org(self, org_id: int) -> dict[str, str]:
        return {"X-Grafana-Org-Id": str(org_id)}

    def ensure_org(self, name: str) -> tuple[int, bool]:
        r = self._http.get(f"/api/orgs/name/{name}")
        if r.status_code == 200:
            return int(r.json()["id"]), False
        r = self._http.post("/api/orgs", json={"name": name})
        r.raise_for_status()
        return int(r.json()["orgId"]), True

    def delete_org(self, name: str) -> None:
        r = self._http.get(f"/api/orgs/name/{name}")
        if r.status_code == 404:
            return
        r.raise_for_status()
        self._http.delete(f"/api/orgs/{r.json()['id']}").raise_for_status()

    def ensure_datasource(
        self, org_id: int, bucket: str, token: str, influx_url: str, influx_org: str
    ) -> bool:
        body: dict[str, Any] = {
            "uid": DATASOURCE_UID,
            "name": "PRTG InfluxDB",
            "type": "influxdb",
            "access": "proxy",
            "url": influx_url,
            "isDefault": True,
            "jsonData": {
                "version": "Flux",
                "organization": influx_org,
                "defaultBucket": bucket,
                "tlsSkipVerify": self._skip_verify,
                "maxSeries": MAX_SERIES,
            },
            "secureJsonData": {"token": token},
        }
        h = self._org(org_id)
        r = self._http.get(f"/api/datasources/uid/{DATASOURCE_UID}", headers=h)
        if r.status_code == 200:
            existing = r.json()
            if (
                existing["url"] == influx_url
                and existing["jsonData"].get("defaultBucket") == bucket
                and existing["jsonData"].get("tlsSkipVerify") == self._skip_verify
                and existing["jsonData"].get("maxSeries") == MAX_SERIES
            ):
                return False  # Token lässt sich nicht auslesen; nur bei geänderten Angaben neu schreiben
            self._http.put(
                f"/api/datasources/uid/{DATASOURCE_UID}", json=body, headers=h
            ).raise_for_status()
            return True
        self._http.post("/api/datasources", json=body, headers=h).raise_for_status()
        return True

    def ensure_dashboards(self, org_id: int, directory: Path) -> int:
        h = self._org(org_id)
        for uid, title in (CORE_HEALTH, SENSOR_DASHBOARD, OWN_FOLDER):
            if self._http.get(f"/api/folders/{uid}", headers=h).status_code != 200:
                self._http.post(
                    "/api/folders", json={"uid": uid, "title": title}, headers=h
                ).raise_for_status()
        count = 0
        for path in sorted(directory.glob("*.json")):
            dash = json.loads(path.read_text(encoding="utf-8"))
            dash["id"] = None
            self._http.post(
                "/api/dashboards/db",
                headers=h,
                json={
                    "dashboard": dash,
                    "folderUid": (
                        CORE_HEALTH if path.name in CORE_HEALTH_FILES else SENSOR_DASHBOARD
                    )[0],
                    "overwrite": True,
                    "message": "Bereitgestellt durch setup",
                },
            ).raise_for_status()
            count += 1
        return count


def run(env: Mapping[str, str]) -> SetupReport:
    cfg = load_from_env(env)
    token = env.get("INFLUX_ADMIN_TOKEN", "")
    password = env.get("GRAFANA_ADMIN_PASSWORD", "")
    if not token or not password:
        raise ConfigError("INFLUX_ADMIN_TOKEN und GRAFANA_ADMIN_PASSWORD müssen gesetzt sein.")
    org = env.get("INFLUX_ORG", "netmon24")
    url = service_url(env, "influxdb")
    verify = internal_tls(env)
    return ensure_tenants(
        cfg,
        InfluxHttp(url, token, org, verify_ssl=verify),
        GrafanaHttp(service_url(env, "grafana"), password, verify_ssl=verify),
        token_dir=Path(env.get("TOKEN_DIR", "/tokens")),
        dashboard_dir=Path(env.get("DASHBOARD_DIR", "/app/dashboards")),
        influx_url=url,
        influx_org=org,
        retention_days=int(env.get("INFLUX_RETENTION_DAYS", "365")),
    )


def main() -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        report = run(dict(os.environ))
    except ConfigError as err:
        log.error("%s", err)
        return 2
    for line in report.created:
        log.info("angelegt: %s", line)
    if not report.created:
        log.info("Alles bereits eingerichtet, nichts geändert.")
    for line in report.problems:
        log.error("%s", line)
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(main())
