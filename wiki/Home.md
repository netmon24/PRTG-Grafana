# PRTG-Grafana

Ihre PRTG-Daten, frei auswertbar: Dieses Paket von netmon24 holt Messwerte und Zustände aus einem oder mehreren PRTG-Servern, speichert sie ein Jahr lang und zeigt sie in 13 fertigen Grafana-Dashboards. Kunden und Kollegen bauen daraus eigene Ansichten, ohne etwas zu programmieren. PRTG wird nur gelesen.

Ein Verwaltungsportal im Browser richtet Kunden und PRTG-Server ein. Dateien bearbeiten müssen Sie dafür nicht.

## Anleitungen
- [[Ausrollen]]: Schritt für Schritt auf einen Server bringen (Docker oder Portainer), aktualisieren, sichern
- [[Betreiber-Handbuch]]: Der laufende Betrieb: Kunden, Zertifikate, Fehlersuche, Grenzen
- [[Tag-Anleitung]]: Eigene Ansichten über PRTG-Tags bauen

## Auf einen Blick
- Fertige Images: `ghcr.io/netmon24/prtg-grafana:<Version>` (für Intel/AMD und ARM)
- Quelltext, Versionen und Änderungsprotokoll: im Repository `netmon24/PRTG-Grafana`
- Fragen und Fehler: bitte als Issue im Repository melden. Sicherheitslücken nicht öffentlich, sondern wie in `SECURITY.md` beschrieben.
