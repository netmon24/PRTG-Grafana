import json


def _module(load_script):
    return load_script("dash_sensorstatus")


def test_file_and_uid(load_script):
    mod = _module(load_script)
    assert mod.FILE == "07-sensorstatus.json"
    d = mod.dashboard()
    assert d["uid"] == "prtg-sensorstatus"
    assert d["title"] == "Sensorstatus"


def test_has_overall_piechart_and_stacked_bars_per_core_without_repeat(load_script):
    d = _module(load_script).dashboard()
    assert len([p for p in d["panels"] if p["type"] == "piechart"]) == 1
    bars = [p for p in d["panels"] if p["type"] == "barchart"]
    assert len(bars) == 1 and bars[0]["options"]["stacking"] == "normal"
    # Grafana setzt die Variable bei Repeat auf einen Einzelwert, die Flux-Filter brechen dann ab
    assert not any("repeat" in p for p in d["panels"])


def test_sensor_table_maps_status_to_text(load_script):
    d = _module(load_script).dashboard()
    table = next(p for p in d["panels"] if p["type"] == "table")
    mapping = json.dumps(table["fieldConfig"], ensure_ascii=False)
    for word in ("OK", "Warnung", "Ausfall", "Pausiert", "Unbekannt"):
        assert word in mapping


def test_variables(load_script):
    d = _module(load_script).dashboard()
    names = [v["name"] for v in d["templating"]["list"]]
    assert names == ["core", "group", "device", "tag", "tag2", "tag3", "stype", "status"]


def test_sensor_table_takes_the_newest_row_per_sensor_for_status(load_script):
    """Wechselt ein Tag (z. B. dns_name), gibt es zwei Reihen je Sensor. last() je Reihe und dann
    reduce nähme den Status der zuletzt stehenden Zeile statt der jüngsten."""
    d = _module(load_script).dashboard()
    table = next(p for p in d["panels"] if p["type"] == "table")
    q = table["targets"][0]["query"]
    cur = q.split("cur = ")[1].split("chg = ")[0]
    assert "top(n: 1" in cur and 'group(columns: ["core", "sensor_id", "_field"])' in cur
