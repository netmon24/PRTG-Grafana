# Betreiber-Handbuch

Dieses Handbuch ist für alle, die PRTG-Grafana betreiben. Wie Sie es auf einen Server bringen, steht in `docs/ausrollen.md`.

## Die vier Dienste

| Dienst | Aufgabe |
|---|---|
| `influxdb` | Die Datenbank. Hier liegen alle Messwerte. |
| `grafana` | Die Anzeige mit den Dashboards. |
| `collector` | Holt die Daten aus PRTG ab und schreibt sie in die Datenbank. |
| `portal` | Die Verwaltungsseite im Browser. |

Für den Notfall gibt es einen fünften Einmal-Dienst, der alles Nötige nachträglich einrichtet: `docker compose run --rm setup`.

## Erste Inbetriebnahme

1. Aus `.env.example` eine `.env` machen und die Pflichtwerte eintragen. Auf einem Server oder mit Portainer folgen Sie `docs/ausrollen.md`.
2. `docker compose up -d` starten. Mit `docker compose ps` sehen Sie danach vier laufende Dienste, InfluxDB und Grafana stehen auf „healthy“. Wer aus dem Quelltext bauen will, nimmt `-f docker-compose.yml -f docker-compose.dev.yml up -d --build`.
3. Im Portal einen Kunden und einen PRTG-Server anlegen und die Verbindung testen.

## Neuer Kunde oder neuer PRTG-Server

Legen Sie ihn im Portal an. Das Portal richtet alles Nötige selbst ein: die Datenablage des Kunden, die Zugangsschlüssel, die Grafana-Organisation, die Datenquelle und die Dashboards. Der Collector übernimmt die Änderung von selbst, ein Neustart ist nicht nötig.

## Benutzer im Portal und Löschen

- Auf der Seite **Benutzer** legen Sie weitere Administratoren an oder löschen sie. Alle haben die gleichen Rechte. Das eigene Konto und der letzte Administrator lassen sich nicht löschen.
- Nach 5 falschen Anmeldungen ist die Anmeldung für 5 Minuten gesperrt, und zwar je Absenderadresse. Steht ein Proxy vor dem Portal, tragen Sie `PORTAL_TRUST_PROXY=true` ein, sonst sieht das Portal nur die Adresse des Proxys.
- **Einen PRTG-Server oder einen Kunden löschen** entfernt auch die gespeicherten Messwerte. Beim Kunden verschwinden zusätzlich die Grafana-Organisation, die Dashboards und die Schlüssel. Das lässt sich nicht rückgängig machen. Sichern Sie vorher (`docs/ausrollen.md`, Abschnitt 8). Zur Bestätigung tippen Sie die Kennung ein.

## Zugänge für Kunden

Das Portal legt für jeden Kunden eine Grafana-Organisation an, aber keine Benutzer. Einen Zugang richten Sie in Grafana als Administrator (`admin`) ein. Die Menünamen können je nach Grafana-Version leicht abweichen:

1. Unter **Administration, Benutzer und Zugriff, Benutzer** einen **neuen Benutzer** anlegen (Name, E-Mail, Benutzername, Passwort).
2. Den Benutzer öffnen und unter **Organisationen** die Organisation des Kunden hinzufügen. Die Kennung ist dieselbe wie im Portal. Als Rolle wählen Sie **Viewer** (nur ansehen) oder **Editor** (darf auch eigene Kopien speichern, siehe `docs/tag-anleitung.md`).
3. Den Benutzer aus allen anderen Organisationen entfernen, falls Grafana ihn dort eingetragen hat, normalerweise in der Hauptorganisation. Die Hauptorganisation darf nicht seine Startorganisation bleiben.

Danach sieht der Kunde nur seine eigene Organisation. Sein Passwort legt er nach der ersten Anmeldung selbst neu fest (im Profil).

## Zertifikate

- **PRTG-Server:** Im Portal den Server bearbeiten, **Verbindung testen** wählen und „Diesem Zertifikat vertrauen“ bestätigen. Ändert sich das Zertifikat später, meldet der Test „Fingerabdruck passt nicht“. Dann prüfen Sie es neu und bestätigen es erneut.
- **Grafana, Datenbank und Portal:** Im Portal auf der Seite **Zertifikate** (siehe Abschnitt „Verschlüsselung“). Die Dienste erkennen die Änderung nach etwa 30 Sekunden und starten selbst neu. Ein Befehl ist nicht nötig.

## Wenn etwas nicht klappt

