"""Verbindungstest für einen Core mit verständlichen deutschen Meldungen."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from ..config import ConfigError, CoreConfig
from ..prtg_client import PrtgClient, PrtgError
from .tlscheck import CertInfo, fetch_cert


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # kein pytest-Testfall
    ok: bool
    code: str  # ok, auth, net, server, tls_untrusted, tls_changed, config
    message_de: str
    cert: CertInfo | None = None


class ConnectionTester:
    def __init__(
        self,
        client_factory: Callable[[CoreConfig, str], PrtgClient] = PrtgClient,
        cert_fetcher: Callable[[str, int], CertInfo] = fetch_cert,
    ) -> None:
        self._factory, self._fetch = client_factory, cert_fetcher

    async def current_cert(self, core: CoreConfig) -> CertInfo:
        url = urlparse(core.url)
        return await asyncio.to_thread(self._fetch, url.hostname or "", url.port or 443)

    async def test(self, core: CoreConfig, env: Mapping[str, str]) -> TestResult:
        try:
            token = core.api_key(env)
        except ConfigError:
            return TestResult(False, "config", "Es ist noch kein API-Key hinterlegt.")
        try:
            client = self._factory(core, token)
        except Exception as err:  # noqa: BLE001 - z. B. unbrauchbare CA-Datei
            return TestResult(
                False,
                "config",
                f"Die Einstellungen dieses Cores sind unbrauchbar ({type(err).__name__}). Zertifikat und Adresse prüfen.",
            )
        try:
            probes = await client.count_probes()
        except PrtgError as err:
            return await self._failure(core, err)
        finally:
            await client.aclose()
        return TestResult(
            True, "ok", f"Verbindung erfolgreich. {probes} Probe(s) gefunden, API-Key gültig."
        )

    async def _failure(self, core: CoreConfig, err: PrtgError) -> TestResult:
        if err.kind == "tls":
            url = urlparse(core.url)
            try:
                cert = await asyncio.to_thread(self._fetch, url.hostname or "", url.port or 443)
            except ConfigError:
                return TestResult(False, "net", err.message)
            if core.pinned_sha256:
                return TestResult(
                    False,
                    "tls_changed",
                    f"{err.message} Das Zertifikat hat sich geändert (zum Beispiel erneuert). "
                    "Vertrauen Sie dem neuen nur, wenn Sie die Änderung erwarten und der Fingerabdruck stimmt.",
                    cert,
                )
            return TestResult(
                False,
                "tls_untrusted",
                "Das Zertifikat des Cores ist nicht vertrauenswürdig. Prüfen Sie den Fingerabdruck "
                "und vertrauen Sie dem Zertifikat nur, wenn er stimmt.",
                cert,
            )
        return TestResult(
            False, err.kind if err.kind in {"auth", "net", "server"} else "net", err.message
        )
