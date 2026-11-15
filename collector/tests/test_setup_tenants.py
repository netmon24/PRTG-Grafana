from pathlib import Path

from prtg_collector.config import AppConfig, CoreConfig, CustomerConfig
from prtg_collector.setup_tenants import ensure_tenants


class FakeInflux:
    def __init__(self):
        self.buckets, self.tokens, self.tasks, self.calls = {}, {}, {}, []

    def ensure_bucket(self, name, retention_days):
        self.calls.append(("bucket", name))
        created = name not in self.buckets
        self.buckets.setdefault(name, f"id-{name}")
        return self.buckets[name], created

    def ensure_task(self, name, flux):
        created = name not in self.tasks
        self.tasks[name] = flux
        return created

    def ensure_token(self, description, bucket_ids, write):
        key = (description, write)
        have = self.tokens.get(key)
        if have and set(have[1]) == set(bucket_ids):
            return have[0], False
        self.tokens[key] = (f"geheim-{description}-{len(bucket_ids)}", list(bucket_ids))
        return self.tokens[key][0], True


class FakeGrafana:
    def __init__(self):
        self.orgs, self.sources, self.dashboards = {}, {}, {}

    def ensure_org(self, name):
        created = name not in self.orgs
        self.orgs.setdefault(name, len(self.orgs) + 2)
        return self.orgs[name], created

    def ensure_datasource(self, org_id, bucket, token, influx_url, influx_org):
        created = org_id not in self.sources
        self.sources[org_id] = (bucket, token)
        return created

    def ensure_dashboards(self, org_id, directory):
        self.dashboards[org_id] = sorted(p.name for p in Path(directory).glob("*.json"))
        return len(self.dashboards[org_id])


def _cfg(*ids):
    core = CoreConfig(id="c", url="https://x.example.invalid:1616", api_key_env="K")
    return AppConfig(customers=[CustomerConfig(id=i, cores=[core]) for i in ids])


def _run(cfg, influx, grafana, tmp_path):
    (tmp_path / "d").mkdir(exist_ok=True)
    (tmp_path / "d" / "01.json").write_text("{}")
    return ensure_tenants(
        cfg,
        influx,
        grafana,
        token_dir=tmp_path / "tokens",
        dashboard_dir=tmp_path / "d",
        influx_url="http://influxdb:8086",
        influx_org="netmon24",
        retention_days=365,
    )


def test_new_customer_gets_bucket_org_datasource_dashboards(tmp_path):
    i, g = FakeInflux(), FakeGrafana()
    report = _run(_cfg("musterfirma"), i, g, tmp_path)
    assert {"prtg_musterfirma", "prtg_musterfirma_1h"} <= set(i.buckets)
    assert "musterfirma" in g.orgs and g.dashboards[g.orgs["musterfirma"]] == ["01.json"]
    assert report.created and not report.problems


def test_second_run_changes_nothing(tmp_path):
    i, g = FakeInflux(), FakeGrafana()
    _run(_cfg("musterfirma"), i, g, tmp_path)
    report = _run(_cfg("musterfirma"), i, g, tmp_path)
    assert report.created == []


def test_write_token_covers_new_bucket(tmp_path):
    i, g = FakeInflux(), FakeGrafana()
    _run(_cfg("a"), i, g, tmp_path)
    _run(_cfg("a", "b"), i, g, tmp_path)
    _, ids = i.tokens[("collector-write", True)]
    assert {"id-prtg_a", "id-prtg_b", "id-prtg_a_1h", "id-prtg_b_1h"} <= set(ids)


def test_read_token_only_for_own_buckets(tmp_path):
    i, g = FakeInflux(), FakeGrafana()
    _run(_cfg("a", "b"), i, g, tmp_path)
    _, ids = i.tokens[("grafana-read-a", False)]
    assert set(ids) == {"id-prtg_a", "id-prtg_a_1h"}


def test_tokens_not_in_report_and_write_token_file_restricted(tmp_path):
    i, g = FakeInflux(), FakeGrafana()
    report = _run(_cfg("a"), i, g, tmp_path)
    text = " ".join(report.created + report.problems)
    assert "geheim-" not in text
    f = tmp_path / "tokens" / "collector.token"
    assert f.read_text().startswith("geheim-collector-write")
    assert oct(f.stat().st_mode & 0o777) == "0o640"


def test_one_failing_customer_does_not_stop_the_others(tmp_path):
    class Boom(FakeInflux):
        def ensure_bucket(self, name, retention_days):
            if name.startswith("prtg_a"):
                raise RuntimeError("kaputt")
            return super().ensure_bucket(name, retention_days)

    i, g = Boom(), FakeGrafana()
    report = _run(_cfg("a", "b"), i, g, tmp_path)
    assert "prtg_b" in i.buckets and any("a" in p for p in report.problems)


