import os
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stub_server import StubServer, obj
from tlsmaterial import make_self_signed

from prtg_collector.portal.app import create_app
from prtg_collector.portal.auth import Auth
from prtg_collector.portal.status import RunStatus
from prtg_collector.portal.store import ConfigStore, CoreInput
from prtg_collector.portal.testing import ConnectionTester

SECRET = "ein-langer-geheimer-schluessel-fuer-tests"
PASSWORD = "richtiges-passwort-123"
KEY = "PRTG-KEY-KLARTEXT-123"


@dataclass
class FakeStatus:
    runs: dict

    def last_runs(self):
        return self.runs


@pytest.fixture
def env(tmp_path):
    store = ConfigStore(tmp_path / "p.db", SECRET)
    store.add_customer("musterfirma", "Musterfirma GmbH")
    auth = Auth(store, "admin", PASSWORD)
    status = FakeStatus({})
    app = create_app(
        store,
        auth,
        ConnectionTester(),
        status=status,
        certs_dir=tmp_path / "certs",
        key_gid=os.getgid(),
    )
    client = TestClient(app, follow_redirects=False)
    return client, store, status


def _csrf(client, path="/"):
    html = client.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _login(client):
    r = client.post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.status_code == 303
    return client


def _add_core(store, url, **kw):
    data = {"id": "core-hq", "name": "Zentrale", "url": url, "api_key": KEY}
    store.add_core("musterfirma", CoreInput(**{**data, **kw}))


PAGES = ["/", "/customers/new", "/customers/musterfirma/cores/new", "/certificates", "/settings"]


@pytest.mark.parametrize("path", PAGES)
def test_requires_login_for_every_page(env, path):
    r = env[0].get(path)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_requires_login_for_every_post(env):
    client, store, _ = env
    _add_core(store, "http://127.0.0.1:1")
    for path in [
        "/customers",
        "/cores/core-hq/test",
        "/cores/core-hq/delete",
        "/certificates/grafana",
        "/certificates/grafana/mode",
    ]:
        assert client.post(path, data={}).status_code == 303


def test_login_page_wrong_password_shows_german_message(env):
    r = env[0].post("/login", data={"username": "admin", "password": "falsch"})
    assert r.status_code == 401 and "Anmeldung fehlgeschlagen" in r.text


def _session_cookie_header(client, url="/login", **headers):
    r = client.post(url, data={"username": "admin", "password": PASSWORD}, headers=headers)
    assert r.status_code == 303
    return r.headers["set-cookie"]


def test_session_cookie_is_secure_over_https_and_behind_a_tls_proxy(env):
    for kwargs in ({"url": "https://testserver/login"}, {"X-Forwarded-Proto": "https"}):
        cookie = _session_cookie_header(env[0], **kwargs)
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie


def test_session_cookie_over_plain_http_has_no_secure_flag_so_login_still_works(env):
    cookie = _session_cookie_header(env[0])
    assert "Secure" not in cookie and "HttpOnly" in cookie


def test_csrf_token_required_on_post(env):
    client = _login(env[0])
    r = client.post("/customers", data={"id": "neu", "name": "Neu"})
    assert r.status_code == 403
    r = client.post("/customers", data={"id": "neu", "name": "Neu", "csrf": "falsch"})
    assert r.status_code == 403


def test_create_customer_and_core_via_forms(env):
    client, store, _ = env
    _login(client)
    r = client.post(
        "/customers",
        data={"id": "beispiel", "name": "Beispiel", "csrf": _csrf(client, "/customers/new")},
    )
    assert r.status_code == 303
    form = {
        "id": "core-a",
        "name": "A",
        "url": "https://prtg.example.invalid:1616",
        "api_key": KEY,
        "verify_mode": "system",
        "interval_s": "60",
        "filter_tags": "web, db",
        "csrf": _csrf(client, "/customers/beispiel/cores/new"),
    }
    r = client.post("/customers/beispiel/cores", data=form)
    assert r.status_code == 303
    core = store.get_core("core-a")
    assert core.filters.tags == ["web", "db"] and core.has_key


