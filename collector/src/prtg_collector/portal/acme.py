"""ACME (Let's Encrypt) für das Portal: Namensprüfung, Antwortserver für HTTP-01 und der Ablauf der Ausstellung."""

from __future__ import annotations

import ipaddress
import logging
import re
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from acme import challenges, client, crypto_util, messages
from acme import errors as acme_errors
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from josepy.jwk import JWKRSA
from requests.exceptions import RequestException

log = logging.getLogger("prtg_collector.portal")

USER_AGENT = "netmon24-prtg-grafana"
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_PREFIX = "/.well-known/acme-challenge/"
PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"
STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"


class AcmeError(Exception):
    """Fehler beim Besorgen eines Zertifikats; `message` ist deutsch, mit nächstem Schritt, ohne Geheimnisse."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def validate_domain(text: str) -> str:
    """Gibt den Namen kleingeschrieben zurück. Erlaubt sind öffentliche Namen mit mindestens einem Punkt.

    Keine IP-Adressen, keine Platzhalter (bräuchten DNS-01), keine Adressen mit Schema, Port oder Pfad."""
    name = (text or "").strip().lower()
    hint = (
        "Der Name ist ungültig. Bitte nur den Namen eintragen, zum Beispiel grafana.firma.de "
        "(ohne https://, Port, Pfad, Platzhalter oder IP-Adresse)."
    )
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise AcmeError(hint)
    labels = name.split(".")
    if (
        len(name) > 253
        or len(labels) < 2
        or not all(_LABEL.match(label) for label in labels)
        or labels[-1].isdigit()
    ):
        raise AcmeError(hint)
    return name


class ChallengeStore:
    """Offene HTTP-01-Antworten: Token → Schlüsselautorisierung."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, str] = {}

    def put(self, token: str, key_authorization: str) -> None:
        with self._lock:
            self._items[token] = key_authorization

    def get(self, token: str) -> str | None:
        with self._lock:
            return self._items.get(token)

    def remove(self, token: str) -> None:
        with self._lock:
            self._items.pop(token, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class ChallengeServer:
    """Beantwortet ausschließlich `GET /.well-known/acme-challenge/<Token>` für offene Token. Alles andere: 404."""

    def __init__(self, store: ChallengeStore, host: str, port: int) -> None:
        handler = self._handler(store)
        self._srv = ThreadingHTTPServer((host, port), handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._srv.server_port)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    @staticmethod
    def _handler(store: ChallengeStore) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: bytes = b"") -> None:
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                token = path[len(_PREFIX) :] if path.startswith(_PREFIX) else ""
                answer = store.get(token) if _TOKEN.match(token) else None
                if answer is None:
                    self._send(404)
                else:
                    self._send(200, answer.encode("ascii", "ignore"))

            def _refuse(self) -> None:
                self._send(405)

            do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

            def log_message(self, *args: Any) -> None:
                pass

        return Handler


def generate_key_and_csr(domain: str) -> tuple[bytes, bytes]:
    """Neuer privater Schlüssel (ECDSA P-256) und Zertifikatsanforderung für genau diesen Namen."""
    key = ec.generate_private_key(ec.SECP256R1())
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key_pem, crypto_util.make_csr(key_pem, [domain])


def translate_error(err: BaseException) -> AcmeError:
    """Übersetzt Fehler der ACME-Bibliothek in verständliche Meldungen. Rohe Fehlertexte werden nie weitergegeben."""
    if isinstance(err, AcmeError):
        return err
    if isinstance(err, messages.Error):
        kind = (err.typ or "").rsplit(":", 1)[-1]
        if kind == "rateLimited":
            return AcmeError(
                "Ratenbegrenzung von Let's Encrypt erreicht. Bitte später erneut versuchen, "
                "zum Üben die Testumgebung verwenden."
            )
        if kind == "invalidEmail" or kind == "invalidContact":
            return AcmeError("Die E-Mail-Adresse wurde von Let's Encrypt abgelehnt.")
        if kind in {"unauthorized", "connection", "dns", "caa", "rejectedIdentifier"}:
            return AcmeError(_NOT_REACHABLE)
        return AcmeError(
            "Let's Encrypt hat die Anfrage abgelehnt. Bitte Name und Einstellungen prüfen."
        )
    if isinstance(err, acme_errors.ValidationError):
        return AcmeError(_NOT_REACHABLE)
    if isinstance(err, RequestException | acme_errors.TimeoutError):
        return AcmeError(
            "Let's Encrypt ist von hier aus nicht erreichbar oder antwortet nicht. "
            "Bitte Internetzugang des Servers prüfen und erneut versuchen."
        )
    return AcmeError(
        "Das Zertifikat konnte nicht besorgt werden. Einzelheiten stehen im Protokoll des Portals."
    )


