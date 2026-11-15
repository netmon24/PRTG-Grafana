"""Anmeldung des Portals: Passwort-Hash, Sitzungen, Sperre nach Fehlversuchen."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Callable

from .store import ConfigStore

ITERATIONS = 200_000
MAX_FAILS = 5
LOCK_S = 300
SESSION_S = 8 * 3600
MIN_PASSWORD_LEN = 12
MAX_USERNAME_LEN = 64  # längere Namen werden gekürzt: kein Speicher-Leck durch riesige Eingaben
MAX_TRACKED = 10_000  # Obergrenze für gemerkte Fehlversuche
PRUNE_AT = 1_000  # ab dieser Zahl werden abgelaufene Einträge aufgeräumt
USERNAME = re.compile(r"^[a-z0-9._-]{1,32}$")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest)


class Auth:
    def __init__(
        self,
        store: ConfigStore,
        bootstrap_user: str,
        bootstrap_password: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store, self._clock = store, clock
        if not store.admin_names():  # erster Start: Zugang aus der .env übernehmen
            store.set_admin_hash(bootstrap_user, hash_password(bootstrap_password))
        self._dummy_hash = hash_password(
            "x"
        )  # einmal berechnet: unbekannter Name kostet einen Lauf
        self._fails: dict[str, tuple[int, float]] = {}
        self._sessions: dict[str, float] = {}
        self._users: dict[str, str] = {}

    def _locked(self, username: str) -> bool:
        count, until = self._fails.get(username, (0, 0.0))
        return count >= MAX_FAILS and self._clock() < until

    def _record_failure_cleanup(self) -> None:
        if len(self._fails) < PRUNE_AT:
            return
        now = self._clock()
        for name in [n for n, (_, until) in self._fails.items() if until <= now]:
            del self._fails[name]
        while len(self._fails) >= MAX_TRACKED:  # Rest: älteste Einträge zuerst verwerfen
            del self._fails[next(iter(self._fails))]

    def login(self, username: str, password: str, ip: str = "") -> str | None:
        """Gesperrt wird die Herkunft (IP-Adresse), nicht das Konto: Fremde sperren so keinen Admin aus."""
        username = username[:MAX_USERNAME_LEN]
        key = ip[:MAX_USERNAME_LEN] or "-"
        if self._locked(key):
            return None
        stored = self._store.admin_hash(username)
        ok = verify_password(password, stored or self._dummy_hash)
        if not (ok and stored):
            if key not in self._fails:
                self._record_failure_cleanup()
            count = self._fails.get(key, (0, 0.0))[0]
            count = 1 if count >= MAX_FAILS and self._clock() >= self._fails[key][1] else count + 1
            self._fails[key] = (count, self._clock() + LOCK_S)
            return None
        self._fails.pop(key, None)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = self._clock() + SESSION_S
        self._users[token] = username
        return token

    def check(self, token: str | None) -> bool:
        expires = self._sessions.get(token or "")
        if expires is None or self._clock() > expires:
            self._sessions.pop(token or "", None)
            return False
        return True

    def username(self, token: str | None) -> str | None:
        return self._users.get(token or "") if self.check(token) else None

    def logout(self, token: str | None) -> None:
        self._sessions.pop(token or "", None)
        self._users.pop(token or "", None)

    def change_password(self, username: str, old: str, new: str, keep: str | None = None) -> bool:
        stored = self._store.admin_hash(username)
        if not stored or not verify_password(old, stored) or len(new) < MIN_PASSWORD_LEN:
            return False
        self._store.set_admin_hash(username, hash_password(new))
        for token in [t for t, user in self._users.items() if user == username and t != keep]:
            self.logout(token)  # fremde Sitzungen mit dem alten Passwort enden
        return True

    # --- Benutzerverwaltung ---
    def users(self) -> list[str]:
        return self._store.admin_names()

    def add_user(self, username: str, password: str) -> bool:
        if (
            not USERNAME.match(username)
            or len(password) < MIN_PASSWORD_LEN
            or self._store.admin_hash(username) is not None
        ):
            return False
        self._store.set_admin_hash(username, hash_password(password))
        return True

    def delete_user(self, username: str, current: str) -> bool:
        """Das eigene Konto und der letzte Administrator bleiben bestehen."""
        names = self._store.admin_names()
        if username == current or username not in names or len(names) <= 1:
            return False
        self._store.delete_admin(username)
        for token in [t for t, user in self._users.items() if user == username]:
            self.logout(token)
        return True
