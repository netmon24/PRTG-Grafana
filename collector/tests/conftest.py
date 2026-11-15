import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))  # die Dashboard-Dateien importieren build_dashboards


@pytest.fixture(scope="session")
def load_script() -> Callable[[str], ModuleType]:
    """Lädt ein Skript aus scripts/ als Modul (scripts/ ist kein Paket)."""

    def load(name: str) -> ModuleType:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load
