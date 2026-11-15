from datetime import UTC, datetime

from prtg_collector.collector import run_all
from prtg_collector.config import AppConfig, CoreConfig, CustomerConfig, Filters
from prtg_collector.dns import DnsCache
from prtg_collector.models import Channel, Device, Inventory, Sensor, Status
from prtg_collector.prtg_client import PrtgError

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
ENV = {"KA": "a", "KB": "b"}


class FakeWriter:
    def __init__(self, buckets=("musterfirma", "beispiel")):
        self.buckets, self.written = set(buckets), []

    def bucket_exists(self, customer: str) -> bool:
        return customer in self.buckets

    def write(self, customer, points):
        self.written.append((customer, [p.to_line_protocol() for p in points]))

    def lines(self, customer=None):
        return [ln for c, ls in self.written if customer in (None, c) for ln in ls]


class FakeClient:
    def __init__(self, inv=None, error=None, channels=(), channel_error=None):
        self.inv, self.error, self.closed = inv, error, False
        self.channels, self.channel_error = list(channels), channel_error

    async def fetch_channels(self):
        if self.channel_error:
            raise self.channel_error
        return self.channels

    async def fetch_inventory(self):
        if self.error:
            raise self.error
        return self.inv

    async def aclose(self):
        self.closed = True


def _inv(n=2):
    dev = Device(1, "srv-01", "10.0.0.1", "P", "G", ("web",))
    return Inventory(
        [dev], [Sensor(10 + i, 1, f"S{i}", "Ping", Status.UP, tags=("web",)) for i in range(n)]
    )


def _cfg(filters=None):
    core = lambda cid, env: CoreConfig(
        id=cid, url="https://x.example.invalid:1616", api_key_env=env, filters=filters or Filters()
    )
    return AppConfig(
        customers=[
            CustomerConfig(id="musterfirma", cores=[core("core-a", "KA")]),
            CustomerConfig(id="beispiel", cores=[core("core-b", "KB")]),
        ]
    )


def _dns():
    return DnsCache(
        reverse=lambda ip: ("srv-01.example.invalid", [], [ip]), forward=lambda n: "10.0.0.1"
    )


async def test_core_failure_does_not_stop_other_cores():
    clients = {
        "core-a": FakeClient(error=PrtgError("core-a", 401, "API-Key abgelehnt (401).")),
        "core-b": FakeClient(inv=_inv()),
    }
    w = FakeWriter()
    results = await run_all(_cfg(), ENV, lambda core, token: clients[core.id], w, _dns(), NOW)
    assert results[("musterfirma", "core-a")].errors == 1
    assert results[("beispiel", "core-b")].errors == 0
    assert any("prtg_sensor_status" in ln for ln in w.lines("beispiel"))
    failed = [ln for ln in w.lines("musterfirma") if ln.startswith("collector_run")]
    assert len(failed) == 1 and "errors=1i" in failed[0] and "API-Key abgelehnt" in failed[0]
    assert all(c.closed for c in clients.values())


async def test_run_writes_collector_run_point_with_counts():
    w = FakeWriter()
    await run_all(_cfg(), ENV, lambda core, token: FakeClient(inv=_inv(2)), w, _dns(), NOW)
    [run] = [ln for ln in w.lines("beispiel") if ln.startswith("collector_run")]
    assert "objects_read=2i" in run and "errors=0i" in run and "schema_version=1i" in run
    assert "core=core-b" in run and "duration_ms=" in run


async def test_dns_name_ends_up_in_points():
    w = FakeWriter()
    await run_all(_cfg(), ENV, lambda core, token: FakeClient(inv=_inv(1)), w, _dns(), NOW)
    assert any("dns_name=srv-01.example.invalid" in ln for ln in w.lines("beispiel"))


async def test_filter_applied_before_writing():
    w = FakeWriter()
    cfg = _cfg(Filters(tags=["gibt-es-nicht"]))
    await run_all(cfg, ENV, lambda core, token: FakeClient(inv=_inv()), w, _dns(), NOW)
    assert not any("prtg_sensor_status" in ln for ln in w.lines())


async def test_empty_inventory_writes_only_collector_run():
    w = FakeWriter()
    await run_all(_cfg(), ENV, lambda core, token: FakeClient(inv=Inventory()), w, _dns(), NOW)
    assert {ln.split(",")[0] for ln in w.lines()} == {"collector_run"}


async def test_unexpected_error_is_counted_not_raised():
    w = FakeWriter()
    clients = {
        "core-a": FakeClient(error=RuntimeError("kaputt geheimwert")),
        "core-b": FakeClient(inv=_inv()),
    }
    results = await run_all(_cfg(), ENV, lambda core, token: clients[core.id], w, _dns(), NOW)
    assert results[("musterfirma", "core-a")].errors == 1
    assert "geheimwert" not in "".join(w.lines())  # nur Fehlertyp, nie Inhalt


