from prtg_collector.portal.status import InfluxStatus, RunStatus
from prtg_collector.writer import InfluxWriter


def test_status_provider_maps_query_rows_to_runs():
    rows = [
        {"core": "core-a", "time": "2026-11-11T12:00:00+00:00", "errors": 0, "last_error": None},
        {
            "core": "core-b",
            "time": "2026-11-11T12:01:00+00:00",
            "errors": 1,
            "last_error": "API-Key abgelehnt (401).",
        },
    ]
    status = InfluxStatus(query=lambda: rows)
    assert status.last_runs() == {
        "core-a": RunStatus(at="2026-11-11 12:00", errors=0, last_error=None),
        "core-b": RunStatus(at="2026-11-11 12:01", errors=1, last_error="API-Key abgelehnt (401)."),
    }


def test_status_provider_survives_query_errors():
    def boom():
        raise OSError("influx weg")

    assert InfluxStatus(query=boom).last_runs() == {}


def test_writer_rebuilds_client_when_token_changes(monkeypatch):
    created = []

    class FakeClient:
        def __init__(self, url, token, org, verify_ssl=True):
            created.append(token)

        def write_api(self, write_options):
            class W:
                def write(self, **kw):
                    pass

            return W()

        def close(self):
            pass

    monkeypatch.setattr("prtg_collector.writer.InfluxDBClient", FakeClient)
    token = ["t1"]
    w = InfluxWriter("http://x", lambda: token[0], "org")
    w.write("k", [object()])
    w.write("k", [object()])
    token[0] = "t2"
    w.write("k", [object()])
    assert created == ["t1", "t2"]


def test_writer_reconnects_when_the_url_changes(monkeypatch):
    created = []

    class FakeClient:
        def __init__(self, url, token, org, verify_ssl=True):
            created.append((url, verify_ssl))

        def write_api(self, write_options):
            class W:
                def write(self, **kw):
                    pass

            return W()

        def close(self):
            pass

    monkeypatch.setattr("prtg_collector.writer.InfluxDBClient", FakeClient)
    url, verify = ["http://influxdb:8086"], [True]
    w = InfluxWriter(lambda: url[0], "t", "org", verify_ssl=lambda: verify[0])
    w.write("k", [object()])
    w.write("k", [object()])
    url[0], verify[0] = "https://influxdb:8086", False  # Betriebsart im Portal umgeschaltet
    w.write("k", [object()])
    assert created == [("http://influxdb:8086", True), ("https://influxdb:8086", False)]


def test_writer_parallel_reconnect_never_writes_into_closed_client(monkeypatch):  # Fehlerrunde F18
    import threading
    import time

    class FakeClient:
        def __init__(self, url, token, org, verify_ssl=True):
            self.closed = False
            if token_seen:
                time.sleep(0.02)  # langsamer Neuaufbau erweitert das Zeitfenster
            token_seen.append(token)

        def write_api(self, write_options):
            client = self

            class W:
                def write(self, **kw):
                    if client.closed:
                        raise RuntimeError("closed client used")
                    time.sleep(0.002)

            return W()

        def close(self):
            self.closed = True

    token_seen: list[str] = []
    monkeypatch.setattr("prtg_collector.writer.InfluxDBClient", FakeClient)
    token = ["a"]
    w = InfluxWriter("http://x", lambda: token[0], "org")
    errors = []

    def work():
        for _ in range(30):
            try:
                w.write("k", [object()])
            except RuntimeError as err:
                errors.append(err)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.02)
    token[0] = "b"
    for t in threads:
        t.join()
    assert errors == []


def test_partial_down_and_spelling_variants_are_mapped():  # F19
    from prtg_collector.models import Status

    for raw in ("PartialDown", "partial_down", "Partial Down", "Down (Partial)"):
        assert Status.from_prtg(raw) is Status.DOWN
    assert Status.from_prtg("Disconnected") is Status.UNKNOWN
    assert Status.from_prtg("Unusual") is Status.WARNING
