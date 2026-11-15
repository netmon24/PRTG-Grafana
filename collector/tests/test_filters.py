from prtg_collector.config import Filters
from prtg_collector.filters import apply_filters
from prtg_collector.models import Device, Inventory, Sensor, Status


def _inv():
    d1 = Device(1, "srv-01", "10.0.0.1", "P", "Produktion", ("web",))
    d2 = Device(2, "srv-02", "10.0.0.2", "P", "Büro", ("db",))
    s = [
        Sensor(10, 1, "Ping", "Ping", Status.UP, tags=()),
        Sensor(11, 1, "Disk (C:) & D", "Disk Free", Status.UP, tags=()),
        Sensor(20, 2, "Ping", "Ping", Status.UP, tags=("kritisch",)),
    ]
    return Inventory([d1, d2], s)


def _ids(inv):
    return sorted(s.id for s in inv.sensors)


def test_empty_filter_keeps_everything():
    assert _ids(apply_filters(_inv(), Filters())) == [10, 11, 20]


def test_filter_by_tag_includes_inherited():
    out = apply_filters(_inv(), Filters(tags=["web"]))
    assert _ids(out) == [10, 11] and [d.id for d in out.devices] == [1]


def test_filter_by_sensor_tag():
    assert _ids(apply_filters(_inv(), Filters(tags=["kritisch"]))) == [20]


def test_filter_sensor_type_and_group():
    out = apply_filters(_inv(), Filters(sensor_types=["Ping"], groups=["Produktion"]))
    assert _ids(out) == [10]


def test_name_regex_handles_parentheses():
    out = apply_filters(_inv(), Filters(name_regex=r"^Disk \(C:\) & D$"))
    assert _ids(out) == [11]


def test_invalid_regex_rejected_at_config():  # Fehlerrunde F07
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Filters(name_regex="(")


def test_overlong_regex_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Filters(name_regex="a" * 300)
