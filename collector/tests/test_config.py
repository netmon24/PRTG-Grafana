from pathlib import Path

import pytest

from prtg_collector.config import AppConfig, ConfigError, CoreConfig, load_config

EXAMPLE = Path(__file__).resolve().parents[2] / "cores.yaml.example"
ENV = {"PRTG_KEY_MUSTERFIRMA_HQ": "geheim-1", "PRTG_KEY_BEISPIEL_LAB": "geheim-2"}


def _write(
    tmp_path: Path, customer_id: str = "kunde", core_id: str = "core", extra: str = ""
) -> Path:
    p = tmp_path / "cores.yaml"
    p.write_text(
        f"""customers:
  - id: {customer_id}
    cores:
      - id: {core_id}
        url: "https://x.example.invalid:1616"
        api_key_env: "KEY"
{extra}"""
    )
    return p


@pytest.mark.parametrize("bad", ['"Kunde A"', '"' + "a" * 33 + '"', '""', '"ÄÖ"'])
def test_rejects_bad_ids(tmp_path, bad):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, customer_id=bad), {"KEY": "x"})


def test_missing_env_var_names_the_variable(tmp_path):
    cfg = load_config(_write(tmp_path), {})
    with pytest.raises(ConfigError) as err:
        cfg.customers[0].cores[0].api_key({})
    assert "KEY" in str(err.value)


def test_secret_value_never_in_error_message(tmp_path):
    with pytest.raises(ConfigError) as err:
        load_config(_write(tmp_path, customer_id='"Kunde A"'), {"KEY": "geheimwert"})
    assert "geheimwert" not in str(err.value)


def test_example_file_loads():
    cfg = load_config(EXAMPLE, ENV)
    assert isinstance(cfg, AppConfig)
    assert [c.id for c in cfg.customers] == ["musterfirma", "beispiel-gmbh"]
    assert cfg.retention_days == 365
    core: CoreConfig = cfg.customers[0].cores[0]
    assert core.interval_s == 60 and core.api_key(ENV) == "geheim-1"


def test_interval_below_10_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, extra="        interval_s: 5\n"), {"KEY": "x"})


def test_duplicate_core_ids_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        """customers:
  - id: a
    cores:
      - {id: c, url: "https://h:1616", api_key_env: K}
      - {id: c, url: "https://h2:1616", api_key_env: K}
"""
    )
    with pytest.raises(ConfigError):
        load_config(p, {"K": "x"})


def test_nested_quantifier_regex_is_rejected_and_normal_ones_pass():  # F07
    import pytest

    from prtg_collector.config import Filters

    for bad in ("(a+)+$", "(.*)*x", "^(\\w+\\s?)*$"):
        with pytest.raises(ValueError):
            Filters(name_regex=bad)
    for ok in ("^srv-[0-9]+$", "(web|app)-\\d+", "(ab)+"):
        assert Filters(name_regex=ok).name_regex == ok


def test_interval_above_five_minutes_is_rejected():  # F28
    import pytest

    from prtg_collector.config import CoreConfig

    with pytest.raises(ValueError):
        CoreConfig(id="c", url="https://x.example.invalid:1616", interval_s=301)
