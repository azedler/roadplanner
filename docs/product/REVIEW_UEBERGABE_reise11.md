# Übergabe: reise(11) — der Polish-Film

**Für den Review.** Der Film wird als MP4 mitgeliefert.

Gerendert mit **Integration 4.106.0** und **Renderer-Add-on 0.23.0**.
(4.107.0 ist inzwischen draußen und ändert ausschließlich, was der
Knopf während des Wartens meldet — kein Pixel am Bild.)

---

## 0. Wozu dieser Film da ist

Er soll **drei Punkte bestätigen oder widerlegen**, und nur diese drei.
Wenn sie stimmen, wird der visuelle Schnitt eingefroren und danach folgt
der erste echte Lyria-Test auf einem konzeptionell stabilen Film.

1. **Visual Prominence** — bekommt der Kern eines Tages einen großen Platz?
2. **Cliplänge** — wirken kurze Clips natürlich, ohne aufgeblasen zu sein?
3. **Filmische Meta-Zeilen** — sind Versorgungsstopps daraus verschwunden?

Alles andere ist absichtlich unverändert.

**Ohne Musik**, wie vorgegeben: `music_mode = off`, keine Lyria-Generierung,
keine Kosten. Originalton bleibt in jedem Clip aus.

---

## 1. Unverändert, wie vereinbart

| Größe | Wert |
|---|---|
| `GOOD_IMAGE_THRESHOLD` | 12.0 |
| Tagescaps | 6 / 10 / 14 / 18 |
| `MAX_FILM_PHOTOS` | 260 |
| Serienkappe | 2 |
| Collage | max. 4 |
| Clips je Tag | 1 / 2 / 3 / 3, höchstens ein Hero |
| Video im gemeinsamen Budget | ja, ersetzt Fotos |
| Story Importance | unabhängig von der Fotomenge |
| Szenengrammatik | nicht neu gebaut |
| Kamera | unverändert, weiterhin zustandslos |
| Filmlänge | **nicht** global angefasst |

---

## 2. Was sich geändert hat

### 2.1 Prominenz wird jetzt **reserviert**, nicht umsortiert

Der Befund aus reise(10) — zentrale Motive korrekt kuratiert, formal
abgedeckt, und trotzdem klein in der Collage — hatte eine mechanische
Ursache, und sie erklärt, warum die vorherige Umsortierung nichts
bewirken konnte:

```python
if style == "collage":
    for position in range(0, len(indices), GROUP_SIZE):
        shots.append((SCENE_COLLAGE, ...))
    return shots
```

**Ein Tag mit dem visuellen Stil „Collage" hatte gar keinen prominenten
Slot.** Jedes Bild ging in eine Kachel, auch das nach vorn sortierte. Es
gab nichts, wohin man hätte sortieren können.

Jetzt wird der Slot **vor** dem Packen freigehalten und das reservierte
Medium aus der Packliste genommen. Regeln dabei:

- **einer pro Tag** — ein Tag hat eine Eröffnung; zwei Reservierungen
  würden die erste nur wieder verdrängen;
- **medienneutral** — zeigt ein Clip das Motiv besser, gewinnt der Clip,
  und dann wird kein Foto verschoben;
- **gar nichts**, wenn ohnehin schon ein Clip auf das Motiv öffnet oder
  der Tag kein zentrales Motiv hat;
- ein **von Hand gesetzter Hero** sticht die automatische Reservierung;
- ein **ausgeschlossenes** Bild kann nicht reserviert werden.

Nebenwirkung, die auffallen kann: Collage-Tage bekommen dadurch erstmals
ein Einzelbild. Das kann den Film etwas verlängern. Die Gesamtlänge wurde
bewusst **nicht** global gegengesteuert — das wird nach der Musik bewertet.

### 2.2 Kurze Clips bekommen einen filmischen Rand

Aus dem Review: einige Segmente wirken „eher wie bewegte Standbilder".
Das Modell antwortet präzise auf „wann ist die gute Stelle" — richtig zum
Finden eines Moments, falsch zum Zeigen.

| Rolle | Zielbereich |
|---|---|
| Hero / Highlight | ca. 4–8 s |
| normal / Kartenbegleiter | ca. 3–6 s |
| Übergangsclip | darf kurz bleiben |

Ein Clip **unterhalb** seines Zielwerts wächst um **0,4–0,8 s je Seite**,
und nur innerhalb von: Aufnahmegrenze, dem vom Vorfilter als brauchbar
beurteilten Fenster, und dem Cliplimit des Films. Nichts wird pauschal
auf einen Zielwert aufgeblasen; ist ein Clip lang genug oder ist kein
sauberer Rand vorhanden, bleibt er wie er ist.

### 2.3 Analyse- und Renderfenster sind getrennt

```
analysiert   5.2 – 7.4   was Gemini beurteilt hat · gespeichert · Cache-Schlüssel
gerendert    4.6 – 8.0   was zu sehen ist · lokal abgeleitet
```

Das analysierte Fenster wird **nie** überschrieben. Es in place zu
verbreitern würde die Bedeutung einer gespeicherten Antwort still
verändern, den Cache entwerten und die Grenze verwischen zwischen dem,
was das Modell empfohlen hat, und dem, was Roadplanner filmisch ergänzt.

**Für diesen Film wurde keine neue Analyse ausgelöst. Null bezahlte
Aufrufe.** Die Erweiterung ist reine Arithmetik in einem Modul, das nichts
aufrufen kann — dafür gibt es einen Test.

