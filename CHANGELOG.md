# Changelog

Hier steht, was sich in jeder Version geändert hat. Die jeweils neueste Version steht oben.

## 0.3.4 (17.11.2026)
- Alle 13 Dashboards beginnen mit einer großen Überschrift und einem Satz, der sagt, wofür das Dashboard da ist. Die Kennzahlen sind größer.
- Tabellen haben deutsche Spaltenköpfe statt der Feldnamen aus der Abfrage (zum Beispiel „Gerät“, „Meldung“, „Dauer“, „Objekte gelesen“). Die Zeit steht als `17.11.2026 15:38:24`. In „Collector-Gesundheit je Core“ ist die Fehlerzahl ab 1 rot.
- Neu in Dashboard 1: „Zehn Geräte mit den meisten Sensorproblemen“. Neu in Dashboard 2: „Zehn Geräte mit der schlechtesten Verfügbarkeit (24 Stunden)“.
- Kacheln, Balken und Statuszellen sind einfarbig statt mit Farbverlauf.
- Aktualisiert: FastAPI 0.162.7, die Actions für den Bau und die Veröffentlichung des Images (Docker-Actions 4.x und 7.x, `actions/checkout` 7, `setup-uv` 10) und die Entwicklungswerkzeuge. Python bleibt auf 3.12.

## 0.3.3 (15.11.2026)
- In allen Dashboards mit Tag-Auswahl gibt es jetzt drei Tag-Felder (Tag, Tag 2, Tag 3). Mehrere Werte in einem Feld gelten als „oder“, verschiedene Felder als „und“. So lassen sich Objekte finden, die mehrere Tags gleichzeitig tragen.
- Das Feld „Hinweis zur Anzeige“ erklärt in jedem Dashboard, wie die Auswahlfelder dieses Dashboards zusammenwirken.

## 0.3.2 (15.11.2026)
- Dashboard 4 (Sensor- und Kanalverlauf) hat jetzt auch die Auswahl nach Tag. In allen Dashboards gilt die Reihenfolge Core, Gruppe, Gerät, Sensor, Tag, Kanal.
- Der PRTG-Status „Teilweise Down“ zählt als Ausfall.
- Liefert ein PRTG-Server eine leere Antwort, ist das kein Fehler. Das Portal gibt einen Hinweis auf Lizenz und Rechte, und die Objekte erscheinen nach 15 Minuten als „Unbekannt“.
- Ist die Datenbank nicht erreichbar, sagt die Meldung das jetzt klar. Vorher stand dort ein irreführender Hinweis auf den Schreib-Schlüssel.
- Zeichenfolgen wie `${` in PRTG-Namen stören Grafana nicht mehr.
- Der Abruftakt eines PRTG-Servers lässt sich auf 10 bis 300 Sekunden einstellen.
- Eine DNS-Abfrage, die hängt, hält den Abruf nach 5 Sekunden nicht mehr auf.
- Namensfilter mit gefährlichen Mustern wie `(a+)+` werden abgelehnt.
- Ist eine gespeicherte Serveradresse nicht mehr gültig, wird nur dieser Server übersprungen, nicht alle.
- Das Portal ist auf 512 MB Arbeitsspeicher begrenzt.
- Im Dashboard Gerätestatus entfällt die Spalte „Letzte Prüfung vor“, die nie gefüllt war.

## 0.3.1 (15.11.2026)
- Alle Dashboards haben unten rechts zwei kleine Felder: „Hinweis zur Anzeige“ (erklärt, was bei einem Datenbankausfall passiert) und „Datenstand“ (wie alt der letzte Abruf ist, gelb nach 3, rot nach 10 Abrufen).

## 0.3.0 (15.11.2026)
- Grafana 12.4.3 statt 11.6.0, weil die alte Linie keine Korrekturen mehr bekommt. Vor dem Update den Datenträger `grafana-data` sichern, denn ein Zurück auf 11.6 geht ohne Sicherung nicht.
- Alle Dashboards laden sich standardmäßig alle 5 Minuten neu.
- Das Image wird bei jeder Veröffentlichung mit Trivy auf Sicherheitslücken geprüft. Ein wöchentlicher Lauf prüft zusätzlich Grafana und InfluxDB.