def test_failing_customer_keeps_its_buckets_in_write_token(tmp_path):  # Befund 3
    class Flaky(FakeGrafana):
        fail_for = None

        def ensure_dashboards(self, org_id, directory):
            if org_id == self.orgs.get(self.fail_for):
                raise RuntimeError("Grafana 500")
            return super().ensure_dashboards(org_id, directory)

    i, g = FakeInflux(), Flaky()
    _run(_cfg("a", "b"), i, g, tmp_path)
    g.fail_for = "b"
    report = _run(_cfg("a", "b"), i, g, tmp_path)
    assert report.problems
    _, ids = i.tokens[("collector-write", True)]
    assert {"id-prtg_b", "id-prtg_b_1h"} <= set(ids)  # Aufnahme von b läuft weiter


def test_dashboards_go_into_core_health_and_sensor_dashboard_folders(tmp_path):
    import json

    import httpx

    from prtg_collector.setup_tenants import GrafanaHttp

    for name in ("01-gesamtuebersicht.json", "06-probe-health.json", "07-sensorstatus.json"):
        (tmp_path / name).write_text(json.dumps({"uid": name[:2], "title": name}))
    folders, placed = {}, {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if request.url.path.startswith("/api/folders/"):
            uid = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200 if uid in folders else 404, json={})
        if request.url.path == "/api/folders":
            folders[body["uid"]] = body["title"]
            return httpx.Response(200, json={})
        placed[body["dashboard"]["title"]] = folders[body["folderUid"]]
        return httpx.Response(200, json={})

    grafana = GrafanaHttp("http://grafana", "pw")
    grafana._http = httpx.Client(base_url="http://grafana", transport=httpx.MockTransport(handler))
    assert grafana.ensure_dashboards(1, tmp_path) == 3
    assert placed == {
        "01-gesamtuebersicht.json": "Core Health",
        "06-probe-health.json": "Core Health",
        "07-sensorstatus.json": "Sensor Dashboard",
    }
    assert folders["eigene"] == "Eigene"  # Ablage für Kopien der Kunden (docs/tag-anleitung.md)


class _Rule:
    def __init__(self, every_seconds):
        self.every_seconds = every_seconds


class _Bucket:
    id = "b1"

    def __init__(self, every_seconds):
        self.retention_rules = [_Rule(every_seconds)]


class _BucketsApi:
    def __init__(self, bucket):
        self.bucket, self.updated = bucket, None

    def find_bucket_by_name(self, name):
        return self.bucket

    def update_bucket(self, bucket):
        self.updated = bucket
        return bucket


def _influx_http(bucket):
    from prtg_collector.setup_tenants import InfluxHttp

    api = _BucketsApi(bucket)
    admin = InfluxHttp.__new__(InfluxHttp)
    admin._c = type("C", (), {"buckets_api": lambda self: api})()
    return admin, api


def test_existing_bucket_retention_is_extended_but_never_shortened():
    admin, api = _influx_http(_Bucket(30 * 86400))
    assert admin.ensure_bucket("prtg_x", 365) == ("b1", False)
    assert api.updated.retention_rules[0].every_seconds == 365 * 86400

    admin, api = _influx_http(_Bucket(365 * 86400))
    admin.ensure_bucket("prtg_x", 30)
    assert api.updated is None  # Verkürzen löscht Daten und bleibt Handarbeit


def test_grafana_delete_org_removes_the_org_and_ignores_a_missing_one():
    import httpx

    from prtg_collector.setup_tenants import GrafanaHttp

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/orgs/name/weg":
            return httpx.Response(200, json={"id": 7})
        if request.url.path == "/api/orgs/name/fehlt":
            return httpx.Response(404, json={})
        return httpx.Response(200, json={})

    grafana = GrafanaHttp("http://grafana", "pw")
    grafana._http = httpx.Client(base_url="http://grafana", transport=httpx.MockTransport(handler))
    grafana.delete_org("fehlt")
    assert ("DELETE", "/api/orgs/7") not in seen
    grafana.delete_org("weg")
    assert seen[-1] == ("DELETE", "/api/orgs/7")


def test_influx_delete_series_uses_the_predicate_on_the_bucket():
    from prtg_collector.setup_tenants import InfluxHttp

    calls = {}

    class Delete:
        def delete(self, **kw):
            calls.update(kw)

    class Buckets:
        def find_bucket_by_name(self, name):
            return None if name == "fehlt" else object()

    admin = InfluxHttp.__new__(InfluxHttp)
    admin._org = "o"
    admin._c = type(
        "C", (), {"delete_api": lambda self: Delete(), "buckets_api": lambda self: Buckets()}
    )()
    admin.delete_series("fehlt", 'core="x"')
    assert calls == {}
    admin.delete_series("prtg_k", 'core="x"')
    assert calls["predicate"] == 'core="x"' and calls["bucket"] == "prtg_k" and calls["org"] == "o"