_NOT_REACHABLE = (
    "Let's Encrypt konnte den Namen nicht prüfen. Der Name muss auf diesen Server zeigen, und "
    "Port 80 des Servers muss aus dem Internet erreichbar sein."
)


class AcmeBackend(Protocol):
    def obtain(
        self,
        *,
        domain: str,
        email: str,
        directory_url: str,
        account_key_pem: bytes | None,
        answers: ChallengeStore,
        csr_pem: bytes,
    ) -> tuple[str, bytes]:
        """Liefert (Zertifikatskette als PEM, Kontoschlüssel als PEM)."""
        ...


class AcmeLibBackend:
    """Ausstellung mit der `acme`-Bibliothek des certbot-Projekts (nur HTTP-01)."""

    def __init__(self, verify_ssl: bool = True, timeout_s: int = 90) -> None:
        self._verify, self._timeout = verify_ssl, timeout_s

    def obtain(
        self,
        *,
        domain: str,
        email: str,
        directory_url: str,
        account_key_pem: bytes | None,
        answers: ChallengeStore,
        csr_pem: bytes,
    ) -> tuple[str, bytes]:
        tokens: list[str] = []
        try:
            if account_key_pem:
                key = JWKRSA.load(account_key_pem)
            else:
                key = JWKRSA(key=rsa.generate_private_key(65537, 2048))
            net = client.ClientNetwork(
                key, user_agent=USER_AGENT, verify_ssl=self._verify, timeout=30
            )
            api = client.ClientV2(client.ClientV2.get_directory(directory_url, net), net)
            reg = messages.NewRegistration.from_data(email=email, terms_of_service_agreed=True)
            try:
                api.new_account(reg)
            except acme_errors.ConflictError as existing:  # Konto gibt es schon: wiederverwenden
                net.account = api.query_registration(
                    messages.RegistrationResource(
                        uri=existing.location, body=messages.Registration()
                    )
                )
            order = api.new_order(csr_pem)
            for authz in order.authorizations:
                if authz.body.status == messages.STATUS_VALID:
                    continue  # Let's Encrypt übernimmt gültige Prüfungen desselben Kontos (Erneuerung)
                chall = next(
                    (c for c in authz.body.challenges if isinstance(c.chall, challenges.HTTP01)),
                    None,
                )
                if chall is None:
                    raise AcmeError(
                        "Let's Encrypt bietet für diesen Namen keine Prüfung über Port 80 an."
                    )
                response, validation = chall.response_and_validation(net.key)
                token = chall.chall.encode("token")
                answers.put(token, validation)
                tokens.append(token)
                api.answer_challenge(chall, response)
            deadline = datetime.now() + timedelta(seconds=self._timeout)
            final = api.poll_and_finalize(order, deadline)
            account_pem = key.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            return str(final.fullchain_pem), account_pem
        except Exception as err:  # noqa: BLE001 - jede Störung wird in eine deutsche Meldung übersetzt
            if not isinstance(err, AcmeError):
                log.warning(
                    "ACME für %s fehlgeschlagen: %s %s",
                    domain,
                    type(err).__name__,
                    getattr(err, "typ", ""),  # ACME-Fehlerart, enthält keine Geheimnisse
                )
            raise translate_error(err) from None
        finally:
            for token in tokens:
                answers.remove(token)
