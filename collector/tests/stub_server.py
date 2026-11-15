"""Stub der PRTG API v2 (nur GET), Antwortformen aus dem Spike vom 11.11.2026."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs, urlparse

LISTS = {
    "/api/v2/experimental/probes": "probes",
    "/api/v2/experimental/groups": "groups",
    "/api/v2/experimental/devices": "devices",
    "/api/v2/experimental/sensors": "sensors",
    "/api/v2/experimental/channels": "channels",
}


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # Verbindungsabbruch nach Timeout ist gewollt
        pass


class StubServer:
    def __init__(
        self,
        probes=(),
        groups=(),
        devices=(),
        sensors=(),
        channels=(),
        max_page: int = 100,
        tls: tuple[str, str] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.data: dict[str, list[dict[str, Any]]] = {
            "probes": list(probes),
            "groups": list(groups),
            "devices": list(devices),
            "sensors": list(sensors),
            "channels": list(channels),
        }
        self.max_page = max_page
        self.fail_with: int | str | None = None  # 401, 500 oder "timeout"
        self.fail_code = "AUTH_FAILED"  # Fehlercode im Antwortkörper, z. B. LICENSE_INACTIVE
        self.empty_body = False
        self.requests: list[tuple[str, str]] = []  # (Methode, Pfad mit Abfrage)
        self.auth_headers: list[str] = []
        self._srv = _QuietServer((host, port), self._handler())
        self.scheme = "http"
        if tls:
            import ssl
            import tempfile

            with tempfile.TemporaryDirectory() as d:
                (cert, key) = (f"{d}/c.pem", f"{d}/k.pem")
                Path(cert).write_text(tls[0])
                Path(key).write_text(tls[1])
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(cert, key)
            self._srv.socket = ctx.wrap_socket(self._srv.socket, server_side=True)
            self.scheme = "https"

    @property
    def url(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self._srv.server_port}"

    def __enter__(self) -> Self:
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        stub = self

        class H(BaseHTTPRequestHandler):
            def _any(self) -> None:
                stub.requests.append((self.command, self.path))
                stub.auth_headers.append(self.headers.get("Authorization", ""))
                if self.command != "GET":
                    return self._send(405, b"Method Not Allowed")
                if stub.fail_with == "timeout":
                    time.sleep(1)
                    return self._send(504, b"{}")
                if isinstance(stub.fail_with, int):
                    body = json.dumps({"code": stub.fail_code, "message": "x", "request_id": "r"})
                    return self._send(stub.fail_with, body.encode())
                url = urlparse(self.path)
                key = LISTS.get(url.path)
                if key is None:
                    return self._send(404, b"404 page not found")
                if stub.empty_body:
                    return self._send(200, b"")
                q = parse_qs(url.query)
                limit = min(int(q.get("limit", ["100"])[0]) or stub.max_page, stub.max_page)
                offset = int(q.get("offset", ["0"])[0])
                items = stub.data[key]
                page = items[offset : offset + limit]
                self._send(
                    200,
                    json.dumps(page).encode(),
                    {"X-Total-Count": str(len(items)), "X-Result-Count": str(len(page))},
                )

            def _send(self, code: int, body: bytes, extra: dict[str, str] | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _any

            def log_message(self, *args: object) -> None:
                pass

        return H


def obj(id: int, name: str, **extra: Any) -> dict[str, Any]:
    return {"id": str(id), "name": name, "href": f"/api/v2/x/{id}", **extra}


def device(
    id: int,
    name: str,
    parent_id: int,
    parent_type: str = "REFERENCED_GROUP",
    host="10.0.0.1",
    tags=(),
    parenttags=(),
):
    return obj(
        id,
        name,
        status="UP",
        parent={"id": str(parent_id), "name": "p", "type": parent_type},
        basic={"host": host, "tags": list(tags), "parenttags": list(parenttags)},
    )


def sensor(
    id: int,
    device_id: int,
    name: str,
    status="UP",
    kind_name="Ping",
    tags=(),
    parenttags=(),
    message="OK",
):
    return obj(
        id,
        name,
        kind="ping",
        kind_name=kind_name,
        status=status,
        message=message,
        parent={"id": str(device_id), "name": "d", "type": "REFERENCED_DEVICE"},
        basic={"tags": list(tags), "parenttags": list(parenttags)},
    )


def channel(
    sensor_id: int,
    index: int,
    name: str,
    value: float | str | None = 1.0,
    unit: str = "%",
):
    """Kanal in der Form aus dem Spike: Id `<sensor>.<index>`, Wert in `last_measurement`."""
    measurement = None if value is None else {"timestamp": "2026-11-11T14:23:12Z", "value": value}
    return {
        "id": f"{sensor_id}.{index}",
        "name": name,
        "kind": "channellimit",
        "basic": {"displayunit": unit, "name": name},
        "last_measurement": measurement,
        "parent": {"id": str(sensor_id), "name": "s", "type": "REFERENCED_SENSOR"},
    }
