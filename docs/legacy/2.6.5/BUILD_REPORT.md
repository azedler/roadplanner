# Build Report – Roadplanner 2.6.5

## Ausgangspunkt

- Roadplanner 2.6.4 Quellstand
- technische Domain `roadplanner_mcp`
- kumulatives Upgrade für eine Installation ab 2.6.3

## Enthaltene Änderungen

- 2.6.4-Normalisierung für `remove`-Operationen;
- eine zentrale OneDrive-Fotoeinrichtung;
- Migration alter OneDrive-Einstellungen;
- kanonische und sichere OneDrive-Ordnerpfade;
- datumsbewusste Ordnerauswahl;
- Zeit- und Eintragsbudget für den Erstscan;
- verbesserte mobile Fortschrittsanzeige;
- neue Kostenkategorien und Legacy-Abbildung;
- Assistent mit Eingabe oben, neuesten Nachrichten oben, Tagesbriefing und
  eingeklappter technischer Diagnose unten.

## Automatisierte Prüfung

- Kompilierung sämtlicher Python-Module;
- JavaScript-Syntaxprüfung;
- JSON- und YAML-Validierung;
- vorhandene Assistenten-, Routing-, Dokument-, Import- und Medienregressionen;
- neue OneDrive-2.6.5-Tests;
- neue Kostenkategorie-Tests;
- neuer Panel-Smoke-Test für Assistent, OneDrive und Kosten;
- ZIP-Integritätsprüfung;
- Secret-, Cache- und Bytecode-Scan.

## Nicht live geprüft

- Neustart der konkreten Zielinstanz;
- echter Microsoft-Graph-Scan des Nutzerkontos;
- tatsächliche iPhone-/Companion-App-Darstellung;
- Laufzeit und Graph-Latenz beim vollständigen Erstscan.
