import asyncio

import pytest
from stub_server import StubServer, channel, device, obj, sensor

from prtg_collector.config import CoreConfig
from prtg_collector.models import Status
from prtg_collector.prtg_client import PrtgClient, PrtgError


def _client(stub: StubServer, **kw) -> PrtgClient:
    cfg = CoreConfig(id="core", url=stub.url, api_key_env="K", **kw)
    return PrtgClient(cfg, token="geheimer-token", backoff_s=0, timeout_s=0.5)


def _stub(**kw) -> StubServer:
    probes = [
        obj(1, "CoreProbe", parent={"id": "0", "name": "Hauptgruppe", "type": "REFERENCED_ROOT"})
    ]
    groups = [
        obj(10, "Produktion", parent={"id": "1", "name": "CoreProbe", "type": "REFERENCED_PROBE"}),
        obj(11, "Halle 1", parent={"id": "10", "name": "Produktion", "type": "REFERENCED_GROUP"}),
    ]
    devices = [device(100, "srv-01", 11, host="10.0.0.1", tags=["web"], parenttags=["Schulung"])]
    sensors = [sensor(1000, 100, "Ping", tags=["ping"], parenttags=["web", "Schulung"])]
    return StubServer(
        probes=kw.get("probes", probes),
        groups=kw.get("groups", groups),
        devices=kw.get("devices", devices),
        sensors=kw.get("sensors", sensors),
        max_page=kw.get("max_page", 100),
    )


async def test_inventory_maps_fields_and_effective_tags():
    with _stub() as stub:
        inv = await _client(stub).fetch_inventory()
    [dev] = inv.devices
    assert (dev.id, dev.name, dev.host, dev.group, dev.probe) == (
        100,
        "srv-01",
        "10.0.0.1",
        "Halle 1",
        "CoreProbe",
    )
    assert dev.tags == ("web", "Schulung")
    [sen] = inv.sensors
    assert (sen.id, sen.device_id, sen.type, sen.status) == (1000, 100, "Ping", Status.UP)
    assert sen.tags == ("ping", "web", "Schulung")  # eigene vor geerbten, ohne Dubletten


async def test_inventory_follows_pagination():
    sensors = [sensor(1000 + i, 100, f"S{i}") for i in range(250)]
    with _stub(sensors=sensors, max_page=100) as stub:
        inv = await _client(stub).fetch_inventory()
        offsets = [p for m, p in stub.requests if "/sensors" in p]
    assert len(inv.sensors) == 250
    assert any("offset=100" in p for p in offsets) and any("offset=200" in p for p in offsets)


async def test_status_words_from_real_system():
    sensors = [
        sensor(1, 100, "a", status="DOWN_ACKNOWLEDGED"),
        sensor(2, 100, "b", status="PAUSED_BY_PARENT"),
    ]
    with _stub(sensors=sensors) as stub:
        inv = await _client(stub).fetch_inventory()
    assert [s.status for s in inv.sensors] == [Status.DOWN, Status.PAUSED]


async def test_401_raises_prtgerror_without_token_in_message():
    with _stub() as stub:
        stub.fail_with = 401
        with pytest.raises(PrtgError) as err:
            await _client(stub).fetch_inventory()
    assert err.value.status == 401 and "API-Key abgelehnt" in str(err.value)
    assert "geheimer-token" not in str(err.value)


async def test_timeout_raises_prtgerror():
    with _stub() as stub:
        stub.fail_with = "timeout"
        with pytest.raises(PrtgError) as err:
            await _client(stub).fetch_inventory()
    assert err.value.status is None and "Keine Antwort" in str(err.value)


async def test_server_error_retried_three_times_then_error():
    with _stub() as stub:
        stub.fail_with = 500
        with pytest.raises(PrtgError):
            await _client(stub).fetch_inventory()
        assert len(stub.requests) == 3


async def test_inactive_license_is_named_and_not_retried():
    with _stub() as stub:
        stub.fail_with, stub.fail_code = 503, "LICENSE_INACTIVE"
        with pytest.raises(PrtgError) as err:
            await _client(stub).fetch_inventory()
        assert len(stub.requests) == 1  # Wiederholen hilft bei einer inaktiven Lizenz nicht
    assert err.value.status == 503 and err.value.kind == "server"
    assert "Lizenz" in str(err.value) and "inaktiv" in str(err.value)


async def test_non_get_never_sent_and_bearer_header_used():
    with _stub() as stub:
        await _client(stub).fetch_inventory()
        assert {m for m, _ in stub.requests} == {"GET"}
        assert set(stub.auth_headers) == {"Bearer geheimer-token"}