### 2.4 Funktionale Stopps sind aus den filmischen Zeilen raus

Vorher wurden sie zurückgehalten und füllten dann den Platz, den die
erzählerischen Namen nicht beansprucht hatten. Eine Routenzeile liest
sich aber als „das war der Tag", nicht als „das blieb übrig".

Die Ausnahme wurde **nachgerechnet, nicht angenommen** — und die
vorhandene Abstufung trifft genau die gewünschte Unterscheidung:

| Fall | Wert | Schwelle 0,55 |
|---|---|---|
| Versorgungsstopp, Ausgangswert | 0,20 | verborgen |
| + in der Tagesgeschichte **erwähnt** | 0,50 | weiterhin verborgen |
| + eigene **Notiz** dazu | 0,65 | sichtbar |
| ausdrücklich als wichtig markiert | 1,00 | sichtbar |

Eine beiläufige Erwähnung befördert also keinen Tankstopp, eine bewusste
schon. **Das Roadbook bleibt vollständig** — das betrifft nur, was der
Film ausspricht. Keine Markennamen, keine zweite Kategorienliste.

### 2.5 Gedrehte Aufnahmen — die Erwartung war falsch

Vor jeder Änderung mit echtem Encoder gemessen:

```
Quelle   1920×1080 mit rotation=90
Proxy    202×360
```

**ffmpeg wertet die Rotationsmatrix selbst aus.** Die Proxys waren immer
richtig; hätte ich nicht gemessen, hätte ich einen Fehler „behoben", den
es nicht gab.

Falsch waren die **Zahlen daneben**: Der technische Vorfilter beurteilte
`height`, was bei so einer Datei die *Breite* des Bildes benennt, und das
Renderpaket übernahm die Bibliotheksmaße in einen Clipeintrag, der eine
Datei der entgegengesetzten Form beschrieb. Jetzt wird die Auflösung an
der **kürzeren Kante** beurteilt — dafür braucht es überhaupt keine
Metadaten — und das Paket nennt, was es an der geschnittenen Datei
**gemessen** hat.

---

## 3. Was bewusst **nicht** gemacht wurde

- **Kamera.** Sie ist besser, der Auftrag stellte es frei, und jede
  Änderung dort erzwingt ein Renderer-Release für etwas, dessen Wirkung
  in der Entwicklungsumgebung nicht anzusehen ist.
- **Display-Name-Schritte für `display_name` und Region.** Diese Felder
  erreichen den Film-Stop gar nicht. Ein Zugriff darauf wäre genau der
  Fehler, den dieses Projekt fünfmal hatte: ein Feld von einem Objekt
  lesen, das es nie trug — Antwort für jeden Stop `""`, während es
  korrekt aussieht. Die real vorhandene Reihenfolge (geschriebener Name →
  bereinigter Ortsname → lieber gar nichts als Providermüll) ist per Test
  festgehalten.
- **Keine neue Szenengrammatik**, keine neue Fotoallokation, keine neuen
  Caps, keine globale Längenänderung.

---

## 4. Worauf es beim Ansehen ankommt

1. **Bekommt der zentrale Tag wirklich einen großen visuellen Platz?**
   Das ist der Kern dieses Blocks.
2. **Wirken die kurzen Clips natürlicher** — und ist keiner künstlich zu
   lang? Ein Übergangsclip darf weiterhin kurz sein.
3. **Sind Versorgungsstopps aus den kleinen Routenzeilen verschwunden?**
4. Foto-/Video-Rhythmus insgesamt.
5. Kartenruhe.
6. Gesamtlänge — durch die Reservierung eher etwas länger als reise(10).

---

## 5. Grenzen dieser Übergabe

**A) Technisch belegt** in der Entwicklungsumgebung: die Reservierung
über jede Kombination aus Stil, Bildzahl und reservierter Position; der
Cliprand samt Grenzen; die Trennung von Analyse- und Renderfenster; die
Meta-Zeilen-Regel samt gemessener Ausnahme; die Rotation gegen einen
**echten** Encoder in vier Ausrichtungen, zwei davon gedreht; und ein
kleiner End-to-End-Film, der alles zusammen prüft.

**B) Nicht belegt: wie es aussieht.** Es gibt hier keinen Browser und
keine Remotion-Installation. Jede Aussage über die Wirkung im fertigen
Film ist eine Erwartung, kein Befund. **Der Film ist der einzige Beleg.**

**C) Gerendert** auf dem Live-System, Integration 4.106.0, Add-on 0.23.0.

---

## 6. Noch offen

- Eine Aufnahme wurde vom Gemini-Sicherheitsfilter abgelehnt
  (`PROHIBITED_CONTENT`, gewöhnliche Urlaubsaufnahme). Sie wird nicht
  erneut gefragt, erscheint nicht als Clip, und die Fotos dieses Tages
  sind unberührt.
- Musik: Architektur vorhanden, in diesem Block bewusst nicht ausgelöst.
- Originalton: Die Analyse notiert, ob der Ton interessant wäre. Das ist
  eine Notiz und wird nie automatisch umgesetzt.

---

## 7. Wenn die drei Punkte bestätigt sind

Dann wird der visuelle Schnitt eingefroren, und der nächste Block ist der
**erste echte Lyria-Test** — auf einem Film, dessen Bildsprache nicht mehr
in Bewegung ist.
