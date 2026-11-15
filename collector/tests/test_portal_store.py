from pathlib import Path

import pytest
from tlsmaterial import make_self_signed

from prtg_collector.config import ConfigError
from prtg_collector.portal.crypto import WrongKeyError
from prtg_collector.portal.store import ConfigStore, CoreInput

SECRET = "ein-langer-geheimer-schluessel-fuer-tests"
KEY = "PRTG-KEY-KLARTEXT-123"


def _core(**kw) -> CoreInput:
    data = {
        "id": "core-hq",
        "name": "Zentrale",
        "url": "https://prtg.example.invalid:1616",
        "api_key": KEY,
    }
    return CoreInput(**{**data, **kw})


def _store(tmp_path) -> ConfigStore:
    s = ConfigStore(tmp_path / "portal.db", SECRET)
    s.add_customer("musterfirma", "Musterfirma GmbH")
    return s


def test_api_key_stored_encrypted(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core())
    assert KEY.encode() not in (tmp_path / "portal.db").read_bytes()


def test_to_app_config_decrypts_key(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core(interval_s=30))
    cfg = s.to_app_config()
    core = cfg.customers[0].cores[0]
    assert core.interval_s == 30 and core.api_key({}) == KEY


def test_empty_key_on_update_keeps_old(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core())
    s.update_core("core-hq", _core(name="Neu", api_key=None))
    core = s.to_app_config().customers[0].cores[0]
    assert core.api_key({}) == KEY and s.get_core("core-hq").name == "Neu"


def test_key_is_never_returned_by_reads(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core())
    view = s.get_core("core-hq")
    assert KEY not in repr(view) and view.has_key is True


def test_version_increases_on_every_change(tmp_path):
    s = _store(tmp_path)
    v0 = s.version()
    s.add_core("musterfirma", _core())
    v1 = s.version()
    s.update_core("core-hq", _core(name="x", api_key=None))
    v2 = s.version()
    s.delete_core("core-hq")
    assert v0 < v1 < v2 < s.version()


def test_wrong_secret_key_fails_clearly(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core())
    with pytest.raises(WrongKeyError) as err:
        ConfigStore(tmp_path / "portal.db", "ein-ganz-anderer-schluessel-123456").to_app_config()
    assert "PORTAL_SECRET_KEY" in str(err.value)


def test_duplicate_customer_rejected(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ConfigError):
        s.add_customer("musterfirma", "Nochmal")


@pytest.mark.parametrize("bad", ["Kunde A", "", "a" * 33, "ä"])
def test_bad_ids_rejected(tmp_path, bad):
    s = ConfigStore(tmp_path / "p.db", SECRET)
    with pytest.raises(ConfigError):
        s.add_customer(bad, "x")


def test_short_secret_key_rejected(tmp_path):
    with pytest.raises(ConfigError):
        ConfigStore(tmp_path / "p.db", "kurz")


def test_core_id_not_changeable_and_unique(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core())
    with pytest.raises(ConfigError):
        s.add_core("musterfirma", _core())
    with pytest.raises(ConfigError):
        s.update_core("core-hq", _core(id="anders", api_key=None))


def test_ca_pem_is_written_to_file_for_the_collector(tmp_path):
    s = _store(tmp_path)
    s.add_core(
        "musterfirma",
        _core(verify_mode="ca", ca_pem=make_self_signed()[0]),
    )
    core = s.to_app_config().customers[0].cores[0]
    assert (
        core.verify_ssl is True
        and core.ca_file
        and "BEGIN CERTIFICATE" in Path(core.ca_file).read_text()
    )


def test_verify_modes_map_to_config(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core(id="a", verify_mode="off"))
    s.add_core("musterfirma", _core(id="b", verify_mode="pinned", pinned_sha256="ab" * 32))
    cores = {c.id: c for c in s.to_app_config().customers[0].cores}
    assert cores["a"].verify_ssl is False
    assert cores["b"].pinned_sha256 == "ab" * 32


