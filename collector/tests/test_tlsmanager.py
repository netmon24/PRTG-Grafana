import os
from datetime import UTC, datetime, timedelta

import pytest
from tlsmaterial import issue_for_csr

from prtg_collector.portal.acme import PRODUCTION, STAGING, AcmeError, ChallengeStore
from prtg_collector.portal.store import ConfigStore
from prtg_collector.portal.tlsmanager import TlsManager

SECRET = "ein-langer-geheimer-schluessel-fuer-tests"
DOMAIN = "grafana.example.invalid"


class FakeBackend:
    def __init__(self, days=90, error=None):
        self.calls, self.days, self.error = [], days, error

    def obtain(self, *, domain, email, directory_url, account_key_pem, answers, csr_pem):
        self.calls.append(
            {
                "domain": domain,
                "email": email,
                "directory": directory_url,
                "account_key": account_key_pem,
            }
        )
        if self.error:
            raise self.error
        return issue_for_csr(csr_pem, self.days), account_key_pem or b"KONTO-SCHLUESSEL-1"


@pytest.fixture
def parts(tmp_path):
    store = ConfigStore(tmp_path / "p.db", SECRET)
    store.set_acme_prefs("admin@example.invalid", staging=True, terms_accepted=True)
    changed, installed = [], []
    backend = FakeBackend()
    mgr = TlsManager(
        store,
        tmp_path / "certs",
        backend,
        ChallengeStore(),
        key_gid=os.getgid(),
        on_change=lambda: changed.append(1),
        after_install=installed.append,
    )
    return mgr, store, backend, tmp_path / "certs", changed, installed


def test_prefs_default_to_staging_without_consent(tmp_path):
    prefs = ConfigStore(tmp_path / "p.db", SECRET).get_acme_prefs()
    assert (prefs.email, prefs.staging, prefs.terms_accepted) == ("", True, False)


def test_email_and_consent_are_required_before_anything_is_requested(parts):
    mgr, store, backend, *_ = parts
    store.set_acme_prefs("", staging=True, terms_accepted=True)
    with pytest.raises(AcmeError, match="E-Mail"):
        mgr.obtain("grafana", DOMAIN)
    store.set_acme_prefs("admin@example.invalid", staging=True, terms_accepted=False)
    with pytest.raises(AcmeError, match="Nutzungsbedingungen"):
        mgr.obtain("grafana", DOMAIN)
    assert backend.calls == []


def test_invalid_domain_is_refused_and_nothing_is_written(parts):
    mgr, store, backend, certs, *_ = parts
    with pytest.raises(AcmeError, match="Name"):
        mgr.obtain("grafana", "192.0.2.1")
    assert backend.calls == [] and not certs.exists()
    assert store.get_tls_setting("grafana").mode == "http"


def test_success_installs_the_certificate_like_an_upload(parts):
    mgr, store, _backend, certs, changed, installed = parts
    not_after = mgr.obtain("grafana", DOMAIN)
    assert not_after > datetime.now(UTC) + timedelta(days=80)
    assert (certs / "grafana.crt").read_text().startswith("-----BEGIN CERTIFICATE")
    key = certs / "grafana.key"
    assert key.stat().st_mode & 0o777 == 0o640 and key.stat().st_gid == os.getgid()
    setting = store.get_tls_setting("grafana")
    assert (setting.mode, setting.domain, setting.last_error) == ("acme", DOMAIN, None)
    assert setting.not_after and (certs / "grafana.enabled").is_file()
    assert changed == [1] and installed == ["grafana"]


def test_staging_is_the_default_and_production_is_a_deliberate_choice(parts):
    mgr, store, backend, *_ = parts
    mgr.obtain("grafana", DOMAIN)
    assert backend.calls[-1]["directory"] == STAGING
    store.set_acme_prefs("admin@example.invalid", staging=False, terms_accepted=True)
    mgr.obtain("grafana", DOMAIN)
    assert backend.calls[-1]["directory"] == PRODUCTION


def test_account_key_is_kept_encrypted_and_reused(parts, tmp_path):
    mgr, _store, backend, *_ = parts
    mgr.obtain("grafana", DOMAIN)
    mgr.obtain("influxdb", "influx.example.invalid")
    assert backend.calls[0]["account_key"] is None
    assert backend.calls[1]["account_key"] == b"KONTO-SCHLUESSEL-1"
    raw = (tmp_path / "p.db").read_bytes()
    assert b"KONTO-SCHLUESSEL-1" not in raw  # nur verschlüsselt gespeichert


def test_failure_keeps_the_old_certificate_and_records_the_error(parts):
    mgr, store, backend, certs, changed, _ = parts
    mgr.obtain("grafana", DOMAIN)
    before = (certs / "grafana.crt").read_text()
    backend.error = AcmeError("Let's Encrypt konnte den Namen nicht prüfen.")
    with pytest.raises(AcmeError):
        mgr.obtain("grafana", DOMAIN)
    assert (certs / "grafana.crt").read_text() == before
    setting = store.get_tls_setting("grafana")
    assert setting.mode == "acme" and (certs / "grafana.enabled").is_file()
    assert "nicht prüfen" in setting.last_error
    assert changed == [1]  # der Fehlversuch löst keinen Neustart aus


def test_renewal_only_touches_acme_certificates_close_to_expiry(parts):
    mgr, store, backend, _certs, *_ = parts
    soon = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    later = (datetime.now(UTC) + timedelta(days=60)).isoformat()
    for kind, domain in (("grafana", DOMAIN), ("influxdb", "influx.example.invalid")):
        mgr.obtain(kind, domain)
    store.record_tls_result("grafana", not_after=soon)
    store.record_tls_result("influxdb", not_after=later)
    backend.calls.clear()
    assert mgr.renew_due(days=30) == ["grafana"]
    assert [c["domain"] for c in backend.calls] == [DOMAIN]
    assert store.get_tls_setting("grafana").not_after > soon  # Gültigkeit ist neu


def test_renewal_ignores_own_certificates_and_survives_errors(parts):
    mgr, store, backend, *_ = parts
    mgr.obtain("grafana", DOMAIN)
    store.record_tls_result(
        "grafana", not_after=(datetime.now(UTC) + timedelta(days=1)).isoformat()
    )
    backend.error = AcmeError("Port 80 nicht erreichbar.")
    assert mgr.renew_due(days=30) == []  # Fehler werden gespeichert, nicht geworfen
    assert "Port 80" in store.get_tls_setting("grafana").last_error
    store.set_tls_setting("grafana", "own")
    backend.calls.clear()
    assert mgr.renew_due(days=30) == [] and backend.calls == []
