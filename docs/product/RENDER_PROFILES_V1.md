# Render Profiles v1 — was jetzt geht, und was es kostet

Zwei Probleme mit einer Ursache: ein voller Filmrender dauert knapp eine
Stunde, und ein fertiger Film ist über zweihundert Megabyte groß. Keine
der beiden Zahlen ist ein Problem *mit* dem Film. Beide sind Probleme
damit, ihn **anzusehen**.

---

## 1. Render-Profile

Fünf Größen, **eine** Bildrate.

| Profil | Auflösung | gedacht für |
|---|---|---|
| Review schnell | 854 × 480 | schnelle Runden, sicher hochladbar |
| Review detailliert | 1280 × 720 | Schrift, Karten, feine Bewegung · **Standard** |
| Full HD | 1920 × 1080 | normale Ausgabe |
| Hohe Qualität | 2560 × 1440 | Archiv, Tablet, Fernseher · **empfohlen** |
| 4K | 3840 × 2160 | **experimentell** |

**Ein Profil entscheidet Pixel und sonst nichts.** Dieselbe Geschichte,
derselbe Szenenplan, dieselben Fotos, dieselben Clips, dieselben
Sekunden. Das ist keine Absichtserklärung, sondern die Bedingung dafür,
dass ein kleiner Render überhaupt etwas über den großen aussagt.

Durchgesetzt wird es an einer Stelle: die Komposition ist auf **1280×720
gebaut und bleibt es**, ein Profil skaliert diese Designfläche als *ein*
Bild. Keine Layoutkomponente erfährt, welches Profil läuft. Die
Alternative — jede Komponente liest die echte Breite — ist genau der Weg,
auf dem eine Überschrift bei 480p anders umbricht als bei 1440p und die
Review-Kopie aufhört, ein Beleg zu sein.

**Die Bildrate ist absichtlich keine Einstellung.** Ein Profil, das die
Bildrate ändern könnte, könnte die Zeitrechnung eines Plans ändern — und
derselbe Szenenplan würde zu zwei verschiedenen Filmen.

---

## 2. Die Review-Kopie

**Kein Render.** Sie liest eine fertige MP4 aus dem Ergebnisordner eines
früheren Auftrags und rechnet sie kleiner. Kein Paket, kein Browser, kein
Foto geöffnet, kein Dienst gerufen.

Erhalten bleiben: Länge, Seitenverhältnis, Bildrate, Tonspur und ihre
Synchronität. Verändert wird: Pixelzahl und Bitrate. Mehr nicht — sonst
wäre die Kopie wertlos, denn ihr ganzer Zweck ist, den Film **an ihr** zu
beurteilen.

### Sicherheit

Die Quelle reist als **Job-ID**, nie als Pfad oder Dateiname. Beide
Seiten bauen `results/<job id>/<fester Name>` aus einem Wert, der vorher
gegen das Job-ID-Muster geprüft wurde. Es gibt **nichts zu
traversieren** — statt eines Sanitizers, der jedes Mal richtig sein muss.

Keine OneDrive-Zugangsdaten, keine Gemini-Aufrufe, keine Downloads, keine
externen Dienste. `review_copy.mjs` enthält keinen Netzaufruf, kein
Token, keinen `process.env`-Zugriff; ein Test prüft das. Die temporäre
Datei wird auf **jedem** Fehlerweg entfernt.

**Kosten dieses Blocks: 0.**

---

## 3. Gemessen — und die Messung hat den Entwurf geändert

Der erste Entwurf hatte **eine** feste Bitraten-Obergrenze. Ein
Zwei-Minuten-Film in 1440p zeigte, was daran falsch ist:

```
480p-Kopie   87,0 MB
720p-Kopie   87,1 MB
```

Byte für Byte gleich groß, und die 480p-Version keinen Deut ansehnlicher.
„Kleiner" hatte aufgehört, etwas zu bedeuten. Die Obergrenze ist jetzt
**pro Pixel** (0,13 Bit je Pixel und Bild); dieselbe Quelle ergibt
danach 24,1 MB gegen 52,6 MB.

### Am echten Maßstab

Quelle: 12:23 Minuten, 1440p, 213 MB — die Größenordnung des zuletzt
gerenderten Films.

| | Größe | Rechenzeit |
|---|---|---|
| Kopie 720p | 96,2 MB | 3,8 min |
| Kopie 480p | 96,2 MB | 2,4 min |

### Zwei Profile, die dasselbe taten

Diese Messung deckte einen zweiten Entwurfsfehler auf: mit **einer**
gemeinsamen Zielgröße bestimmt bei echter Filmlänge die Zielgröße die
Bitrate und nicht die Obergrenze — beide Reviewprofile landeten deshalb
auf **demselben Byte** (96,2 MB). 480p war nicht kleiner, nur weniger
stark komprimiert. Damit waren es nicht zwei Profile, sondern **ein
Profil mit zwei Namen**, und „Review schnell" versprach etwas, das es
nicht lieferte.

Die Zielgröße gehört deshalb ans **Profil**, und die beiden haben jetzt
verschiedene Zwecke:

| Profil | Zielgröße (~12 min) | wofür |
|---|---|---|
| Review schnell · 480p | ~50 MB | Iterationsgeschwindigkeit, überall hochladbar |
| Review detailliert · 720p | ~90 MB | Schrift, Karten, Cropping, feine Bewegung |

