# Übergabe: der erste Film aus einem Renderprofil (480p)

**Für den Review.** Der Film wird als MP4 mitgeliefert.

Gerendert mit **Integration 4.109.0** und **Renderer-Add-on 0.24.0**,
im Profil **Review schnell · 480p** (854 × 480).

---

## 0. Wozu dieser Film da ist

**Der Schnitt ist unverändert.** Seit reise(11) wurde an Bildauswahl,
Szenengrammatik, Kamera, Cliplängen und Filmlänge **nichts** angefasst.
Wer hier nach Änderungen am Schnitt sucht, sucht nach etwas, das es nicht
gibt.

Geändert hat sich genau eine Sache: **in welcher Größe gerendert wird.**
Dieser Film beantwortet deshalb genau eine Frage:

> Hält die Bildsprache, wenn sie kleiner gezeichnet wird?

Ohne Musik, wie bisher. Keine Lyria-Generierung, keine Kosten.
Originalton bleibt in jedem Clip aus.

---

## 1. Was technisch passiert ist

Die Komposition ist weiterhin auf einer **logischen Fläche von
1280 × 720** gebaut und bleibt es. Ein Profil skaliert diese Fläche als
*ein* Bild. Keine Layoutkomponente weiß, in welcher Größe gerendert wird
— nichts wird für eine andere Größe neu gesetzt.

Das ist eine bewusste Entscheidung mit einer klaren Kehrseite:

| | |
|---|---|
| **Vorteil** | Der Film ist bei jeder Größe derselbe Film. Ein kleiner Render sagt etwas über den großen aus. |
| **Kehrseite** | Nichts ist für 480p **optimiert**. Schrift wird nicht größer gesetzt, Abstände werden nicht enger — alles schrumpft gleichmäßig mit. |

Genau diese Kehrseite ist das, was am Film zu beurteilen ist. Bei 480p
hat eine Schrift, die für 720p entworfen wurde, nur noch **zwei Drittel**
der Pixel in jeder Richtung.

---

## 2. Worauf es beim Ansehen ankommt

Bitte **in dieser Reihenfolge**, und bitte nur darauf:

### 2.1 Lesbarkeit — der Kern dieser Übergabe

- Sind **Kapitelüberschriften und Tagestexte** noch mühelos lesbar, oder
  muss man sich anstrengen?
- Sind die **kleinen Meta-Zeilen** (Route, Entfernung, Dauer) noch
  lesbar? Sie sind die kleinste Schrift im Film und kippen zuerst.
- Sind **Kartenbeschriftungen** noch zu entziffern, oder verschmelzen
  Ortsnamen mit der Linie?

### 2.2 Bilddetail

- Erkennt man auf **Kollagekacheln** noch, was das Bild zeigt? Eine
  Vierer-Kachel bei 480p ist ein sehr kleines Foto.
- Wirken **Gesichter** in Crew-Szenen und Fotos noch wie Gesichter?

### 2.3 Bewegung

- Wirken **Kamerafahrten und Kartenbewegungen** noch ruhig, oder
  zerfallen sie bei dieser Bitrate in sichtbare Blöcke?
- Gilt dasselbe für die **Videoclips**? Bewegtes Material kostet
  Kompression am meisten.

### 2.4 Die eigentliche Produktfrage

> **Welche Urteile über den Film kannst du an dieser Fassung *nicht*
> fällen, die du an einer großen fällen könntest?**

Wenn 480p für alles taugt, was einen Schnitt ausmacht, ist die
Entwicklungsschleife gelöst. Wenn nicht, will ich genau wissen **wofür
nicht** — dann wird künftig in 720p abgenommen und 480p bleibt für
Zwischenstände.

---

## 3. Bitte *nicht* kommentieren

Bildauswahl, Reihenfolge, Cliplängen, Prominenz, Kartenruhe,
Gesamtlänge. Alles unverändert seit reise(11) und an diesem Film nicht
neu zu verhandeln — sonst kommen Befunde zu einem Schnitt, den niemand
angefasst hat.

---

## 4. Was der Block sonst noch gebracht hat

**Fünf Größen**, eine Bildrate. Die Bildrate ist absichtlich keine
Einstellung: Ein Profil, das sie ändern könnte, könnte die Zeitrechnung
eines Plans ändern, und derselbe Szenenplan würde zu zwei verschiedenen
Filmen.

**Eine Review-Kopie**, die einen fertigen Film kleiner rechnet, ohne neu
zu rendern. Kein Paket, kein Browser, kein Foto, kein Dienst, keine
Kosten. Gemessen an einem Film von 12:23 (1440p, 213 MB): **50,2 MB** in
480p und **90,2 MB** in 720p, jeweils in wenigen Minuten.

Die Zielgröße gehört zum Profil, nicht zum Feature — die erste Fassung
hatte eine gemeinsame, und dann kamen beide Profile auf **dasselbe Byte**
heraus. Das waren nicht zwei Profile, sondern eines mit zwei Namen.

