"""TLS zwischen den Diensten: Prüfung der Zertifikate lässt sich abschalten, wenn sie nicht auf die Containernamen lauten."""

from pathlib import Path

import httpx
import pytest
import yaml

from prtg_collector import writer as writer_module
from prtg_collector.netenv import internal_verify, service_url
from prtg_collector.setup_tenants import GrafanaHttp

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", " false "])
def test_verification_can_be_switched_off(value):
    assert internal_verify({"INTERNAL_TLS_VERIFY": value}) is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "vielleicht"])
def test_verification_stays_on_for_other_values(value):
    assert internal_verify({"INTERNAL_TLS_VERIFY": value}) is True


def test_verification_is_on_without_tls_markers_and_off_with_them(tmp_path):
    env = {"CERTS_DIR": str(tmp_path)}
    assert (
        internal_verify(env) is True and internal_verify({**env, "INTERNAL_TLS_VERIFY": ""}) is True
    )
    (tmp_path / "grafana.enabled").write_text("1")
    assert (
        internal_verify(env) is False
    )  # Zertifikat lautet auf den öffentlichen Namen, nicht auf den Containernamen
    assert internal_verify({**env, "INTERNAL_TLS_VERIFY": "true"}) is True  # ausdrücklich gewünscht


def test_service_url_follows_the_marker(tmp_path):
    env = {"CERTS_DIR": str(tmp_path)}
    assert service_url(env, "influxdb") == "http://influxdb:8086"
    assert service_url(env, "grafana") == "http://grafana:3000"
    (tmp_path / "influxdb.enabled").write_text("1")
    assert service_url(env, "influxdb") == "https://influxdb:8086"
    assert service_url(env, "grafana") == "http://grafana:3000"  # je Dienst getrennt


def test_service_url_can_be_overridden_explicitly(tmp_path):
    (tmp_path / "influxdb.enabled").write_text("1")
    env = {"CERTS_DIR": str(tmp_path), "INFLUX_INTERNAL_URL": "http://anderer-host:9999"}
    assert service_url(env, "influxdb") == "http://anderer-host:9999"
    assert service_url({**env, "INFLUX_INTERNAL_URL": ""}, "influxdb") == "https://influxdb:8086"
    assert (
        service_url({"CERTS_DIR": str(tmp_path), "INFLUX_URL": "http://x:1"}, "influxdb")
        == "http://x:1"
    )


def test_writer_passes_verify_flag_to_influx_client(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **kw):
            seen.update(kw)

        def write_api(self, **kw):
            return object()

    monkeypatch.setattr(writer_module, "InfluxDBClient", Recorder)
    writer_module.InfluxWriter("https://influxdb:8086", "t", "org", verify_ssl=False)
    assert seen["verify_ssl"] is False
    writer_module.InfluxWriter("https://influxdb:8086", "t", "org")
    assert seen["verify_ssl"] is True


def _grafana(verify: bool, existing: dict | None):
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content and httpx.Response(200, content=request.content).json() or {}
        calls.append((request.method, request.url.path, body))
        if request.method == "GET":
            return httpx.Response(200, json=existing) if existing else httpx.Response(404)
        return httpx.Response(200, json={})

    g = GrafanaHttp("https://grafana:3000", "pw", verify_ssl=verify)
    g._http = httpx.Client(
        base_url="https://grafana:3000", transport=httpx.MockTransport(handler), auth=("a", "b")
    )
    return g, calls


def test_datasource_skips_verification_only_when_switched_off():
    for verify in (True, False):
        g, calls = _grafana(verify, existing=None)
        assert g.ensure_datasource(1, "prtg_k", "tok", "https://influxdb:8086", "org") is True
        [post] = [c for c in calls if c[0] == "POST"]
        assert post[2]["jsonData"]["tlsSkipVerify"] is (not verify)


def test_datasource_is_updated_when_verification_setting_changes():
    existing = {
        "url": "https://influxdb:8086",
        "jsonData": {"defaultBucket": "prtg_k", "tlsSkipVerify": False},
    }
    g, calls = _grafana(False, existing)
    assert g.ensure_datasource(1, "prtg_k", "tok", "https://influxdb:8086", "org") is True
    assert any(c[0] == "PUT" for c in calls)


def _compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def test_compose_enables_tls_in_influxdb_and_grafana_only_when_certificates_exist():
    services = _compose()
    influx = " ".join(services["influxdb"]["entrypoint"])
    grafana = " ".join(services["grafana"]["entrypoint"])
    assert "INFLUXD_TLS_CERT" in influx and "[ -s /certs/influxdb.crt ]" in influx
    assert "GF_SERVER_CERT_FILE" in grafana and "[ -s /certs/grafana.crt ]" in grafana
    # TLS nur mit Marker; ohne Marker bleibt der Dienst bei http, auch wenn ein Zertifikat vorliegt
    assert "[ -f /certs/influxdb.enabled ]" in influx and "[ -f /certs/grafana.enabled ]" in grafana
    for name in ("influxdb", "grafana"):
        assert "tls-certs:/certs:ro" in services[name]["volumes"]