def test_bad_core_input_shows_message_not_crash(env):
    client = _login(env[0])
    form = {
        "id": "Kein Slug",
        "name": "A",
        "url": "https://x.example.invalid",
        "csrf": _csrf(client, "/customers/musterfirma/cores/new"),
    }
    r = client.post("/customers/musterfirma/cores", data=form)
    assert r.status_code == 400 and "Kennung" in r.text


def test_api_key_never_in_rendered_html(env):
    client, store, _ = env
    _add_core(store, "https://prtg.example.invalid:1616")
    _login(client)
    for path in ["/", "/cores/core-hq/edit"]:
        assert KEY not in client.get(path).text
    assert "gespeichert" in client.get("/cores/core-hq/edit").text


def test_connection_test_reports_401_in_german(env):
    client, store, _ = env
    with StubServer() as stub:
        stub.fail_with = 401
        _add_core(store, stub.url)
        _login(client)
        r = client.post("/cores/core-hq/test", data={"csrf": _csrf(client, "/cores/core-hq/edit")})
    assert r.status_code == 200 and "API-Key abgelehnt" in r.text
    assert store.get_core("core-hq").last_test_ok is False


def test_successful_connection_test_is_saved_and_shown(env):
    client, store, _ = env
    probes = [obj(1, "CoreProbe", parent={"id": "0", "name": "H", "type": "REFERENCED_ROOT"})]
    with StubServer(probes=probes) as stub:
        _add_core(store, stub.url)
        _login(client)
        r = client.post("/cores/core-hq/test", data={"csrf": _csrf(client, "/cores/core-hq/edit")})
    assert "Verbindung erfolgreich" in r.text and "1 Probe" in r.text
    assert store.get_core("core-hq").last_test_ok is True


def test_unknown_certificate_offers_trust_with_fingerprint(env):
    client, store, _ = env
    cert, key = make_self_signed()
    with StubServer(tls=(cert, key)) as stub:
        _add_core(store, stub.url)
        _login(client)
        r = client.post("/cores/core-hq/test", data={"csrf": _csrf(client, "/cores/core-hq/edit")})
    assert "Diesem Zertifikat vertrauen" in r.text
    assert re.search(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}", r.text)


def test_trust_pins_fingerprint_and_next_test_passes(env):
    client, store, _ = env
    cert, key = make_self_signed()
    with StubServer(tls=(cert, key)) as stub:
        _add_core(store, stub.url)
        _login(client)
        r = client.post("/cores/core-hq/test", data={"csrf": _csrf(client, "/cores/core-hq/edit")})
        sha = re.search(r'name="sha256" value="([0-9a-f]{64})"', r.text).group(1)
        r = client.post(
            "/cores/core-hq/trust",
            data={"sha256": sha, "csrf": _csrf(client, "/cores/core-hq/edit")},
        )
        assert r.status_code == 200
        assert store.get_core("core-hq").verify_mode == "pinned"
        r = client.post("/cores/core-hq/test", data={"csrf": _csrf(client, "/cores/core-hq/edit")})
    assert "Verbindung erfolgreich" in r.text


def test_trust_with_wrong_fingerprint_is_refused(env):
    client, store, _ = env
    cert, key = make_self_signed()
    with StubServer(tls=(cert, key)) as stub:
        _add_core(store, stub.url)
        _login(client)
        r = client.post(
            "/cores/core-hq/trust",
            data={"sha256": "00" * 32, "csrf": _csrf(client, "/cores/core-hq/edit")},
        )
    assert r.status_code == 400 and store.get_core("core-hq").verify_mode == "system"


