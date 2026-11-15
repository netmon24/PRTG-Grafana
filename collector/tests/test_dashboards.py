import json
import re
from itertools import pairwise
from pathlib import Path

import build_dashboards

ORDER = ["core", "group", "device", "sensor", "tag", "tag2", "tag3", "channel", "stype", "status"]
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


def _built(tmp_path: Path) -> dict[str, dict]:
    paths = build_dashboards.build_all(tmp_path)
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in paths}


def test_builds_the_five_base_dashboards_and_every_extra_one_valid(tmp_path):
    dashboards = _built(tmp_path)
    assert {
        "01-gesamtuebersicht.json",
        "02-verfuegbarkeit.json",
        "03-tag-ansicht.json",
        "04-kanalverlauf.json",
        "05-ereignisse.json",
    } <= set(dashboards)
    assert len({d["uid"] for d in dashboards.values()}) == len(dashboards)
    for d in dashboards.values():
        assert d["uid"].startswith("prtg-") and d["title"] and d["panels"]


def test_variable_order_is_fixed_in_every_dashboard(tmp_path):
    for d in _built(tmp_path).values():
        names = [v["name"] for v in d["templating"]["list"]]
        assert names == [n for n in ORDER if n in names]  # gleiche Reihenfolge, Teilmenge erlaubt
        assert names[0] == "core"


def test_panel_count_max_12_and_unique_ids(tmp_path):
    for d in _built(tmp_path).values():
        assert len(d["panels"]) <= 12
        ids = [p["id"] for p in d["panels"]]
        assert len(ids) == len(set(ids))


def test_no_emoji_no_real_hostnames_no_secrets(tmp_path):
    for d in _built(tmp_path).values():
        text = json.dumps(d, ensure_ascii=False)
        assert not EMOJI.search(text)
        assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text)
        assert "token" not in text.lower().replace("tokens", "")


def test_all_queries_use_the_provisioned_datasource_and_default_bucket(tmp_path):
    for d in _built(tmp_path).values():
        for p in d["panels"]:
            if p["type"] == "text":
                continue
            assert p["datasource"]["uid"] == "prtg-influx"
            for t in p["targets"]:
                assert "v.defaultBucket" in t["query"]
                assert 'bucket: "' not in t["query"]  # nie einen festen Bucketnamen einbauen


def test_every_dashboard_shows_data_freshness(tmp_path):
    for d in _built(tmp_path).values():
        titles = [p.get("title", "") for p in d["panels"]]
        assert any("Datenstand" in t for t in titles)


def test_status_never_conveyed_by_colour_alone_tables_map_text(tmp_path):
    d = _built(tmp_path)["01-gesamtuebersicht.json"]
    table = next(p for p in d["panels"] if p["type"] == "table" and "Probleme" in p["title"])
    mapping = json.dumps(table["fieldConfig"])
    for word in ("Warnung", "Ausfall", "Unbekannt"):
        assert word in mapping


def test_channel_dashboard_selects_sensor_and_channel_and_compares_periods(tmp_path):
    d = _built(tmp_path)["04-kanalverlauf.json"]
    names = [v["name"] for v in d["templating"]["list"]]
    assert names == ["core", "group", "device", "sensor", "tag", "tag2", "tag3", "channel"]
    queries = " ".join(t["query"] for p in d["panels"] for t in p.get("targets", []))
    assert "prtg_channel" in queries
    assert "${tag:regex}" in queries  # Auswahl nach PRTG-Tag wie in den anderen Dashboards
    assert "timeShift" in queries  # Vergleich mit Vortag und Vorwoche
    for word in ("Vortag", "Vorwoche"):
        assert word in json.dumps(d, ensure_ascii=False)


def test_event_dashboard_is_derived_from_status_changes_and_says_so(tmp_path):
    d = _built(tmp_path)["05-ereignisse.json"]
    queries = " ".join(t["query"] for p in d["panels"] for t in p.get("targets", []))
    assert "prtg_sensor_status" in queries and "difference(" in queries
    note = next(p for p in d["panels"] if p["type"] == "text")
    assert "abgeleitet" in note["options"]["content"]


