# Changelog – Roadplanner Renderer (PoC)

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
