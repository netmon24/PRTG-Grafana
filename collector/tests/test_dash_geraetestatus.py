import json

import build_dashboards as bd


def test_geraetestatus_dashboard(load_script):
    mod = load_script("dash_geraetestatus")
    assert mod.FILE == "13-geraetestatus.json"
    d = mod.dashboard()
    assert d["uid"] == "prtg-geraetestatus"
    assert d["title"] == "Gerätestatus"
    names = [v["name"] for v in d["templating"]["list"]]
    assert names == ["core", "group", "device", "tag", "tag2", "tag3", "stype", "status"]
    assert len(d["panels"]) - 1 <= 12  # ohne die Titelzeile
    ids = [p["id"] for p in d["panels"]]
    assert len(ids) == len(set(ids))

    tables = [p for p in d["panels"] if p["type"] == "table"]
    assert len(tables) == 2
    text = json.dumps(d, ensure_ascii=False)
    for label in ("OK", "Warnung", "Ausfall", "Pausiert", "Unbekannt"):
        assert label in text
    for t in tables:
        mapping = [
            o
            for o in t["fieldConfig"]["overrides"]
            if {"id": "mappings"}.items() <= o["properties"][0].items()
        ]
        assert mapping, "Status-Text-Mapping fehlt"
    assert "prtg-kanalverlauf" in text  # Link zu Dashboard 4
    sensor_q = tables[1]["targets"][0]["query"]
    assert "${stype:regex}" in sensor_q and "${status:regex}" in sensor_q
    assert any(p["type"] == "text" for p in d["panels"])
    assert bd.freshness().splitlines()[0] in text
