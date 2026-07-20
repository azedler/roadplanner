# Architekturhinweise – Roadplanner 2.6.5

## OneDrive-Konfiguration

Die privaten OneDrive-Einstellungen im Experience Store sind die Laufzeitquelle.
Alte Config-Entry-Werte werden nur beim ersten Start als Migrationsquelle
verwendet. Das Panel schreibt ausschließlich über `onedrive_configure` in den
privaten Einstellungsspeicher.

## Selektiver Scan

Der Scan besteht aus zwei Phasen:

1. Selektiver rekursiver Erstscan
   - Ordnerstruktur seitenweise lesen;
   - technische und datierte irrelevante Zweige früh verwerfen;
   - Bildmetadaten lokal gegen das Reisefenster prüfen;
   - pro Lauf sowohl Eintrags- als auch Zeitbudget beachten.
2. Delta-Betrieb
   - Änderungen über den gespeicherten Microsoft-Graph-Delta-Cursor lesen;
   - neue, geänderte und gelöschte Dateien nachführen.

Originalbilder werden erst für Vorschau beziehungsweise Anzeige angefordert.

## Kostenmigration

Die Kostenarchivdateien behalten ihr bestehendes Schema. Kategorien werden beim
Laden normalisiert, sodass keine harte, riskante Dateimigration beim Start nötig
ist. Jeder spätere Schreibvorgang persistiert den normalisierten Zustand.

## Assistentenlayout

Der Chat bleibt chronologisch im Backend. Nur die Darstellung verwendet eine
umgekehrte Kopie. Dadurch bleiben Zusammenfassung, Änderungskorb und Provider-
Kontext unverändert. Die Eingabe befindet sich oberhalb der Nachrichtenliste;
die Diagnose ist ein semantisches, standardmäßig geschlossenes `details`-
Element.