def test_readonly_store_reads_but_cannot_write(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core())
    db = tmp_path / "portal.db"
    db.chmod(0o444)
    tmp_path.chmod(0o555)  # wie ein schreibgeschütztes Volume
    try:
        ro = ConfigStore(db, SECRET, readonly=True, ca_dir=tmp_path.parent / "ca-extern")
        assert ro.to_app_config().customers[0].cores[0].api_key({}) == KEY
        with pytest.raises(Exception, match="readonly"):
            ro.add_customer("neu", "Neu")
    finally:
        tmp_path.chmod(0o755)
        db.chmod(0o644)


def test_ca_files_go_to_configured_dir(tmp_path):
    s = ConfigStore(tmp_path / "portal.db", SECRET, ca_dir=tmp_path / "ca-extern")
    s.add_customer("musterfirma", "M")
    s.add_core(
        "musterfirma",
        _core(verify_mode="ca", ca_pem=make_self_signed()[0]),
    )
    core = s.to_app_config().customers[0].cores[0]
    assert core.ca_file and core.ca_file.startswith(str(tmp_path / "ca-extern"))


def test_editing_a_pinned_core_keeps_the_pin(tmp_path):  # Befund 1
    s = _store(tmp_path)
    s.add_core("musterfirma", _core(verify_mode="pinned", pinned_sha256="ab" * 32))
    s.update_core(
        "core-hq", _core(interval_s=30, api_key=None, verify_mode="pinned", pinned_sha256=None)
    )
    core = s.to_app_config().customers[0].cores[0]
    assert core.pinned_sha256 == "ab" * 32 and core.interval_s == 30


def test_editing_a_ca_core_keeps_the_ca(tmp_path):  # Befund 1
    pem = make_self_signed()[0]
    s = ConfigStore(tmp_path / "p.db", SECRET, ca_dir=tmp_path / "ca")
    s.add_customer("musterfirma", "M")
    s.add_core("musterfirma", _core(verify_mode="ca", ca_pem=pem))
    s.update_core("core-hq", _core(verify_mode="ca", ca_pem=None, api_key=None, name="Neu"))
    assert s.to_app_config().customers[0].cores[0].ca_file


def test_switching_away_from_pinned_clears_the_pin(tmp_path):
    s = _store(tmp_path)
    s.add_core("musterfirma", _core(verify_mode="pinned", pinned_sha256="ab" * 32))
    s.update_core("core-hq", _core(verify_mode="system", api_key=None))
    assert s.get_core("core-hq").pinned_sha256 is None


def test_wrong_secret_key_is_rejected_when_the_store_opens(tmp_path):
    import pytest

    from prtg_collector.portal.crypto import WrongKeyError

    ConfigStore(tmp_path / "p.db", "der-richtige-schluessel-123")
    with pytest.raises(WrongKeyError):
        ConfigStore(tmp_path / "p.db", "ein-anderer-schluessel-456")
    ConfigStore(tmp_path / "p.db", "der-richtige-schluessel-123")


def test_existing_store_without_key_check_is_verified_against_a_stored_secret(tmp_path):
    import pytest

    from prtg_collector.portal.crypto import WrongKeyError
    from prtg_collector.portal.store import CoreInput

    store = ConfigStore(tmp_path / "p.db", "der-richtige-schluessel-123")
    store.add_customer("k", "K")
    store.add_core("k", CoreInput(id="c", name="C", url="https://x.example:1616", api_key="geheim"))
    store._db.execute("DELETE FROM meta WHERE key='key_check'")  # Stand vor dieser Prüfung
    store._db.commit()
    with pytest.raises(WrongKeyError):
        ConfigStore(tmp_path / "p.db", "ein-anderer-schluessel-456")


def test_collector_skips_a_core_with_invalid_stored_url_instead_of_stopping(tmp_path):  # F09
    s = _store(tmp_path)
    s.add_core("musterfirma", _core())
    s.add_core("musterfirma", _core(id="core-zwei", name="Zwei"))
    s._db.execute("UPDATE core SET url='ftp://nein' WHERE id='core-zwei'")
    s._db.commit()
    assert [c.id for c in s.to_app_config().customers[0].cores] == ["core-hq"]
