# Roadplanner 3.0 – Vision & UX Blueprint

**Status:** Verbindliches Zielbild
**Stand:** 21. Juli 2026
**Projekt:** `azedler/roadplanner`
**Zielrelease:** Roadplanner 3.0
**Auslieferung:** GitHub → Pull Request → Release → HACS

---

## 1. Zweck

Dieses Dokument beschreibt das gemeinsame Zielbild für Roadplanner 3.0. Es verbindet Produktvision, UX, Datenmodell, Medienlogik, Assistentenverhalten, technische Leitplanken und Umsetzungsreihenfolge.

Roadplanner 3.0 unterstützt die vollständige Reise:

> **Planen → Vorbereiten → Reisen → Erinnern**

Es ist die verbindliche Grundlage für alle folgenden Epics, Architekturentscheidungen und Releases.

---

## 2. Ausgangslage

Roadplanner besitzt bereits:

- Home-Assistant-Custom-Integration
- HACS-Installation und Updates
- öffentliches GitHub-Repository
- Branches `develop` und `main`
- Pull-Request- und Release-Prozess
- Roadbook als zentrale Reisedatenbasis
- Assistent mit Änderungskorb
- Entscheidungsansichten
- OneDrive-Anbindung
- mobile Nutzung auf iPhone und iPad

Bereits erreicht:

- Stopps werden in den Stoppkarten korrekt nummeriert.
- Entscheidungen können den bestehenden Plan als Baseline berücksichtigen.
- Assistentenlinks und Fehlermeldungen wurden verbessert.
- Releases werden über HACS verteilt.

Noch offen:

- Karte, Tagesgrafik und Stoppkarten verwenden noch nicht überall dasselbe Tagesmodell.
- Legacy-Felder wie `day.end` können als Pseudo-Stopp erscheinen.
- Stopps besitzen noch nicht automatisch eine vollständige Bildergalerie.
- Planungsbilder und eigene Reisefotos sind noch nicht sauber getrennt.
- Die Hauptnavigation ist noch funktionsorientiert statt reisephasenorientiert.

---

## 3. Produktvision

Roadplanner ist ein intelligenter Reiseplaner, Reisebegleiter und Erinnerungsmanager für Home Assistant.

Vor der Reise zeigt er, was euch erwartet. Während der Reise unterstützt er Route, Tagesablauf und Entscheidungen. Nach dem Besuch priorisiert er eure eigenen Fotos und verwandelt die Reise schrittweise in ein persönliches Reisetagebuch.

> **Vor der Reise bereitet Roadplanner Orte visuell und inhaltlich vor. Nach dem Besuch sammelt und priorisiert er eure eigenen Erinnerungen.**

Roadplanner soll sich nicht wie eine Datenbank mit Tabs anfühlen, sondern wie eine fortlaufende Reisegeschichte.

---

## 4. Leitprinzipien

### Eine Wahrheit je Information

- Roadbook: Tage, Stopps und Reiseverlauf
- Documents: Buchungen und Nachweise
- Media: Bilder und Zuordnungen
- Expenses: Kosten
- Decisions: offene und getroffene Entscheidungen
- Assistant: Vorschläge, keine ungeprüften Fakten
- Routing: abgeleitete Ansicht, keine führende Datenquelle

### Eine kanonische Stoppfolge

Karte, Tagesgrafik, Navigation, Assistent, Entscheidungen und Export verwenden dieselben echten Stopps in derselben Reihenfolge.

### Review vor fachlicher Änderung

Assistenten und Importe schreiben fachliche Änderungen nicht direkt ins Roadbook. Änderungen werden vorgeschlagen, geprüft und bestätigt.

### Eigene Bilder haben Vorrang

Vor dem Besuch: externe Planungsbilder.
Nach dem Besuch: passende eigene OneDrive-Fotos.

Planungsbilder bleiben erhalten und werden nicht gelöscht.

### Mobile First

- keine abgeschnittenen Dialoge
- keine überdeckenden Toasts
- große Touch-Ziele
- feste Bildverhältnisse
- Lazy Loading
- Safe-Area-Abstände
- keine wichtige Funktion nur per Hover

### Fehlertoleranz

Ein fehlendes Bild, eine Route, eine Geocodierung oder ein externer Dienst darf nicht die gesamte Ansicht unbrauchbar machen.

---

## 5. Reisephasen und Navigation

Roadplanner 3.0 organisiert die Oberfläche nach der Situation des Reisenden.