def test_device_columns_link_to_channel_dashboard(tmp_path):
    built = _built(tmp_path)
    for name in ("01-gesamtuebersicht.json", "02-verfuegbarkeit.json"):
        text = json.dumps(built[name])
        assert "/d/prtg-kanalverlauf" in text


def test_sensor_type_and_status_variables_exist_after_the_base_ones():
    names = [v["name"] for v in build_dashboards.variables("status", "stype", "core")["list"]]
    assert names == ["core", "stype", "status"]


def test_script_builds_all_dashboards_when_run_directly(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "-I", str(root / "scripts/build_dashboards.py"), str(tmp_path)], check=True
    )
    assert len(list(tmp_path.glob("*.json"))) >= 13


def test_every_data_freshness_tile_turns_yellow_after_3_and_red_after_10_intervals(tmp_path):
    for name, d in _built(tmp_path).items():
        tiles = [
            p for p in d["panels"] if p["type"] == "stat" and p["title"].startswith("Datenstand")
        ]
        assert tiles, name
        for tile in tiles:
            steps = tile["fieldConfig"]["defaults"]["thresholds"]["steps"]
            assert [s["value"] for s in steps][1:] == [180, 600], f"{name}: {steps}"


def test_current_state_counts_each_sensor_once_even_if_a_tag_changed(tmp_path):
    # dns_name ist ein Tag: wechselt er, gibt es zwei Reihen je Sensor. last() allein zählte beide.
    allowed = (
        "collector_run",
        "difference()",
    )  # eine Reihe je Core / Statuswechsel mit eigener Zusammenführung
    for name, d in _built(tmp_path).items():
        queries = [t["query"] for p in d["panels"] for t in p.get("targets", []) if "query" in t]
        for q in queries:
            if any(a in q for a in allowed):
                continue
            for m in re.finditer(r"\|> last\(\)\n(.*)\n(.*)", q):
                assert 'group(columns: ["core", ' in m.group(1) and "top(n: 1" in m.group(2), (
                    f"{name}: last() ohne Verdichtung je Sensor, Gerät oder Kanal"
                )


def test_every_used_variable_is_defined_in_the_dashboard(tmp_path):
    """Eine nicht definierte ${var} bleibt in Grafana wörtlich stehen und macht die Flux-Abfrage ungültig."""
    builtin = {"__interval", "__from", "__to", "timeFilter"}
    for name, d in _built(tmp_path).items():
        defined = {v["name"] for v in d["templating"]["list"]}
        for p in d["panels"]:
            for t in p.get("targets", []):
                used = set(re.findall(r"\$\{(\w+)", t.get("query", "")))
                assert used - builtin <= defined, (name, p["title"], used - builtin - defined)
        for v in d["templating"]["list"]:
            used = set(re.findall(r"\$\{(\w+)", v.get("query", "")))
            assert used - builtin <= defined, (name, v["name"])


def test_collector_table_never_pivots_text_together_with_numbers(tmp_path):
    """last_error (Text) entsteht nur bei Fehlern. In derselben Tabelle wie die Zahlenfelder gibt pivot
    sonst "schema collision" und das Panel zeigt gerade im Fehlerfall nur einen Abfragefehler."""
    d = _built(tmp_path)["01-gesamtuebersicht.json"]
    panel = next(p for p in d["panels"] if p["title"] == "Collector-Gesundheit je Core")
    queries = [t["query"] for t in panel["targets"]]
    for q in queries:
        if "pivot(" in q:
            assert 'r._field != "last_error"' in q
    assert any('r._field == "last_error"' in q and "pivot(" not in q for q in queries)
    assert any(t["id"] == "joinByField" for t in panel["transformations"])


