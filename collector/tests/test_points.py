from datetime import UTC, datetime

from prtg_collector.models import Channel, Device, Event, Inventory, Sensor, Status
from prtg_collector.points import (
    PointStats,
    channel_points,
    device_points,
    event_points,
    join_tags,
    status_points,
    tag_catalog_points,
    worst_status,
)

NOW = datetime(2026, 11, 11, 12, 0, 0, 123456, tzinfo=UTC)


def _dev(id=1, name="srv-01", host="10.0.0.1", tags=("web", "kunde-a")):
    return Device(id=id, name=name, host=host, probe="Probe 1", group="Produktion", tags=tags)


def _sen(id=10, device_id=1, name="Ping", status=Status.UP, message="OK", tags=("web",)):
    return Sensor(
        id=id,
        device_id=device_id,
        name=name,
        type="Ping",
        status=status,
        message=message,
        last_check_age_s=5.0,
        tags=tags,
    )


def _lines(points):
    return [p.to_line_protocol() for p in points]


def test_join_tags_format():
    assert join_tags(("web", "kunde-a")) == "|web|kunde-a|"
    assert join_tags(()) == "|"


def test_device_worst_status_order():
    assert worst_status([Status.UP, Status.DOWN, Status.WARNING]) is Status.DOWN
    assert worst_status([Status.UP, Status.WARNING, Status.UNKNOWN]) is Status.WARNING
    assert (
        worst_status([Status.UP, Status.UNKNOWN]) is Status.UP
    )  # Befund 4: Unbekannt wertet nicht, wenn andere zählen
    assert worst_status([Status.UNKNOWN, Status.PAUSED]) is Status.UNKNOWN
    assert worst_status([Status.UNKNOWN, Status.UNKNOWN]) is Status.UNKNOWN
    assert worst_status([Status.PAUSED, Status.UP]) is Status.UP


def test_all_paused_device_has_status_code_3():
    inv = Inventory(
        devices=[_dev()], sensors=[_sen(status=Status.PAUSED), _sen(id=11, status=Status.PAUSED)]
    )
    line = _lines(device_points("k", "c", inv, {1: "srv.example.invalid"}, NOW))[0]
    assert "status_code=3i" in line and "sensor_count=2i" in line


def test_status_point_has_expected_tags_and_fields():
    inv = Inventory(
        devices=[_dev()], sensors=[_sen(status=Status.DOWN, message="Zeitüberschreitung")]
    )
    stats = PointStats()
    [line] = _lines(
        status_points("musterfirma", "core-hq", inv, {1: "srv-01.example.invalid"}, NOW, stats)
    )
    assert line.startswith("prtg_sensor_status,")
    for part in (
        "customer=musterfirma",
        "core=core-hq",
        "device_id=1",
        "sensor_id=10",
        "dns_name=srv-01.example.invalid",
        r"prtg_tags=|web|",
    ):
        assert part in line
    assert 'status="down"' in line and "status_code=2i" in line
    assert line.endswith(" 1794398400")  # auf Sekunde gerundet
    assert stats.dropped == 0


def test_missing_dns_writes_placeholder_so_queries_never_lack_the_column():
    inv = Inventory(devices=[_dev(host="")], sensors=[_sen()])
    [line] = _lines(status_points("k", "c", inv, {}, NOW))
    assert r"dns_name=nicht\ auflösbar" in line
    assert "host=-" in line  # leerer Host: Platzhalter statt fehlender Spalte


def test_sensor_without_known_device_dropped_and_counted():
    inv = Inventory(devices=[_dev()], sensors=[_sen(device_id=999)])
    stats = PointStats()
    assert status_points("k", "c", inv, {}, NOW, stats) == []
    assert stats.dropped == 1


def test_message_truncated_to_200_and_control_chars_removed():
    inv = Inventory(devices=[_dev()], sensors=[_sen(message="a\x00b\n" + "x" * 300)])
    [line] = _lines(status_points("k", "c", inv, {}, NOW))
    msg = line.split('message="', 1)[1].split('"', 1)[0]
    assert len(msg) == 200 and "\x00" not in msg and "\n" not in msg


def test_special_characters_roundtrip():
    name = "Disk (C:) & Ä,=x"
    inv = Inventory(devices=[_dev(name=name)], sensors=[_sen(name=name)])
    [line] = _lines(status_points("k", "c", inv, {}, NOW))
    assert r"device=Disk\ (C:)\ &\ Ä\,\=x" in line
    assert r"sensor=Disk\ (C:)\ &\ Ä\,\=x" in line


def test_text_channel_value_skipped():
    inv = Inventory(devices=[_dev()], sensors=[_sen()])
    chans = [Channel(10, 1, "Antwortzeit", "ms", 12.5), Channel(10, 2, "Text", "", None)]
    lines = _lines(channel_points("k", "c", inv, chans, {}, NOW))
    assert len(lines) == 1 and "value=12.5" in lines[0] and r"unit=ms" in lines[0]


def test_sensor_without_channels_gives_no_channel_points():
    inv = Inventory(devices=[_dev()], sensors=[_sen()])
    assert channel_points("k", "c", inv, [], {}, NOW) == []


def test_event_without_id_gets_stable_hash():
    ev = Event(
        id=None,
        time=datetime(2026, 11, 11, 11, 0, tzinfo=UTC),
        sensor_id=10,
        device_id=1,
        type="status",
        status=Status.DOWN,
        message="Ausfall",
    )
    a = _lines(event_points("k", "c", [ev], {1: "x"}))
    b = _lines(event_points("k", "c", [ev], {1: "x"}))
    assert a == b and "event_id=" in a[0]


def test_same_input_same_timestamp_same_points():
    inv = Inventory(devices=[_dev()], sensors=[_sen()])
    assert _lines(status_points("k", "c", inv, {}, NOW)) == _lines(
        status_points("k", "c", inv, {}, NOW)
    )


def test_tag_catalog_counts_objects_per_tag():
    inv = Inventory(
        devices=[_dev(), _dev(id=2)], sensors=[_sen(tags=("web",)), _sen(id=11, tags=("web", "db"))]
    )
    lines = _lines(tag_catalog_points("k", "c", inv, NOW))
    web_sensor = [ln for ln in lines if "prtg_tag=web" in ln and "object_kind=sensor" in ln]
    assert len(web_sensor) == 1 and "count=2i" in web_sensor[0]


def test_backslash_in_message_survives():
    inv = Inventory(devices=[_dev()], sensors=[_sen(message=r"Laufwerk C:\ voll")])
    [line] = _lines(status_points("k", "c", inv, {}, NOW))
    assert r"C:\\ voll" in line  # Line Protocol maskiert den Backslash, Inhalt bleibt


def test_channel_without_unit_gets_placeholder_tag():
    inv = Inventory(devices=[_dev()], sensors=[_sen()])
    [line] = _lines(channel_points("k", "c", inv, [Channel(10, 1, "Anzahl", "", 3.0)], {}, NOW))
    assert "unit=-" in line  # Influx lässt leere Tags weg, Abfragen brauchen die Spalte
