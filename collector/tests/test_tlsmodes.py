import pytest
from tlsmaterial import make_self_signed

from prtg_collector.config import ConfigError
from prtg_collector.portal.store import ConfigStore
from prtg_collector.portal.tlsmodes import SERVICES, sync_markers

SECRET = "ein-langer-geheimer-schluessel-fuer-tests"


def _store(tmp_path) -> ConfigStore:
    return ConfigStore(tmp_path / "portal.db", SECRET)


def _with_cert(store, kind="grafana"):
    cert, key = make_self_signed()
    store.save_tls_asset(kind, cert, key)


def test_services_are_grafana_influxdb_portal():
    assert SERVICES == ("grafana", "influxdb", "portal")


def test_default_mode_is_plain_http(tmp_path):
    s = _store(tmp_path).get_tls_setting("grafana")
    assert (s.mode, s.domain, s.not_after, s.last_error) == ("http", None, None, None)


def test_mode_and_domain_roundtrip(tmp_path):
    store = _store(tmp_path)
    _with_cert(store)
    store.set_tls_setting("grafana", "own")
    assert store.get_tls_setting("grafana").mode == "own"
    store.set_tls_setting("grafana", "acme", "grafana.example.invalid")
    s = store.get_tls_setting("grafana")
    assert (s.mode, s.domain) == ("acme", "grafana.example.invalid")


def test_unknown_service_and_mode_are_refused(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ConfigError):
        store.get_tls_setting("prtg")
    with pytest.raises(ConfigError):
        store.set_tls_setting("grafana", "ftp")


def test_own_and_acme_need_a_certificate(tmp_path):
    store = _store(tmp_path)
    for mode in ("own", "acme"):
        with pytest.raises(ConfigError, match="Kein Zertifikat vorhanden"):
            store.set_tls_setting("grafana", mode)
    assert store.get_tls_setting("grafana").mode == "http"


def test_result_is_recorded(tmp_path):
    store = _store(tmp_path)
    store.record_tls_result("portal", error="Port 80 nicht erreichbar")
    assert store.get_tls_setting("portal").last_error == "Port 80 nicht erreichbar"
    store.record_tls_result("portal", not_after="2027-01-01T00:00:00+00:00")
    s = store.get_tls_setting("portal")
    assert s.not_after == "2027-01-01T00:00:00+00:00" and s.last_error is None and s.last_ok_at


def test_markers_follow_the_mode_and_certificates_stay(tmp_path):
    store, certs = _store(tmp_path), tmp_path / "certs"
    _with_cert(store)
    (certs).mkdir()
    (certs / "grafana.crt").write_text("x")
    store.set_tls_setting("grafana", "own")
    sync_markers(certs, store)
    assert (certs / "grafana.enabled").is_file()
    assert not (certs / "influxdb.enabled").exists()
    store.set_tls_setting("grafana", "http")
    sync_markers(certs, store)
    assert not (certs / "grafana.enabled").exists()
    assert (certs / "grafana.crt").read_text() == "x"  # Zurückschalten braucht keine Neuausstellung