def test_changed_certificate_is_rejected_when_pinned(env):
    client, store, _ = env
    cert, key = make_self_signed()
    with StubServer(tls=(cert, key)) as stub:
        _add_core(store, stub.url, verify_mode="pinned", pinned_sha256="ab" * 32)
        _login(client)
        r = client.post("/cores/core-hq/test", data={"csrf": _csrf(client, "/cores/core-hq/edit")})
    assert "Fingerabdruck passt nicht" in r.text
    # Erneuertes Zertifikat: der neue Fingerabdruck wird gezeigt und lässt sich bewusst übernehmen
    assert "Diesem Zertifikat vertrauen" in r.text
    assert "hat sich geändert" in r.text


def test_tls_upload_accepts_valid_pair_and_writes_files(env, tmp_path):
    client = _login(env[0])
    cert, key = make_self_signed()
    r = client.post(
        "/certificates/grafana",
        data={"cert_pem": cert, "key_pem": key, "csrf": _csrf(client, "/certificates")},
    )
    assert r.status_code == 200 and "docker compose restart" in r.text
    assert (tmp_path / "certs" / "grafana.crt").read_text() == cert
    key_file = tmp_path / "certs" / "grafana.key"
    assert key_file.is_file()
    # Lesbar nur für Besitzer und die gemeinsame Gruppe (InfluxDB und Grafana), nie für alle
    assert key_file.stat().st_mode & 0o777 == 0o640
    assert key_file.stat().st_gid == os.getgid()
    assert "docker compose up -d" in r.text  # nennt auch die Umstellung der internen Adressen


def test_tls_upload_rejects_mismatched_key_and_expired_cert(env):
    client = _login(env[0])
    cert, _ = make_self_signed()
    _, other_key = make_self_signed()
    r = client.post(
        "/certificates/grafana",
        data={"cert_pem": cert, "key_pem": other_key, "csrf": _csrf(client, "/certificates")},
    )
    assert r.status_code == 400 and "gehören nicht zusammen" in r.text
    old_cert, old_key = make_self_signed(days=1, start_offset_days=-10)
    r = client.post(
        "/certificates/influxdb",
        data={"cert_pem": old_cert, "key_pem": old_key, "csrf": _csrf(client, "/certificates")},
    )
    assert r.status_code == 400 and "abgelaufen" in r.text


def test_core_card_shows_status_from_last_run(env):
    client, store, status = env
    _add_core(store, "https://prtg.example.invalid:1616")
    _login(client)
    assert "noch nie abgerufen" in client.get("/").text
    status.runs = {
        "core-hq": RunStatus(at="2026-11-11 12:00", errors=2, last_error="API-Key abgelehnt (401).")
    }
    page = client.get("/").text
    assert "API-Key abgelehnt (401)." in page and "2026-11-11 12:00" in page
    status.runs = {"core-hq": RunStatus(at="2026-11-11 12:01", errors=0, last_error=None)}
    assert "läuft" in client.get("/").text


def test_change_password_via_settings(env):
    client, _, _ = env
    _login(client)
    r = client.post(
        "/settings/password",
        data={"old": PASSWORD, "new": "neues-passwort-4567", "csrf": _csrf(client, "/settings")},
    )
    assert r.status_code == 200 and "Passwort geändert" in r.text
    r = client.post(
        "/settings/password",
        data={"old": "falsch", "new": "noch-ein-passwort-12", "csrf": _csrf(client, "/settings")},
    )
    assert r.status_code == 400


def test_delete_core(env):
    client, store, _ = env
    _add_core(store, "https://prtg.example.invalid:1616")
    _login(client)
    r = client.post(
        "/cores/core-hq/delete", data={"csrf": _csrf(client, "/"), "confirm": "core-hq"}
    )
    assert r.status_code == 303 and store.cores() == []


def test_security_headers_present(env):
    r = env[0].get("/login")
    assert (
        r.headers["x-frame-options"] == "DENY"
        and "default-src 'self'" in r.headers["content-security-policy"]
    )


