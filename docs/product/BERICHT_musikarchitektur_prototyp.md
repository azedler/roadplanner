# Musikarchitektur A/B/C — Abschlussbericht

Integration **4.113.0** · Renderer-Add-on **0.27.0** · PR #339 (Struktur-Tags),
PR #340 (Release)

---

## A) Technisch hier bestätigt

Alles in diesem Abschnitt ist in diesem Testsystem gelaufen und gemessen —
gegen Fixtures, Fakes und echtes ffmpeg. **Kein einziger bezahlter Aufruf.**

### Die Kette

| Stufe | Modul | Zustand |
|---|---|---|
| MusicCueSheet (Ausschnitt) | `music_cue_sheet.build_window_cue_sheet` | deterministisch, ohne Medien |
| MusicStyleLock | `music_style_lock` | strukturiert, stabiler Hash |
| Prompt-Direktor (optional) | `music_prototype.validate_director_text` | validiert, verwerfend |
| GenerationPlan | `music_prototype.build_prototype` | 3 Rollen, 3 Anfragen |
| MusicAsset-Store | `trip_film_music_service` | Musikordner, überlebt Neustart |
| Mix A/B/C | `trip_film_music.build_music_variant_package` | je Ebene ein Pegel |
| Lautheit | `audiomux` + `render.mjs` | gemessen, statisch korrigiert |
| Post-Render-Mux | `add_music`-Job je Fassung | `-c:v copy` |

### Die drei Fassungen

- **A · nur Lyria** — ein durchgehendes Stück
- **B · Atmosphäre + Akzent** — Klangbett bei Faktor 0,38, Akzent bei 1,0
- **C · nur Atmosphäre** — dasselbe Klangbett bei Faktor 0,72

Bild, Schnitt, Texte, Karte, Videoclips sind identisch — es ändert sich
ausschließlich der Ton.

### Kosten

3 Generierungen × 0,08 USD = **0,24 USD**, abgerechnet **pro Anfrage**.

Nicht sechs: B und C teilen sich **dieselbe Klangbett-Datei**. Zwei
Klangbetten würden die beiden Fassungen zusätzlich in ihrem *Material*
unterscheiden, und die Antwort aus dem Hörtest wäre wertlos. Ein Test prüft
das auf der Platte, nicht nur im Plan — nach der Generierung liegen genau
drei MP3 im Ordner.

### Lautheit — gemessen

Drei Mischungen durch echtes ffmpeg, EBU R128:

| Fassung | integriert | True Peak | statische Korrektur |
|---|---|---|---|
| A | −20,0 LUFS | −18,4 dBTP | +10,9 dB |
| B | −20,0 LUFS | −16,3 dBTP | +10,4 dB |
| C | −20,0 LUFS | −17,4 dBTP | +14,7 dB |

Spannweite **0,0 LU**, kein Clipping.

**Warum statisch und nicht `loudnorm`:** `loudnorm` regelt in einem Durchgang
den Pegel *während* des Stücks nach. Das Material, das davon am stärksten
verändert wird, ist sparsames, gleichmäßiges — also genau Fassung C. Der
Vergleich wäre dann „Architektur gegen Architektur plus automatischen
Pegelreiter" gewesen, und das Pumpen wäre als „das Klangbett klingt künstlich"
zurückgemeldet worden. Stattdessen wird der Mix als Mix gemessen und um **eine
Zahl** verschoben.

Die Korrektur ist auf ±18 dB begrenzt, und ob das Ziel *erreicht* wurde, steht
im Ergebnis (`music_loudness_matched`). Der eine Fall, in dem die Fassungen
nicht vergleichbar sind, ist damit sichtbar statt verschluckt.

### StyleLock — und was er nicht behauptet

Fest: warm nordic family travel score · gentle to moderate, 75–85 BPM · D-Dur
oder nah verwandt · akustische Gitarre, weiches Cello, warmes Pad · warm,
intim, leicht verspielt, weit, zurückhaltend · dazu die vollständige
Vermeidungsliste, weil Lyria **kein `negative_prompt`** hat.

Jede Eigenschaft trägt selbst `prompt_only`. Lyria nimmt **keine** davon als
Feld entgegen — es gibt kein Tempo-Feld, kein Tonart-Feld, keinen Seed und
keine Dauer. Ein Schema, in dem alles gleich verbindlich aussieht, wäre der
älteste Fehler dieses Projekts an einer neuen Stelle.

Messbar und gemessen: Dauer, Lautheit, True Peak.
**Nicht gemessen und ausdrücklich so benannt: Tempo und Tonart.** Eine
Schätzung im Feldnamen einer Messung ist der Weg, auf dem eine Vermutung zur
Tatsache im Bericht wird.

### Tests

