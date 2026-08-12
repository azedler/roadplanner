# Abschlussbericht: Medienschärfe in 1440p

Integration **4.112.0** · Renderer-Add-on **0.26.0**

---

## A. Ursache

**Eine Zahl:** `FILM_IMAGE_MAX_EDGE = 900`. Jedes Foto wurde auf 900 Pixel
längste Kante gebracht, unabhängig davon, wohin es im Film gehört. Was
der Rahmen tatsächlich zeichnet, gemessen an der Layout-Arithmetik der
Komposition selbst:

| Slot | braucht bei 1440p | hatte | Upscale |
|---|---|---|---|
| **Hero, quer** | 2790 px | 900 | **3,10×** |
| **Vollbild, quer** | 2714 px | 900 | **3,02×** |
| **Hero, hoch** | 1570 px | 900 | 1,74× |
| **Vollbild, hoch** | 1526 px | 900 | 1,70× |
| Collage 2er | 1120 px | 900 | 1,24× |
| Collage 4er | 648 px | 900 | 0,72× |
| Collage 6er | 648 px | 900 | 0,72× |
| Collage 9er | 416 px | 900 | 0,46× |

Das deckt sich genau mit dem, was im Review auffiel: **große Einzelbilder
weich, kleine Kacheln nicht.** Vierer-, Sechser- und Neunerkacheln bekamen
schon vorher mehr Pixel geliefert, als sie zeichnen.

Zwei weitere Deckel, beide echt:

**Der Download.** Die Kette holte `c1920x1440` — bewusst eine gerenderte
Vorschau statt des Originals, weil iPhone-Fotos HEIC sind und Pillow das
nicht dekodieren kann. Aber 1920 ist weniger als die 2790, die ein
Querformat-Hero braucht. Aus 1920 Pixeln lassen sich 2790 nicht
herstellen, egal wie sorgfältig man skaliert.

**Der Videoclip.** `RENDER_HEIGHT = 720`, mit der Begründung „das ist die
Größe, in der der Film rendert" — richtig, solange es eine Größe gab. Bei
1440p wurde ein Vollbild-Clip **2,0×** hochgezogen.

**Entwarnung bei den Karten.** Die Karte im Film ist kein Rasterbild:
`TripMap.tsx` zeichnet sie als SVG mit `viewBox`, sie wird also direkt in
Zielauflösung gerastert und kann gar nicht hochskaliert werden. Der
640-Pixel-Schnappschuss von Google Static Maps steckt im PDF-Export, nicht
im Film. Kein Kartenumbau, auch kein kleiner.

**Eine Korrektur an meiner ersten Analyse.** Ich hatte für alle Slots
`object-fit: cover` unterstellt. Tatsächlich ist nur das querformatige
Vollbild `cover`; alles andere ist `contain` — die Collage ausdrücklich
mit der Begründung, dass eine Wand aus Erinnerungen nicht beschnitten
werden darf. Damit ist das Problem enger als zunächst gemeldet, und die
Collagen gehören nicht dazu.

---

## B. Implementierung

**Zielgröße aus Slot × Profil × Bildform.** Drei Eingaben, alle aus dem
Rendering abgelesen statt gewählt:

- **Die Box** aus derselben Rasterrechnung, die `collageLayout` in der
  Komposition benutzt.
- **Der Fit** aus ihrem `objectFit`.
- **Die Reserve** aus der Ken-Burns-Spanne: **1,09** für ein Hero, **1,06**
  sonst. Der Auftrag schlug 1,15–1,30 als Schätzung vor; der Code kennt die
  echte Zahl, und mehr Bewegung gibt es nicht.

Weil `contain` auf der anderen Achse bindet, braucht ein Hochformat-Hero
1570 statt 2790 Pixel. Die Zielgröße entscheidet sich deshalb neben dem
Decode, aus der tatsächlichen Bildform — nicht aus einer Slot-Pauschale.

**Slot-aware, nicht nur profile-aware.** Der Export bestimmt vor dem
ersten Download, wo jedes Bild landet, aus demselben Planerlauf, den die
Längenschätzung ohnehin macht. Der kostet nichts: Der Planer zählt Bilder
und öffnet keins. Ein Bild ohne Slot im Plan bekommt das Vollbild —
großzügig mit Absicht, weil ein ausgefallener Download die übrigen Bilder
eines Tages in **größere** Slots schiebt, nie in kleinere.

**Encoding.** Qualität (76 → 80 → 82) und Byte-Budget wachsen mit der
Bildgröße; Chroma-Subsampling ist aus (4:4:4). Genau **eine**
verlustbehaftete Stufe: Die Vorschau kommt bereits als JPEG, danach wird
einmal neu codiert. Resize per Lanczos, serverseitig, nicht im Browser.

**Nie über die Quelle hinaus.** `thumbnail` vergrößert nicht. Ein kleines
Original bleibt klein und wird als solches gemeldet.

**Download.** Bei Bedarf wird **zuerst** eine größere Rendition angefragt,
die alten Kandidaten folgen unverändert dahinter. Ob der Dienst sie
liefert, ist dessen Entscheidung — deshalb steht sie als Kandidat da und
nicht als Annahme, und die Diagnose meldet, was ankam.

