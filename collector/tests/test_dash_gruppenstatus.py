import json


def _dash(load_script):
    mod = load_script("dash_gruppenstatus")
    return mod, mod.dashboard()


def test_file_and_uid(load_script):
    mod, d = _dash(load_script)
    assert mod.FILE == "08-gruppenstatus.json"
    assert d["uid"] == "prtg-gruppenstatus"
    assert d["title"] == "Gruppenstatus"


def test_variables_fixed(load_script):
    _, d = _dash(load_script)
    assert [v["name"] for v in d["templating"]["list"]] == [
        "core",
        "group",
        "tag",
        "tag2",
        "tag3",
        "status",
    ]


def test_tile_panel_has_status_text_mapping_and_text_mode(load_script):
    _, d = _dash(load_script)
    tiles = [
        p
        for p in d["panels"]
        if p["type"] == "stat" and p["options"]["textMode"] == "value_and_name"
    ]
    assert len(tiles) == 1
    mapping = tiles[0]["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert mapping["2"]["text"] == "Ausfall" and mapping["0"]["text"] == "OK"
    assert tiles[0]["options"]["reduceOptions"]["values"] is True


def test_worst_status_logic_down_before_warning_before_ok(load_script):
    mod, _ = _dash(load_script)
    q = mod.group_status_query()
    # Rangfolge: Ausfall (2) vor Warnung (1) vor OK (0) vor Unbekannt (4) vor Pausiert (3)
    pos = [
        q.index(s) for s in ("r._value == 2 then 4", "r._value == 1 then 3", "r._value == 0 then 2")
    ]
    assert pos == sorted(pos)
    assert 'sort(columns: ["prio"], desc: true)' in q and "limit(n: 1)" in q


def test_sensor_count_is_labelled_as_count_in_the_worst_status(load_script):
    """Die Zahl ist die der Sensoren im schlechtesten Status, nicht aller Sensoren der Gruppe."""
    d = load_script("dash_gruppenstatus").dashboard()
    text = json.dumps(d, ensure_ascii=False)
    assert "Sensoren im schlechtesten Status" in text
    assert "(Sensoren: " not in text
    assert '"Sensoren"' not in text