def test_objects_are_keyed_by_core_not_by_bare_id(tmp_path):
    """PRTG vergibt Ids je Core. Hat ein Kunde mehrere Cores im selben Bucket, würden Gruppierung,
    pivot und Verbindung nur über device_id oder sensor_id Objekte verschiedener Cores vermischen."""
    bare = re.compile(
        r'group\(columns: \["(device_id|sensor_id)"[],]'
        r'|rowKey: \["(device_id|sensor_id)"[],]'
        r"|contains\(value: r\.sensor_id, set: ids\)"
    )
    for name, d in _built(tmp_path).items():
        for p in d["panels"]:
            for t in p.get("targets", []):
                assert not bare.search(t.get("query", "")), (name, p["title"])
            for tr in p.get("transformations", []):
                if tr["id"] == "joinByField":
                    assert tr["options"]["byField"] not in ("device_id", "sensor_id"), (
                        name,
                        p["title"],
                    )


def test_device_master_reads_the_whole_period_not_only_the_last_quarter_hour():
    """Die Prozentspalten reichen bis 365 Tage zurück. Läse die Stammdatenabfrage nur 15 Minuten,
    hätten nach einem Collectorausfall oder für gelöschte Geräte die Zeilen keinen Namen."""
    q = build_dashboards.device_master()
    assert "range(start: -365d)" in q
    assert "range(start: -15m)" not in q
    assert "900000000000" in q  # veralteter Punkt gilt als Status "unbekannt" (4)


def test_handbook_names_the_language_dependency_of_dashboards_6_and_10():
    text = (Path(__file__).resolve().parents[2] / "docs" / "betreiber-handbuch.md").read_text(
        encoding="utf-8"
    )
    assert "Sprache der Oberfläche" in text and "Dashboards 6" in text


def test_variable_queries_use_the_tag_index_not_a_raw_data_scan(tmp_path):
    """Bei 10 000 Sensoren braucht ein Vollscan über sieben Tage je Variable bis zu Minuten (gemessen 38 bis
    über 600 s gegen Grafanas Grenze von 30 s); schema.tagValues liest den Tag-Index."""
    for name, d in _built(tmp_path).items():
        for v in d["templating"]["list"]:
            if v["type"] != "query":
                continue
            q = v["query"]
            assert "schema.tagValues(" in q, (name, v["name"])
            assert "distinct(" not in q and "|> group()" not in q, (name, v["name"])
            assert "contains(" not in q, (
                name,
                v["name"],
            )  # nur einfache Prädikate nutzen den Index


def test_committed_dashboard_files_match_the_generator(tmp_path):
    """Die JSON-Dateien in grafana/dashboards müssen aus scripts/build_dashboards.py stammen."""
    committed = Path(__file__).resolve().parents[2] / "grafana" / "dashboards"
    for path in build_dashboards.build_all(tmp_path):
        assert (committed / path.name).read_text(encoding="utf-8") == path.read_text(
            encoding="utf-8"
        ), f"{path.name}: python3 -I scripts/build_dashboards.py ausführen"


def test_tables_list_worst_status_first_not_by_status_number(tmp_path):
    """Unbekannt (4) und Pausiert (3) haben größere Zahlen als Ausfall (2) und dürfen nicht vorn stehen."""
    for name, dash in _built(tmp_path).items():
        for p in dash["panels"]:
            for t in p.get("targets", []):
                q = t.get("query", "")
                only_down_or_warn = "r.status_code == 1 or r.status_code == 2" in q
                assert 'sort(columns: ["status_code"' not in q or only_down_or_warn, (
                    name,
                    p["title"],
                )