- In Dashboard 1 zeigen das Feld „Datenstand“ und die Tabelle „Collector-Gesundheit“ Fehler je PRTG-Server.
- Im Portal zeigt die Übersicht für jeden PRTG-Server eine Ampel mit der letzten Fehlermeldung.
- Die Protokolle der Dienste: `docker compose logs collector portal`.
- **Die Datenbank ist ausgefallen:** Dann zeigen die Felder in Grafana Fehlermeldungen. Diese Texte kann Grafana nicht ersetzen. Deshalb erklärt unten rechts jedes Dashboard im Feld „Hinweis zur Anzeige“ in einfachen Worten, was los ist und dass sich die Felder von selbst wieder füllen. Daneben zeigt das Feld „Datenstand“, wie alt der letzte Abruf ist (gelb nach 3, rot nach 10 Abrufen). Der Collector hebt in dieser Zeit nichts auf und überspringt die Abrufe. Sobald die Datenbank wieder läuft (prüfen mit `docker compose ps` und `docker compose logs influxdb`), füllen sich die Dashboards bei der nächsten Aktualisierung. Die Lücke im Verlauf bleibt.
- **Aktualisierung:** Alle Dashboards laden sich alle 5 Minuten neu. Oben rechts lässt sich das ändern (5 Minuten bis 1 Stunde).
- **Die PRTG-Lizenz ist inaktiv:** Ist die Lizenz eines PRTG-Servers abgelaufen oder inaktiv, antwortet er auf jeden Aufruf der Schnittstelle mit dem Fehler 503 und dem Text `LICENSE_INACTIVE`. Das passiert auch mit einem gültigen API-Key, und die normale Weboberfläche auf Port 1616 lässt sich oft trotzdem öffnen. Das Portal und der Collector nennen die Ursache („Die PRTG-Lizenz ist inaktiv“). Die Abhilfe liegt in PRTG: Lizenz aktivieren oder erneuern, danach holt der Collector von selbst wieder Daten. Bis dahin können Sie den Server im Portal ausschalten, dann zeigt Dashboard 1 keinen Fehler für ihn. Prüfen ohne Zugangsdaten geht so: `curl -k https://<server>:1616/api/v2/experimental/probes` zeigt den Fehlercode in der Antwort.
- **Häufige Meldungen:** „API-Key abgelehnt (401)“ heißt, der Key muss in der neuen PRTG-Oberfläche (Port 1616) neu erzeugt werden. „Pfad nicht gefunden (404)“ heißt, der Port stimmt nicht. „Zertifikat nicht vertrauenswürdig“ heißt, Sie vertrauen ihm im Portal oder hinterlegen die ausstellende Stelle.

## Sichern

Sichern Sie diese fünf Datenträger: `influx-data`, `grafana-data`, `portal-data`, `tls-certs` und `collector-token`. Bewahren Sie außerdem den `PORTAL_SECRET_KEY` auf. Ohne ihn sind die gespeicherten API-Keys nicht mehr lesbar. Die Befehle zum Sichern und Wiederherstellen stehen in `docs/ausrollen.md`, Abschnitt 8.

## Verschlüsselung (TLS) für Grafana, Datenbank und Portal

Im Portal auf der Seite **Zertifikate** wählen Sie für jeden Dienst eine von drei Betriebsarten:

- **Ungesichert (http):** Der Standard, gedacht für ein internes Netz.
- **https mit eigenem Zertifikat:** Zertifikat und privaten Schlüssel hochladen (Format PEM, ohne Passwort). Mit dem Hochladen schaltet der Dienst um.
- **https mit Let's Encrypt:** Das Portal besorgt das Zertifikat und erneuert es selbst.

Das Umschalten geht ohne Dateiarbeit. Der Dienst bemerkt die Änderung innerhalb von etwa 30 Sekunden und startet neu, er ist dabei kurz nicht erreichbar. Collector und Portal folgen selbst auf https oder zurück auf http. Beim Zurückschalten auf http bleiben die Zertifikate erhalten.

### Let's Encrypt einrichten

1. Der Name des Dienstes (zum Beispiel `grafana.firma.de`) muss auf Ihren Server zeigen, und **Port 80 des Servers muss aus dem Internet erreichbar sein**. Darüber beantwortet das Portal die Prüfung von Let's Encrypt. Der Port lässt sich mit `ACME_HTTP_PORT` ändern, wenn Port 80 belegt ist. Leiten Sie dann von Port 80 dorthin weiter.
2. Im Portal die E-Mail-Adresse eintragen, den Nutzungsbedingungen zustimmen und speichern. **„Testumgebung verwenden“ ist vorausgewählt.** Die Testzertifikate vertraut kein Browser, dafür verbrauchen Fehlversuche nicht die Grenzen des echten Dienstes. Für das echte Zertifikat nehmen Sie den Haken heraus und besorgen das Zertifikat erneut.
3. Für jeden Dienst den Namen eintragen und „Zertifikat jetzt besorgen“ wählen. Das dauert bis zu eine Minute.
4. Die Erneuerung läuft von selbst: Das Portal prüft täglich und erneuert Zertifikate, die in weniger als 30 Tagen ablaufen. Das Ergebnis und ein möglicher Fehler stehen je Dienst auf der Seite. Scheitert eine Erneuerung, bleibt das bisherige Zertifikat unverändert.

Grenzen: Es funktioniert nur die Prüfung über Port 80. Zertifikate für ganze Domains (Platzhalter) und die Prüfung über DNS gibt es nicht. Wer das braucht, besorgt sich ein eigenes Zertifikat, zum Beispiel mit `certbot`, und lädt es hoch. Das Portal startet nach dem Umschalten selbst neu und ist dann über https erreichbar.

