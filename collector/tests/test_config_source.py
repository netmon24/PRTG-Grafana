import pytest

from prtg_collector.config import ConfigError
from prtg_collector.config_source import load_from_env
from prtg_collector.portal.store import ConfigStore, CoreInput

SECRET = "ein-langer-geheimer-schluessel-fuer-tests"
YAML = """customers:
  - id: ausyaml
    cores:
      - {id: c, url: "https://h.example.invalid:1616", api_key_env: K}
"""


def test_prefers_portal_database_when_it_has_customers(tmp_path):
    store = ConfigStore(tmp_path / "portal.db", SECRET)
    store.add_customer("ausdb", "DB")
    store.add_core(
        "ausdb", CoreInput(id="c", name="C", url="https://h.example.invalid:1616", api_key="k")
    )
    (tmp_path / "cores.yaml").write_text(YAML)
    env = {
        "PORTAL_DATA_DIR": str(tmp_path),
        "PORTAL_SECRET_KEY": SECRET,
        "CORES_FILE": str(tmp_path / "cores.yaml"),
    }
    assert [c.id for c in load_from_env(env).customers] == ["ausdb"]


def test_falls_back_to_yaml_when_database_empty(tmp_path):
    ConfigStore(tmp_path / "portal.db", SECRET)
    (tmp_path / "cores.yaml").write_text(YAML)
    env = {
        "PORTAL_DATA_DIR": str(tmp_path),
        "PORTAL_SECRET_KEY": SECRET,
        "CORES_FILE": str(tmp_path / "cores.yaml"),
    }
    assert [c.id for c in load_from_env(env).customers] == ["ausyaml"]


def test_nothing_configured_gives_empty_config_not_crash(tmp_path):
    env = {
        "PORTAL_DATA_DIR": str(tmp_path),
        "PORTAL_SECRET_KEY": SECRET,
        "CORES_FILE": str(tmp_path / "gibt-es-nicht.yaml"),
    }
    assert load_from_env(env).customers == []


def test_database_without_secret_key_is_a_clear_error(tmp_path):
    ConfigStore(tmp_path / "portal.db", SECRET)
    with pytest.raises(ConfigError, match="PORTAL_SECRET_KEY"):
        load_from_env({"PORTAL_DATA_DIR": str(tmp_path)})


def test_load_from_env_works_on_readonly_data_dir(tmp_path):
    store = ConfigStore(tmp_path / "portal.db", SECRET)
    store.add_customer("ausdb", "DB")
    store.add_core(
        "ausdb", CoreInput(id="c", name="C", url="https://h.example.invalid:1616", api_key="k")
    )
    (tmp_path / "portal.db").chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        env = {
            "PORTAL_DATA_DIR": str(tmp_path),
            "PORTAL_SECRET_KEY": SECRET,
            "CA_DIR": str(tmp_path.parent / "ca-x"),
        }
        assert [c.id for c in load_from_env(env).customers] == ["ausdb"]
    finally:
        tmp_path.chmod(0o755)
        (tmp_path / "portal.db").chmod(0o644)
