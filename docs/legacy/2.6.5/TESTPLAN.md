# Testplan – Roadplanner 2.6.5

## Statisch

- Python-Module kompilieren.
- Panel-JavaScript mit Node syntaktisch prüfen.
- JSON- und YAML-Dateien parsen.
- Installations- und Full-ZIP mit `unzip -t` prüfen.
- Keine Secrets, `__pycache__`-Ordner oder `.pyc`-Dateien verpacken.

## OneDrive

- Doppelte benachbarte Pfadsegmente normalisieren.
- Kameraordnerpräfix in einem datierten Kindpfad nur in der Anzeige kürzen.
- `2018_alt` und `.picasaoriginals` überspringen.
- `2026`, `2026-07`, `07` unter `2026` und `Juli` unter `2026` erkennen.
- Außerhalb des Reisezeitfensters liegende Jahres-/Monatsordner nicht öffnen.
- Mehrere Seiten innerhalb eines Zeit-/Eintragsbudgets verarbeiten.
- Fortschritt und Wiederaufnahme nach einem begrenzten Lauf prüfen.
- Nach Abschluss in Delta-Betrieb wechseln.

## Kosten

- Alle elf neuen Kategorien akzeptieren.
- Alte Kategorien verlustfrei abbilden.
- Unbekannte Kategorien als `other` behandeln.
- Frontendauswahl und HA-Serviceauswahl abgleichen.

## Assistent

- Composer wird vor dem Thread gerendert.
- Nachrichten werden im Panel neueste zuerst dargestellt.
- Tagesbriefing ist im Hauptaktionsbereich vorhanden.
- Technische Statuskarten stehen ausschließlich im unteren Diagnosebereich.
- Mobile Breiten aus 2.6.3 bleiben ohne horizontalen Überlauf.

## Live-Abnahme

Die echte Microsoft-Anmeldung, Microsoft-Graph-Latenz, der tatsächliche
Kameraordner und die iPhone-/Companion-App-Darstellung werden nach Installation
auf der Zielinstanz geprüft.
