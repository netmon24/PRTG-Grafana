"""Erzeugt wiki/ aus docs/. Die Wiki-Seiten sind Kopien, Änderungen gehören nach docs/.

Aufruf: python scripts/build_wiki.py
Veröffentlichen (erst wenn das Repository öffentlich ist und die erste Wiki-Seite im Browser
gespeichert wurde): den Inhalt von wiki/ in ein Verzeichnis kopieren, das auf
https://github.com/netmon24/PRTG-Grafana.wiki.git zeigt, committen und pushen.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPIES = {
    "Ausrollen.md": "ausrollen.md",
    "Betreiber-Handbuch.md": "betreiber-handbuch.md",
    "Tag-Anleitung.md": "tag-anleitung.md",
}
NOTE = (
    "> Diese Seite ist eine Kopie von `docs/{doc}` im Repository. "
    "Änderungen bitte dort machen, das Wiki wird daraus neu erzeugt.\n\n"
)
HOME = """# PRTG-Grafana

Ihre PRTG-Daten, frei auswertbar: Dieses Paket von netmon24 holt Messwerte und Zustände aus einem oder mehreren PRTG-Servern, speichert sie ein Jahr lang und zeigt sie in 13 fertigen Grafana-Dashboards. Kunden und Kollegen bauen daraus eigene Ansichten, ohne etwas zu programmieren. PRTG wird nur gelesen.

Ein Verwaltungsportal im Browser richtet Kunden und PRTG-Server ein. Dateien bearbeiten müssen Sie dafür nicht.

## Anleitungen
{links}
## Auf einen Blick
- Fertige Images: `ghcr.io/netmon24/prtg-grafana:<Version>` (für Intel/AMD und ARM)
- Quelltext, Versionen und Änderungsprotokoll: im Repository `netmon24/PRTG-Grafana`
- Fragen und Fehler: bitte als Issue im Repository melden. Sicherheitslücken nicht öffentlich, sondern wie in `SECURITY.md` beschrieben.
"""
SUMMARIES = {
    "Ausrollen.md": "Schritt für Schritt auf einen Server bringen (Docker oder Portainer), aktualisieren, sichern",
    "Betreiber-Handbuch.md": "Der laufende Betrieb: Kunden, Zertifikate, Fehlersuche, Grenzen",
    "Tag-Anleitung.md": "Eigene Ansichten über PRTG-Tags bauen",
}


def build(out: Path) -> None:
    out.mkdir(exist_ok=True)
    pages = [page.removesuffix(".md") for page in COPIES]
    for page, doc in COPIES.items():
        source = (ROOT / "docs" / doc).read_text(encoding="utf-8")
        (out / page).write_text(NOTE.format(doc=doc) + source, encoding="utf-8")
    links = "".join(f"- [[{p}]]: {SUMMARIES[p + '.md']}\n" for p in pages)
    (out / "Home.md").write_text(HOME.format(links=links), encoding="utf-8")
    sidebar = "".join(f"- [[{p}]]\n" for p in ("Home", *pages))
    (out / "_Sidebar.md").write_text(f"**PRTG-Grafana**\n\n{sidebar}", encoding="utf-8")


def main() -> None:
    build(ROOT / "wiki")


if __name__ == "__main__":
    main()