def test_compose_internal_urls_and_verification_come_from_the_env():
    services = _compose()
    for name in ("collector", "portal", "setup"):
        env = services[name]["environment"]
        assert "INFLUX_INTERNAL_URL" in env["INFLUX_INTERNAL_URL"]
        assert "INTERNAL_TLS_VERIFY" in env["INTERNAL_TLS_VERIFY"]
        assert env["CERTS_DIR"] == "/certs"
        assert any(v.startswith("tls-certs:/certs") for v in services[name]["volumes"])
    assert "GRAFANA_INTERNAL_URL" in services["portal"]["environment"]["GRAFANA_INTERNAL_URL"]


def test_compose_gives_portal_influxdb_and_grafana_the_shared_key_group():
    services = _compose()
    for name in ("portal", "grafana"):
        assert "TLS_KEY_GID" in services[name]["group_add"][0]


def test_influxdb_gets_a_private_copy_of_the_key_owned_by_its_own_user():
    # InfluxDB wechselt nach dem Start auf den Benutzer influxdb und verliert dabei zusätzliche Gruppen
    script = " ".join(_compose()["influxdb"]["entrypoint"])
    assert "chown influxdb" in script and "chmod 600" in script
    assert "INFLUXD_TLS_KEY=/run/influxdb-tls/" in script
    assert "group_add" not in _compose()["influxdb"]


def test_unverified_internal_connections_are_logged_once_instead_of_warning_each_time(caplog):
    import warnings

    from urllib3.exceptions import InsecureRequestWarning

    from prtg_collector.netenv import internal_tls

    with caplog.at_level("WARNING", logger="prtg_collector"):
        assert internal_tls({"INTERNAL_TLS_VERIFY": "false"}) is False
    assert "Zertifikatsprüfung" in caplog.text
    with warnings.catch_warnings(record=True) as seen:
        warnings.warn("x", InsecureRequestWarning)
    assert seen == []
    caplog.clear()
    assert internal_tls({}) is True
    assert caplog.text == ""


def test_start_scripts_restart_the_service_when_marker_or_certificate_change():
    services = _compose()
    for name in ("influxdb", "grafana"):
        script = " ".join(services[name]["entrypoint"])
        assert f"/certs/{name}.enabled" in script and "cksum" in script
        assert "trap" in script and "kill" in script  # Dienst beenden, Docker startet ihn neu
        assert "$$" in script  # Compose ersetzt sonst $-Ausdrücke selbst
        assert "$(" not in script.replace("$$(", "")
        assert services[name]["restart"] == "unless-stopped"


@pytest.mark.parametrize("name", ["INFLUX_INTERNAL_URL", "INFLUX_URL", "GRAFANA_INTERNAL_URL"])
def test_verification_is_never_switched_off_automatically_for_an_explicit_address(tmp_path, name):
    (tmp_path / "influxdb.enabled").write_text("1")
    env = {"CERTS_DIR": str(tmp_path), name: "https://fremder-host.example.invalid:8086"}
    assert (
        internal_verify(env) is True
    )  # nur Containernamen im Compose-Netz werden ohne Prüfung angesprochen
    assert internal_verify({**env, "INTERNAL_TLS_VERIFY": "false"}) is False  # ausdrücklich gewollt


def test_compose_publishes_the_acme_challenge_port_of_the_portal():
    portal = _compose()["portal"]
    assert any(p.endswith(":8081") and "ACME_HTTP_PORT:-80" in p for p in portal["ports"])
    env = portal["environment"]
    assert env["ACME_HTTP_INTERNAL_PORT"] == "8081"
    assert "ACME_DIRECTORY_URL" in env


def test_datasource_raises_series_limit_for_wide_selections():
    g, calls = _grafana(True, existing=None)
    g.ensure_datasource(1, "prtg_k", "tok", "https://influxdb:8086", "org")
    [post] = [c for c in calls if c[0] == "POST"]
    assert post[2]["jsonData"]["maxSeries"] >= 5000


def test_datasource_without_series_limit_is_updated():
    existing = {
        "url": "https://influxdb:8086",
        "jsonData": {"defaultBucket": "prtg_k", "tlsSkipVerify": False},
    }
    g, calls = _grafana(True, existing)
    assert g.ensure_datasource(1, "prtg_k", "tok", "https://influxdb:8086", "org") is True
    assert any(c[0] == "PUT" for c in calls)
