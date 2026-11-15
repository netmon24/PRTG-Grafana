"""Führt alle Dashboard-Abfragen gegen eine echte InfluxDB aus (pytest -m integration)."""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import build_dashboards
import httpx
import pytest
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from prtg_collector.setup_tenants import InfluxHttp
from prtg_collector.writer import bucket_name

pytestmark = pytest.mark.integration
URL, TOKEN, ORG = (
    os.environ.get("INFLUX_URL"),
    os.environ.get("INFLUX_ADMIN_TOKEN"),
    os.environ.get("INFLUX_ORG", "netmon24"),
)


@pytest.fixture
def seeded():
    if not (URL and TOKEN):
        pytest.skip("INFLUX_URL und INFLUX_ADMIN_TOKEN nicht gesetzt")
    customer = "dt" + uuid.uuid4().hex[:8]
    admin = InfluxHttp(URL, TOKEN, ORG)
    admin.ensure_bucket(bucket_name(customer), 1)
    admin.ensure_bucket(bucket_name(customer) + "_1h", 1)
    client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    write = client.write_api(write_options=SYNCHRONOUS)
    now = datetime.now(UTC).replace(second=0, microsecond=0)

    def device_series(dev_id, name, codes, tags="|web|"):
        pts = []
        for i, code in enumerate(codes):
            ts = now - timedelta(minutes=len(codes) - i)
            pts.append(
                Point("prtg_device")
                .tag("customer", customer)
                .tag("core", "core-a")
                .tag("group", "G")
                .tag("probe", "P")
                .tag("device", name)
                .tag("device_id", str(dev_id))
                .tag("host", "h")
                .tag("dns_name", f"{name}.example.invalid")
                .tag("prtg_tags", tags)
                .field("status_code", code)
                .field("sensor_count", 2)
                .field("sensors_down", int(code == 2))
                .field("sensors_warning", 0)
                .time(ts, WritePrecision.S)
            )
        write.write(bucket=bucket_name(customer), record=pts)

    def sensor_series(sid, status_codes, tags="|web|"):
        pts = []
        for i, code in enumerate(status_codes):
            ts = now - timedelta(minutes=len(status_codes) - i)
            pts.append(
                Point("prtg_sensor_status")
                .tag("customer", customer)
                .tag("core", "core-a")
                .tag("group", "G")
                .tag("probe", "P")
                .tag("device", "A")
                .tag("device_id", "1")
                .tag("host", "h")
                .tag("dns_name", "nicht auflösbar")
                .tag("prtg_tags", tags)
                .tag("sensor", f"S{sid}")
                .tag("sensor_id", str(sid))
                .tag("sensor_type", "Ping")
                .field("status", "x")
                .field("status_code", code)
                .field("message", "Meldung")
                .time(ts, WritePrecision.S)
            )
        write.write(bucket=bucket_name(customer), record=pts)

    def channel_series(sid, name, unit, values):
        pts = []
        for i, value in enumerate(values):
            ts = now - timedelta(minutes=len(values) - i)
            pts.append(
                Point("prtg_channel")
                .tag("customer", customer)
                .tag("core", "core-a")
                .tag("group", "G")
                .tag("probe", "P")
                .tag("device", "A")
                .tag("device_id", "1")
                .tag("host", "h")
                .tag("dns_name", "nicht auflösbar")
                .tag("prtg_tags", "|web|")
                .tag("sensor", f"S{sid}")
                .tag("sensor_id", str(sid))
                .tag("sensor_type", "Ping")
                .tag("channel", name)
                .tag("channel_id", "0")
                .tag("unit", unit)
                .field("value", value)
                .time(ts, WritePrecision.S)
            )
        write.write(bucket=bucket_name(customer), record=pts)

    channel_series(10, "Antwortzeit", "ms", [10.0, 12.5, 11.0, 30.0, 9.0])
    sensor_series(10, [0] * 8 + [2, 2])
    sensor_series(11, [0] * 9 + [1])
    write.write(
        bucket=bucket_name(customer),
        record=[
            Point("prtg_tag_catalog")
            .tag("customer", customer)
            .tag("core", "core-a")
            .tag("prtg_tag", "web")
            .tag("object_kind", "sensor")
            .field("count", 2)
            .time(now, WritePrecision.S),
            Point("collector_run")
            .tag("customer", customer)
            .tag("core", "core-a")
            .field("duration_ms", 5)
            .field("objects_read", 2)
            .field("points_written", 4)
            .field("errors", 0)
            .field("schema_version", 1)
            .time(now, WritePrecision.S),
        ],
    )
    device_series(1, "A", [0] * 7 + [2, 2] + [3])  # 2 Ausfälle bei 9 gewerteten Abrufen: 77,8 %
    device_series(2, "B", [3] * 10)  # nur pausiert: darf nicht erscheinen
    yield customer, client
    for suffix in ("", "_1h"):
        b = client.buckets_api().find_bucket_by_name(bucket_name(customer) + suffix)
        client.buckets_api().delete_bucket(b)
    client.close()