async def test_write_failure_names_the_cause_with_http_status():  # Befund 5
    class StatusError(Exception):
        status = 404

    class BrokenWriter(FakeWriter):
        def write(self, customer, points):
            raise StatusError("bucket not found")

    clients = {"core-a": FakeClient(inv=_inv()), "core-b": FakeClient(inv=_inv())}
    results = await run_all(
        _cfg(), ENV, lambda core, token: clients[core.id], BrokenWriter(), _dns(), NOW
    )
    err = results[("musterfirma", "core-a")]
    assert err.errors == 1 and "HTTP 404" in err.last_error and "Einrichtung" in err.last_error


async def test_channel_values_are_written():
    w = FakeWriter()
    chans = [Channel(10, 0, "Health", "%", 100.0), Channel(10, 1, "Text", "", None)]
    clients = {"core-a": FakeClient(inv=_inv(1), channels=chans), "core-b": FakeClient(inv=_inv(1))}
    await run_all(_cfg(), ENV, lambda core, token: clients[core.id], w, _dns(), NOW)
    lines = [ln for ln in w.lines("musterfirma") if ln.startswith("prtg_channel")]
    assert len(lines) == 1 and "channel=Health" in lines[0] and "value=100" in lines[0]


async def test_channel_failure_keeps_status_data_and_reports_error():
    w = FakeWriter()
    err = PrtgError("core-a", 404, "Pfad nicht gefunden (404).")
    clients = {
        "core-a": FakeClient(inv=_inv(2), channel_error=err),
        "core-b": FakeClient(inv=_inv(1)),
    }
    res = await run_all(_cfg(), ENV, lambda core, token: clients[core.id], w, _dns(), NOW)
    lines = w.lines("musterfirma")
    assert any(ln.startswith("prtg_sensor_status") for ln in lines)
    result = res[("musterfirma", "core-a")]
    assert result.errors == 1 and "Kanalwerte" in (result.last_error or "")


async def test_run_core_has_overall_time_limit():  # Fehlerrunde F22
    import asyncio

    from prtg_collector.collector import run_core

    class Hanging(FakeClient):
        async def fetch_inventory(self):
            await asyncio.sleep(30)  # tröpfelnde Antwort: der Lese-Timeout je Chunk greift nie

    writer = FakeWriter()
    core = CoreConfig(id="core-a", url="https://x.example.invalid:1616", api_key_env="KA")
    res = await asyncio.wait_for(
        run_core("musterfirma", core, Hanging(), writer, DnsCache(), NOW, time_limit_s=0.1), 5
    )
    assert res.errors == 1 and "abgebrochen" in (res.last_error or "")
    assert any("collector_run" in ln and "errors=1" in ln for ln in writer.lines())


async def test_slow_dns_does_not_delay_other_cores_writes():  # Fehlerrunde F06
    import asyncio
    import time

    from prtg_collector.collector import run_core

    def slow(ip):
        time.sleep(0.3)
        raise OSError

    def fast(ip):
        raise OSError

    dev = [Device(i, f"d{i}", f"10.0.{i // 250}.{i % 250 + 1}", "P", "G") for i in range(1, 121)]
    big = Inventory(dev, [Sensor(1000 + d.id, d.id, "Ping", "Ping", Status.UP) for d in dev])
    done: dict[str, float] = {}

    class TimedWriter(FakeWriter):
        def write(self, customer, points):
            done[customer] = time.monotonic() - t0
            super().write(customer, points)

    writer = TimedWriter()
    core = CoreConfig(id="core-a", url="https://x.example.invalid:1616", api_key_env="KA")
    t0 = time.monotonic()
    await asyncio.gather(
        run_core("grossfirma", core, FakeClient(big), writer, DnsCache(reverse=slow), NOW),
        run_core("klein", core, FakeClient(_inv()), writer, DnsCache(reverse=fast), NOW),
    )
    assert done["klein"] < 1.0  # vorher wartete das Schreiben hinter allen DNS-Aufgaben


async def test_empty_answer_is_no_error_but_leaves_a_hint():  # F20
    w = FakeWriter()
    results = await run_all(
        _cfg(), ENV, lambda core, token: FakeClient(inv=Inventory()), w, _dns(), NOW
    )
    res = results[("musterfirma", "core-a")]
    assert res.errors == 0 and "keine Geräte" in res.last_error


async def test_influx_outage_is_reported_as_unreachable_not_as_bad_token():  # F21
    class DownWriter(FakeWriter):
        def write(self, customer, points):
            raise ConnectionError("refused")

    clients = {"core-a": FakeClient(inv=_inv()), "core-b": FakeClient(inv=_inv())}
    results = await run_all(
        _cfg(), ENV, lambda core, token: clients[core.id], DownWriter(), _dns(), NOW
    )
    msg = results[("musterfirma", "core-a")].last_error
    assert "nicht erreichbar" in msg and "Schreib-Token" not in msg


async def test_hanging_dns_lookup_is_cut_off_per_host(monkeypatch):  # F06
    import time

    from prtg_collector import collector as col

    monkeypatch.setattr(col, "DNS_TIMEOUT_S", 0.1)
    dev = Device(1, "srv", "10.0.0.1", "P", "G")
    inv = Inventory([dev], [Sensor(10, 1, "S", "Ping", Status.UP)])
    started = time.monotonic()
    names = await col._resolve_dns(inv, DnsCache(reverse=lambda ip: time.sleep(1.0)))
    assert names == {1: ""} and time.monotonic() - started < 0.8
