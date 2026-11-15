import pytest
from stub_server import StubServer
from tlsmaterial import make_self_signed

from prtg_collector.config import ConfigError
from prtg_collector.portal.tlscheck import fetch_cert, validate_pair


def test_fetch_cert_returns_fingerprint_of_served_certificate():
    cert, key = make_self_signed(cn="prtg.example.invalid")
    with StubServer(tls=(cert, key)) as stub:
        port = int(stub.url.rsplit(":", 1)[1])
        info = fetch_cert("127.0.0.1", port)
    assert "prtg.example.invalid" in info.subject and len(info.sha256) == 64


def test_validate_pair_ok_and_errors():
    cert, key = make_self_signed()
    assert validate_pair(cert, key).sha256
    with pytest.raises(ConfigError, match="nicht lesbar"):
        validate_pair("kein pem", key)
    with pytest.raises(ConfigError, match="nicht lesbar"):
        validate_pair(cert, "kein pem")