def test_selection_filters_are_regex_matches_and_all_means_match_everything(tmp_path):
    """contains(set: [...]) wird je Zeile ausgewertet und läuft bei vielen Sensoren in die Zeitgrenze.
    Ein Regex wird in InfluxDB ausgewertet; "Alle" ist darum `.*` statt einer Liste aller Werte."""
    for name, dash in _built(tmp_path).items():
        for var in dash["templating"]["list"]:
            if var["name"] in (
                "core",
                "tag",
                "tag2",
                "tag3",
                "group",
                "device",
                "sensor",
                "channel",
                "stype",
            ):
                assert var["allValue"] == ".*", (name, var["name"])
        for p in dash["panels"]:
            for t in p.get("targets", []):
                assert ":json}" not in t.get("query", ""), (name, p["title"])


def test_every_dashboard_refreshes_every_five_minutes_by_default(tmp_path):
    for name, dash in _built(tmp_path).items():
        assert dash["refresh"] == "5m", name
        assert dash["timepicker"]["refresh_intervals"][0] == "5m", name


def _fresh_panels(dash):
    return [p for p in dash["panels"] if p.get("title", "").startswith("Datenstand")]


def test_every_dashboard_has_exactly_one_small_datenstand_field_at_the_bottom_right(tmp_path):
    for name, dash in _built(tmp_path).items():
        fresh = _fresh_panels(dash)
        assert len(fresh) == 1, name
        pos = fresh[0]["gridPos"]
        bottom = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in dash["panels"])
        assert pos["x"] + pos["w"] == 24 and pos["y"] + pos["h"] == bottom, name
        assert pos["w"] <= 6 and pos["h"] <= 4, name  # klein
        others = [p for p in dash["panels"] if p is not fresh[0]]
        assert all(
            p["gridPos"]["y"] < pos["y"]
            for p in others
            if p["gridPos"]["x"] + p["gridPos"]["w"] > pos["x"]
        ), name


def test_every_dashboard_has_one_disclaimer_that_explains_errors_and_data_age(tmp_path):
    for name, dash in _built(tmp_path).items():
        notes = [p for p in dash["panels"] if p.get("title") == "Hinweis zur Anzeige"]
        assert len(notes) == 1, name
        content = notes[0]["options"]["content"]
        assert "Datenbank" in content and "nicht erreichbar" in content, name
        assert "Datenstand" in content and "5 Minuten" in content, name
        assert notes[0]["gridPos"]["h"] <= 4, name
        # Erläuterung, wie die Auswahlfelder dieses Dashboards zusammenwirken
        assert "**Auswahl:**" in content and "„oder“" in content, name
        labels = [
            v["label"] for v in dash["templating"]["list"] if v["name"] not in ("tag2", "tag3")
        ]
        if len(labels) > 1:
            assert "„und“" in content and all(label in content for label in labels), name
        if "tag" in [v["name"] for v in dash["templating"]["list"]]:
            assert "Tag 2" in content and "Tag 3" in content, name


def test_removing_the_old_datenstand_tiles_leaves_no_gap_in_the_tile_row(tmp_path):
    for name, dash in _built(tmp_path).items():
        tiles = sorted(
            (
                p["gridPos"]
                for p in dash["panels"]
                if p["type"] == "stat" and p["gridPos"]["y"] == 0 and p["gridPos"]["h"] == 4
            ),
            key=lambda g: g["x"],
        )
        if len(tiles) < 2:
            continue
        for a, b in pairwise(tiles):
            assert a["x"] + a["w"] == b["x"], name
        assert tiles[-1]["x"] + tiles[-1]["w"] == 24, name


def test_three_tag_fields_combine_with_and_in_every_tag_dashboard(tmp_path):
    for name, dash in _built(tmp_path).items():
        names = [v["name"] for v in dash["templating"]["list"]]
        if "tag" not in names:
            continue
        assert names[names.index("tag") : names.index("tag") + 3] == ["tag", "tag2", "tag3"], name
        queries = " ".join(t.get("query", "") for p in dash["panels"] for t in p.get("targets", []))
        if (
            "prtg_tags" in queries
        ):  # jedes Feld ist ein eigener Filter, also „und“ zwischen den Feldern
            for var in ("tag", "tag2", "tag3"):
                assert "${" + var + ":regex}" in queries, (name, var)
