"""Verschlüsselung der gespeicherten Geheimnisse (API-Keys, Schlüsseldateien)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..config import ConfigError

MIN_SECRET_LEN = 16


class WrongKeyError(ConfigError):
    """PORTAL_SECRET_KEY passt nicht zu den gespeicherten Daten."""


class Crypto:
    def __init__(self, secret: str) -> None:
        if len(secret) < MIN_SECRET_LEN:
            raise ConfigError(
                f"PORTAL_SECRET_KEY ist zu kurz (mindestens {MIN_SECRET_LEN} Zeichen)."
            )
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(key)

    def encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            raise WrongKeyError(
                "PORTAL_SECRET_KEY passt nicht zu den gespeicherten Daten. "
                "Den ursprünglichen Schlüssel wieder in die .env eintragen."
            ) from None
