import http.client

import pytest
from acme import errors as acme_errors
from acme import messages
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from prtg_collector.portal.acme import (
    AcmeError,
    ChallengeServer,
    ChallengeStore,
    generate_key_and_csr,
    translate_error,
    validate_domain,
)

TOKEN = "evaGxfADs6pSRb2LAv9IZf17Dt3juxGJ-PCt92wr-oA"
AUTH = TOKEN + ".nP1qzpXGymHBrUEepNY9HCfk4bLn6S5wgeEOQAuEAs4"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("grafana.example.invalid", "grafana.example.invalid"),
        ("  Grafana.Example.Invalid ", "grafana.example.invalid"),
        ("a-b.c1.example.invalid", "a-b.c1.example.invalid"),
    ],
)
def test_valid_domains_are_normalised(text, expected):
    assert validate_domain(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "localhost",  # kein öffentlicher Name
        "192.0.2.10",  # IP-Adresse
        "2001:db8::1",
        "*.example.invalid",  # Platzhalter braucht DNS-01
        "https://grafana.example.invalid",
        "grafana.example.invalid/pfad",
        "grafana example.invalid",
        "grafana.example.invalid:3000",
        "-grafana.example.invalid",
        "grafana..example.invalid",
        "grafana.example.123",  # reine Zahl als letzte Stelle
        "ä.example.invalid",
        ("a" * 64) + ".example.invalid",
        ".".join(["abcdefghij"] * 30),
    ],
)
def test_invalid_domains_are_refused_with_a_hint(text):
    with pytest.raises(AcmeError, match="Name"):
        validate_domain(text)


def test_challenge_store_returns_only_known_tokens_and_forgets_removed_ones():
    store = ChallengeStore()
    assert store.get(TOKEN) is None
    store.put(TOKEN, AUTH)
    assert store.get(TOKEN) == AUTH
    assert len(store) == 1
    store.remove(TOKEN)
    assert store.get(TOKEN) is None and len(store) == 0


def _request(server, method, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


@pytest.fixture
def server():
    store = ChallengeStore()
    store.put(TOKEN, AUTH)
    srv = ChallengeServer(store, "127.0.0.1", 0)
    srv.start()
    yield srv
    srv.stop()


def test_server_answers_a_known_token_with_the_key_authorization(server):
    status, body = _request(server, "GET", f"/.well-known/acme-challenge/{TOKEN}")
    assert status == 200 and body.decode() == AUTH


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/acme-challenge/unbekannt1234567890",
        "/.well-known/acme-challenge/",
        "/.well-known/acme-challenge/../../etc/passwd",
        f"/.well-known/acme-challenge/{TOKEN}/zusatz",
        "/.well-known/acme-challenge/" + "a" * 5000,
        "/",
        "/index.html",
        "/.well-known/acme-challenge/%2e%2e%2fetc",
    ],
)
def test_server_answers_everything_else_with_404(server, path):
    assert _request(server, "GET", path)[0] == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_server_refuses_other_methods(server, method):
    assert _request(server, method, f"/.well-known/acme-challenge/{TOKEN}")[0] == 405


def test_csr_contains_the_domain_and_matches_the_returned_key():
    key_pem, csr_pem = generate_key_and_csr("grafana.example.invalid")
    csr = x509.load_pem_x509_csr(csr_pem)
    key = serialization.load_pem_private_key(key_pem, None)
    assert csr.is_signature_valid
    assert csr.public_key().public_numbers() == key.public_key().public_numbers()
    names = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["grafana.example.invalid"]


def test_each_issue_gets_a_new_private_key():
    assert (
        generate_key_and_csr("a.example.invalid")[0] != generate_key_and_csr("a.example.invalid")[0]
    )


def test_errors_are_translated_to_german_hints():
    rate = messages.Error(typ="urn:ietf:params:acme:error:rateLimited", detail="zu viele")
    assert "Ratenbegrenzung" in translate_error(rate).message
    assert "Testumgebung" in translate_error(rate).message
    assert "erreichbar" in translate_error(RequestsConnectionError("x")).message
    assert "erreichbar" in translate_error(Timeout("x")).message
    assert "Port 80" in translate_error(acme_errors.ValidationError([])).message
    unknown = translate_error(RuntimeError("geheim: Token abc"))
    assert (
        "geheim" not in unknown.message and "Token" not in unknown.message
    )  # nie rohe Meldungen durchreichen
    assert translate_error(AcmeError("schon deutsch")).message == "schon deutsch"
