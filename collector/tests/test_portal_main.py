import logging

from prtg_collector.portal import __main__ as portal_main


def test_portal_start_fails_without_secret_key(monkeypatch, caplog):
    for name in ("PORTAL_SECRET_KEY", "PORTAL_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level(logging.ERROR):
        assert portal_main.main() == 2
    assert "PORTAL_SECRET_KEY" in caplog.text


def test_portal_start_fails_with_short_admin_password_on_first_start(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PORTAL_SECRET_KEY", "ein-langer-geheimer-schluessel-fuer-tests")
    monkeypatch.setenv("PORTAL_ADMIN_PASSWORD", "kurz")
    monkeypatch.setenv("PORTAL_DATA_DIR", str(tmp_path))
    with caplog.at_level(logging.ERROR):
        assert portal_main.main() == 2
    assert "PORTAL_ADMIN_PASSWORD" in caplog.text


def test_build_app_wires_setup_on_change_and_status(monkeypatch, tmp_path):
    import re
    import time

    from fastapi.testclient import TestClient

    from prtg_collector.setup_tenants import SetupReport

    runs = []
    env = {
        "PORTAL_SECRET_KEY": "ein-langer-geheimer-schluessel-fuer-tests",
        "PORTAL_ADMIN_PASSWORD": "richtiges-passwort-123",
        "PORTAL_DATA_DIR": str(tmp_path),
        "CERTS_DIR": str(tmp_path / "certs"),
    }
    app = portal_main.build_app(
        env, run_setup=lambda e: runs.append(1) or SetupReport(problems=["Kunde x: kaputt"])
    )
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"username": "admin", "password": "richtiges-passwort-123"})
    csrf = re.search(r'name="csrf" value="([^"]+)"', client.get("/customers/new").text).group(1)
    client.post("/customers", data={"id": "neu", "name": "Neu", "csrf": csrf})
    deadline = time.time() + 3
    while len(runs) < 2 and time.time() < deadline:  # Start-Abgleich plus Änderung
        time.sleep(0.02)
    assert len(runs) >= 2
    assert "Kunde x: kaputt" in client.get("/").text


def _env(tmp_path, **extra):
    return {
        "PORTAL_SECRET_KEY": "ein-langer-geheimer-schluessel-fuer-tests",
        "PORTAL_ADMIN_PASSWORD": "richtiges-passwort-123",
        "PORTAL_DATA_DIR": str(tmp_path),
        "CERTS_DIR": str(tmp_path / "certs"),
        **extra,
    }


def test_portal_uses_tls_only_with_marker_certificate_and_key(tmp_path):
    certs = tmp_path / "certs"
    certs.mkdir()
    env = _env(tmp_path)
    assert portal_main.ssl_files(env) is None
    (certs / "portal.crt").write_text("c")
    (certs / "portal.key").write_text("k")
    assert portal_main.ssl_files(env) is None  # ohne Marker bleibt das Portal bei http
    (certs / "portal.enabled").write_text("1")
    assert portal_main.ssl_files(env) == (str(certs / "portal.crt"), str(certs / "portal.key"))
    (certs / "portal.key").write_text("")
    assert portal_main.ssl_files(env) is None  # leerer Schlüssel: lieber http als gar nicht starten


def test_build_app_provides_the_certificate_manager_and_challenge_store(tmp_path):
    app = portal_main.build_app(_env(tmp_path))
    assert app.state.tls_manager is not None and len(app.state.challenge_store) == 0


def test_challenge_server_listens_on_the_configured_port(tmp_path):
    import http.client

    app = portal_main.build_app(_env(tmp_path, ACME_HTTP_INTERNAL_PORT="0"))
    app.state.challenge_store.put("t" * 20, "antwort")
    server = portal_main.start_challenge_server(_env(tmp_path, ACME_HTTP_INTERNAL_PORT="0"), app)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        conn.request("GET", "/.well-known/acme-challenge/" + "t" * 20)
        resp = conn.getresponse()
        assert resp.status == 200 and resp.read() == b"antwort"
    finally:
        server.stop()


def test_portal_restarts_itself_after_its_own_certificate_changes(tmp_path):
    import re

    from fastapi.testclient import TestClient
    from tlsmaterial import make_self_signed

    restarts = []
    app = portal_main.build_app(_env(tmp_path), restart=lambda: restarts.append(1))
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"username": "admin", "password": "richtiges-passwort-123"})

    def post(path, data):
        page = client.get("/certificates").text
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        return client.post(path, data={**data, "csrf": csrf})

    cert, key = make_self_signed()
    assert post("/certificates/grafana", {"cert_pem": cert, "key_pem": key}).status_code == 200
    assert restarts == []  # andere Dienste startet Docker, nicht das Portal
    assert post("/certificates/portal", {"cert_pem": cert, "key_pem": key}).status_code == 200
    assert post("/certificates/portal/mode", {"mode": "http"}).status_code == 200
    assert restarts == [1, 1]
