"""Ausstellung gegen Pebble, den Testserver des Let's-Encrypt-Projekts (pytest -m integration, braucht Docker).

Die Prüfung läuft echt: Pebble ruft die HTTP-01-Antwort über Port 5002 bei diesem Rechner ab."""

import shutil
import subprocess
import time
import uuid

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from prtg_collector.portal.acme import (
    AcmeError,
    AcmeLibBackend,
    ChallengeServer,
    ChallengeStore,
    generate_key_and_csr,
)

pytestmark = pytest.mark.integration
DIRECTORY = "https://localhost:14000/dir"
DOMAIN = "grafana.example.test"


@pytest.fixture(scope="module")
def pebble():
    if shutil.which("docker") is None:
        pytest.skip("Docker nicht verfügbar")
    name = "pebble-" + uuid.uuid4().hex[:6]
    run = subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-p", "14000:14000",
            "--add-host", f"{DOMAIN}:host-gateway",
            "-e", "PEBBLE_VA_NOSLEEP=1", "-e", "PEBBLE_WFE_NONCEREJECT=0",
            "-e", "PEBBLE_AUTHZREUSE=100",  # Erneuerung: gültige Prüfung desselben Kontos wird übernommen
            "ghcr.io/letsencrypt/pebble:latest",
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    if run.returncode != 0:
        pytest.skip(f"Pebble nicht startbar: {run.stderr[:100]}")
    try:
        for _ in range(40):
            try:
                if httpx.get(DIRECTORY, verify=False, timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            pytest.skip("Pebble antwortet nicht")
        yield
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.fixture
def responder():
    store = ChallengeStore()
    server = ChallengeServer(store, "0.0.0.0", 5002)
    server.start()
    yield store
    server.stop()


def _obtain(store, domain, account_key=None):
    key_pem, csr_pem = generate_key_and_csr(domain)
    chain, account = AcmeLibBackend(verify_ssl=False, timeout_s=30).obtain(
        domain=domain,
        email="admin@example.invalid",
        directory_url=DIRECTORY,
        account_key_pem=account_key,
        answers=store,
        csr_pem=csr_pem,
    )
    return key_pem, chain, account


def test_issues_a_certificate_for_the_requested_name(pebble, responder):
    key_pem, chain, account = _obtain(responder, DOMAIN)
    cert = x509.load_pem_x509_certificate(chain.encode())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == [DOMAIN]
    key = serialization.load_pem_private_key(key_pem, None)
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    assert account.startswith(b"-----BEGIN")
    assert len(responder) == 0  # Token sind nach der Ausstellung wieder entfernt


def test_second_issue_reuses_the_account_key(pebble, responder):
    _, _, account = _obtain(responder, DOMAIN)
    _, chain, again = _obtain(responder, DOMAIN, account_key=account)
    assert again == account and "BEGIN CERTIFICATE" in chain


def test_validation_failure_gives_a_german_hint_and_leaves_no_token(pebble, responder):
    with pytest.raises(AcmeError, match="Port 80"):
        _obtain(responder, "nirgendwo.example.test")
    assert len(responder) == 0
