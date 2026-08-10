# Changelog – Roadplanner Renderer (PoC)

## 0.21.0

- **Die Kamera schaut voraus, statt hinterherzuziehen.** Sie folgte der
  *aktuellen* Position des Campers — deshalb kam eine Kurve als Ruck: Der
  Blick erfuhr davon im selben Moment wie das Fahrzeug und musste dann
  aufholen. Jetzt zielt sie eine halbe Sekunde Fahrtweg voraus, sodass die
  Kurve schon ins Bild driftet, während der Camper sie erreicht — der
  Unterschied zwischen gefahren werden und geschleppt werden. Der
  Vorausblick wird aus derselben Routenfunktion gelesen wie alles andere
  und bleibt damit eine **reine Funktion des Bildindex**: kein
  gespeicherter Vorframe, nichts, was in parallelen Tabs anders rendert.
  Am Ende einer Etappe hört er von selbst auf zu führen, weil der
  Fortschritt ohnehin geklemmt wird.
- Dämpfung, Serienzittern-Unterdrückung und die Begrenzung der Nachführung
  bleiben unverändert — sie waren bereits richtig.

## 0.20.0

- **Die Zeitgrenze eines Films richtet sich jetzt nach dem Film, nicht nach
  dem Film von damals.** Die feste Grenze von 2 400 s war an „rund 9 000
  Bilder" gemessen — ein Satz mit eingebautem Verfallsdatum. Als die
  Integration aufhörte, die Fotos eines Tages in immer vollere Collagen zu
  packen, wuchs der Film um die Hälfte, die Bildzahl verdoppelte sich
  ungefähr, und diese Konstante blieb stehen. Zwei echte Renderläufe starben
  jenseits der Hälfte mit nichts am Ende — genau der Ausfall, den die
  Verdoppelung verhindern sollte. Die Grenze wird jetzt je Lauf aus den
  tatsächlich zu zeichnenden Bildern berechnet (400 ms je Bild, das
  Dreifache des auf einer Entwicklermaschine gemessenen Werts); die alte
  Konstante ist nur noch die Untergrenze.
- **Ein hängender Browser wird daran erkannt, dass nichts mehr passiert.**
  Neuer Wächter: Meldet der Render zehn Minuten lang keinen Fortschritt,
  bricht er mit `RENDER_STALLED` ab. Eine Wanduhr kann „langsam" nicht von
  „steckengeblieben" unterscheiden — das hier kann es, weshalb die Grenze
  oben es nicht mehr versuchen muss.
- Die Fehlermeldung nennt jetzt die Bildzahl, um die es ging.
- **Das Image enthält jetzt jedes Modul, das der Renderer importiert.** Der
  Dockerfile zählt die Laufzeitquellen einzeln auf — ein zweiter Ort, an dem
  der Quellbaum steht. Beim ersten neuen Modul ging das auf die
  unangenehmste Art schief: Das Image baute, der Container startete, der
  Heartbeat meldete „bereit" — und erst der erste Render scheiterte an einer
  fehlenden Datei. Ein Test folgt jetzt den Importen ab dem Einstiegspunkt
  und vergleicht sie mit der Liste im Dockerfile.

## 0.1.0-poc.2

- Die gemeldete App-Version stimmt jetzt auch, wenn Home Assistant das
  Image selbst baut. Der Supervisor uebergibt dabei `BUILD_VERSION` aus der
  `config.yaml`; das Dockerfile las nur `APP_VERSION` und fiel auf einen
  Platzhalter zurueck, sodass der Heartbeat `0.0.0-dev` meldete - eine
  Version, die es nicht gibt.

## 0.1.0-poc.1

- Erste experimentelle Fassung. Beweist Installation, Heartbeat,
  Auftragsuebernahme, Status, Ergebnisartefakte und Neustartverhalten
  ueber einen gemeinsamen Ordner unter `/share`.
- Enthaelt bewusst kein Remotion, keinen Browser und keinen Videoexport.
- Keine npm-Laufzeitabhaengigkeiten.