def test_adding_customer_and_core_triggers_tenant_setup(tmp_path):
    calls = []
    store = ConfigStore(tmp_path / "p.db", SECRET)
    auth = Auth(store, "admin", PASSWORD)
    app = create_app(store, auth, ConnectionTester(), on_change=lambda: calls.append(1))
    client = _login(TestClient(app, follow_redirects=False))
    client.post(
        "/customers", data={"id": "neu", "name": "Neu", "csrf": _csrf(client, "/customers/new")}
    )
    assert len(calls) == 1
    form = {
        "id": "core-a",
        "name": "A",
        "url": "https://prtg.example.invalid:1616",
        "api_key": KEY,
        "csrf": _csrf(client, "/customers/neu/cores/new"),
    }
    client.post("/customers/neu/cores", data=form)
    assert len(calls) == 2


def test_malformed_ca_pem_is_refused_in_german(env):  # Befund 2
    client = _login(env[0])
    form = {
        "id": "core-a",
        "name": "A",
        "url": "https://prtg.example.invalid:1616",
        "api_key": KEY,
        "verify_mode": "ca",
        "ca_pem": "das ist kein PEM",
        "csrf": _csrf(client, "/customers/musterfirma/cores/new"),
    }
    r = client.post("/customers/musterfirma/cores", data=form)
    assert r.status_code == 400 and "CA-Zertifikat" in r.text


def test_connection_test_survives_unusable_client_construction(env):  # Befund 2
    _client, store, _ = env

    def boom(core, token):
        raise OSError("Datei weg")

    store.add_core(
        "musterfirma",
        CoreInput(id="core-hq", name="Z", url="https://x.example.invalid:1616", api_key=KEY),
    )
    app = create_app(store, Auth(store, "admin", PASSWORD), ConnectionTester(client_factory=boom))
    c = _login(TestClient(app, follow_redirects=False))
    r = c.post("/cores/core-hq/test", data={"csrf": _csrf(c, "/cores/core-hq/edit")})
    assert r.status_code == 200 and "Einstellungen dieses Cores sind unbrauchbar" in r.text


@pytest.mark.skipif(os.getuid() == 0, reason="root darf jede Gruppe setzen")
def test_tls_upload_names_the_problem_when_group_cannot_be_set(tmp_path):
    store = ConfigStore(tmp_path / "p.db", SECRET)
    auth = Auth(store, "admin", PASSWORD)
    app = create_app(
        store,
        auth,
        ConnectionTester(),
        status=FakeStatus({}),
        certs_dir=tmp_path / "certs",
        key_gid=0,  # Gruppe root: für einen normalen Benutzer nicht setzbar
    )
    client = _login(TestClient(app, follow_redirects=False))
    cert, key = make_self_signed()
    r = client.post(
        "/certificates/grafana",
        data={"cert_pem": cert, "key_pem": key, "csrf": _csrf(client, "/certificates")},
    )
    assert r.status_code == 500 and "TLS_KEY_GID" in r.text


def _upload(client, kind="grafana"):
    cert, key = make_self_signed()
    r = client.post(
        f"/certificates/{kind}",
        data={"cert_pem": cert, "key_pem": key, "csrf": _csrf(client, "/certificates")},
    )
    assert r.status_code == 200
    return cert


def _mode(client, kind, mode):
    return client.post(
        f"/certificates/{kind}/mode",
        data={"mode": mode, "csrf": _csrf(client, "/certificates")},
    )


def test_certificates_page_offers_a_mode_for_each_service(env):
    client = _login(env[0])
    page = client.get("/certificates").text
    for kind in ("grafana", "influxdb", "portal"):
        assert f'action="/certificates/{kind}/mode"' in page
    assert "ungesichert" in page and "Let's Encrypt" in page


