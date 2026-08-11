# Übergabe: reise(10) — der erste Film mit Videoclips

**Für den Review.** Der Film ist gerendert und wird als MP4 mitgeliefert.

---

## 0. Was an diesem Film neu ist — in einem Satz

Es ist der erste Film, in dem **Videoaufnahmen der Reise vorkommen**. Alles
andere — Bildzuteilung, Kapitelstruktur, Textebene — ist gegenüber reise(9)
**absichtlich eingefroren**.

**Ohne Musik.** Bewusst: Die Musikarchitektur ist vorhanden, aber es wurde
keine bezahlte Lyria-Generierung ausgelöst. Originalton ist ebenfalls aus —
in jedem Clip, ohne Ausnahme. Beides ist kein Versäumnis, sondern die
Vorgabe für diese Abnahme.

---

## 1. Eingefroren geblieben, wie vereinbart

Aus dem Review zu reise(9) stand: keine weitere Justage an der Zuteilung.
Das wurde eingehalten. Unverändert:

| Größe | Wert |
|---|---|
| `GOOD_IMAGE_THRESHOLD` | 12.0 |
| Bilder je Tag (Kappe nach Wichtigkeit) | 6 / 10 / 14 / 18 |
| `MAX_FILM_PHOTOS` | 260 |
| Serienkappe (`MAX_PER_SERIES`) | 2 |
| Collagengruppe (`GROUP_SIZE`) | 4 |
| `FILM_FPS` | 30 |

Die 14 wurde **nicht** auf 18 angehoben. Die Übergangskappe von 6 steht.

---

## 2. Der Videoblock, so wie er jetzt läuft

### Der Weg einer Aufnahme

```
OneDrive → technischer Vorfilter (lokal, kostenlos)
         → Fenster (max. 30 s, überlappend)
         → Analyseproxy: 360p, 8 fps, tonlos
         → Gemini: "was ist hier, und wann?"
         → Segment auf die Zeitachse der Aufnahme gelegt, gespeichert
         → Film liest gespeicherte Segmente, schneidet Renderproxy 720p, tonlos
```

Der Film **ruft kein Modell auf**. Ein Render kann keine Kosten erzeugen; er
liest nur, was vorher bezahlt und gespeichert wurde. Eine Reise ohne Analyse
ist ein Film ohne Clips, und das ist ein normaler Film.

### Was das Modell zu sehen bekommt

Ein verkleinerter, **tonloser** Ausschnitt. Nie das Original. Kein Ort, kein
Tag, kein Name — das Modell wird gefragt, *wann* der gute Teil ist, nicht
*wo* es war. Alles, was man ihm erzählt, kann es als eigene Beobachtung
zurückgeben.

### Wie viele Clips ein Tag bekommt

| Wichtigkeit | Clips |
|---|---|
| Übergangstag | 1 |
| normal | 2 |
| Höhepunkt | 3 |
| großer Höhepunkt | 3 |

Höchstens **ein Hero-Clip** je Tag. Die Sekunden eines Clips gehen **vom
Zeitbudget des Tages ab**, und die Fotos teilen sich, was übrig bleibt
(`trip_film_plan.py:759-770`). Ein Clip lässt sich nicht kürzen — er wurde auf
einen Moment geschnitten, und ihn zu trimmen hieße, ihn nochmal zu schneiden.

**Präzisierung**, weil eine frühere Fassung dieses Dokuments hier zu absolut
war: Ein Tag, der sein Zeitbudget ohnehin füllt, wird durch Clips **nicht
länger** — die Fotos weichen. Ein Tag mit wenigen Bildern, der sein Budget gar
nicht ausschöpfte, **wird länger**. Über den ganzen Film summiert sich das:
Dieser läuft **12:23**, der letzte ohne Video lag bei rund zehn Minuten.

Renderproxy: 720p, **kein Ton**, höchstens 12 s je Clip.

