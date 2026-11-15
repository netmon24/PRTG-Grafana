"""Gegen eine laufende InfluxDB (docker compose up -d influxdb): uv run pytest -m integration"""

import os
import uuid
from datetime import UTC, datetime

import pytest
from influxdb_client import InfluxDBClient
from stub_server import StubServer, device, obj, sensor

from prtg_collector.collector import run_all
from prtg_collector.config import AppConfig, CoreConfig, CustomerConfig
from prtg_collector.dns import DnsCache
from prtg_collector.prtg_client import PrtgClient
from prtg_collector.setup_tenants import InfluxHttp
from prtg_collector.writer import InfluxWriter, bucket_name

pytestmark = pytest.mark.integration
URL, TOKEN, ORG = (
    os.environ.get("INFLUX_URL"),
    os.environ.get("INFLUX_ADMIN_TOKEN"),
    os.environ.get("INFLUX_ORG", "netmon24"),
)


@pytest.fixture
def influx():
    if not (URL and TOKEN):
        pytest.skip("INFLUX_URL und INFLUX_ADMIN_TOKEN nicht gesetzt")
    customer = "it" + uuid.uuid4().hex[:8]
    admin = InfluxHttp(URL, TOKEN, ORG)
    bucket_id, _ = admin.ensure_bucket(bucket_name(customer), 1)
    write_token, _ = admin.ensure_token(f"it-{customer}", [bucket_id], write=True)
    client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    yield customer, write_token, client
    client.buckets_api().delete_bucket(
        client.buckets_api().find_bucket_by_name(bucket_name(customer))
    )
    client.close()


def _count(client, customer, measurement, field):
    q = (
        f'from(bucket: "{bucket_name(customer)}") |> range(start: -2d) '
        f'|> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}") |> count()'
    )
    tables = client.query_api().query(q)
    return sum(rec.get_value() for t in tables for rec in t.records)


async def test_run_writes_real_points_and_second_run_same_timestamp_adds_no_duplicates(influx):
    customer, write_token, client = influx
    probes = [obj(1, "P", parent={"id": "0", "name": "H", "type": "REFERENCED_ROOT"})]
    groups = [obj(10, "G", parent={"id": "1", "name": "P", "type": "REFERENCED_PROBE"})]
    devices = [device(100, "srv-01", 10, host="127.0.0.1", tags=["web"])]
    sensors = [sensor(1000, 100, "Ping", tags=["ping"]), sensor(1001, 100, "Port", status="DOWN")]
    with StubServer(probes=probes, groups=groups, devices=devices, sensors=sensors) as stub:
        cfg = AppConfig(
            customers=[
                CustomerConfig(
                    id=customer, cores=[CoreConfig(id="core-a", url=stub.url, api_key_env="K")]
                )
            ]
        )
        writer = InfluxWriter(URL, write_token, ORG)
        now = datetime.now(UTC).replace(microsecond=0)
        factory = lambda core, token: PrtgClient(core, token, backoff_s=0)
        dns = DnsCache()
        await run_all(cfg, {"K": "t"}, factory, writer, dns, now)
        fields = {
            "prtg_sensor_status": "status_code",
            "prtg_device": "status_code",
            "collector_run": "errors",
        }
        first = {m: _count(client, customer, m, f) for m, f in fields.items()}
        await run_all(cfg, {"K": "t"}, factory, writer, dns, now)
        second = {m: _count(client, customer, m, f) for m, f in fields.items()}
    assert first == {"prtg_sensor_status": 2, "prtg_device": 1, "collector_run": 1}
    assert second == first
