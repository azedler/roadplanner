# Roadplanner 2.6.5 – OneDrive-Scan, Kostenkategorien und Assistenten-UX

Roadplanner 2.6.5 ist ein kumulatives Wartungsrelease für Installationen ab
2.6.3. Es enthält den 2.6.4-Fix für Löschoperationen sowie die folgenden
Verbesserungen.

## OneDrive Personal

- Die OneDrive-Konfiguration wird nur noch im Roadplanner-Fotobereich gepflegt:
  `Fotos → OneDrive einrichten`.
- Die gleichlautenden Felder wurden aus den allgemeinen Integrationsoptionen
  entfernt. Vorhandene Werte werden beim ersten Start in den privaten
  OneDrive-Einstellungsspeicher übernommen.
- Ordnerpfade werden kanonisch normalisiert. Historisch doppelt gespeicherte
  benachbarte Segmente werden bereinigt.
- Die Fortschrittsanzeige kürzt lange Kameraordnerpfade mobil sauber und zeigt
  den vollständigen Pfad als Titel an.
- Datierte Jahres- und Monatsordner außerhalb des Reisezeitraums werden bereits
  auf Ordnerebene übersprungen. Unterstützt werden u. a. `2026`, `2026-07`,
  `07`, `Juli` und Namen wie `Handy_Upload_Iphone_Aron_2026`.
- Versteckte oder technische Ordner wie `.picasaoriginals` und
  `.thumbnails` werden nicht durchsucht.
- Der Standardumfang steigt von 250 auf bis zu 2.000 Metadateneinträge pro
  Lauf. Zusätzlich beendet ein Zeitbudget von standardmäßig 12 Sekunden den
  Lauf kontrolliert. Beide Werte sind im Foto-Dialog konfigurierbar.
- Mehrere Microsoft-Graph-Seiten können in einem Lauf verarbeitet werden.
- Die Fortschrittsanzeige unterscheidet relevante, abgeschlossene und
  übersprungene Ordner sowie das jeweilige Abbruchkriterium.
- Eine neue Scanstrategie setzt alte 2.6.1-Zwischenstände einmalig zurück. Nach
  abgeschlossenem Erstscan arbeitet Roadplanner wieder ausschließlich über den
  gespeicherten Delta-Cursor.

Roadplanner lädt beim Erstscan keine Originalbilder herunter. Es liest nur die
Metadaten der relevanten Ordner und übernimmt ausschließlich Fotos im
Reisezeitfenster einschließlich des konfigurierten Puffers.

## Kostenkategorien

Die Kostenkategorien wurden auf die im Reisealltag benötigte Auswahl reduziert:

- Tanken (`fuel`)
- Laden (`charging`)
- Campingplatz (`campsite`)
- Stellplatz (`motorhome_site`)
- Parken (`parking`)
- Restaurant (`restaurant`)
- Imbiss (`snack`)
- Lebensmittel (`groceries`)
- Fähre (`ferry`)
- Transportmittel (`transport`)
- Sonstiges (`other`)

Bestehende Ausgaben bleiben erhalten. Alte Kategorien werden beim Lesen sicher
abgebildet, zum Beispiel `camping → campsite` und nicht mehr benötigte
Spezialkategorien → `other`. Beim nächsten Speichern des Kostenbuchs werden die
normalisierten Werte dauerhaft geschrieben.

## Assistent

- Das Eingabefeld steht jetzt oben.
- Die neuesten Nachrichten stehen oben; ältere Nachrichten folgen darunter.
- Nach dem Senden bleibt die Ansicht am Anfang des Gesprächs.
- Der Tagesbriefing-Button bleibt als primäre Aktion gut sichtbar.
- Providerstatus, Tokenwerte, Plugins, Verbindungstest und Admin-Diagnose
  befinden sich weiter unten in einem standardmäßig geschlossenen Bereich
  `Technik & Diagnose`.
- Der Änderungskorb bleibt neben dem Gespräch beziehungsweise mobil darunter
  sichtbar.

## Kumulativer 2.6.4-Fix

Bei einer sicheren `remove`-Operation werden fehlende oder nicht objektförmige
`changes`-Werte zu `{}` normalisiert. Add- und Update-Operationen bleiben strikt
validiert.

## Kompatibilität

- Technische Domain: `roadplanner_mcp`
- Bestehender Config Entry bleibt erhalten.
- Roadbook, Dokumente, Kosten, Aufgaben, OneDrive-Tokens, Fotozuordnungen,
  Entscheidungsvorlagen und Assistenteneinstellungen werden nicht gelöscht.
- HA-MCP Custom Component 1.1.0 wird nicht verändert.