**Video-Renderproxy** aus dem Profil, nie über die Höhe der Aufnahme.
**Der Analyseproxy bleibt exakt wie er war** (360p, 8 fps, stumm). Eine
größere Filmkopie ruft nichts auf, analysiert nichts neu, ändert kein
Segment und keinen Story Value.

**Cache.** Der Renditionsname steckt bereits im Cache-Schlüssel, ein
720p-Lauf und ein 1440p-Lauf halten also getrennte Kopien. Die semantische
Medienanalyse bleibt davon unberührt.

**Diagnose.** Pro Paket: wie viele Bilder ausreichend sind, wie viele die
**Quelle** begrenzt hat, das größte vorbereitete Bild, die Fotobytes — und
die acht knappsten einzeln mit Quelle, Vorbereitung und Bedarf. Ein zu
kleines Bild bleibt im Film und wird nur markiert.

**Nicht angefasst:** Schriftgrößen, Abstände, Safe Areas, Karten- und
Collagelayout, keine 1440p-spezifischen CSS-Regeln. Der Test gab dafür
keinen Anlass.

---

## C. Messwerte

Zielgrößen nach Profil (Quelle 4032×3024 bzw. 3024×4032):

| Foto | 480p | 720p | 1080p | **1440p** | 4K |
|---|---|---|---|---|---|
| Hero quer | 931 | 1395 | 2093 | **2790** | 4186 |
| Hero hoch | 523 | 785 | 1177 | **1570** | 2354 |
| Vollbild quer | 905 | 1357 | 2035 | **2714** | 4070 |
| Collage 2er | 480 | 594 | 890 | 1187 | 1781 |
| Collage 4er | 480 | 480 | 514 | 687 | 1030 |
| Collage 9er | 480 | 480 | 480 | 480 | 661 |

Videoclip, Vollbild: **720 → 1440 Zeilen** bei 1440p, **720 → 480** bei
480p, jeweils gedeckelt durch die Höhe der Aufnahme.

**Was hier fehlt und nur live entstehen kann:** die Vorher/Nachher-Werte
für *deine* Medien — welche Quellauflösung tatsächlich ankommt, wie viele
Bilder die Quelle begrenzt und wie oft die größere Rendition ausgeliefert
wird. Genau dafür ist die Diagnose gebaut; sie steht im Ergebnis des
nächsten Renders.

---

## D. Performance

Paketgröße, gerechnet über einen echten Planaufbau (23 Tage, 11 Bilder je
Tag, 253 vorbereitete Bilder), mit den gemessenen Bytes je Pixel des
Bestands:

| Profil | Paket | davon große Bilder |
|---|---|---|
| 480p | **8,0 MB** | 0 |
| 720p | 13,1 MB | 0 |
| **1440p** | **45,5 MB** | 46 von 253 |
| *bisher, fest 900 px* | *~20 MB* | – |

**480p wird sparsamer**, 1440p wächst um Faktor **2,3** — nicht um die 5,
vor denen der Auftrag warnte.

Der CI-Filmrender (synthetische Fotos, review_720) lief vorher 1341 s und
nachher 1354 s, also unverändert im Rahmen der Streuung. Aussagekräftig
ist das nur begrenzt: Die CI-Bilder sind klein und von der Änderung kaum
betroffen. **Vorbereitungszeit, RAM und Renderzeit für echte Medien in
1440p sind hier nicht gemessen** und ergeben sich aus deinem Lauf.

---

## E. Versionen

| | |
|---|---|
| Integration | **4.112.0**, Tag `v4.112.0` |
| Renderer-Add-on | **0.26.0** |
| PRs | #336 (Block), #337 (Release) |

Beide werden gebraucht. Der Renderer prüft die Bytegröße jedes Bildes
selbst; ein 0.25.0 würde das neue Paket ablehnen.

---

## F. Live-Schritte

1. **Integration** über HACS auf **4.112.0**, Home Assistant neu starten.
2. **Add-on** auf **0.26.0** aktualisieren.
3. **Keine** neue Fotoanalyse, **keine** neue Videoanalyse.
4. **Keinen** MusicPlan erzeugen, **keine** Lyria-Musik.
5. Prüfausschnitt rendern: **1440p**, **Startzeit explizit 0:00**, ohne
   Musik.
6. Datei bei ChatGPT hochladen, zusammen mit
   `REVIEW_UEBERGABE_1440p_pruefausschnitt.md`.

Dann **stopp**.

> **Zu Schritt 5:** Die Startzeit muss von Hand auf 0:00 gesetzt werden.
> Seit 4.111.0 wählt „automatisch" bei Punktgleichstand die Filmmitte
> statt des frühesten Fensters — sinnvoll für sich, aber du würdest zwei
> verschiedene Ausschnitte vergleichen. Der bereits gerenderte Clip von
> 0:00–1:00 ist das A; der neue muss dasselbe Fenster sein.

---

## Was dieser Block nicht beantwortet

Ob die Fotos jetzt **sichtbar** hochwertiger sind. Die Arithmetik sagt,
dass ein Hero nicht mehr 3,1× hochgezogen wird, und die Tests halten die
Kette zusammen. Ob das im fertigen Bild den Unterschied macht, den du
gesucht hast, entscheidet der Vergleich der beiden Clips — und der findet
auf deinem System statt, nicht hier.
