import asyncio
from datetime import UTC, datetime  # noqa: F401

from test_collector import FakeClient, FakeWriter, _dns, _inv

from prtg_collector.collector import serve_dynamic
from prtg_collector.config import AppConfig, CoreConfig, CustomerConfig


def _cfg(*cores: tuple[str, str]) -> AppConfig:
    """cores: (core_id, api_key)"""
    return AppConfig.model_construct(
        retention_days=365,
        customers=[
            CustomerConfig.model_construct(
                id="kunde",
                cores=[
                    CoreConfig(id=cid, url="https://x.example.invalid:1616", api_key_value=key)
                    for cid, key in cores
                ],
            )
        ]
        if cores
        else [],
    )


async def _until(cond, timeout=3.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


class Harness:
    def __init__(self, cfg):
        self.cfg, self.writer, self.tokens, self.stop = (
            cfg,
            FakeWriter(("kunde",)),
            [],
            asyncio.Event(),
        )

    def factory(self, core, token):
        self.tokens.append((core.id, token))
        return FakeClient(inv=_inv())

    def runs(self, core_id):
        return sum(
            1
            for ln in self.writer.lines("kunde")
            if ln.startswith("collector_run") and f"core={core_id}" in ln
        )

    async def start(self):
        self.task = asyncio.create_task(
            serve_dynamic(
                lambda: self.cfg,
                {},
                self.factory,
                self.writer,
                _dns(),
                self.stop,
                poll_s=0.02,
                interval_scale=0.003,
            )
        )

    async def finish(self):
        self.stop.set()
        await asyncio.wait_for(self.task, 3)


async def test_collector_picks_up_new_core_without_restart():
    h = Harness(_cfg())
    await h.start()
    await asyncio.sleep(0.1)
    assert h.writer.written == []
    h.cfg = _cfg(("core-a", "k1"))
    assert await _until(lambda: h.runs("core-a") >= 1)
    await h.finish()


async def test_removed_core_stops_polling():
    h = Harness(_cfg(("core-a", "k1"), ("core-b", "k2")))
    await h.start()
    assert await _until(lambda: h.runs("core-a") >= 2 and h.runs("core-b") >= 2)
    h.cfg = _cfg(("core-b", "k2"))
    await asyncio.sleep(0.15)
    frozen = h.runs("core-a")
    await asyncio.sleep(0.2)
    assert h.runs("core-a") == frozen
    assert h.runs("core-b") > 2
    await h.finish()


async def test_changed_api_key_restarts_core_with_new_token():
    h = Harness(_cfg(("core-a", "alt")))
    await h.start()
    assert await _until(lambda: ("core-a", "alt") in h.tokens)
    h.cfg = _cfg(("core-a", "neu"))
    assert await _until(lambda: ("core-a", "neu") in h.tokens)
    await h.finish()


async def test_core_without_api_key_is_skipped_not_fatal():
    cfg = _cfg(("core-a", "k1"))
    cfg.customers[0].cores.append(CoreConfig(id="core-ohne", url="https://y.example.invalid:1616"))
    h = Harness(cfg)
    await h.start()
    assert await _until(lambda: h.runs("core-a") >= 1)
    assert h.runs("core-ohne") == 0
    await h.finish()


async def test_unreadable_config_keeps_running_cores_and_recovers():
    h = Harness(_cfg(("core-a", "k1")))
    state = {"fail": False}

    def load():
        if state["fail"]:
            raise OSError("database is locked")
        return h.cfg

    h.task = asyncio.create_task(
        serve_dynamic(
            load, {}, h.factory, h.writer, _dns(), h.stop, poll_s=0.02, interval_scale=0.003
        )
    )
    assert await _until(lambda: h.runs("core-a") >= 1)
    state["fail"] = True
    before = h.runs("core-a")
    assert await _until(
        lambda: h.runs("core-a") > before
    )  # Takt: 60 s x 0,003  # läuft mit der letzten bekannten Konfiguration weiter
    state["fail"] = False
    h.cfg = _cfg(("core-a", "k1"), ("core-b", "k2"))
    assert await _until(lambda: h.runs("core-b") >= 1)
    await h.finish()


async def test_core_whose_client_cannot_be_built_reports_error_and_retries():  # Befund 2
    h = Harness(_cfg(("core-a", "k1")))
    attempts = []

    def factory(core, token):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("CA-Datei kaputt")
        return FakeClient(inv=_inv())

    h.factory = factory
    await h.start()
    assert await _until(lambda: h.runs("core-a") >= 1 and len(attempts) >= 3)
    errors = [
        ln for ln in h.writer.lines("kunde") if ln.startswith("collector_run") and "errors=1i" in ln
    ]
    assert errors and "Unerwarteter Fehler" in errors[0]
    await h.finish()