### Die Zahlen dieses Laufs (vom Panel abgelesen)

- **20 Videos gefunden**, davon **17 technisch brauchbar**
- **21 Analysefenster**, davon **20 beantwortet**
- **14 gespeicherte Momente**
- **1 Aufnahme abgelehnt** — Gemini-Sicherheitsfilter (`PROHIBITED_CONTENT`)
  an einer gewöhnlichen Urlaubsaufnahme. Diese Filter weisen private
  Familienaufnahmen regelmäßig und zu Unrecht zurück. Die Aufnahme wird
  nicht erneut gefragt (das Ergebnis wäre dasselbe) und erscheint nicht als
  Clip. Die **Fotos** dieses Tages sind unberührt.

---

## 3. Weitere Änderungen seit reise(9), die man sehen kann

**Prominenz statt bloßer Abdeckung.** In reise(9) waren die Motive des Tages,
um den es der Reise teils ging, korrekt vorhanden — in drei viertelgroßen
Collagenkacheln, während ein Nebenmotiv den Bildschirm allein hielt. Jede
Abdeckungsprüfung war grün. Neben „ist das Motiv da?" steht jetzt „bekommt es
einen Platz, an dem man es sieht?". Prominent ist, wer das Bild für einen
Moment allein hat. **Medium-neutral**: Foto und Clip werden auf ihren eigenen
Punktwerten verglichen, in beide Richtungen.

**Der wichtigste Tagestyp öffnet auf seinem eigenen Bild.** Vorher liefen alle
23 Kapitel identisch: Titelkarte → Karte → Text → Hero → Collage. Jetzt
beginnt der stärkste Tagestyp mit seinem stärksten Bild; Titelkarte und Karte
folgen. Alle anderen behalten ihre Reihenfolge — ein Film, in dem jeder Tag
anders aufgebaut ist, ist nicht abwechslungsreich, sondern uneinheitlich.

**Die Kamera führt.** Sie blickt eine halbe Sekunde Fahrtweg voraus
(15 Frames), statt auf die aktuelle Position zu zielen. Rein aus dem
Bildindex berechnet, kein gemerkter Vorframe — Remotion rendert in parallelen
Tabs.

**Collagen packen nicht mehr beliebig dicht.** Vorher lief ein Übergangstag
mit drei *und* mit acht Bildern exakt gleich lang: Alles über die erste Gruppe
hinaus wurde hineingequetscht. Mehr Bilder kauften also Dichte statt Zeit.
Die Gruppengröße ist jetzt gedeckelt.

**Schlusskollage.** Bis 4.102.0 wurden hochkant aufgenommene Fotos dort
beschnitten (`cover` statt `contain`, plus Rasterzeilen ohne feste Höhe) —
im letzten Bild des Films, neben korrekt wirkenden Querformaten. Behoben in
4.103.0. **Das konnte hier nicht gerendert und daher nicht angesehen werden.**

---

## 4. Was beim Ansehen besonders interessiert

1. **Fühlen sich die Clips wie Teil des Films an** oder wie Fremdkörper
   zwischen Fotos? Länge, Übergang, Position im Tag.
2. **War es richtig, die Clips vom Bildbudget abzuziehen?** Ein Tag mit
   Clips zeigt weniger Fotos. Merkt man das — und stört es?
3. **Ein Hero-Clip je Tag: zu wenig, zu viel, richtig?**
4. **Die Schlusskollage** — sind hochkante Fotos vollständig zu sehen?
5. **Der Film läuft ohne Musik.** Trägt er das, oder fehlt ohne Ton eine
   Ebene, die die Bildschnitte gerade noch zusammenhält?
6. **Die Kamerabewegung** auf den Kartenszenen: ruhiger als vorher?

---

## 5. Grenzen dieser Übergabe — bitte mitlesen

Klar getrennt, was wo gezeigt wurde:

