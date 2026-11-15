"""Wiki-Seiten (wiki/) sind aus docs/ erzeugt und weichen nicht ab."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_wiki_pages_are_current(load_script, tmp_path):
    build_wiki = load_script("build_wiki")
    build_wiki.build(tmp_path)
    expected = sorted(p.name for p in tmp_path.iterdir())
    assert sorted(p.name for p in (ROOT / "wiki").iterdir()) == expected
    for name in expected:
        assert (ROOT / "wiki" / name).read_text(encoding="utf-8") == (tmp_path / name).read_text(
            encoding="utf-8"
        ), f"{name} weicht ab: python scripts/build_wiki.py"


def test_home_and_sidebar_link_every_page(load_script, tmp_path):
    build_wiki = load_script("build_wiki")
    build_wiki.build(tmp_path)
    for page in ("Home", *(p.removesuffix(".md") for p in build_wiki.COPIES)):
        assert f"[[{page}]]" in (tmp_path / "_Sidebar.md").read_text(encoding="utf-8"), page
    for page in (p.removesuffix(".md") for p in build_wiki.COPIES):
        assert f"[[{page}]]" in (tmp_path / "Home.md").read_text(encoding="utf-8"), page