---

## 5. Grenzen dieser Übergabe

**A) Technisch belegt** in der Entwicklungsumgebung: die Profiltabelle
über beide Deployables; dass kein Modul, das über den *Inhalt* eines
Films entscheidet, das Profil überhaupt lesen kann; die Review-Kopie
gegen einen **echten** Encoder in fünf Formen, mit gemessener Länge,
Form, Tonspur und Dateigröße; die Bitratenarithmetik samt Randfällen.

Dazu ein echter Render in CI: ein vollständiger Reisefilm kommt nach dem
Umbau in der erwarteten Auflösung und **exakt in der geplanten Länge**
heraus.

**B) Nicht belegt: wie es aussieht.** Hier gibt es keinen Browser und
kein Remotion. Jede Aussage über die Wirkung ist eine Erwartung, kein
Befund. **Der Film ist der einzige Beleg.**

**C) Jetzt gemessen — und das Ergebnis widerspricht einem Ziel des
Blocks.** Siehe Abschnitt 7.

---

## 6. Bekannte Lücke, die dieser Block aufgerissen hat

Die **Filmkarte nennt weder die Renderdauer noch das verwendete Profil.**
Solange es eine Größe gab, war das verschmerzbar. Jetzt gibt es fünf, und
„was hat 480p gekostet?" ist genau die Frage, die man in der Karte
beantwortet haben möchte, in der man die Größe auswählt.

Ebenfalls offen: **ein Render lässt sich nicht abbrechen.** Er läuft im
Add-on, nicht in Home Assistant — ein Neustart von Home Assistant ist für
ihn kein Ereignis. Stoppen lässt er sich heute nur durch einen Neustart
des Add-ons, der ihn sauber als unterbrochen ablegt und aufräumt.

---

## 7. Der wichtigste Befund: 480p löst das Zeitproblem nicht

Auf dem Live-System gemessen, derselbe Film, dieselbe Maschine:

| | 720p (Add-on 0.23) | 480p (Add-on 0.24) |
|---|---|---|
| Filmlänge | 743 s | 734 s |
| **Dateigröße** | **221 MB** | **47 MB** |
| **Renderzeit** | **59,8 min** | **51,2 min** |
| je Bild | 161 ms | 139 ms |

**2,25-mal weniger Pixel haben 14 % Zeit gespart.** Höchstens ein Achtel
der Kosten eines Bildes sind die Pixel; der Rest ist Layout und
JavaScript, und das ist dieselbe Arbeit, egal wie groß gezeichnet wird.
Remotion rechnet die Bilder hier nacheinander in **einem** Browsertab —
bewusst, weil eine Home-Assistant-Box ihre Kerne mit dem Haushalt teilt.

Der Block hatte zwei Ziele. Das Ergebnis ist unterschiedlich:

- **Dateigröße: erreicht.** 221 MB → 47 MB, ein Faktor von fast fünf.
  Dazu die Review-Kopie, die aus einem fertigen Film in Minuten 50 MB
  macht, ohne neu zu rendern.
- **Iterationsgeschwindigkeit: nicht erreicht.** 51 Minuten sind keine
  schnelle Runde. Die Auflösung war der falsche Hebel.

Das ist kein Fehler in der Umsetzung, sondern eine widerlegte Annahme —
meine. Ich hatte es vermutet und deshalb ausdrücklich nicht
hochgerechnet; jetzt ist es gemessen.

**Was wirklich helfen würde**, in der Reihenfolge ihrer Nebenwirkungen:

1. **Mehr als ein Browsertab.** Der einzige große Hebel, denn die Bilder
   werden derzeit streng nacheinander gezeichnet. Kostet Kerne und
   Arbeitsspeicher auf einem Gerät, das nebenbei ein Haus steuert — und
   genau deshalb steht die Zahl heute auf eins.
2. **Weniger Bilder je Sekunde für Reviewfassungen.** Halb so viele
   Bilder ist grob halb so viel Zeit. Die Filmlänge bliebe gleich, die
   Bewegung würde ruckeliger. Das greift allerdings in den Szenenplan
   ein, denn dessen Bildzahlen sind bei 30 gerechnet — also genau die
   Grenze, die dieser Block bewusst nicht überschritten hat.
3. **Kürzere Abnahmefilme.** Nicht die ganze Reise, sondern ein paar
   Kapitel. Ändert nichts am Renderer und beantwortet die meisten Fragen
   zum Schnitt genauso gut.

Nebenbefund aus derselben Messung, bereits behoben: Die Zeitgrenze für
einen Render wurde mit der Pixelzahl **verkleinert**. Da die Renderzeit
aber kaum an den Pixeln hängt, war die Reserve bei 480p auf das 1,28-fache
der echten Dauer geschrumpft — gegen das 2,49-fache bei 720p. Am dünnsten
also ausgerechnet beim Profil für schnelle Runden. Die Grenze wird jetzt
nur noch vergrößert, nie verkleinert.
