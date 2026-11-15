"""Zertifikate lesen und prüfen: Fingerabdruck eines Cores, Zertifikat/Schlüssel-Paare hochladen."""

from __future__ import annotations

import datetime as dt
import hashlib
import socket
import ssl
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from ..config import ConfigError


@dataclass(frozen=True)
class CertInfo:
    subject: str
    issuer: str
    not_after: dt.datetime
    sha256: str  # Hexadezimal ohne Doppelpunkte, Fingerabdruck des DER-Zertifikats

    @property
    def sha256_pretty(self) -> str:
        return ":".join(self.sha256[i : i + 2] for i in range(0, len(self.sha256), 2)).upper()


def _info(cert: x509.Certificate) -> CertInfo:
    return CertInfo(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        not_after=cert.not_valid_after_utc,
        sha256=hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest(),
    )


def fetch_cert(host: str, port: int, timeout: float = 5.0) -> CertInfo:
    """Holt das Zertifikat, das der Server anbietet, ohne es zu prüfen (nur zur Anzeige)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=host) as tls,
        ):
            der = tls.getpeercert(binary_form=True)
    except OSError:
        raise ConfigError(f"Zertifikat von {host}:{port} nicht abrufbar.") from None
    return _info(x509.load_der_x509_certificate(der or b""))


def validate_pair(cert_pem: str, key_pem: str) -> CertInfo:
    """Prüft, ob Zertifikat und Schlüssel zusammenpassen und das Zertifikat gültig ist."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
    except ValueError:
        raise ConfigError("Das Zertifikat ist nicht lesbar (PEM-Format erwartet).") from None
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except (ValueError, TypeError):
        raise ConfigError(
            "Der Schlüssel ist nicht lesbar oder durch ein Passwort geschützt."
        ) from None
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    pub = serialization.Encoding.DER
    if key.public_key().public_bytes(pub, spki) != cert.public_key().public_bytes(pub, spki):
        raise ConfigError("Zertifikat und Schlüssel gehören nicht zusammen.")
    now = dt.datetime.now(dt.UTC)
    if cert.not_valid_after_utc < now:
        raise ConfigError("Das Zertifikat ist abgelaufen.")
    if cert.not_valid_before_utc > now:
        raise ConfigError("Das Zertifikat ist noch nicht gültig.")
    return _info(cert)