### Primäre Navigation

1. **Reise**
2. **Heute**
3. **Erinnerungen**
4. **Reisebegleiter**

### Reise

- Reiseübersicht
- Tage und Stopps
- Route
- Entscheidungen
- Dokumente
- Aufgaben
- Kosten
- Import und Einstellungen als Werkzeuge

### Heute

- Tagesbriefing
- aktuelle Route
- nächster Stopp
- Navigation
- Wetter
- Dokumente und Tickets
- spontane Änderungen

### Erinnerungen

- eigene Fotos
- Galerie
- Reisetagebuch
- Tages- und Reisehighlights
- Statistiken
- später Travel Movie

### Reisebegleiter

- Planung unterstützen
- Entscheidungen vorbereiten
- Tagesbriefing
- Hinweise unterwegs
- Abendzusammenfassung

---

## 6. Dashboard 3.0

Das Dashboard wird reise- und situationsbezogen.

### Kopfbereich

- Reisetitel
- Länder und Regionen
- Zeitraum
- Fortschritt
- aktueller Tag
- Titelbild der Reise oder des Tages

### Planung

- Planungsfortschritt
- offene Entscheidungen
- offene Aufgaben
- fehlende Dokumente
- geschätzte Gesamtdistanz
- noch nicht angereicherte Stopps

### Heute

- heutige Etappe
- nächster Stopp
- Übernachtungsziel
- Wetter
- Fahrzeit
- wichtige Hinweise
- Navigation starten

### Aktive Zusammenfassung

Beispiel:

> Guten Morgen. Heute fahrt ihr vom Berg der Kreuze über die Weiße Düne zum RMK Matsi Beach. Die Route umfasst sechs echte Stopps. Für zwei Stopps wurden neue Planungsbilder gefunden.

Technische Diagnose bleibt verfügbar, aber eingeklappt und am Ende der Seite.

---

## 7. Kanonisches Tagesmodell

### Problem

Karte, Tagesgrafik und Stoppkarten können voneinander abweichen. Echte Stopps fehlen oder veraltete Werte wie „Riga“ erscheinen als Pseudo-Stopp.

### Ziel

Ein zentraler Day Renderer erzeugt genau ein abgeleitetes Tagesmodell.

```text
Roadbook-Tag
    ↓
Canonical Day Model
    ↓
Karte | Tagesgrafik | Navigation | Assistent | Entscheidungen | Export
```

### Modell

```json
{
  "day_id": "day-05",
  "date": "2026-07-21",
  "stops": [],
  "start_stop_id": "stop-001",
  "overnight_stop_id": "stop-006",
  "route_segments": [],
  "distance_km": 0,
  "duration_minutes": 0,
  "cover_image": null,
  "warnings": []
}
```

### Regeln

- Nur echte Roadbook-Stopps erscheinen in der Tagesroute.
- `day.start` und `day.end` sind Kontext oder Legacy-Felder, keine zusätzlichen Stopps.
- „Riga“ erscheint nur, wenn ein echter Riga-Stopp existiert.
- Marker, Nummern, Tagesgrafik und Navigation verwenden dieselbe Sequenz.
- Fähren werden als eigene Routensegmente modelliert.

---

## 8. Medienmodell: Planung und Erinnerung

Roadplanner unterscheidet zwei Bildwelten.

### Planungsbilder

Vor dem Besuch vermitteln sie einen visuellen Eindruck.

Provider-Reihenfolge:

1. gespeicherte Planungsbilder
2. Wikimedia Commons
3. Openverse
4. optional später Google Places Photos
5. manuelle URL
6. Platzhalter

Gespeichert werden:

- Provider
- Bild- und Thumbnail-URL
- Quellseite
- Autor
- Lizenz und Lizenz-URL
- Caption
- Abmessungen
- Suchkontext

### Eigene Reisefotos

Nach dem Besuch werden passende OneDrive-Fotos automatisch zugeordnet und bevorzugt.

Bekannte Quelle:

```text
Bilder/Handy_Upload_Iphone_Aron
```

Die Suche berücksichtigt nur relevante Reisejahre und -monate. Vollständige rekursive Scans bei jedem Öffnen sind zu vermeiden.

### Datenmodell

```json
{
  "media": {
    "planning_images": [],
    "travel_images": [],
    "primary_planning_image_id": null,
    "primary_travel_image_id": null,
    "display_mode": "auto"
  }
}
```