async def test_empty_body_returns_empty_inventory():
    with _stub() as stub:
        stub.empty_body = True
        inv = await _client(stub).fetch_inventory()
    assert inv.devices == [] and inv.sensors == []


async def test_pinned_connection_without_tls_is_refused():
    with _stub() as stub:
        client = _client(stub, pinned_sha256="ab" * 32)
        with pytest.raises(PrtgError) as err:
            await client.fetch_inventory()
    assert err.value.kind == "tls" and "nicht verschlüsselt" in str(err.value)


async def test_malformed_objects_are_skipped_not_fatal_and_null_is_not_none():  # Befund 6
    good = sensor(1, 100, "Ping")
    nameless = sensor(2, 100, "x")
    nameless["name"] = None
    nameless["message"] = None
    no_id = sensor(3, 100, "y")
    del no_id["id"]
    with _stub(sensors=[good, nameless, no_id]) as stub:
        inv = await _client(stub).fetch_inventory()
    assert [s.id for s in inv.sensors] == [1, 2]
    assert inv.sensors[1].name == "" and inv.sensors[1].message == ""
    assert inv.skipped == 1


async def test_channels_mapped_from_spike_shape():
    chans = [
        channel(1000, 0, "Health", 100, "%"),
        channel(1000, 3, "Antwortzeit", 12.5, "ms"),
        channel(1000, 4, "Status", "ok"),  # Text: kein Messwert
        channel(1000, 5, "Leer", None),
    ]
    with _stub() as stub:
        stub.data["channels"] = chans
        got = await _client(stub).fetch_channels()
    by_id = {c.id: c for c in got}
    assert (by_id[0].sensor_id, by_id[0].name, by_id[0].unit, by_id[0].value) == (
        1000,
        "Health",
        "%",
        100.0,
    )
    assert by_id[3].value == 12.5 and by_id[3].unit == "ms"
    assert by_id[4].value is None and by_id[5].value is None


async def test_channels_follow_pagination_and_skip_malformed():
    chans = [channel(1000, i, f"K{i}", i) for i in range(250)] + [{"id": "kaputt"}]
    with _stub(max_page=100) as stub:
        stub.data["channels"] = chans
        got = await _client(stub).fetch_channels()
    assert len(got) == 250


async def test_channels_only_use_get():
    with _stub() as stub:
        await _client(stub).fetch_channels()
        assert {m for m, _ in stub.requests} == {"GET"}


def _tls_stub():
    from tlsmaterial import make_self_signed

    cert, key = make_self_signed()
    return cert, StubServer(
        probes=[obj(1, "P", parent={"id": "0", "name": "x", "type": "REFERENCED_ROOT"})],
        tls=(cert, key),
    )


async def test_https_without_pin_and_verify_off_works():  # Fehlerrunde F01
    _, stub = _tls_stub()
    with stub:
        assert await _client(stub, verify_ssl=False).count_probes() == 1


async def test_https_with_ca_file_works(tmp_path):  # Fehlerrunde F01
    cert, stub = _tls_stub()
    ca = tmp_path / "ca.pem"
    ca.write_text(cert)
    with stub:
        client = _client(stub, verify_ssl=True, ca_file=str(ca))
        assert await client.count_probes() == 1


async def test_https_with_wrong_pin_is_refused():
    _, stub = _tls_stub()
    with stub, pytest.raises(PrtgError) as err:
        await _client(stub, pinned_sha256="ab" * 32).count_probes()
    assert err.value.kind == "tls" and "geändert" in str(err.value)


def _mock_client(handler) -> PrtgClient:
    import httpx

    client = _client(_stub())
    client._http = httpx.AsyncClient(
        base_url="http://x", transport=httpx.MockTransport(handler), timeout=1
    )
    return client


async def test_bad_total_header_is_prtg_error():  # Fehlerrunde F08
    import httpx

    def handler(request):
        return httpx.Response(200, json=[{"id": 1}], headers={"X-Total-Count": "abc"})

    with pytest.raises(PrtgError):
        await _mock_client(handler)._list("sensors")


async def test_pagination_without_progress_terminates():  # Fehlerrunde F08
    import httpx

    def handler(request):  # ignoriert offset, kein X-Total-Count
        return httpx.Response(200, json=[{"id": i} for i in range(5)])

    with pytest.raises(PrtgError):
        await asyncio.wait_for(_mock_client(handler)._list("sensors"), 5)


def test_dollar_brace_in_names_is_defused_for_grafana():  # F24
    from prtg_collector.prtg_client import _s

    assert "${" not in _s("srv-${core}")