Ein Test prüft beide Bänder (40–60 MB und 80–100 MB) und verlangt, dass
der Abstand real ist statt zufällig. Nur die **Review**profile tragen
eine Zielgröße; die Größe eines Renderprofils ergibt sich aus seiner
Qualitätseinstellung, nicht aus einem Byte-Budget.

---

## 3a. Der Render selbst — ein gemessener Punkt

CI baut das Image und rendert einen **echten** Reisefilm: 10 Kapitel, 58
Szenen, 65 Bilder, Crew, Karte, Musik. Nach dem Umbau:

```
render_profile   review_720
Auflösung        1280 × 720
Filmlänge        271,5 s   (geplant 271,43 s)
Dateigröße       17,0 MB
Renderzeit       1217,2 s  = 20,3 min
```

Zwei Dinge stehen damit fest, die vorher offen waren: **die
Designflächen-Skalierung rendert** — der Film kommt in der erwarteten
Auflösung und exakt in der geplanten Länge heraus — und das Ergebnis
**nennt sein eigenes Profil**.

Die Renderzeit entspricht rund **4,5× Echtzeit** auf einem
GitHub-Runner mit einem Browser-Tab.

**Auf andere Profile ist das nicht übertragbar**, und ich rechne es
nicht hoch. Renderzeit ist nicht proportional zur Pixelzahl: ein
erheblicher Teil je Bild ist Layout und JavaScript, was bei 480p genauso
anfällt wie bei 1440p. Was 480p wirklich spart, ist erst am ersten
480p-Render zu sehen.

---

## 4. Eine Zahl an zwei Stellen

Die Profiltabelle existiert in beiden Deployables, und ein Test liest
**beide Dateien** und vergleicht jede ID, Breite, Höhe, Bildrate und
Endung. Der älteste Fehler dieses Projekts ist eine Zahl, die auf einer
Seite erhöht wurde; er hat vier Releases gekostet. Ein Profil, das links
1440p und rechts 1080p bedeutet, würde tadellos rendern.

Dabei fiel auf, dass die **Bildrate ein drittes Literal** in der
Filmkomponente war, neben Plan und Profiltabelle. Drei Dateien sagten
„dreißig", keine hätte gemerkt, wenn eine andere sich ändert — und eine
Abweichung dort hätte fehlerfrei gerendert, nur jede Szene um einen festen
Bruchteil falsch lang. Kommt jetzt aus der Tabelle, mit Test.

---

## 5. Was CI gefunden hat, und warum der Fund mehr wert war

Der erste Renderlauf starb mit

```
Cannot find module '/opt/roadplanner-renderer/src/render_profiles.mjs'
```

Die Laufzeitschicht des Images kopiert Module **namentlich** statt den
ganzen Baum; die zwei neuen standen nicht auf der Liste. Alles davor sah
gesund aus: Image gebaut, Container gestartet, Heartbeat „ready".

Über dieser Zeile stand seit dem letzten Mal, als dasselbe passiert war,
der Kommentar: *„A test now reads this line and the imports in src/, and
fails if they disagree."* **Diesen Test gab es nicht.**

Er existiert jetzt — und er wurde absichtlich kaputt gemacht, um ihn zu
prüfen, was sich gelohnt hat: die erste Fassung war wertlos. Sie erkannte
nur `from "./x.mjs"`, während der Einstiegspunkt den Renderer über
`await import(...)` erreicht. Sie lief über eine einzige Datei, fand
nichts und wäre grün geblieben, während das Image genau so kaputt war,
wie sie es hätte fangen sollen.

---

## 6. Grenzen dieser Übergabe

**A) Technisch belegt** in der Entwicklungsumgebung: die Profiltabelle
über beide Deployables; die Isolation des Profils von allem, was über den
*Inhalt* eines Films entscheidet; die Review-Kopie gegen einen **echten**
Encoder in fünf Formen (Querformat mit Ton, ohne Ton, kleiner als das
Ziel, Hochformat, krumme Maße) mit gemessener Länge, Form, Tonspur und
Dateigröße; die Bitratenarithmetik; und die Bildratenkette über drei
Dateien.

Dazu **ein** echter Render aus CI (Abschnitt 3a): dass ein vollständiger
Reisefilm nach dem Umbau in der richtigen Auflösung und exakt in der
geplanten Länge herauskommt.

**B) Nicht belegt: Renderzeiten der anderen Profile.** Es gibt genau
einen gemessenen Punkt, und der ist review_720. Was 480p oder 1440p
kosten, weiß ich nicht — und ich rechne es nicht aus der Pixelzahl hoch,
weil Layout und JavaScript je Bild gleich viel kosten, egal wie groß das
Bild ist.

**Ebenfalls nicht belegt: wie der skalierte Film aussieht.** Dass er in
der richtigen Größe herauskommt, sagt nichts darüber, ob er bei 1440p
gut aussieht. In dieser Umgebung gibt es keinen Browser und kein
`node_modules`. **Der Film ist der einzige Beleg dafür.**

**C) Auf dem Live-System:** Integration aktualisieren, dann das
Renderer-Add-on auf **0.24.0**. Ohne das Add-on-Update kennt der Renderer
weder Profile noch Review-Kopie. Der Größenwähler steht neben der
Musikauswahl in der Filmkarte; „Review-Kopie erstellen" erscheint neben
dem fertigen Film.
