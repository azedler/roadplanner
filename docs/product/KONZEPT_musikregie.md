# Konzept zur Prüfung: Musikregie für einen Reisefilm

**Bitte um kritische Bewertung.** Wir wollen wissen, ob dieses Konzept
tragen kann, bevor wir es fertig bauen. Zustimmung hilft uns nicht;
Einwände helfen. Am Ende stehen konkrete Fragen.

---

## Die Ausgangslage

Roadplanner ist eine Home-Assistant-Integration, die aus einer Reise
einen Film macht: 15:12 Minuten, ein Kapitel je Reisetag, aus den
Fotos und kurzen Videoclips der Reise selbst. Der Film wird von einem
Renderer (Remotion, deterministisch) aus einem **FilmScenePlan**
gezeichnet — einer Liste von Szenen mit Typ, Kapitel, Bildzahl und
Dauer in Frames.

Bisher hat der Film keine Musik oder einen einzelnen Titel aus einem
Ordner, unter den ganzen Film gelegt.

**Die Frage, die wir beantworten wollen:** Kann generative Musik, die
sich am tatsächlichen Ablauf des Films orientiert, einen spürbaren
Unterschied machen — gegenüber einem einzelnen gut gewählten Stück?

---

## Die geplante Kette

```
FilmScenePlan          deterministisch, existiert bereits
   ↓
MusicCueSheet          deterministisch abgeleitet
   ↓
MusicPlan              ein Sprachmodell setzt die Abschnittsgrenzen
   ↓
MusicGenerationPlan    was muss gekauft werden, zu welchem Preis
   ↓
Lyria                  generiert die Audiodateien
   ↓
MusicAssets            gespeichert, wiederverwendbar
   ↓
MusicTimeline          deterministisch: was spielt wann, wie laut
   ↓
Post-Render-Mux        ffmpeg, Videostream wird kopiert
```

### Was jede Stufe tut

**MusicCueSheet.** Aus dem Szenenplan entsteht eine Liste von Cues: je
Kapitel Startzeit, Dauer, Story-Rolle, Wichtigkeit, ob eine Karte
vorkommt, ob ein Videoclip vorkommt, wie bewegt der Abschnitt ist
(Anteil bewegter Szenentypen), welche erzählerische Funktion er hat
(Eröffnung, Tag, Fahrt, Moment, Schluss).

**Ausdrücklich ohne Medien.** Keine Fotos, keine Videos, keine
Dateinamen. Nur Zeiten, Szenenarten und die Worte, die die Reise selbst
schon hat (Titel, Bogen, Motive). Das hält die Planung datensparsam und
billig — und unabhängig davon, in welchem Format die Medien vorliegen.

**MusicPlan.** Ein Sprachmodell bekommt dieses Cue Sheet und darf
**genau eine Sache** entscheiden: wo die Musikabschnitte beginnen und
wie jeder sich anfühlen soll. Nicht die Länge, nicht die Anzahl, nicht
die Übergänge — das ist Arithmetik, die feststeht.

Jede Antwort wird gegen Regeln geprüft, die Geld kosten:

- Ein Abschnitt darf nie länger sein, als eine Generierung liefert
- Die Abschnitte müssen den Film lückenlos decken
- Eine Grenze liegt auf einer Cue-Grenze (Musik wechselt nie mitten im Tag)
- Die Anzahl bleibt klein (ein Soundtrack ist keine Playlist)

**Ein gebrochener Vorschlag wird nicht repariert**, sondern verworfen;
dann gilt die deterministische Aufteilung. Ein zurechtgebogener Plan
wäre einer, den niemand gewählt hat.

**MusicGenerationPlan.** Beantwortet vor dem Bezahlen: welche Stücke
müssen jetzt tatsächlich erzeugt werden, wie viele Anfragen sind das,
was kostet es. Aus Plan, Cache und Providergrenzen zusammen.

**MusicTimeline.** Jeder Abschnitt bekommt seine Startzeit im Film,
Lautstärke, Ein- und Ausblendung, Überlappung zum nächsten.

**Post-Render-Mux.** Der Film wird zuerst **stumm** gerendert. Dann
misst ffprobe seine exakte Länge, die Timeline wird darüber gelegt, und
ffmpeg mischt die Tonspur ein — **mit `-c:v copy`**, das Bild wird nicht
neu berechnet. Ein anderer Soundtrack kostet damit Sekunden statt eines
neuen Renders von anderthalb Stunden.

---

## Der Provider

**Lyria 3** über die Gemini Developer API.

| | |
|---|---|
| `lyria-3-pro-preview` | bis 184 s, 0,08 USD je Anfrage |
| `lyria-3-clip-preview` | exakt 30 s, 0,04 USD je Anfrage |
| Ausgabe | MP3, 192 kbps, 44,1 kHz Stereo |
| Abrechnung | **pro Anfrage**, nicht pro Sekunde |
| Limit | 10 Anfragen/Minute |