## 0.2.6 (15.11.2026)
- Ist die Lizenz eines PRTG-Servers inaktiv, nennen Portal und Collector die Ursache und versuchen es nicht endlos weiter.

## 0.2.5 (15.11.2026)
- Das Portal kennt jetzt mehrere Administratoren (Seite „Benutzer“).
- Die Sperre nach falschen Anmeldungen gilt je Absenderadresse. Fremde können den Administrator nicht mehr aussperren.
- Beim Löschen eines Kunden oder PRTG-Servers werden auch die gespeicherten Messwerte gelöscht, zur Bestätigung tippen Sie die Kennung ein.
- Ein falscher `PORTAL_SECRET_KEY` fällt beim Start auf. Fehler beim Speichern zeigen eine verständliche Seite.
- Die Aufbewahrungszeit lässt sich für bestehende Kunden verlängern. Der Grafana-Ordner „Eigene“ wird angelegt.
- Neue Einstellung `GRAFANA_BIND`.
- Neue Hinweise in der Anleitung zu Passwörtern, Port 80, Benutzern für Kunden und zur Wiederherstellung.

## 0.2.4 (15.11.2026)
- Die Dashboards laden mit „Alle“ deutlich schneller, auf dem Demo-Bestand mit rund 1300 Sensoren in der Regel in unter einer Sekunde.

## 0.2.3 (14.11.2026)
- Behoben: PRTG-Server mit https, aber ohne hinterlegten Fingerabdruck, lieferten nie Daten.
- Behoben: Ein ungültiger Gerätename beendete den ganzen Abruf eines Servers.
- Behoben: In der Tag-Ansicht fehlte eine Auswahl, und alle Felder brachen mit einem Fehler ab.
- Behoben: Bei mehreren PRTG-Servern mit gleichen Nummern stimmten einige Prozentwerte nicht.
- Die Tabellen zeigen den schlechtesten Status zuerst: Ausfall, Warnung, Unbekannt, Pausiert, OK.
- Die Auswahllisten laden auf großen Installationen deutlich schneller.
- Das Portal prüft Serveradressen, Zertifikate und Namensfilter beim Speichern. Die Anmeldung blockiert das Portal nicht mehr.
- Das Image wird aus festgelegten, geprüften Paketversionen gebaut.
- Das netmon24-Logo erscheint im Portal und in der README.

## 0.2.2 (13.11.2026)
- Das Sitzungscookie des Portals ist bei https besonders geschützt. Die Anleitung beschreibt, warum der Betrieb im Netz Verschlüsselung braucht.
- Lizenz Apache-2.0, `NOTICE` und `SECURITY.md`.

## 0.2.1 (13.11.2026)
- Behoben: Sensoren und Geräte wurden doppelt gezählt, wenn sich der DNS-Name eines Geräts änderte.

## 0.2.0 (13.11.2026)
- Acht weitere Dashboards (6 bis 13): Probe- und Core-Health, Sensorstatus, Gruppenstatus, Festplatten, DNS und Active Directory, Zertifikate, Webseiten, Gerätestatus. Neue Auswahl nach Sensortyp und Status.
- Die Dashboards liegen in zwei Ordnern: „Core Health“ und „Sensor Dashboard“.
- Wiki-Seiten und ein Prüfwerkzeug für die Veröffentlichung kamen dazu.

## 0.1.0 (12.11.2026)
- Erste Auslieferung als fertiges Image: `ghcr.io/netmon24/prtg-grafana:<Version>` für Intel/AMD und ARM.
- Anleitung zum Ausrollen mit Docker und mit Portainer.
- Verschlüsselung (TLS) für Grafana, Datenbank und Portal, mit eigenem Zertifikat oder Let's Encrypt.
- Messwerte der Kanäle werden gespeichert. Dashboard 4 (Sensor- und Kanalverlauf) und Dashboard 5 (Meldungen und Ereignisse) kamen dazu.

## Vorstufe: Demo-Fassung (11.11.2026)
- Abruf von PRTG-Servern (nur lesend), Datenbank je Kunde, Verwaltungsportal, Grafana mit einer Organisation je Kunde und die Dashboards 1 bis 3.