| Datei | prüft |
|---|---|
| `test_music_prototype.py` | 3 Generierungen · geteiltes Klangbett · gleicher Stil in jeder Anfrage · Bett bekommt *keinen* Energiebogen · Fenster begrenzt · Cue Sheet ohne Medien · Direktor darf keine Zeiten nennen |
| `test_music_architecture_mix.mjs` | echte ffmpeg-Mischung, Lautheit, Clipping, Ebenenpegel, Preview-Fades, kein dynamischer Normalisierer |
| `test_trip_film_music_service.py` | 3 Käufe, **zweites Service-Objekt über demselben Ordner** zahlt nichts · genau die eigenen Ebenen je Fassung · fehlende Ebene wird abgelehnt |
| `test_trip_film_music_plan.py` | positionsabhängige Struktur-Tags, und dass der Dienst sie übergibt |

Zwei Mutationen zur Gegenprobe gefahren: das geteilte Klangbett kaputtgemacht
(zwei Tests fallen um), das `position`-Argument entfernt (ein Test fällt um).

### Beim Bauen gefunden und behoben

1. **`Number(null)` ist `0`, nicht `NaN`.** Jede fehlende Lautheitsmessung wäre
   als plausible Null angekommen: ein ungemessener Mix wäre um 18 dB *gesenkt*
   worden, ein ungemessener Peak hätte jede Korrektur bei −1,5 dB gedeckelt.
2. **Der Analyse-Filtergraph zählt Eingänge ab 0, der Mux ab 1.** ffmpeg löst
   das still auf einen anderen Stream auf, statt zu scheitern.
3. **Der Variantenname gehört nicht in den Dateinamen.** Jeder Mux ist ohnehin
   ein eigener Job mit eigenem Ergebnisordner. Mit eigenem Dateinamen wäre die
   Review-Kopie — die im übergebenen Job nach *dem einen* Namen sucht — für
   alle drei still auf den stummen Schnitt zurückgefallen.
4. **Der Lautheitsblock landete versehentlich auch in `createReviewCopy`**, wo
   es weder Ebenen noch Ziel gibt.

---

## B) Auf LIVE zu bestätigen

Nichts davon ist hier messbar:

- welches Lyria-Modell tatsächlich antwortet
- die **tatsächliche Länge** der zurückgegebenen Stücke — es gibt keinen
  Dauer-Parameter und kein Dauerfeld in der Antwort
- die tatsächlichen Kosten je Anfrage und die Gesamtsumme
- **die musikalische Qualität** — der eigentliche Zweck des Ganzen
- ob Klangbett und Akzent harmonisch zusammenpassen oder gegeneinander
  arbeiten (Tonart und Tempo sind nur Promptwünsche)
- ob −20 LUFS unter deinem Film angenehm sitzt

---

## C) Live-Schritte

1. Integration auf **4.113.0** aktualisieren
2. Renderer-Add-on auf **0.27.0** aktualisieren — *ohne das läuft der Vergleich
   auf dem alten Mixer und alle drei Fassungen klingen gleich laut ohne es zu
   sein*
3. Home Assistant neu starten
4. **Keine** neue Foto- oder Videoanalyse
5. **Noch keinen** vollständigen Soundtrack erzeugen
6. Prüfausschnitt rendern (Story-Editor → „Prüfausschnitt (60–90 s)")
7. „Musikarchitektur vergleichen (A/B/C)" öffnen — Ausschnitt, Fassungen und
   Kosten stehen dann da
8. Modell, Zweck, Anfragezahl, Provider-Tracklänge und Schätzung prüfen
9. Bewusst „3 Stück erzeugen (0.24 USD)" bestätigen
10. Für jede Fassung „Fassung A/B/C auflegen"
11. Von jeder eine 480p-Review-Kopie erzeugen
12. Die drei Clips bei ChatGPT hochladen
13. **Stopp.** Auf die Architekturentscheidung warten.

### Die Fragen für den Review

1. Welche Fassung fühlt sich am meisten wie ein fertiger Reisefilm an?
2. Welche wirkt am wenigsten generisch?
3. Welche unterstützt Fotos am besten? Welche Videoclips? Welche die
   Kartenfahrt?
4. Welche lenkt am wenigsten von Text ab?
5. Ist das Klangbett angenehm oder störend?
6. Kämpfen Bett und Akzent miteinander?
7. Ist A — die einfachste Lösung — vielleicht schon die bessere?
8. Welche Architektur soll für den ganzen Film weitergebaut werden?

---

## Was bewusst offen bleibt

Der Code legt sich **nicht** fest. `music_architecture` kennt drei
gleichberechtigte Optionen; keine ist Standard. Hätte das Schema angenommen,
dass jeder Film ein durchgehendes Klangbett hat, wäre das Experiment nicht
mehr widerlegbar gewesen — die Architektur wäre die Voreinstellung und die
Frage wäre still zu „wie laut soll das Bett sein" geworden.

Im Produkt heißt es **Atmosphäre**, nicht „Drone". Ein Drone ist *eine*
Technik, eine zurückhaltende durchgehende Ebene herzustellen; gewollt ist die
Ebene.