### Anzeigepriorität

```text
Eigene geeignete Reisefotos vorhanden?
    Ja → eigene Fotos anzeigen
    Nein → Planungsbilder anzeigen
```

Planungsbilder bleiben als „Vorschau“ erhalten.

---

## 9. Auswahl eigener Bilder

Die Auswahl erfolgt hybrid.

### Lokale technische Vorauswahl

- unscharfe Bilder aussortieren
- Screenshots und Fehlaufnahmen erkennen
- Dubletten und Serienbilder reduzieren
- beschädigte oder zu kleine Dateien verwerfen
- Aufnahmezeit und geografische Nähe bewerten

### Optionale KI-Bewertung

- stärkstes Motiv
- emotionale Relevanz
- Abwechslung
- Eignung als Titelbild
- beste zwei bis drei Highlights pro Stopp

### Ergebnis

- ein Titelbild
- zwei bis drei Galerie-Highlights
- vollständiges Stoppalbum

### Zuordnungsfaktoren

- Aufnahmezeit innerhalb des Besuchsfensters
- geografische Nähe
- Aufenthaltsdauer
- EXIF-Daten
- bekannte Tagesroute
- manuell bestätigte Zuordnungen

Alle Zuordnungen bleiben korrigierbar.

---

## 10. Stopp- und Tagesgalerien

### Stoppkarte

- ein großes Hauptbild
- zwei kleine Vorschaubilder
- festes Seitenverhältnis
- Skeleton beim Laden
- Lazy Loading

### Vollbildgalerie

- Wischen zwischen Bildern
- Quelle und Lizenz
- Hauptbild wählen
- Reihenfolge ändern
- entfernen oder ersetzen
- Planungsbilder und eigene Fotos getrennt anzeigen

### Fehler

Fehler erscheinen im Bildbereich:

> Für diesen Stopp konnten noch keine passenden Bilder geladen werden.
> **Erneut versuchen**

Keine großflächigen roten Toasts über nachfolgenden Inhalten.

### Tagescover

Vor der Reise wird das beste Planungsbild der Tagesstopps verwendet. Nach dem Besuch wird es durch ein geeignetes eigenes Tagesfoto ersetzt.

Verwendung:

- Tageskopf
- Reiseübersicht
- Timeline
- Reisetagebuch
- später Travel Movie

---

## 11. Entscheidungen 3.0

Bei „beibehalten oder wechseln“ muss der aktuelle Roadbook-Plan als erste Option erscheinen.

Kennzeichnung:

```text
AKTUELL GEPLANT
```

Jede Option kann enthalten:

- Hauptbild und zwei Vorschaubilder
- Ort
- Status „aktuell geplant“ oder „Alternative“
- Entfernung und zusätzliche Fahrzeit
- Kosten
- Vor- und Nachteile
- Ausstattungschips
- Öffnungszeiten
- Navigation
- Quellen
- kurze KI-Begründung

Fehlende Bilder oder Routen verhindern die Entscheidung nicht.

Auswahlverhalten:

- aktueller Plan → keine Änderung nötig
- Alternative → Vorschlag in den Änderungskorb
- keine direkte ungeprüfte Roadbook-Änderung

---

## 12. Reisebegleiter

### Vor der Reise

- Stopps anreichern
- fehlende Informationen anzeigen
- Entscheidungen vorbereiten
- Dokumente und Aufgaben prüfen
- Route plausibilisieren

### Morgens

- Tagesroute
- Wetter
- Highlights
- Fahrzeit
- besondere Hinweise

### Unterwegs

- nächster Stopp
- Navigation
- spontane Alternativen
- Einkauf, Tanken und Versorgung
- Buchungs- und Fährhinweise

### Abends

- Tageszusammenfassung anbieten
- Fotos prüfen und zuordnen
- Kosten und Notizen ergänzen
- unerledigte Aufgaben verschieben

### Antwortverhalten

- Nachricht sofort sichtbar
- sichtbarer Denkstatus
- sichere anklickbare Links
- Google-Maps-Links
- mobile persistente Fehlerdialoge
- robuste Normalisierung strukturierter Gemini-Ausgaben

---

## 13. Hintergrundjobs und Performance

### Jobs

- Geocoding
- Planungsbilder suchen
- Lizenzen prüfen
- Kurzbeschreibung und Highlights
- Routensegmente
- OneDrive-Fotos zuordnen
- Bildqualität bewerten
- Tagescover aktualisieren