def _query(flux: str) -> list[dict]:
    r = httpx.post(
        f"{URL}/api/v2/query",
        params={"org": ORG},
        headers={
            "Authorization": f"Token {TOKEN}",
            "Accept": "application/csv",
            "Content-Type": "application/json",
        },
        json={"query": flux, "type": "flux"},
        timeout=30,
    )
    assert r.status_code == 200, r.text[:300]
    rows = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
    assert not any(ln.startswith(",error") for ln in rows), rows[:2]
    return rows


def test_availability_flux_matches_known_series(seeded):
    customer, _ = seeded
    flux = build_dashboards.render_flux(
        build_dashboards.availability_by_device("-24h", hourly=False),
        bucket_name(customer),
        core="core-a",
    )
    rows = _query(flux)
    values = [r.strip(",").split(",")[-1] for r in rows]
    numeric = [v for v in values if v.replace(".", "", 1).isdigit() and "." in v]
    assert len(numeric) == 1, rows  # Gerät B (nur pausiert) erscheint nicht
    assert abs(float(numeric[0]) - 77.78) < 0.1


def test_every_panel_query_is_valid_flux(seeded, tmp_path):
    customer, _ = seeded
    for path in build_dashboards.build_all(tmp_path):
        dash = json.loads(path.read_text(encoding="utf-8"))
        for panel in dash["panels"]:
            for t in panel.get("targets", []):
                flux = build_dashboards.render_flux(
                    t["query"], bucket_name(customer), core="core-a", group="G", device="A"
                )
                rows = _query(flux)  # wirft bei Syntax- und Typfehlern
                if panel["title"] in {
                    "Sensoren gesamt",
                    "Offene Probleme",
                    "Datenstand (Alter des letzten Abrufs)",
                    "Collector-Gesundheit je Core",
                    "Verfügbarkeit 24 Stunden",
                    "Aktuelle Werte der gewählten Kanäle",
                    "Kanalwerte im Verlauf",
                    "Ereignisse (Statuswechsel)",
                    "Statuswechsel im Zeitraum",
                }:
                    assert len(rows) >= 2, f"{panel['title']}: keine Daten trotz gesetzter Reihen"


def test_status_changes_are_derived_with_old_and_new_status(seeded):
    customer, _ = seeded
    flux = build_dashboards.render_flux(
        build_dashboards.status_changes(), bucket_name(customer), core="core-a", group="G"
    )
    rows = _query(flux)
    joined = "\n".join(rows)
    # Sensor 10: OK -> Ausfall (0 -> 2); Sensor 11: OK -> Warnung (0 -> 1)
    assert ",S10," in joined and ",S11," in joined
    header, *body = rows
    cols = header.strip(",").split(",")
    by = [dict(zip(cols, r.strip(",").split(","), strict=False)) for r in body]
    s10 = next(r for r in by if r["sensor"] == "S10")
    assert (s10["von"], s10["neu"]) == ("0", "2")
