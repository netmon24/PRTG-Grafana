"""Administrationsportal: Kunden, Core-Server, Zertifikate. Einfache Formulare ohne JavaScript."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ..config import ConfigError, Filters
from .acme import AcmeError
from .auth import Auth
from .purge import Purger
from .status import NullStatus, StatusProvider
from .store import ConfigStore, CoreInput
from .testing import ConnectionTester
from .tlscheck import validate_pair
from .tlsmanager import TlsManager
from .tlsmodes import install_pair, sync_markers

log = logging.getLogger("prtg_collector.portal")

HERE = Path(__file__).parent
COOKIE = "prtg_session"


def _is_https(request: Request) -> bool:
    """Direkt über TLS oder hinter einem Proxy, der https meldet. Nur dann bekommt das Cookie `Secure`;
    über reines http würde der Browser es verwerfen und die Anmeldung scheitern."""
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


KINDS = {"grafana": "Grafana", "influxdb": "InfluxDB", "portal": "Portal"}
RESTART_HINT = "docker compose restart grafana influxdb"
UMSTELLUNG_HINT = "docker compose up -d"
_FIELD_HINTS = {
    "id": "Die Kennung darf nur a-z, 0-9, _ und - enthalten (höchstens 32 Zeichen).",
    "url": "Die Adresse muss mit https:// beginnen und den Port enthalten, zum Beispiel https://prtg.firma.de:1616. Bitte keine Zugangsdaten in die Adresse schreiben.",
    "name": "Bitte einen Namen eingeben.",
    "interval_s": "Der Abruftakt muss zwischen 10 und 300 Sekunden liegen.",
    "name_regex": "Das Namensmuster ist kein gültiger regulärer Ausdruck oder länger als 200 Zeichen.",
    "ca_pem": "Das CA-Zertifikat ist nicht lesbar. Erwartet wird eine PEM-Datei (-----BEGIN CERTIFICATE-----).",
}


def _split(text: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,\n]", text or "") if t.strip()]


def _int(text: str | None) -> int | None:
    return int(text) if text and text.strip().isdigit() else None


def create_app(
    store: ConfigStore,
    auth: Auth,
    tester: ConnectionTester,
    status: StatusProvider | None = None,
    certs_dir: Path | None = None,
    key_gid: int | None = None,
    tls: TlsManager | None = None,
    portal_restart: Callable[[], None] | None = None,
    on_change: Callable[[], None] | None = None,
    setup_problems: Callable[[], list[str]] | None = None,
    trust_forwarded: bool = False,
    purger: Purger | None = None,
) -> FastAPI:
    login_slots = asyncio.Semaphore(4)
    app = FastAPI(
        title="netmon24 PRTG-Grafana Portal", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    status = status or NullStatus()
    csrf_secret = os.urandom(32)

    def changed() -> None:
        if on_change:
            on_change()

    def csrf_for(session: str) -> str:
        return hmac.new(csrf_secret, session.encode(), hashlib.sha256).hexdigest()

    def render(request: Request, name: str, code: int = 200, **ctx: Any) -> HTMLResponse:
        session = request.cookies.get(COOKIE, "")
        ctx.update(
            csrf=csrf_for(session) if auth.check(session) else "", logged_in=auth.check(session)
        )
        return templates.TemplateResponse(request, name, ctx, status_code=code)

    @app.middleware("http")
    async def headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        resp: Response = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, err: ConfigError) -> HTMLResponse:
        missing = str(err).endswith("gibt es nicht.")
        return render(request, "error.html", 404 if missing else 400, error=str(err))

    @app.exception_handler(OSError)
    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, err: Exception) -> HTMLResponse:
        log.error("Speichern im Portal fehlgeschlagen: %s", err)
        return render(
            request,
            "error.html",
            500,
            error="Speichern nicht möglich. Bitte Platz und Schreibrechte des Datenvolumens "
            "(portal-data) prüfen und es erneut versuchen.",
        )

    async def guard(request: Request) -> dict[str, Any]:
        """Anmeldung prüfen, bei ändernden Aufrufen zusätzlich das CSRF-Token."""
        session = request.cookies.get(COOKIE, "")
        if not auth.check(session):
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        form: dict[str, Any] = {}
        if request.method == "POST":
            form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
            if not hmac.compare_digest(form.get("csrf", ""), csrf_for(session)):
                raise HTTPException(
                    status_code=403, detail="Ungültiges Formular-Token. Seite neu laden."
                )
        return form

    def client_ip(request: Request) -> str:
        """Herkunft für die Anmeldesperre. Der Kopf X-Forwarded-For gilt nur hinter einem eigenen Proxy."""
        if trust_forwarded:
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
            if forwarded:
                return forwarded
        return request.client.host if request.client else ""

    def redirect(path: str) -> RedirectResponse:
        return RedirectResponse(path, status_code=303)

    # --- Anmeldung ---
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse:
        return render(request, "login.html")

    @app.post("/login")
    async def login(request: Request) -> Response:
        form = await request.form()
        async with login_slots:  # begrenzt die gleichzeitigen Hash-Läufe (je ca. 40 ms CPU)
            token = await asyncio.to_thread(
                auth.login,
                str(form.get("username", "")),
                str(form.get("password", "")),
                client_ip(request),
            )
        if token is None:
            return render(
                request,
                "login.html",
                401,
                error="Anmeldung fehlgeschlagen. Nach mehreren Fehlversuchen ist der Zugang von dieser Adresse 5 Minuten gesperrt.",
            )
        resp = redirect("/")
        resp.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=_is_https(request),
            max_age=8 * 3600,
        )
        return resp

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        await guard(request)
        auth.logout(request.cookies.get(COOKIE))
        resp = redirect("/login")
        resp.delete_cookie(COOKIE, httponly=True, samesite="strict", secure=_is_https(request))
        return resp

    # --- Übersicht ---
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        await guard(request)
        runs = status.last_runs()
        groups = [
            {"customer": c, "cores": [(core, runs.get(core.id)) for core in store.cores(c["id"])]}
            for c in store.customers()
        ]
        return render(
            request,
            "index.html",
            groups=groups,
            setup_problems=(setup_problems() if setup_problems else []),
        )

    # --- Kunden ---
    @app.get("/customers/new", response_class=HTMLResponse)
    async def customer_new(request: Request) -> HTMLResponse:
        await guard(request)
        return render(request, "customer_new.html")

    @app.post("/customers")
    async def customer_create(request: Request) -> Response:
        form = await guard(request)
        try:
            store.add_customer(
                form.get("id", "").strip(), form.get("name", "").strip() or form.get("id", "")
            )
        except ConfigError as err:
            return render(request, "customer_new.html", 400, error=str(err), form=form)
        changed()
        return redirect("/")

    # --- Cores ---
    def _core_input(form: dict[str, Any], core_id: str | None = None) -> CoreInput:
        return CoreInput(
            id=core_id or form.get("id", "").strip(),
            name=form.get("name", "").strip(),
            url=form.get("url", "").strip(),
            api_key=form.get("api_key") or None,
            verify_mode=form.get("verify_mode", "system"),
            ca_pem=form.get("ca_pem") or None,
            pinned_sha256=form.get("pinned_sha256") or None,
            interval_s=_int(form.get("interval_s")) or 60,
            channel_interval_s=_int(form.get("channel_interval_s")),
            events_interval_s=_int(form.get("events_interval_s")),
            filters=Filters(
                tags=_split(form.get("filter_tags", "")),
                groups=_split(form.get("filter_groups", "")),
                sensor_types=_split(form.get("filter_types", "")),
                name_regex=(form.get("filter_regex") or "").strip() or None,
            ),
        )

    def _problem(err: ValidationError) -> str:
        field = str(err.errors()[0]["loc"][0]) if err.errors() else ""
        return _FIELD_HINTS.get(field, f"Eingabe ungültig ({field}).")

    @app.get("/customers/{customer_id}/cores/new", response_class=HTMLResponse)
    async def core_new(request: Request, customer_id: str) -> HTMLResponse:
        await guard(request)
        return render(request, "core_form.html", customer_id=customer_id, core=None, form={})

    @app.post("/customers/{customer_id}/cores")
    async def core_create(request: Request, customer_id: str) -> Response:
        form = await guard(request)
        try:
            core_id = store.add_core(customer_id, _core_input(form))
        except ValidationError as err:
            return render(
                request,
                "core_form.html",
                400,
                customer_id=customer_id,
                core=None,
                form=form,
                error=_problem(err),
            )
        except ConfigError as err:
            return render(
                request,
                "core_form.html",
                400,
                customer_id=customer_id,
                core=None,
                form=form,
                error=str(err),
            )
        changed()
        return redirect(f"/cores/{core_id}/edit")

    @app.get("/cores/{core_id}/edit", response_class=HTMLResponse)
    async def core_edit(request: Request, core_id: str) -> HTMLResponse:
        await guard(request)
        core = store.get_core(core_id)
        return render(request, "core_form.html", customer_id=core.customer_id, core=core, form={})

    @app.post("/cores/{core_id}")
    async def core_update(request: Request, core_id: str) -> Response:
        form = await guard(request)
        core = store.get_core(core_id)
        try:
            store.update_core(core_id, _core_input(form, core_id))
        except ValidationError as err:
            return render(
                request,
                "core_form.html",
                400,
                customer_id=core.customer_id,
                core=core,
                form=form,
                error=_problem(err),
            )
        except ConfigError as err:
            return render(
                request,
                "core_form.html",
                400,
                customer_id=core.customer_id,
                core=core,
                form=form,
                error=str(err),
            )
        return render(
            request,
            "core_form.html",
            customer_id=core.customer_id,
            core=store.get_core(core_id),
            form={},
            info="Gespeichert.",
        )

    @app.post("/cores/{core_id}/delete")
    async def core_delete(request: Request, core_id: str) -> Response:
        form = await guard(request)
        core = store.get_core(core_id)
        if form.get("confirm", "").strip() != core_id:
            return render(
                request,
                "core_form.html",
                400,
                customer_id=core.customer_id,
                core=core,
                form={},
                error=f"Nicht gelöscht: Zur Bestätigung die Kennung {core_id} eintippen.",
            )
        if purger is not None:
            try:
                await asyncio.to_thread(purger.delete_core, core.customer_id, core_id)
            except Exception as err:
                log.exception("Löschen der Daten von %s fehlgeschlagen", core_id)
                return render(
                    request,
                    "core_form.html",
                    502,
                    customer_id=core.customer_id,
                    core=core,
                    form={},
                    error=f"Die Daten konnten nicht gelöscht werden ({type(err).__name__}). "
                    "Der Core-Server bleibt eingetragen. InfluxDB prüfen und erneut versuchen.",
                )
        store.delete_core(core_id)
        changed()
        return redirect("/")

    @app.get("/customers/{customer_id}/delete", response_class=HTMLResponse)
    async def customer_delete_page(request: Request, customer_id: str) -> HTMLResponse:
        await guard(request)
        name = store.customer_name(customer_id)
        if name is None:
            raise ConfigError(f"Kunde {customer_id} gibt es nicht.")
        return render(
            request,
            "customer_delete.html",
            customer_id=customer_id,
            customer_name=name,
            cores=store.cores(customer_id),
        )

    @app.post("/customers/{customer_id}/delete")
    async def customer_delete(request: Request, customer_id: str) -> Response:
        form = await guard(request)
        name = store.customer_name(customer_id)
        if name is None:
            raise ConfigError(f"Kunde {customer_id} gibt es nicht.")
        cores = store.cores(customer_id)
        if form.get("confirm", "").strip() != customer_id:
            return render(
                request,
                "customer_delete.html",
                400,
                customer_id=customer_id,
                customer_name=name,
                cores=cores,
                error=f"Nicht gelöscht: Zur Bestätigung die Kennung {customer_id} eintippen.",
            )
        if purger is not None:
            try:
                await asyncio.to_thread(purger.delete_customer, customer_id)
            except Exception as err:
                log.exception("Löschen von Kunde %s fehlgeschlagen", customer_id)
                return render(
                    request,
                    "customer_delete.html",
                    502,
                    customer_id=customer_id,
                    customer_name=name,
                    cores=cores,
                    error=f"Die Daten konnten nicht vollständig gelöscht werden ({type(err).__name__}). "
                    "Der Kunde bleibt eingetragen. InfluxDB und Grafana prüfen und erneut versuchen.",
                )
        store.delete_customer(customer_id)
        changed()
        return redirect("/")

    @app.post("/cores/{core_id}/test", response_class=HTMLResponse)
    async def core_test(request: Request, core_id: str) -> HTMLResponse:
        await guard(request)
        core = store.get_core(core_id)
        result = await tester.test(store.core_config(core_id), {})
        store.set_test_result(core_id, result.ok, result.message_de)
        return render(request, "core_result.html", core=core, result=result)

    @app.post("/cores/{core_id}/trust", response_class=HTMLResponse)
    async def core_trust(request: Request, core_id: str) -> HTMLResponse:
        form = await guard(request)
        core = store.get_core(core_id)
        try:
            cert = await tester.current_cert(store.core_config(core_id))
        except ConfigError as err:
            return render(request, "core_result.html", 400, core=core, result=None, error=str(err))
        if cert.sha256 != form.get("sha256", ""):
            return render(
                request,
                "core_result.html",
                400,
                core=core,
                result=None,
                error="Das Zertifikat hat sich seit dem Test geändert. Bitte den Test wiederholen.",
            )
        store.set_pinned(core_id, cert.sha256)
        return render(
            request,
            "core_result.html",
            core=store.get_core(core_id),
            result=None,
            info="Dem Zertifikat wird jetzt vertraut. Bitte die Verbindung erneut testen.",
        )

    # --- Zertifikate und Betriebsart je Dienst ---
    def cert_page(request: Request, code: int = 200, **ctx: Any) -> HTMLResponse:
        settings = {kind: store.get_tls_setting(kind) for kind in KINDS}
        return render(
            request,
            "certificates.html",
            code,
            kinds=KINDS,
            settings=settings,
            prefs=store.get_acme_prefs(),
            acme_available=tls is not None,
            restart=RESTART_HINT,
            apply=UMSTELLUNG_HINT,
            **ctx,
        )

    def restart_if_portal(kind: str) -> None:
        """Das Portal liest sein Zertifikat beim Start: nach einer Änderung startet es sich selbst neu."""
        if kind == "portal" and portal_restart:
            portal_restart()

    def sync() -> None:
        if certs_dir is not None:
            sync_markers(certs_dir, store)

    @app.get("/certificates", response_class=HTMLResponse)
    async def certificates(request: Request) -> HTMLResponse:
        await guard(request)
        return cert_page(request)

    @app.post("/certificates/acme/settings", response_class=HTMLResponse)
    async def acme_settings(request: Request) -> HTMLResponse:
        form = await guard(request)
        email = form.get("email", "").strip()
        if "@" not in email or " " in email:
            return cert_page(request, 400, error="Bitte eine gültige E-Mail-Adresse eintragen.")
        store.set_acme_prefs(
            email, staging=form.get("staging") == "1", terms_accepted=form.get("terms") == "1"
        )
        return cert_page(request, info_text="Angaben für Let's Encrypt gespeichert.")

    @app.post("/certificates/{kind}/acme", response_class=HTMLResponse)
    async def certificate_acme(request: Request, kind: str) -> HTMLResponse:
        form = await guard(request)
        if kind not in KINDS or tls is None:
            raise HTTPException(status_code=404)
        try:
            await asyncio.to_thread(tls.obtain, kind, form.get("domain", ""))
        except AcmeError as err:
            return cert_page(request, 400, error=err.message)
        setting = store.get_tls_setting(kind)
        restart_if_portal(kind)
        return cert_page(
            request,
            info_text=f"Zertifikat von Let's Encrypt für {KINDS[kind]} besorgt (gültig bis "
            f"{(setting.not_after or '')[:10]}). Der Dienst startet neu und ist kurz nicht erreichbar.",
        )

    @app.post("/certificates/{kind}", response_class=HTMLResponse)
    async def certificate_upload(request: Request, kind: str) -> HTMLResponse:
        form = await guard(request)
        if kind not in KINDS:
            raise HTTPException(status_code=404)
        try:
            info = validate_pair(form.get("cert_pem", ""), form.get("key_pem", ""))
        except ConfigError as err:
            return cert_page(request, 400, error=str(err))
        if certs_dir is not None:
            try:
                install_pair(certs_dir, kind, form["cert_pem"], form["key_pem"], key_gid)
            except OSError:
                return cert_page(
                    request,
                    500,
                    error="Der Schlüssel konnte nicht mit der gemeinsamen Gruppe abgelegt werden. "
                    "Ist TLS_KEY_GID in der .env gesetzt und der Dienst neu gestartet? "
                    "Das bisherige Zertifikat bleibt unverändert.",
                )
        store.save_tls_asset(kind, form["cert_pem"], form["key_pem"])
        store.set_tls_setting(kind, "own")
        store.record_tls_result(kind, not_after=info.not_after.isoformat())
        sync()
        changed()
        restart_if_portal(kind)
        return cert_page(request, uploaded=KINDS[kind], info=info)

    @app.post("/certificates/{kind}/mode", response_class=HTMLResponse)
    async def certificate_mode(request: Request, kind: str) -> HTMLResponse:
        form = await guard(request)
        if kind not in KINDS:
            raise HTTPException(status_code=404)
        try:
            store.set_tls_setting(kind, form.get("mode", ""))
        except ConfigError as err:
            return cert_page(request, 400, error=str(err))
        sync()
        changed()
        restart_if_portal(kind)
        return cert_page(
            request,
            changed_mode=KINDS[kind],
            info_text=f"Betriebsart für {KINDS[kind]} gespeichert. Der Dienst startet neu "
            "und ist kurz nicht erreichbar.",
        )

    # --- Einstellungen ---
    @app.get("/settings", response_class=HTMLResponse)
    async def settings(request: Request) -> HTMLResponse:
        await guard(request)
        return render(request, "settings.html")

    @app.post("/settings/password", response_class=HTMLResponse)
    async def change_password(request: Request) -> HTMLResponse:
        form = await guard(request)
        session = request.cookies.get(COOKIE)
        user = auth.username(session) or ""
        if not auth.change_password(user, form.get("old", ""), form.get("new", ""), keep=session):
            return render(
                request,
                "settings.html",
                400,
                error="Das Passwort wurde nicht geändert. Altes Passwort prüfen, neues mit mindestens 12 Zeichen.",
            )
        return render(request, "settings.html", info="Passwort geändert.")

    # --- Benutzer ---
    @app.get("/users", response_class=HTMLResponse)
    async def users_page(request: Request) -> HTMLResponse:
        await guard(request)
        me = auth.username(request.cookies.get(COOKIE)) or ""
        return render(request, "users.html", users=auth.users(), me=me)

    @app.post("/users", response_class=HTMLResponse)
    async def user_add(request: Request) -> HTMLResponse:
        form = await guard(request)
        me = auth.username(request.cookies.get(COOKIE)) or ""
        name = form.get("username", "").strip()
        if not auth.add_user(name, form.get("password", "")):
            return render(
                request,
                "users.html",
                400,
                users=auth.users(),
                me=me,
                error="Der Benutzer wurde nicht angelegt. Name: a-z, 0-9, Punkt, Strich, höchstens 32 Zeichen, "
                "noch nicht vergeben. Passwort: mindestens 12 Zeichen.",
            )
        return render(
            request, "users.html", users=auth.users(), me=me, info=f"Benutzer {name} angelegt."
        )

    @app.post("/users/{username}/delete", response_class=HTMLResponse)
    async def user_delete(request: Request, username: str) -> HTMLResponse:
        await guard(request)
        me = auth.username(request.cookies.get(COOKIE)) or ""
        if not auth.delete_user(username, current=me):
            return render(
                request,
                "users.html",
                400,
                users=auth.users(),
                me=me,
                error="Der Benutzer wurde nicht gelöscht. Das eigene Konto und der letzte Administrator bleiben bestehen.",
            )
        return render(
            request, "users.html", users=auth.users(), me=me, info=f"Benutzer {username} gelöscht."
        )

    return app
