import json

import pytest


@pytest.fixture
def module(load_script):
    return load_script("dash_webseiten")


def test_file_uid_and_title(module):
    d = module.dashboard()
    assert module.FILE == "12-webseiten.json"
    assert d["uid"] == "prtg-webseiten"
    assert d["title"] == "Webseiten-Monitoring"


def test_variables_in_fixed_order(module):
    names = [v["name"] for v in module.dashboard()["templating"]["list"]]
    assert names == ["core", "group", "device", "sensor", "tag", "tag2", "tag3"]


def test_queries_use_httpsensor_tag_and_loading_time_channel(module):
    d = module.dashboard()
    queries = " ".join(t["query"] for p in d["panels"] for t in p.get("targets", []))
    assert "httpsensor" in queries
    assert "Ladezeit" in queries and "Loading Time" in queries
    assert "Antwortcode" in queries


def test_table_maps_status_to_text_and_panels_limit(module):
    d = module.dashboard()
    assert len(d["panels"]) <= 12
    table = next(p for p in d["panels"] if p["type"] == "table")
    mapping = json.dumps(table["fieldConfig"], ensure_ascii=False)
    for word in ("OK", "Warnung", "Ausfall"):
        assert word in mapping
    assert any("Datenstand" in p.get("title", "") for p in d["panels"])
    assert "Annahme" in json.dumps(d, ensure_ascii=False)