def test_upload_switches_service_to_own_certificate_and_sets_marker(env, tmp_path):
    client, store, _ = env
    _login(client)
    _upload(client)
    assert store.get_tls_setting("grafana").mode == "own"
    assert (tmp_path / "certs" / "grafana.enabled").is_file()
    assert store.get_tls_setting("grafana").not_after  # Gültigkeit für die Anzeige


def test_switching_back_to_http_removes_marker_but_keeps_certificate(env, tmp_path):
    client, store, _ = env
    _login(client)
    _upload(client)
    r = _mode(client, "grafana", "http")
    assert r.status_code == 200 and "neu" in r.text  # Hinweis: Dienst startet neu
    assert store.get_tls_setting("grafana").mode == "http"
    assert not (tmp_path / "certs" / "grafana.enabled").exists()
    assert (tmp_path / "certs" / "grafana.crt").is_file()
    assert _mode(client, "grafana", "own").status_code == 200  # ohne neuen Upload


def test_own_certificate_without_upload_is_refused_in_german(env):
    client = _login(env[0])
    r = _mode(client, "influxdb", "own")
    assert r.status_code == 400 and "Kein Zertifikat vorhanden" in r.text


def test_mode_change_triggers_setup_and_rejects_unknown_service(env, tmp_path):
    store = env[1]
    calls = []
    # eigene App mit on_change
    from prtg_collector.portal.app import create_app

    app = create_app(
        store,
        Auth(store, "admin", PASSWORD),
        ConnectionTester(),
        certs_dir=tmp_path / "certs",
        on_change=lambda: calls.append(1),
    )
    c = _login(TestClient(app, follow_redirects=False))
    _upload(c)
    n = len(calls)
    assert _mode(c, "grafana", "http").status_code == 200 and len(calls) == n + 1
    assert _mode(c, "prtg", "http").status_code == 404


class _AcmeFake:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def obtain(self, *, domain, email, directory_url, account_key_pem, answers, csr_pem):
        from tlsmaterial import issue_for_csr

        self.calls.append((domain, email, directory_url))
        if self.error:
            raise self.error
        return issue_for_csr(csr_pem), b"KONTO"


@pytest.fixture
def acme_env(tmp_path):
    from prtg_collector.portal.acme import ChallengeStore
    from prtg_collector.portal.tlsmanager import TlsManager

    store = ConfigStore(tmp_path / "p.db", SECRET)
    backend = _AcmeFake()
    certs = tmp_path / "certs"
    manager = TlsManager(store, certs, backend, ChallengeStore(), key_gid=os.getgid())
    app = create_app(
        store,
        Auth(store, "admin", PASSWORD),
        ConnectionTester(),
        certs_dir=certs,
        key_gid=os.getgid(),
        tls=manager,
    )
    client = _login(TestClient(app, follow_redirects=False))
    return client, store, backend, certs


def _prefs(client, email="admin@example.invalid", terms=True, staging=True):
    data = {"email": email, "csrf": _csrf(client, "/certificates")}
    if terms:
        data["terms"] = "1"
    if staging:
        data["staging"] = "1"
    return client.post("/certificates/acme/settings", data=data)


def test_page_offers_lets_encrypt_with_test_environment_preselected_and_no_consent(acme_env):
    page = acme_env[0].get("/certificates").text
    assert re.search(r'<input[^>]*name="staging"[^>]*checked', page)
    assert not re.search(r'<input[^>]*name="terms"[^>]*checked', page)
    for kind in ("grafana", "influxdb", "portal"):
        assert f'action="/certificates/{kind}/acme"' in page
    assert "Nutzungsbedingungen" in page and "Port 80" in page


def test_lets_encrypt_settings_are_saved(acme_env):
    client, store, *_ = acme_env
    assert _prefs(client, staging=False).status_code == 200
    prefs = store.get_acme_prefs()
    assert (prefs.email, prefs.staging, prefs.terms_accepted) == (
        "admin@example.invalid",
        False,
        True,
    )