### Technische Hinweise zur Sicherheit

- Die Verbindungen der Dienste untereinander prüfen das Zertifikat nicht, solange ein Dienst TLS nutzt. Das Zertifikat gilt für den öffentlichen Namen, die Dienste sprechen sich aber mit `influxdb` und `grafana` an. Mit `INTERNAL_TLS_VERIFY=true` in der `.env` erzwingen Sie die Prüfung. Eine ausdrücklich gesetzte Adresse (`INFLUX_INTERNAL_URL`, `GRAFANA_INTERNAL_URL`) wird immer geprüft, außer `INTERNAL_TLS_VERIFY=false` verlangt es anders.
- Der private Schlüssel liegt im Datenträger `tls-certs` mit den Rechten 0640 (Besitzer Portal, Gruppe `TLS_KEY_GID`, Standard 2000). Grafana liest ihn über diese Gruppe. Die Datenbank bekommt beim Start eine eigene Kopie, die nur ihr gehört. Der Schlüssel des Let's-Encrypt-Kontos liegt verschlüsselt in der Datenbank des Portals. Das Portal hat keinen Zugriff auf Docker.
- Der kleine Antwortserver auf Port 80 beantwortet nur offene Prüfungen von Let's Encrypt, alles andere mit „404“.
- Kommt ein Dienst nach dem Umschalten nicht wieder hoch, zeigt `docker compose logs <dienst>` den Grund. Zum Zurückschalten stellen Sie im Portal die Betriebsart auf „ungesichert“. Geht das Portal selbst nicht mehr, löschen Sie die Datei `<dienst>.enabled` aus dem Datenträger `tls-certs`.

## Grenzen dieser Version

- Dashboard 5 zeigt Statuswechsel, die aus den Abrufen abgeleitet sind. PRTG-Meldungstexte gibt es nicht, weil PRTG sie über diese Schnittstelle nicht herausgibt.
- Messwerte der Kanäle werden jede Minute als aktueller Wert gespeichert. Ein nachträglicher Verlauf aus PRTG wird nicht abgeholt.
- Bei sehr vielen Kanälen (zehntausende) kann ein Abruf lange dauern. Fällt er aus, bleiben die Statusdaten erhalten, und der Fehler steht unter „Collector-Gesundheit“.
- Die Dashboards 6 (Probe- und Core-Health) und 10 (DNS und Active Directory) erkennen Kanäle an ihrem deutschen Namen, zum Beispiel „Zustand“, „Speichernutzung“ oder „Antwortzeit“. PRTG liefert die Kanalnamen in der Sprache der Oberfläche. Bei englischer oder gemischter Oberfläche zeigen diese beiden Dashboards „Keine Daten“. Die Dashboards 9, 11 und 12 kennen deutsche und englische Namen. Ob PRTG die Namen tatsächlich nach der Oberflächensprache liefert, wurde nicht geprüft.
- Dashboard 6 erwartet die Standard-Nummern von PRTG: Gerät 40 (lokale Probe), Sensor 1003 (Zustand der Probe) und Sensor 1002 (Zustand des Servers). Weichen sie ab, bleibt das Dashboard leer.
- Hat ein Kunde mehrere PRTG-Server, unterscheiden die Dashboards die Objekte nach Server und Nummer. PRTG vergibt die Nummern je Server, sie können sich zwischen Servern wiederholen.
- **Größe und Ausstattung:** Der Demo-Server mit drei PRTG-Servern und rund 1300 Sensoren im 60-Sekunden-Takt läuft mit 2 GB Arbeitsspeicher. Der Platz im Datenträger `influx-data` wächst mit der Zahl der Sensoren und Kanäle und mit der Aufbewahrungszeit. Messen Sie nach einer Woche mit `docker system df -v` und rechnen Sie auf Ihre Aufbewahrung hoch. Für größere Installationen (ab einigen tausend Sensoren) rechnen Sie mit mehr Arbeitsspeicher und schnellen Datenträgern. Das ist eine Schätzung und nicht gemessen.
- **Geschwindigkeit:** Die Auswahl „Alle“ ist so gebaut, dass die meisten Felder auf dem Demo-Server in unter einer Sekunde laden (vorher 4 bis über 30 Sekunden). Verlaufsfelder brauchen bei 24 Stunden Daten etwa 3 Sekunden für den Status und bis zu 26 Sekunden für Dashboard 4 mit „Alle“ und rund 900 Reihen. Bei einem PRTG-Server mit etwa 10 000 Sensoren und mehreren Tagen Daten ist das Ziel von höchstens 5 Sekunden nicht belegt. Wählen Sie dort ein Gerät oder einen Sensor aus. Die Auswahllisten für Gruppe, Gerät, Sensor und Kanal zeigen alle Werte der letzten sieben Tage, unabhängig von dem, was Sie darüber ausgewählt haben. Grafana bricht Abfragen nach 30 Sekunden ab. Mit `GF_DATAPROXY_TIMEOUT` (in Sekunden) am Dienst `grafana` lässt sich das erhöhen.
