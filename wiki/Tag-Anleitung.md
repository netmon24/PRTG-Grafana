> Diese Seite ist eine Kopie von `docs/tag-anleitung.md` im Repository. Änderungen bitte dort machen, das Wiki wird daraus neu erzeugt.

# Eigene Ansicht per PRTG-Tag

Sie brauchen keinen Code. Die Tags, die Sie in PRTG vergeben, stehen in Grafana als Filter bereit.

1. In PRTG am Gerät oder Sensor ein Tag vergeben, zum Beispiel `linie1`. Geerbte Tags der übergeordneten Objekte zählen mit.
2. Nach höchstens zwei Abrufen (etwa zwei Minuten) erscheint das Tag in Grafana.
3. Dashboard **3 Tag-Ansicht** öffnen, oben bei **Tag** `linie1` wählen. Kacheln, Tabelle, Verlauf und Verfügbarkeit zeigen nur noch die passenden Objekte.
4. Oben rechts **Speichern unter** wählen, einen eigenen Namen vergeben und den Ordner **Eigene** wählen. Ihre Kopie bleibt erhalten, wenn das mitgelieferte Dashboard später aktualisiert wird.
5. In der Kopie lassen sich Panels verschieben, hinzufügen und Schwellenfarben ändern.

Hinweis: Das mitgelieferte Dashboard wird beim Einrichten überschrieben. Ändern Sie deshalb immer eine Kopie.

## Mehrere Tags und Felder verknüpfen

- **Innerhalb eines Feldes gilt „oder“:** Wählen Sie im Feld Tag `web` und `db`, sehen Sie Objekte mit mindestens einem der beiden Tags.
- **Zwischen Feldern gilt „und“:** Core, Gruppe, Gerät, Sensor, Tag und Kanal wirken als hintereinander gesetzte Filter. Core `A` plus Tag `web` zeigt Objekte in Core A mit dem Tag `web`.
- **Mehrere Tags, die alle zutreffen müssen:** Dafür gibt es drei Tag-Felder (Tag, Tag 2, Tag 3), die zusammen als „und“ gelten. Tag `web` und Tag 2 `db` zeigt nur Objekte, die beide Tags tragen.
- „All“ in einem Feld bedeutet: dieses Feld filtert nicht.

Der Hinweis steht auch in jedem Dashboard im Feld „Hinweis zur Anzeige“ unten.

