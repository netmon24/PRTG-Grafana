"""Demo-Ersatz für PRTG (nur zum Vorführen ohne Core-Server): liefert erfundene Geräte und Sensoren.
Start: cd collector && uv run python ../scripts/demo_stub.py   (gibt den Port aus)
Im Portal als Core-Adresse http://host.docker.internal:<Port> eintragen, Zertifikat: nicht prüfen."""

import os, sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "collector" / "tests"))
from stub_server import StubServer, channel, device, obj, sensor

probes = [obj(1, "Demo-Probe", parent={"id": "0", "name": "Root", "type": "REFERENCED_ROOT"})]
groups = [obj(10, "Produktion", parent={"id": "1", "name": "Demo-Probe", "type": "REFERENCED_PROBE"}),
          obj(11, "Büro", parent={"id": "1", "name": "Demo-Probe", "type": "REFERENCED_PROBE"}),
          obj(12, "Rechenzentrum", parent={"id": "1", "name": "Demo-Probe", "type": "REFERENCED_PROBE"})]
specs = [(100, "Fertigungslinie 1", 10, ["maschine", "linie1"]), (101, "Fertigungslinie 2", 10, ["maschine", "linie2"]),
         (102, "Abfüllanlage", 10, ["maschine", "linie1"]), (103, "Drucker Empfang", 11, ["drucker"]),
         (104, "Switch Büro", 11, ["netzwerk"]), (105, "Datenbank-Server", 12, ["server", "db"]),
         (106, "Webserver", 12, ["server", "web"]), (107, "Firewall", 12, ["netzwerk", "kritisch"])]
devices = [device(i, n, g, host=f"10.20.0.{i-90}", tags=t) for i, n, g, t in specs]
sensors, sid = [], 1000
import itertools
cycle = itertools.cycle(["UP", "UP", "UP", "WARNING", "UP", "DOWN", "UP", "PAUSED_BY_PARENT", "UP", "UP"])
for did, name, g, tags in specs:
    for kind in ("Ping", "CPU-Last", "Speicher", "Port 443"):
        sensors.append(sensor(sid, did, f"{kind}", status=next(cycle), kind_name=kind, tags=[kind.lower().split()[0]], parenttags=tags))
        sid += 1
import math, random
random.seed(7)
CHANNELS = [("Antwortzeit", "ms"), ("Auslastung", "%")]
chans = [channel(int(sen["id"]), i, n, 0.0, u) for sen in sensors for i, (n, u) in enumerate(CHANNELS)]


def tick(step: int) -> None:
    """Lässt Kanalwerte schwanken und gelegentlich einen Sensor den Status wechseln (für Dashboard 4 und 5)."""
    for c in chans:
        base = 20 + 15 * math.sin(step / 8 + int(c["id"].split(".")[0]) % 7)
        c["last_measurement"]["value"] = round(max(0.0, base + random.uniform(-4, 4)), 2)
    if step % 5 == 0:
        sen = random.choice(sensors)
        sen["status"] = random.choice(["UP", "UP", "UP", "WARNING", "DOWN"])


with StubServer(probes=probes, groups=groups, devices=devices, sensors=sensors, channels=chans, host="0.0.0.0", port=int(os.environ.get("DEMO_PORT", "0"))) as s:
    print("PORT", s._srv.server_port, flush=True)
    n = 0
    while True:
        tick(n)
        n += 1
        time.sleep(20)
