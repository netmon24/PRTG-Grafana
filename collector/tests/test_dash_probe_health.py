import json


def _queries(d):
    return [t["query"] for p in d["panels"] if p["type"] != "text" for t in p["targets"]]


def test_identity(load_script):
    mod = load_script("dash_probe_health")
    d = mod.dashboard()
    assert mod.FILE == "06-probe-health.json"
    assert d["uid"] == "prtg-probe-health"
    assert d["title"] == "Probe- und Core-Health"


def test_queries_use_local_probe_device_and_health_sensors(load_script):
    d = load_script("dash_probe_health").dashboard()
    queries = _queries(d)
    assert all('r.device_id == "40"' in q for q in queries if "collector_run" not in q)
    assert any('r.sensor_id == "1003"' in q for q in queries)
    assert any('r.sensor_id == "1002"' in q for q in queries)


def test_memory_panel_in_bytes(load_script):
    d = load_script("dash_probe_health").dashboard()
    mem = [p for p in d["panels"] if "Speicher" in p["title"]]
    assert mem
    assert mem[0]["fieldConfig"]["defaults"]["unit"] == "bytes"
    q = mem[0]["targets"][0]["query"]
    assert "Speichernutzung" in q and "Freier Datenspeicher" in q


def test_status_tiles_map_status_to_text(load_script):
    d = load_script("dash_probe_health").dashboard()
    tiles = [p for p in d["panels"] if p["type"] == "stat" and "Zustand" in p["title"]]
    assert len(tiles) == 2
    for p in tiles:
        values = p["fieldConfig"]["defaults"]["mappings"][0]["options"]
        assert values["2"]["text"] == "Ausfall" and values["0"]["text"] == "OK"
    json.dumps(d)
