> Diese Seite ist eine Kopie von `docs/ausrollen.md` im Repository. Änderungen bitte dort machen, das Wiki wird daraus neu erzeugt.

# Auf einem Server ausrollen

Diese Anleitung bringt PRTG-Grafana auf einen Server, auf dem **Docker schon läuft**. Zwei Wege führen zum Ziel: mit der Kommandozeile (Weg A) oder über **Portainer** (Weg B). Beide ziehen die fertigen Images aus der GitHub-Registry. Auf dem Server wird nichts gebaut, und der Quelltext wird nicht gebraucht.

> **Voraussetzung für jeden Betrieb außerhalb eines Testnetzes: TLS.** Im Portal melden Sie sich mit einem Passwort an und tragen API-Keys Ihrer PRTG-Server ein. Über `http` laufen beides unverschlüsselt durchs Netz. Betreiben Sie das Portal deshalb nur dann auf `0.0.0.0`, wenn Sie vorher Abschnitt 7 umgesetzt haben (eigenes Zertifikat, Let's Encrypt oder ein Proxy davor). Reines `http` ist für einen Rechner im Labor oder eine Vorführung gedacht.

**Was geprüft wurde:** Die Anleitung wurde am 13.11.2026 in einem frischen, leeren Docker (Docker 29.9.3 mit Compose 5.7.1, ARM64) durchgespielt: bei der Registry anmelden, das Image laden, mit Docker (Weg A) und mit Portainer 2.45.3 (Weg B) starten, im Portal Kunde und PRTG-Server anlegen, in Grafana Organisation und Dashboards ansehen und prüfen, dass Daten ankommen. Weg B wurde zusätzlich im Browser durchgeklickt (Administrator, Registry, Stack mit Web-Editor und Umgebungsvariablen, Update mit erneutem Laden); Abschnitt 4 und 7 folgen dieser Oberfläche. **Nicht geprüft:** ein Linux-Server mit Intel/AMD-Prozessor und die Einrichtung mit einer echten Domain. Stellen, bei denen eine Angabe nur eine Schätzung ist, sind als „Schätzung“ benannt.

## 1. Voraussetzungen

| Was | Wert |
|---|---|
| Server | Linux mit x86-64 oder ARM64 (das Image gibt es für beide) |
| Docker | Docker Engine 24 oder neuer mit dem Compose-Plugin (`docker compose`, nicht `docker-compose`) (Schätzung für die Mindestversion) |
| Arbeitsspeicher, Platz | 2 GB RAM, 10 GB Platz für den Anfang; Platzbedarf und Hochrechnung im Betreiber-Handbuch (Abschnitt „Grenzen dieser Version“, Größe und Ausstattung). Der Platz wächst mit der Zahl der Sensoren und der Aufbewahrungszeit |
| Netz | Der Server erreicht den PRTG-Core auf Port 1616 und `ghcr.io` auf Port 443 |
| Uhrzeit | Zeitsynchronisation aktiv (NTP), sonst stimmen Zeitstempel und Zertifikate nicht |

Prüfen, ob Docker so vorhanden ist:

```bash
docker version
docker compose version
```

Fehlt Docker, folgen Sie zuerst der offiziellen Installationsanleitung für Ihre Distribution: `https://docs.docker.com/engine/install/`. Danach sorgt `sudo systemctl enable --now docker` dafür, dass Docker nach einem Neustart des Servers wieder läuft. Die Dienste starten dann selbst wieder (`restart: unless-stopped`).

## 2. Zugang zur Registry einrichten (einmalig)

Die Images liegen unter `ghcr.io/netmon24/prtg-grafana`. **Ist das Paket öffentlich, entfällt dieser Abschnitt** und Sie gehen gleich zu Abschnitt 3. Ist es privat, braucht, wer sie ziehen soll, ein **Zugriffstoken (classic)** von GitHub:

1. GitHub, Einstellungen, Entwicklereinstellungen, **Personal access tokens (classic)**, neues Token mit nur dem Recht `read:packages`. Ein Ablaufdatum setzen.
2. Ist die Organisation per SSO geschützt: beim Token **Konfigurieren SSO** für `netmon24` freigeben.
3. Das Konto muss Leserecht auf das Paket haben. Das Paket ist mit dem Repository verknüpft, also genügt Leserecht auf das Repository `netmon24/PRTG-Grafana`.

Der Name des Tokens ist unerheblich. Er steht nie in Dateien, nur im Docker-Login.

## 3. Weg A: Docker auf der Kommandozeile

**Anmelden** (das Token kommt über die Standardeingabe, damit es nicht im Verlauf der Shell steht):

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <GitHub-Benutzer> --password-stdin
```

**Dateien holen.** Es genügen `docker-compose.yml` und `.env.example`:

```bash
sudo mkdir -p /opt/prtg-grafana && sudo chown "$USER" /opt/prtg-grafana
cd /opt/prtg-grafana
git clone https://github.com/netmon24/PRTG-Grafana.git .
```

(`git clone` fragt nach Benutzer und dem Token als Passwort. Alternativ laden Sie beide Dateien auf GitHub einzeln herunter.)

**`.env` anlegen:**

```bash
cp .env.example .env
```

Pflichtwerte setzen. Zufallswerte erzeugen Sie mit `openssl rand -hex 24`:

| Variable | Bedeutung |
|---|---|
| `GRAFANA_ADMIN_PASSWORD` | Passwort des Grafana-Administrators |
| `INFLUX_ADMIN_PASSWORD` | Passwort des InfluxDB-Administrators |
| `INFLUX_ADMIN_TOKEN` | Administrator-Token der InfluxDB, 48 Zeichen Zufall genügen |
| `PORTAL_ADMIN_PASSWORD` | Passwort für die Anmeldung im Portal |
| `PORTAL_SECRET_KEY` | verschlüsselt die API-Keys im Portal, **nach der ersten Nutzung nie mehr ändern**, sicher aufbewahren |

**Zeichen in Werten:** Verwenden Sie in Passwörtern kein Dollarzeichen (`$`). Docker Compose und Portainer lesen es als Beginn einer Variable und schneiden den Rest ab. Zufallswerte aus `openssl rand -hex 24` sind unproblematisch.

**Passwörter später ändern:** `GRAFANA_ADMIN_PASSWORD` und `INFLUX_ADMIN_PASSWORD` gelten nur beim allerersten Start der jeweiligen Datenbank. Ein neuer Wert in der `.env` ändert das Passwort im Dienst **nicht**. Ändern Sie es zuerst im Dienst (Grafana: `docker compose exec grafana grafana cli admin reset-admin-password '<neu>'`; InfluxDB: `docker compose exec influxdb influx user password --name admin --password '<neu>'`) und tragen Sie danach denselben Wert in die `.env` ein, denn das Portal meldet sich damit in Grafana an. Das Portal-Passwort (`PORTAL_ADMIN_PASSWORD`) gilt ebenfalls nur, bis es im Portal unter **Einstellungen** geändert wurde. `INFLUX_ADMIN_TOKEN` lässt sich nachträglich nicht über die `.env` ändern.

**Aufbewahrung:** `INFLUX_RETENTION_DAYS` gilt für neu angelegte Buckets. Bei bestehenden verlängert das Portal die Aufbewahrung beim nächsten Start und beim Speichern eines Kunden. Ein Verkürzen nimmt es nicht vor, weil dabei Daten gelöscht werden; das geschieht von Hand in InfluxDB (`influx bucket update`).

Auf einem Server fast immer nötig:

| Variable | Wozu |
|---|---|
| `PORTAL_BIND=0.0.0.0` | Das Portal ist sonst nur auf dem Server selbst erreichbar (Standard `127.0.0.1`). Außerhalb eines Testnetzes nur zusammen mit TLS (Abschnitt 7). |
| `PRTG_GRAFANA_VERSION` | Welche Version läuft. Ohne Eintrag gilt die Version der Compose-Datei. Für den Betrieb festschreiben. |

Grafana hört standardmäßig auf allen Schnittstellen (Port 3000, `GRAFANA_PORT`; nur lokal mit `GRAFANA_BIND=127.0.0.1`), InfluxDB nur lokal (`INFLUX_BIND`). **Der Antwortserver für Let's Encrypt belegt außerdem Port 80 des Servers** (`ACME_HTTP_PORT`, `ACME_HTTP_BIND`); er beantwortet nur offene Prüfungen, sonst 404. Ist Port 80 belegt oder soll er zu bleiben, `ACME_HTTP_BIND=127.0.0.1` setzen. Öffnen Sie das Portal und Grafana erst im Netz, wenn Sie in Abschnitt 7 TLS eingerichtet haben oder ein Proxy davor steht.

**Prüfen und starten:**

```bash
docker compose config --quiet && echo "Datei in Ordnung"
docker compose pull
docker compose up -d
docker compose ps
```

`docker compose ps` zeigt vier Dienste: `influxdb`, `grafana`, `collector`, `portal`. InfluxDB und Grafana stehen nach etwa einer Minute auf „healthy“. Der Dienst `setup` gehört nicht dazu, das Portal übernimmt die Einrichtung.

Weiter mit Abschnitt 5.

## 4. Weg B: Portainer

Die Bezeichnungen sind die englischen Texte von Portainer 2.45.3. Andere Versionen weichen leicht ab.

**Portainer selbst, falls neu:** Neuere Portainer-Versionen (getestet 2.45.3) verlangen bei der ersten Einrichtung ein **Setup-Token**. Es steht im Protokoll des Portainer-Containers: `docker logs portainer 2>&1 | grep setup_token`. Fügen Sie es auf der Einrichtungsseite ein, bevor Sie den Administrator anlegen. Danach fragt Portainer nach **Edge Compute**: **Skip**. Im **Quick Setup** wählen Sie **Get Started**.

**Registry in Portainer eintragen:**

1. Im Menü links unter Administration **Registries**, dann **Add registry**, Anbieter **Custom registry**.
2. Name `ghcr`, Registry URL `ghcr.io`, den Schalter **Authentication** einschalten, Username = GitHub-Benutzer, Password = das Token aus Abschnitt 2.
3. **Add registry**. Portainer nutzt die Anmeldung automatisch, wenn ein Image mit `ghcr.io/…` gezogen wird.

**Stack anlegen:**

1. Auf der Startseite die Umgebung **local** anklicken, im Menü links **Stacks**, dann **Add stack**.
2. Name: `prtg-grafana`. Der Name wird zum Präfix der Volumes (Abschnitt 8).
3. **Build method: Web editor**. Den Inhalt der Datei `docker-compose.yml` hineinkopieren. Sie enthält kein `build`, weil sie nur Images zieht, deshalb läuft sie im Web-Editor.
4. Unter **Environment variables** die Werte aus der Tabelle in Abschnitt 3 eintragen: die fünf Pflichtwerte `GRAFANA_ADMIN_PASSWORD`, `INFLUX_ADMIN_PASSWORD`, `INFLUX_ADMIN_TOKEN`, `PORTAL_ADMIN_PASSWORD`, `PORTAL_SECRET_KEY`, dazu `PORTAL_BIND=0.0.0.0` und `PRTG_GRAFANA_VERSION`. Über **Advanced mode** fügen Sie mehrere Zeilen im Format `NAME=Wert` auf einmal ein und schalten mit **Simple mode** zurück, dann stehen sie als einzelne Felder da. Über **Load variables from .env file** laden Sie eine fertige `.env`.
5. **Deploy the stack**. Fehlt ein Pflichtwert, bricht Portainer mit dem Text aus der Compose-Datei ab, etwa „PORTAL_SECRET_KEY fehlt in der .env“.

Variante: **Build method: Repository** mit `https://github.com/netmon24/PRTG-Grafana`, Authentifizierung mit GitHub-Benutzer und Token und dem Pfad `docker-compose.yml`. Das lohnt sich, wenn Sie Änderungen an der Compose-Datei automatisch nachziehen wollen. Die Werte der Umgebung tragen Sie trotzdem in Portainer ein.

Die Werte stehen danach in der Datenbank von Portainer. Wer dort Administrator ist, sieht sie. Das Passwort des Portals und `PORTAL_SECRET_KEY` sollten Sie zusätzlich im Passwortmanager ablegen.

Beim Starten sehen Sie unter **Containers** die vier Container `prtg-grafana-influxdb-1`, `…-grafana-1`, `…-collector-1`, `…-portal-1`. InfluxDB und Grafana werden nach etwa einer Minute „healthy“.

## 5. Erste Schritte nach dem Start

1. Portal öffnen: `http://<Server>:8088` (Port `PORTAL_PORT`; mit TLS aus Abschnitt 7 `https://...`), anmelden mit `PORTAL_ADMIN_USER` (Standard `admin`) und `PORTAL_ADMIN_PASSWORD`.
2. **Kunde hinzufügen**, dann **Core-Server hinzufügen**: Adresse `https://<core>:1616`, API-Key der neuen PRTG-Oberfläche. **Verbindung testen**. Bei einem selbstsignierten Zertifikat zeigt das Portal den Fingerabdruck, ein Klick vertraut ihm.
   Eine Grafana-Organisation samt Dashboards entsteht erst, wenn der Kunde mindestens einen Core hat. Ein Kunde allein richtet nichts ein.
3. Grafana öffnen: `http://<Server>:3000`, Benutzer `admin`, Passwort `GRAFANA_ADMIN_PASSWORD`. Oben links die Organisation des Kunden wählen. Nach etwa zwei Minuten füllen sich die Dashboards.

Prüfliste, wenn etwas fehlt: Portal, **Übersicht** zeigt je Core eine Ampel mit der letzten Fehlermeldung. Dashboard 1 zeigt den Datenstand. Meldungen und ihre Bedeutung stehen im Betreiber-Handbuch unter „Fehlersuche“.

## 6. Aktualisieren und zurückgehen

Vor jedem Update eine **Sicherung** machen (Abschnitt 8).

**Kommandozeile:**

```bash
# in /opt/prtg-grafana: neue Version in der .env eintragen, dann
docker compose pull
docker compose up -d
docker compose ps
```

**Portainer:** Stack öffnen, Tab **Editor**, im aufklappbaren Abschnitt **Environment variables** `PRTG_GRAFANA_VERSION` auf die neue Version setzen, **Update the stack**. Im Dialog „Are you sure?“ **Re-pull image and redeploy** einschalten und **Update** wählen. Portainer erstellt die Container neu, die Volumes bleiben.

Welche Versionen es gibt, steht im `CHANGELOG.md` und unter **Packages** der Organisation auf GitHub. Der Tag `latest` zeigt immer auf die neueste Version. Für den Betrieb schreiben Sie die Nummer fest, damit ein Neustart nie unbemerkt eine neue Version zieht.

**Zurück auf die vorige Version:** `PRTG_GRAFANA_VERSION` auf die alte Nummer setzen und wie oben neu starten. Die Daten bleiben in den Volumes. Hat die neue Version das Datenformat geändert, steht das im CHANGELOG, dann stellen Sie zusätzlich die Sicherung vor dem Update zurück.

**Grafana und InfluxDB aktuell halten:** Die Compose-Datei nennt Grafana (`grafana/grafana-oss:12.4.3`) mit fester Versionsnummer und InfluxDB als `influxdb:2.7`. Beide Pakete erhalten laufend Sicherheitskorrekturen, die nicht mit einer neuen Version dieses Pakets kommen. Die mitgelieferten Dashboards sind mit Grafana 12.4.3 geprüft (alle Abfragen gegen den Demo-Datenbestand ohne Fehler; die Darstellung im Browser nicht Panel für Panel). Das Image dieses Pakets prüft die Veröffentlichung mit Trivy und bricht bei behebbaren Funden mit hoher oder kritischer Schwere ab. Grafana und InfluxDB prüft ein wöchentlicher Lauf (`.github/workflows/sicherheit.yml`) nur als Hinweis. Stand 15.11.2026 meldete Trivy auch für diese Hersteller-Images behebbare Funde im Betriebssystem (zum Beispiel curl, gnutls); sie lassen sich nur mit neueren Hersteller-Images beheben. Heben Sie die Versionsnummern vor dem Betrieb auf den jeweils neuesten Stand und stellen Sie Grafana und das Portal nicht ungeschützt ins offene Internet (Abschnitt 7).

## 7. Zugriff aus dem Netz absichern (Voraussetzung für den Betrieb)

Ohne eine der beiden ersten Möglichkeiten gehört das Portal nicht ins Netz.

**Reihenfolge bei der Ersteinrichtung:** Das Zertifikat wird im Portal eingerichtet, das Portal soll aber vorher nicht offen im Netz stehen. Lassen Sie deshalb zunächst `PORTAL_BIND=127.0.0.1` (Standard) und öffnen Sie das Portal über einen SSH-Tunnel: `ssh -L 8088:127.0.0.1:8088 <Benutzer>@<Server>`, dann `http://localhost:8088`. Richten Sie dort die Zertifikate ein, starten Sie die Dienste wie angezeigt neu und setzen Sie erst danach `PORTAL_BIND=0.0.0.0`. Für Let's Encrypt muss der Antwortserver auf Port 80 schon laufen; das tut er ab dem ersten Start.


- **Eigene Zertifikate oder Let's Encrypt:** Im Portal auf der Seite **Zertifikate** je Dienst die Betriebsart wählen. Für Let's Encrypt muss der Name auf den Server zeigen und **Port 80** des Servers aus dem Internet erreichbar und frei sein. Ist Port 80 schon belegt, setzen Sie `ACME_HTTP_PORT` auf einen anderen Port und leiten am Router von 80 darauf weiter. Details im Betreiber-Handbuch.
- **Proxy des Betreibers:** Sie können auch einen eigenen Reverse-Proxy (etwa der von Portainer genutzte) vor Grafana und Portal stellen. Dann bleiben beide Dienste auf `http` und der Proxy terminiert TLS.
- **Sitzungscookie:** Läuft das Portal über `https`, setzt es das Cookie mit dem Merkmal `Secure` (zusätzlich `HttpOnly` und `SameSite=Strict`). Das gilt auch hinter einem Proxy, wenn dieser den Kopf `X-Forwarded-Proto: https` mitschickt. Die meisten Proxys tun das, bei nginx und Traefik ist es eine Zeile Konfiguration. Fehlt der Kopf, bleibt das Cookie ohne `Secure` und der Browser sendet es bei einem Wechsel auf `http` mit.
- **Firewall:** Offen nur, was nötig ist: Grafana (3000), Portal (8088), Port 80 nur für Let's Encrypt. InfluxDB (8086) bleibt nach innen, `INFLUX_BIND` bleibt auf `127.0.0.1`. Docker veröffentlicht Ports an der Firewall des Servers vorbei. Beschränken Sie den Zugriff deshalb über `*_BIND` oder die Regeln in der Kette `DOCKER-USER`.

## 8. Sichern und wiederherstellen

Zu sichern sind fünf Volumes. Sie heißen `<Stackname>_<Volume>`. Auf der Kommandozeile heißt der Stack wie das Verzeichnis (`prtg-grafana`), in Portainer wie der Stack (`prtg-grafana`). Die Namen zeigt `docker volume ls`.

| Volume | Inhalt | Verlust heißt |
|---|---|---|
| `influx-data` | alle Messwerte | Daten weg |
| `grafana-data` | Organisationen, Benutzer, eigene Dashboards | Neu einrichten |
| `portal-data` | Kunden, Cores, verschlüsselte API-Keys | Neu eintragen |
| `tls-certs` | Zertifikate und Schlüssel | Neu hochladen oder neu besorgen |
| `collector-token` | Schreib-Token des Collectors | stellt das Portal neu aus |

Zusätzlich gehören `PORTAL_SECRET_KEY` und die `.env` an einen sicheren Ort. Ohne den Schlüssel sind die gesicherten API-Keys nicht lesbar.

**Sichern** (für `influx-data` vorher kurz stoppen, damit die Datei sauber ist):

```bash
docker compose stop influxdb collector
for v in influx-data grafana-data portal-data tls-certs collector-token; do
  docker run --rm -v prtg-grafana_$v:/v:ro -v "$PWD":/b alpine \
    tar czf /b/$v-$(date +%F).tgz -C /v .
done
docker compose start influxdb collector
```

**Wiederherstellen** in ein leeres Volume (Dienste vorher stoppen: `docker compose down`, ohne `-v`):

```bash
docker volume create prtg-grafana_portal-data
docker run --rm -v prtg-grafana_portal-data:/v -v "$PWD":/b:ro alpine \
  tar xzf /b/portal-data-2026-11-12.tgz -C /v
docker compose up -d
```

Das Beispiel zeigt `portal-data`; für die anderen Volumes ersetzen Sie Namen und Datei. Zusammen mit `portal-data` gehört immer der passende `PORTAL_SECRET_KEY` zurück. Dateirechte und Besitzer bleiben beim Zurückspielen erhalten. In Portainer führen Sie die gleichen Befehle auf dem Server aus. Sichern Sie die Dateien danach außerhalb des Servers.

**Stack löschen:** Wollen Sie die Daten behalten, setzen Sie in Portainer bei **Remove** **kein** Häkchen bei „Remove associated volumes“ (fehlt die Auswahl in Ihrer Portainer-Version, bleiben die Volumes stehen; `docker volume ls` zeigt sie). Auf der Kommandozeile löscht `docker compose down -v` die Volumes mit, `docker compose down` lässt sie.

## 9. Wenn etwas nicht klappt

| Meldung oder Beobachtung | Ursache und Abhilfe |
|---|---|
| `denied` oder `unauthorized` beim Ziehen | Anmeldung fehlt oder ist abgelaufen, das Token hat nicht `read:packages`, die SSO-Freigabe fehlt oder das Konto hat kein Leserecht auf das Repository |
| `manifest unknown` | Die Version aus `PRTG_GRAFANA_VERSION` gibt es nicht. Nummer ohne `v` prüfen |
| `no matching manifest for linux/…` | Die Architektur fehlt. Das Image gibt es für x86-64 und ARM64, andere (etwa 32-Bit-ARM) laufen nicht |
| `PORTAL_SECRET_KEY fehlt in der .env` | Pflichtwert nicht gesetzt (Weg A: `.env` im selben Verzeichnis wie die Compose-Datei, Weg B: Umgebungsvariable im Stack) |
| `port is already allocated` oder `address already in use` | Der Host-Port ist belegt. `GRAFANA_PORT`, `PORTAL_PORT`, `INFLUX_PORT` oder `ACME_HTTP_PORT` ändern |
| Portal im Netz nicht erreichbar | `PORTAL_BIND` steht auf `127.0.0.1` (Standard). Auf `0.0.0.0` setzen und neu starten, Firewall prüfen |
| Portal zeigt „Einrichtung fehlgeschlagen“ oder Grafana hat keine Dashboards | Das Portal versucht die Einrichtung nach dem Start rund drei Minuten lang erneut (Grafana und InfluxDB brauchen nach einem Neustart etwas) und gibt dann auf. Danach `docker compose restart portal` oder einen Kunden im Portal speichern; die Ursache steht in `docker compose logs portal` |
| Ein Core meldet 503, „Die PRTG-Lizenz ist inaktiv“ | Die Lizenz des PRTG-Cores ist inaktiv oder abgelaufen; PRTG sperrt dann die ganze API v2. Im Core die Lizenz aktivieren (Betreiber-Handbuch, Fehlersuche) |
| Ein Dienst startet immer wieder neu | `docker compose logs <Dienst>` bzw. in Portainer **Logs** des Containers lesen |
| Dashboards bleiben leer | Portal, Übersicht: Ampel und letzte Fehlermeldung des Cores. Danach Handbuch, „Fehlersuche“ |

Mehr im Betreiber-Handbuch (`docs/betreiber-handbuch.md`).
