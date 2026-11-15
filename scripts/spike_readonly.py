#!/usr/bin/env python3
"""Lesender Spike gegen PRTG API v2: nur GET, Ergebnis anonymisiert in einen Ordner.

Aufruf (Zugang nur über Umgebungsvariablen, nie im Chat oder Repo):
    PRTG_BASE_URL=https://core:1616 PRTG_API_KEY=... [PRTG_VERIFY_SSL=false] \
        python3 scripts/spike_readonly.py [Ausgabeordner, Standard spike-raw]
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PREFIX = "/api/v2"
# Runde 2 (nach Runde 1: Listen für probes/groups/devices/sensors liefern 200 als reine Liste,
# höchstens 100 Einträge; Kanäle, Meldungen, Tags und OpenAPI waren unter den ersten Pfaden 404).
LIST_PATHS = [
    "/experimental/sensors",
    "/experimental/sensors?limit=1000",
    "/experimental/sensors?offset=100",
    "/experimental/sensors?skip=100",
    "/experimental/sensors?page=2",
    "/experimental/sensors?start=100",
    "/sensors",
    "/devices",
    "/experimental/channels",
    "/channels",
    "/experimental/measurements",
    "/measurements",
    "/experimental/messages",
    "/messages",
    "/logs",
    "/log",
    "/events",
    "/notifications",
    "/experimental/notifications",
    "/experimental/history",
    "/experimental/tags",
    "/tags",
    "/experimental/schemas",
    "/schemas",
    "/",
    "/swagger.json",
    "/openapi.yaml",
    "/docs",
]
ROOT_PATHS = ["/openapi.json", "/swagger.json", "/swagger/v1/swagger.json", "/docs", "/api/v2/docs"]
SENSOR_PATHS = [
    "/sensors/{id}",
    "/sensors/{id}/channels",
    "/sensors/{id}/measurements",
    "/sensors/{id}/data",
    "/sensors/{id}/messages",
    "/sensors/{id}/history",
    "/sensors/{id}/tags",
    "/experimental/sensors/{id}/channels",
    "/experimental/sensors/{id}/measurements",
    "/experimental/sensors/{id}/data",
    "/experimental/sensors/{id}/history",
    "/experimental/sensors/{id}/messages",
    "/channels?filter=parentid%20%3D%20%22{id}%22",
    "/experimental/channels?filter=parentid%20%3D%20%22{id}%22",
]
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")


def _mask(text: str, base_url: str, token: str) -> str:
    host = urllib.parse.urlparse(base_url).hostname or ""
    if host:
        text = text.replace(host, "prtg.example.invalid")
    if token:
        text = text.replace(token, "<token>")
    seen: dict[str, str] = {}

    def repl(m: re.Match[str]) -> str:
        return seen.setdefault(m.group(0), f"192.0.2.{len(seen) + 1}")

    return _IPV4.sub(repl, text)


def _get(
    base_url: str, token: str, path: str, ctx: ssl.SSLContext | None, prefix: str = PREFIX
) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(
        base_url.rstrip("/") + prefix + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace"), dict(err.headers)
    except (urllib.error.URLError, TimeoutError) as err:
        return 0, str(err), {}


def _first_id(body: str) -> str | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    items = data.get("items") if isinstance(data, dict) else data
    if isinstance(items, list) and items and isinstance(items[0], dict):
        value = items[0].get("id")
        return str(value) if value is not None else None
    return None


def run(base_url: str, token: str, verify_ssl: bool, out_dir: Path) -> dict[str, int]:
    """Fragt alle Kandidatenpfade ab (nur GET), speichert maskierte Antworten, gibt Statusliste zurück."""
    ctx = None if verify_ssl else ssl._create_unverified_context()
    out_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, int] = {}
    headers: dict[str, dict[str, str]] = {}
    paging: dict[str, dict[str, object]] = {}
    sensor_id: str | None = None

    def probe(path: str, prefix: str = PREFIX) -> str:
        code, body, hdrs = _get(base_url, token, path, ctx, prefix)
        key = prefix + path
        status[key] = code
        keep = {
            k: v
            for k, v in hdrs.items()
            if k.lower() in {"link", "allow", "content-type", "retry-after"}
            or k.lower().startswith(("x-", "ratelimit"))
        }
        headers[key] = {k: _mask(v, base_url, token) for k, v in keep.items()}
        name = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_") + ".json"
        (out_dir / name).write_text(_mask(body[:200_000], base_url, token), encoding="utf-8")
        try:
            data = json.loads(body)
        except ValueError:
            return body
        if isinstance(data, list):
            ids = [str(x.get("id")) for x in data if isinstance(x, dict)]
            paging[key] = {"anzahl": len(data), "erste_id": ids[:1], "letzte_id": ids[-1:]}
        return body

    for path in LIST_PATHS:
        body = probe(path)
        if path == "/experimental/sensors":
            sensor_id = _first_id(body)
    for path in ROOT_PATHS:
        probe(path, prefix="")
    if sensor_id:
        for tpl in SENSOR_PATHS:
            probe(tpl.format(id=sensor_id))
    (out_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    (out_dir / "headers.json").write_text(json.dumps(headers, indent=2), encoding="utf-8")
    (out_dir / "paging.json").write_text(json.dumps(paging, indent=2), encoding="utf-8")
    return status


def main() -> int:
    base, token = os.environ.get("PRTG_BASE_URL"), os.environ.get("PRTG_API_KEY")
    if not base or not token:
        print("PRTG_BASE_URL und PRTG_API_KEY müssen als Umgebungsvariablen gesetzt sein.")
        return 2
    verify = os.environ.get("PRTG_VERIFY_SSL", "true").lower() != "false"
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "spike-raw")
    for path, code in run(base, token, verify, out).items():
        print(f"{code:>4}  {path}")
    print(f"Maskierte Antworten in {out}/ (nicht ins Git übernehmen).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