**A) Technisch belegt** (in der Entwicklungsumgebung, gegen Fixtures und
echten ffmpeg): der Videopfad end-to-end, die Proxy-Maße für sieben
Aufnahmeformate, die Zuteilung, die Szenenpläne, sämtliche Grenzwerte.

**B) Nicht belegt:** wie es aussieht. Es gibt in der Entwicklungsumgebung
keinen Browser und keine Remotion-Installation. Jede Aussage über die Wirkung
im fertigen Film ist eine Erwartung, kein Befund. **Der Film selbst ist der
einzige Beleg** — deshalb dieser Review.

**C) Der Film wurde auf dem Live-System gerendert**, Integration 4.104.0 mit
Renderer-Add-on 0.22.0.

**Die Zahlen dieses Renders:** h264, 1280×720, **743,0 s** (12:23), **221 MB**.
Renderdauer **3586,5 s** (59,8 min) — rund 161 ms je Frame bei 22.290 Frames.

Für die Bewertung unwichtig, für die Geschichte des Projekts nicht: Der
Renderer hatte bis vor kurzem eine **feste** Grenze von 40 Minuten, bemessen
an einem Film halber Länge. Dieser Film hätte sie um zwanzig Minuten gerissen
— und genau daran sind zwei frühere Renderversuche gestorben. Die Grenze
wächst jetzt mit der Bildzahl (hier rund 148 Minuten); gebraucht wurden 40 %.

---

## 6. Bekannte offene Punkte

- **Eine Aufnahme fehlt** (Sicherheitsfilter, siehe oben). Keine technische
  Ursache, nichts zu reparieren.
- **Rotierte Aufnahmen melden im Dateikopf Querformat.** Der technische
  Vorfilter beurteilt daher die Kopfmaße, nicht das Bild. Bisher ohne
  erkennbare Folge, aber es ist eine bekannte Unschärfe.
- **Musik**: Architektur vorhanden, für diesen Film bewusst nicht ausgelöst.
- **Originalton**: Die Analyse notiert, ob der Ton einer Stelle interessant
  wäre. Das ist eine Notiz und wird nie automatisch umgesetzt.

---

## 7. Was in dieser Sitzung sonst gefunden wurde

Der Videoblock ist erst nach einer Kette von Fehlern gelaufen. Sie stehen
hier, weil sie ein Muster zeigen, nicht als Chronik:

1. Der Schalter für die Videoanalyse kam nie dort an, wo entschieden wird —
   er wurde von einem Objekt gelesen, das dieses Feld nie hatte. Antwort:
   für **jede** Konfiguration „aus".
2. Ein Lauf über zwanzig Minuten gab kein Lebenszeichen, und ein
   Verbindungsabbruch wurde als Fehler gemeldet, obwohl der Server
   weiterarbeitete.
3. Das Ergebnis eines bezahlten Laufs existierte nur in der Antwort auf den
   Klick — ein Neuladen der Seite löschte jede Fehlerbegründung.
4. **Hochkantvideos wurden von ffmpeg abgelehnt, bevor ein einziges Bild
   entstand** (`height not divisible by 2 (202x359)`). Querformat landete
   zufällig auf geraden Zahlen. Das war die eigentliche Ursache.
5. Eine Ablehnung durch den Sicherheitsfilter galt als Fehlschlag und wurde
   deshalb bei **jedem** Lauf erneut versucht.

Vier der fünf haben dieselbe Form: **eine fehlende Antwort, die als Zustand
dargestellt wird**, oder **eine Sache, die an zwei Stellen steht und
auseinanderläuft**. Beides steht im Fehlerkatalog des Projekts. Die Tests
dagegen prüfen jetzt Eigenschaften statt Zeichenketten — der ffmpeg-Test
lässt den echten Encoder über sieben Aufnahmeformate laufen, weil ein Test,
der die Argumentzeile liest, die kaputte Fassung anstandslos durchgewinkt hat.
