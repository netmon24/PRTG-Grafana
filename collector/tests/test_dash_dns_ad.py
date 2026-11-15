import json

import pytest


@pytest.fixture(scope="module")
def mod(load_script):
    return load_script("dash_dns_ad")


@pytest.fixture(scope="module")
def dash(mod):
    return mod.dashboard()


def _queries(panel):
    return [t["query"] for t in panel.get("targets", [])]


def test_file_and_uid(mod, dash):
    assert mod.FILE == "10-dns-ad.json"
    assert dash["uid"] == "prtg-dns-ad"
    assert dash["title"] == "DNS und Active Directory"
    json.dumps(dash)


def test_variables_in_fixed_order(dash):
    assert [v["name"] for v in dash["templating"]["list"]] == [
        "core",
        "group",
        "device",
        "sensor",
        "tag",
        "tag2",
        "tag3",
    ]


def test_at_most_12_panels_with_unique_ids(dash):
    ids = [p["id"] for p in dash["panels"]]
    assert len(ids) <= 12 and len(set(ids)) == len(ids)


def test_queries_select_by_paessler_tags(dash):
    all_queries = "\n".join(q for p in dash["panels"] for q in _queries(p))
    assert "dnssensor" in all_queries
    assert "ldapsensor" in all_queries
    for p in dash["panels"]:
        if p["type"] == "timeseries":
            assert ("dnssensor" in _queries(p)[0]) != ("ldapsensor" in _queries(p)[0])
            assert p["fieldConfig"]["defaults"]["custom"]["thresholdsStyle"]["mode"] == "dashed"


def test_sensor_table_maps_status_to_text(dash):
    table = next(p for p in dash["panels"] if p["type"] == "table" and "Sensoren" in p["title"])
    mapped = [
        o
        for o in table["fieldConfig"]["overrides"]
        if any(pr["id"] == "mappings" for pr in o["properties"])
    ]
    assert mapped
    texts = {v["text"] for v in mapped[0]["properties"][0]["value"][0]["options"].values()}
    assert {"OK", "Warnung", "Ausfall"} <= texts


def test_availability_ignores_paused_and_unknown(dash):
    panel = next(p for p in dash["panels"] if "Verfügbarkeit" in p["title"])
    assert "<= 2" in _queries(panel)[0]


def test_freshness_panel_present(dash):
    assert any("Datenstand" in p["title"] for p in dash["panels"])