def test_lets_encrypt_settings_need_a_plausible_email(acme_env):
    client, store, *_ = acme_env
    r = _prefs(client, email="keine-adresse")
    assert r.status_code == 400 and "E-Mail" in r.text
    assert store.get_acme_prefs().email == ""


def _obtain(client, kind, domain):
    return client.post(
        f"/certificates/{kind}/acme",
        data={"domain": domain, "csrf": _csrf(client, "/certificates")},
    )


def test_obtaining_without_consent_is_refused_in_german(acme_env):
    client, _store, backend, _ = acme_env
    _prefs(client, terms=False)
    r = _obtain(client, "grafana", "grafana.example.invalid")
    assert r.status_code == 400 and "Nutzungsbedingungen" in r.text and backend.calls == []


def test_obtaining_installs_the_certificate_and_shows_validity(acme_env):
    client, store, backend, certs = acme_env
    _prefs(client)
    r = _obtain(client, "grafana", "Grafana.Example.Invalid")
    assert r.status_code == 200 and "Let's Encrypt" in r.text and "gültig bis" in r.text
    assert backend.calls[0][0] == "grafana.example.invalid" and "staging" in backend.calls[0][2]
    assert store.get_tls_setting("grafana").mode == "acme"
    assert (certs / "grafana.enabled").is_file()


def test_invalid_domain_and_backend_errors_are_shown_without_changing_state(acme_env):
    from prtg_collector.portal.acme import AcmeError

    client, store, backend, certs = acme_env
    _prefs(client)
    r = _obtain(client, "grafana", "192.0.2.5")
    assert r.status_code == 400 and "Name" in r.text
    backend.error = AcmeError("Let's Encrypt konnte den Namen nicht prüfen. Port 80 prüfen.")
    r = _obtain(client, "grafana", "grafana.example.invalid")
    assert r.status_code == 400 and "Port 80 prüfen" in r.text
    assert (
        store.get_tls_setting("grafana").mode == "http" and not (certs / "grafana.enabled").exists()
    )


def test_acme_posts_need_login_and_csrf(acme_env):
    client = acme_env[0]
    for path in ("/certificates/acme/settings", "/certificates/grafana/acme"):
        assert client.post(path, data={"domain": "x.example.invalid"}).status_code == 403
        anonymous = TestClient(client.app, follow_redirects=False)
        assert anonymous.post(path, data={}).status_code == 303


@pytest.mark.skipif(os.getuid() == 0, reason="root darf jede Gruppe setzen")
def test_failed_key_write_leaves_existing_pair_untouched(tmp_path):
    """F10: Schlägt das Ablegen fehl, bleiben Zertifikat und Schlüssel ein passendes Paar."""
    store = ConfigStore(tmp_path / "p.db", SECRET)
    auth = Auth(store, "admin", PASSWORD)

    def app_for(gid):
        return create_app(
            store,
            auth,
            ConnectionTester(),
            status=FakeStatus({}),
            certs_dir=tmp_path / "certs",
            key_gid=gid,
        )

    good = _login(TestClient(app_for(os.getgid()), follow_redirects=False))
    cert_a, key_a = make_self_signed()
    r = good.post(
        "/certificates/grafana",
        data={"cert_pem": cert_a, "key_pem": key_a, "csrf": _csrf(good, "/certificates")},
    )
    assert r.status_code == 200
    bad = _login(TestClient(app_for(0), follow_redirects=False))
    cert_b, key_b = make_self_signed()
    r = bad.post(
        "/certificates/grafana",
        data={"cert_pem": cert_b, "key_pem": key_b, "csrf": _csrf(bad, "/certificates")},
    )
    assert r.status_code == 500
    certs = tmp_path / "certs"
    assert (certs / "grafana.crt").read_text() == cert_a
    assert (certs / "grafana.key").read_text() == key_a
    assert not list(certs.glob(".*.tmp"))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://",
        "javascript:alert(1)",
        "ftp://x.example",
        "https://user:pw" + "@host.example",
        "https://host.example:0",
        "https://host.example:99999",
        "https:///nur-pfad",
    ],
)
def test_core_url_is_validated(url):
    from prtg_collector.portal.store import CoreInput

    with pytest.raises(ValueError):
        CoreInput(id="a", name="n", url=url)