**Kein Dauer-Parameter.** Die Länge wird über Struktur-Tags im Prompt
(`[Intro] [Main Theme] [Outro]`) und einen Satz gesteuert. Das Ergebnis
schwankt — dokumentiert etwa 68 bis 84 Sekunden, wenn 75 verlangt
wurden.

**Kein negative_prompt.** Alles, was die Musik *nicht* sein soll, muss
im selben Freitext stehen.

Kosten für den 15-Minuten-Film: **6 Abschnitte × 0,08 = 0,48 USD.**

---

## Der geplante Ablauf zur Abnahme

1. **Stiltest**, 3 Clips à 30 s mit `clip`: 0,12 USD
2. **Prototyp**, 60–90 s echter Film mit echter Musik, `pro`: 0,08 USD
3. Beurteilen. Wenn nein: zurück zu 1 mit anderem Prompt
4. Erst bei GO der volle Soundtrack: 0,48 USD

---

## Woran wir zweifeln — bitte hier ansetzen

Das ist der eigentliche Zweck dieser Übergabe.

### 1. Sechs unabhängig erzeugte Stücke sind kein Soundtrack

Jeder Abschnitt entsteht aus einem eigenen Aufruf. Sie teilen einen
Stilsatz im Prompt, aber **es gibt keinen Mechanismus für musikalische
Kontinuität**: keinen Seed, kein „setze dieses Stück fort", keine
Tonart- oder Tempovorgabe, die über die Aufrufe hinweg gilt.

Realistisch kommen sechs Stücke in unterschiedlichen Tonarten und
Tempi zurück. Ein Crossfade zwischen zwei nicht verwandten Tonarten
klingt möglicherweise schlechter als ein harter Schnitt — oder
schlechter als ein einziges durchlaufendes Stück.

**Frage: Ist das ein grundsätzlicher Konstruktionsfehler?**

### 2. Vielleicht ist ein einziges Stück besser

Die naheliegende Alternative: **ein** Stück von 184 Sekunden, das
mehrfach läuft, oder ein einzelner lizenzierter Titel. Kostet 0,08
statt 0,48, hat garantiert eine durchgehende Klangwelt und kein
Übergangsproblem.

Der Preis dafür: Die Musik weiß nichts vom Film.

**Frage: Ist die Zuordnung „Abschnitt beginnt, wo die Reise sich
wendet" hörbar genug, um sechs Stücke und ihre Übergänge zu
rechtfertigen?**

### 3. Reicht ein Cue Sheet als Signal?

Das Modell sieht Zeiten, Szenenarten, Kapitelrollen, Bewegungsanteile
— **keine Bilder**. Es weiß, dass Tag 9 eine Kartenetappe mit hohem
Bewegungsanteil ist, aber nicht, dass darauf ein Elch steht.

**Frage: Sind das genug Informationen, um musikalisch sinnvolle
Entscheidungen zu treffen — oder produziert das Modell damit nur
plausibel klingende Beliebigkeit?**

### 4. Die Länge ist eine Bitte, keine Zusage

Kommt ein Stück kürzer zurück als sein Abschnitt, wird der Film an
dieser Stelle still. Wir planen mit 12 % Reserve und wollen die
tatsächliche Länge messen und zuschneiden.

**Frage: Reicht das? Oder muss die Timeline sich an das anpassen, was
kam, statt umgekehrt?**

### 5. Ist die Makroebene richtig gewählt?

Wir vertonen bewusst *nicht* einzelne Schnitte — keine Betonung auf
das Heldenfoto, kein Beat-Matching. Die Musik trägt Bögen über Minuten.

**Frage: Ist das die richtige Auflösung für einen persönlichen
Reisefilm, oder ist der Unterschied zu „ein Stück drunterlegen" damit
so klein, dass die ganze Architektur ihn nicht rechtfertigt?**

---

## Was wir ausdrücklich nicht wollen

Kein Beat-Matching, kein Ducking, keine Sprecherstimme, kein
automatisches Sound Design, keine Musik je Tag. Der Originalton der
Videoclips bleibt aus.

Die Musik soll klingen wie die liebevolle Begleitung einer echten
Familienreise — nicht wie Tourismuswerbung, Kinotrailer oder ein
Vlog-Intro.

---

## Die Fragen, um die wir bitten

1. Ist die Kette als Ganzes sinnvoll, oder gibt es eine Stufe, die
   nichts beiträgt?
2. Ist Punkt 1 oben (fehlende musikalische Kontinuität zwischen den
   Aufrufen) ein Ausschlusskriterium?
3. Gibt es einen Weg, Kontinuität über mehrere Lyria-Aufrufe zu
   erreichen, den wir übersehen?
4. Wäre ein einziges längeres Stück für diesen Zweck ehrlicher?
5. Wenn ihr das Konzept verwerfen würdet: an welcher Stelle, und was
   wäre stattdessen der richtige Ansatz?

Bitte begründet auch, was gut ist — das entscheidet mit, was bleibt.