### Statusmodell

```text
pending → running → partial → completed → failed_retryable → failed_final
```

### Regeln

- begrenzte Parallelität
- Timeouts pro Anbieter
- Retry mit Backoff
- keine Suche bei jedem Dashboard-Aufruf
- Ergebnisse speichern
- Provider-Ausfälle isolieren
- manueller Retry
- technische Details nur in der Diagnose

### Offline und Cache

- Vorschaubilder vor Reisebeginn laden
- Originale erst in der Galerie
- Cache nur für aktive Reise
- Cachegröße begrenzen
- Delta Sync für OneDrive
- Medien-IDs statt dauerhaft gespeicherter ablaufender Download-URLs

---

## 14. Technische Architektur

Gemeinsame Domain Services:

- `StopOrderService`
- `CanonicalDayService`
- `ImageProviderService`
- `MediaSelectionService`
- `OneDriveAssignmentService`
- `DecisionService`
- `AssistantStructuredOutputService`
- `BackgroundJobService`

Provider-Vertrag:

```text
search(context) → normalized image candidates
resolve(candidate) → current URLs and metadata
```

Das Frontend erhält vorbereitete View-Modelle und berechnet keine eigene Geschäftslogik für Reihenfolge, Baseline oder Medienpriorität.

---

## 15. Roadplanner-3.0-Epics

### Epic A – Canonical Travel Model

- einheitliche Stoppfolge
- Canonical Day Renderer
- Karte, Timeline, Navigation und Assistent synchronisieren
- Legacy-Pseudo-Stopps entfernen
- Fähren als Routensegmente

### Epic B – Visual Planning

- Planungsbilder pro Stopp
- Wikimedia Commons
- Openverse
- Galerie
- Tagescover
- Inline-Fehler
- gespeicherte Suchergebnisse

### Epic C – Personal Memories

- OneDrive-Fotos priorisieren
- automatische Zuordnung
- Qualitäts- und Dublettenfilter
- Titelbilder und Highlights
- persönliche Tagescover

### Epic D – Decision Engine 3.0

- aktuelle Planung als Baseline
- mehrere Bilder
- Kosten, Zeit und Ausstattung
- robuste Teilfehler
- verständliche Empfehlung

### Epic E – Travel Companion

- Morgenbriefing
- Tageshinweise
- Abendzusammenfassung
- aktive, aber nicht aufdringliche Vorschläge

### Epic F – UX 3.0

- neue Navigation
- Dashboard 3.0
- Stoppkarten 3.0
- mobile Dialoge
- progressive Offenlegung

### Epic G – Build & Release Automation

- Cachebereinigung
- Tests
- Validator
- Versionsabgleich
- Changelogprüfung
- HACS-Preflight
- Release Notes
- automatisierter GitHub-Release-Prozess

---

## 16. Build- und Release-Zielbild

Langfristiger Befehl:

```bash
python tools/build_release.py 3.0.0
```

Das Werkzeug soll:

1. Arbeitsbaum prüfen
2. Python-Caches entfernen
3. Python- und JavaScript-Tests ausführen
4. Repository validieren
5. Versionen abgleichen
6. Changelog prüfen
7. HACS-Preflight ausführen
8. Release Notes erzeugen
9. Artefakte und Prüfsummen bauen
10. PR- und Releasevorbereitung ausgeben

Später kann GitHub Actions Tag und Release nach grünen Checks und expliziter Freigabe erzeugen.

---

## 17. UX-Regeln

### Muss

- wichtigste Information zuerst
- maximal eine primäre Aktion pro Karte
- technische Details eingeklappt
- klare Lade- und Fehlerzustände
- keine überdeckenden Meldungen
- konsistente Terminologie
- Bilder unterstützen, verdrängen aber nicht Navigation und Reiseinformationen

### Nicht erwünscht

- technische Module als überladene Hauptnavigation
- unterschiedliche Stoppreihenfolgen je Ansicht
- erneute Bildsuche bei jedem Öffnen
- ablaufende URLs als dauerhafte Medienreferenz
- direkte Assistentenschreibzugriffe ohne Review

---

## 18. Akzeptanzkriterien