def test_core_url_with_credentials_is_refused(env):
    client, store, _ = env
    _login(client)
    r = client.post(
        "/customers/musterfirma/cores",
        data={
            "id": "x",
            "name": "X",
            "url": "https://admin:geheim123" + "@prtg.example:1616",
            "api_key": KEY,
            "csrf": _csrf(client, "/customers/musterfirma/cores/new"),
        },
    )
    assert r.status_code == 400 and "keine Zugangsdaten" in r.text
    assert store.cores("musterfirma") == []


def test_login_runs_password_check_off_the_event_loop(env, monkeypatch):
    """F29: Die Passwortprüfung läuft in einem Arbeitsthread, nicht in der Ereignisschleife."""
    import asyncio

    from prtg_collector.portal import auth as auth_mod

    in_loop = []
    real = auth_mod.Auth.login

    def spy(self, username, password, ip=""):
        try:
            asyncio.get_running_loop()
            in_loop.append(True)
        except RuntimeError:
            in_loop.append(False)
        return real(self, username, password, ip)

    monkeypatch.setattr(auth_mod.Auth, "login", spy)
    client, _, _ = env
    client.post("/login", data={"username": "admin", "password": "falsch"})
    assert in_loop == [False]


def test_login_page_shows_the_logo_and_a_favicon_that_are_served(env):
    client = env[0]
    html = client.get("/login").text
    for path in ("/static/netmon24-logo-white.svg", "/static/netmon24-mark.svg"):
        assert path in html
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert 'alt="netmon24"' in html  # Bild trägt eine Textalternative


def test_readme_logo_files_exist():
    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text(encoding="utf-8")
    paths = re.findall(r'src="(docs/assets/[^"]+)"|srcset="(docs/assets/[^"]+)"', readme)
    files = {p for pair in paths for p in pair if p}
    assert files, "README zeigt kein Logo"
    assert all((root / f).is_file() for f in files)


def test_unknown_core_shows_a_german_error_page_not_a_bare_500(env):
    client, _, _ = env
    _login(client)
    r = client.get("/cores/gibt-es-nicht/edit")
    assert r.status_code == 404
    assert "gibt es nicht" in r.text and "<html" in r.text


def test_disk_full_while_saving_shows_a_german_error_page(env, monkeypatch):
    client, store, _ = env
    _login(client)
    token = _csrf(client, "/customers/new")

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "add_customer", boom)
    r = client.post("/customers", data={"csrf": token, "id": "neu", "name": "Neu"})
    assert r.status_code == 500
    assert "Speichern" in r.text and "Platz" in r.text


def test_users_can_be_added_and_deleted_in_the_portal(env):
    client, _, _ = env
    _login(client)
    token = _csrf(client, "/users")
    r = client.post(
        "/users", data={"csrf": token, "username": "zweite", "password": "zweites-passwort-123"}
    )
    assert r.status_code == 200 and "zweite" in r.text
    other = TestClient(client.app, follow_redirects=False)
    assert (
        other.post(
            "/login", data={"username": "zweite", "password": "zweites-passwort-123"}
        ).status_code
        == 303
    )
    r = client.post("/users/zweite/delete", data={"csrf": _csrf(client, "/users")})
    assert r.status_code == 200 and "gelöscht" in r.text
    r = client.post("/users/admin/delete", data={"csrf": _csrf(client, "/users")})
    assert r.status_code == 400  # eigenes Konto


