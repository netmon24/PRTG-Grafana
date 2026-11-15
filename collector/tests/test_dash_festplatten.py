import json

import pytest


@pytest.fixture(scope="module")
def mod(load_script):
    return load_script("dash_festplatten")


def _panel(d, title):
    return next(p for p in d["panels"] if p["title"] == title)


def test_uid_file_and_title(mod):
    d = mod.dashboard()
    assert mod.FILE == "09-festplatten.json"
    assert d["uid"] == "prtg-festplatten"
    assert d["title"] == "Festplatten und Speicherplatz"
    assert [v["name"] for v in d["templating"]["list"]] == [
        "core",
        "group",
        "device",
        "sensor",
        "tag",
        "tag2",
        "tag3",
    ]


def test_queries_select_disk_tags_and_use_variable_filters(mod):
    d = mod.dashboard()
    for p in d["panels"]:
        if p["type"] == "text":
            continue
        for t in p["targets"]:
            q = t["query"]
            if "collector_run" in q:  # Datenstand
                continue
            assert "diskspacesensor" in q and "diskfreesensor" in q
            assert "${tag:regex}" in q and "${core:regex}" in q and "${sensor:regex}" in q


def test_two_numbers_with_bargauge_below(mod):
    d = mod.dashboard()
    total, free = _panel(d, "Speicher gesamt"), _panel(d, "Speicher offen")
    bars = [p for p in d["panels"] if p["type"] == "bargauge"]
    assert total["type"] == free["type"] == "stat"
    assert len(bars) == 1 and bars[0]["options"]["orientation"] == "horizontal"
    assert bars[0]["gridPos"]["y"] > max(total["gridPos"]["y"], free["gridPos"]["y"])
    assert total["fieldConfig"]["defaults"]["unit"] == "bytes"
    steps = {
        s["value"]: s["color"] for s in bars[0]["fieldConfig"]["defaults"]["thresholds"]["steps"]
    }
    assert steps[None] == "#e5484d" and steps[10] == "#f2c94c" and steps[20] == "#35d763"
    assert len(d["panels"]) <= 12
    json.dumps(d)


def test_bytes_unit_does_not_leak_onto_the_percent_column(load_script):
    import re

    d = load_script("dash_festplatten").dashboard()
    table = next(p for p in d["panels"] if p["type"] == "table")
    for override in table["fieldConfig"]["overrides"]:
        matcher = override["matcher"]
        if matcher["id"] == "byRegexp":
            assert not re.search(matcher["options"], "Frei in Prozent")