1. Karte, Tagesgrafik, Stoppkarten, Navigation und Assistent zeigen dieselben echten Stopps in derselben Reihenfolge.
2. Legacy-Texte wie „Riga“ erscheinen nicht als Pseudo-Stopp.
3. Jeder geplante Stopp kann automatisch bis zu drei Planungsbilder erhalten.
4. Quelle, Autor und Lizenz sind nachvollziehbar.
5. Ein Provider-Ausfall blockiert weder Stopp noch Tag.
6. Nach dem Besuch werden passende eigene OneDrive-Fotos bevorzugt.
7. Eigene Bilder werden zeitlich und geografisch zugeordnet.
8. Dubletten und ungeeignete Fotos werden reduziert.
9. Jeder Stopp besitzt Titelbild und Galerie-Highlights.
10. Jeder Tag kann vor und nach dem Besuch ein Coverbild besitzen.
11. Entscheidungen enthalten bei „beibehalten oder wechseln“ immer die aktuelle Planung.
12. Entscheidungen bleiben mit unvollständigen Zusatzdaten nutzbar.
13. Die Navigation orientiert sich an Reisephasen.
14. Assistentenlinks sind anklickbar und sicher.
15. Fehlerdialoge sind auf iPhone und iPad vollständig lesbar.
16. Hintergrundjobs blockieren die Oberfläche nicht.
17. Ermittelte Daten werden gespeichert und nicht unnötig neu geladen.
18. Bestehende Reisen bleiben ohne Datenverlust verwendbar.
19. Tests und HACS-Prüfung sind automatisiert ausführbar.
20. Upgrade und Rollback sind dokumentiert.

---

## 19. Nicht-Ziele des ersten 3.0-Releases

- vollständig automatischer Reisefilm
- unbegrenzte kommerzielle Bildanbieter
- öffentliche Mehrbenutzer-Abstimmungen
- komplexe Social-Funktionen
- vollständige Offline-Navigation
- Cloud-Hosting außerhalb von Home Assistant
- automatische Veröffentlichung ohne menschliche Freigabe

---

## 20. Empfohlene Umsetzungsreihenfolge

1. Canonical Day Model und Day Renderer
2. UX-Wireframes für Dashboard, Tagesansicht und Stoppkarte
3. Medienmodell `planning_images` / `travel_images`
4. Wikimedia- und Openverse-Provider
5. Galerie und Inline-Fehler
6. OneDrive-Zuordnung und Fotoauswahl
7. Tagescover
8. Decision Cards 3.0
9. Travel Companion
10. neue Hauptnavigation
11. Build- und Release-Automation
12. vollständige mobile Abnahme

---

## 21. Offene Entscheidungen

- maximale Anzahl eigener Highlights pro Stopp
- Gewichtung der lokalen Bildqualität
- Opt-in und Kosten für KI-Bildbewertung
- Cachegröße der aktiven Reise
- Zeitpunkt automatischer Hintergrundjobs
- Grenze zwischen automatisch speicherbarer Medienanreicherung und reviewpflichtiger Reiseänderung
- Google Places Photos und API-Kosten
- Navigation auf sehr kleinen Smartphones

---

## 22. Definition of Done

Roadplanner 3.0 ist fertig, wenn:

- Planen, Heute, Erinnerungen und Reisebegleiter konsistent zusammenwirken;
- alle Tagesansichten dasselbe kanonische Modell verwenden;
- Planungsbilder automatisch gefunden und sauber lizenziert angezeigt werden;
- eigene OneDrive-Fotos nach dem Besuch bevorzugt werden;
- Entscheidungen den aktuellen Plan korrekt berücksichtigen;
- die mobile Oberfläche stabil und aufgeräumt ist;
- bestehende Reisen ohne Datenverlust funktionieren;
- Tests und HACS-Prüfungen grün sind;
- Upgrade und Rollback dokumentiert sind;
- das Release reproduzierbar gebaut werden kann.

---

## 23. Kurzfassung

Vor der Reise:

> Roadplanner zeigt, wie die geplanten Orte aussehen könnten, welche Optionen bestehen und was noch vorbereitet werden muss.

Während der Reise:

> Roadplanner zeigt den richtigen Tagesablauf, führt zum nächsten echten Stopp und liefert relevante Hinweise.

Nach dem Besuch:

> Roadplanner erkennt passende eigene Fotos, ersetzt fremde Vorschauen in den Hauptansichten und baut daraus Erinnerungen, Tagesgeschichten und später einen Reisefilm.

**Roadplanner 3.0 ist damit ein durchgängiger Reisebegleiter vom ersten Entwurf bis zur persönlichen Erinnerung.**