def test_login_lock_follows_the_forwarded_address_only_when_the_proxy_is_trusted(env, tmp_path):
    client = env[0]
    for _ in range(5):
        client.post(
            "/login",
            data={"username": "admin", "password": "falsch"},
            headers={"x-forwarded-for": "203.0.113.9"},
        )
    # ohne vertrauten Proxy zählt die Adresse der Verbindung, der Kopf lässt sich nicht fälschen
    r = client.post(
        "/login",
        data={"username": "admin", "password": PASSWORD},
        headers={"x-forwarded-for": "198.51.100.1"},
    )
    assert r.status_code == 401  # alle Versuche kamen von derselben Verbindung

    store2 = ConfigStore(tmp_path / "p2.db", SECRET)
    app = create_app(
        store2, Auth(store2, "admin", PASSWORD), ConnectionTester(), trust_forwarded=True
    )
    c = TestClient(app, follow_redirects=False)
    for _ in range(5):
        c.post(
            "/login",
            data={"username": "admin", "password": "falsch"},
            headers={"x-forwarded-for": "203.0.113.9"},
        )
    ok = c.post(
        "/login",
        data={"username": "admin", "password": PASSWORD},
        headers={"x-forwarded-for": "198.51.100.1"},
    )
    assert ok.status_code == 303


class FakePurger:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def delete_core(self, customer_id, core_id):
        self._do(("core", customer_id, core_id))

    def delete_customer(self, customer_id):
        self._do(("customer", customer_id))

    def _do(self, call):
        if self.fail:
            raise OSError("InfluxDB nicht erreichbar")
        self.calls.append(call)


def _purge_env(tmp_path, purger):
    store = ConfigStore(tmp_path / "p.db", SECRET)
    store.add_customer("musterfirma", "Musterfirma GmbH")
    _add_core(store, "http://127.0.0.1:1")
    app = create_app(store, Auth(store, "admin", PASSWORD), ConnectionTester(), purger=purger)
    return _login(TestClient(app, follow_redirects=False)), store


def test_deleting_a_core_needs_the_typed_id_and_deletes_its_data(tmp_path):
    purger = FakePurger()
    client, store = _purge_env(tmp_path, purger)
    token = _csrf(client, "/cores/core-hq/edit")
    r = client.post("/cores/core-hq/delete", data={"csrf": token, "confirm": "falsch"})
    assert r.status_code == 400 and purger.calls == [] and store.get_core("core-hq")
    r = client.post("/cores/core-hq/delete", data={"csrf": token, "confirm": "core-hq"})
    assert r.status_code == 303
    assert purger.calls == [("core", "musterfirma", "core-hq")]
    assert store.cores("musterfirma") == []


def test_deleting_a_customer_removes_data_and_entries(tmp_path):
    purger = FakePurger()
    client, store = _purge_env(tmp_path, purger)
    token = _csrf(client, "/customers/musterfirma/delete")
    assert "unwiderruflich" in client.get("/customers/musterfirma/delete").text
    assert (
        client.post(
            "/customers/musterfirma/delete", data={"csrf": token, "confirm": "x"}
        ).status_code
        == 400
    )
    r = client.post("/customers/musterfirma/delete", data={"csrf": token, "confirm": "musterfirma"})
    assert r.status_code == 303 and purger.calls == [("customer", "musterfirma")]
    assert store.customers() == [] and store.cores() == []


def test_failed_data_deletion_keeps_the_entry_so_it_can_be_retried(tmp_path):
    client, store = _purge_env(tmp_path, FakePurger(fail=True))
    token = _csrf(client, "/cores/core-hq/edit")
    r = client.post("/cores/core-hq/delete", data={"csrf": token, "confirm": "core-hq"})
    assert r.status_code == 502 and "bleibt eingetragen" in r.text
    assert store.get_core("core-hq")
    r = client.post("/customers/musterfirma/delete", data={"csrf": token, "confirm": "musterfirma"})
    assert r.status_code == 502 and store.customer_name("musterfirma")
