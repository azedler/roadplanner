# Changelog

All notable changes to Roadplanner will be documented here.

The project follows Semantic Versioning for public releases.

## [Unreleased]

## [4.37.1] - 2026-08-07

### Fixed

- **Die Karte „Renderer-App" passte nach dem Mini-Export nicht mehr ins Bild** (live: „Testauftrag senden" und „Tagesvideo erzeugen" rechts abgeschnitten, die Seite seitlich verschoben). Zwei Ursachen, die sich addierten: `.notice > div` ist ein Flex-Element und schrumpft ohne `min-width: 0` nicht unter die Eigenbreite seines Inhalts – und ein `<select>` ist so breit wie seine **längste** Option. Ein Tag namens „Tag 7 · 2026-07-23 · Nuuksio Nationalpark · 81 Fotos" hat damit die ganze Karte über den Bildschirmrand geschoben. Die Tagesauswahl ist jetzt auf die Kartenbreite begrenzt, und Hinweisinhalte dürfen schrumpfen. Ein Test hält beides fest.

## [4.37.0] - 2026-08-07

### Added

- **Mini-Export: ein echter Reisetag durch die Renderer-App.** Der Nachweis, dass Roadplanner reale Daten kontrolliert übergeben kann und daraus ein gültiges Video entsteht – ausdrücklich kein Export-Feature. Die Strecke ist vollständig: Roadplanner → Renderpaket in `/share` → App → Remotion → ffprobe → MP4 zurück. In der Karte „Renderer-App" lassen sich die Reisetage laden, einer auswählen und daraus ein Tagesvideo erzeugen.
- **Das Renderpaket ist bewusst klein.** Übergeben werden Tages-ID, Datum, Titel, die gespeicherte Tageszusammenfassung, die Namen der Stopps sowie Strecke und Fahrzeit – Letztere **nur, wenn Roadplanner sie ohnehin schon lokal hat**. Nichts wird nachgeschlagen, um das Paket zu füllen. Weder das Roadbook noch die Mediathek verlassen die Integration.
- **Fotos werden neu codiert, nie kopiert.** Bis zu fünf Bilder, auf 1280 px verkleinert, als frische JPEGs aus den Bilddaten geschrieben. Genau das entfernt die EXIF-Daten – einschließlich der GPS-Position, die jedes Telefon in ein Urlaubsfoto schreibt und die sonst unwiderruflich in einem geteilten Verzeichnis läge. Die Drehung aus dem Orientierungs-Tag wird vorher auf die Pixel angewandt, sonst läge jedes Hochformat auf der Seite. Ein Bild, das danach noch Metadaten trägt, wird verworfen statt ausgeliefert.
- **Kein Dateiname reist mit.** Die Bilder sind nummeriert, und der Lesende baut `photo-<n>.jpg` aus der Zahl. Damit kommt keine Zeichenkette aus dem Paket je in die Nähe eines Pfades – die Abwehr ist, dass die Eingabe gar nicht existiert.
- **Feste Grenzen statt Hoffnung:** höchstens 5 Bilder à 400 kB, 3 MB je Paket, 12 Stopps, begrenzte Textlängen. Jede Grenze wird geprüft, bevor irgendetwas geschrieben wird.
- **Das Renderpaket überlebt seinen Auftrag nicht.** Die App löscht es nach jedem Job – erfolgreich oder nicht. Ein Paket, das nie abgeholt wurde, wird nach einer Stunde entfernt; Ergebnisse dürfen 24 Stunden bleiben, fremde Fotos nicht.
- Der Auftrag wird **zuletzt** geschrieben. Seine Existenz belegt damit ein vollständiges Paket – dieselbe Begründung, aus der `result.json` die letzte Datei der App ist.
- **Die App prüft jedes Bild gegen den angekündigten SHA-256, bevor sie es benutzt** – und zwar bevor ein Browser startet. Ein Symlink im Paket wird abgelehnt statt verfolgt. Die CI weist das an einem echten Container nach, inklusive eines vollständigen Tagesvideos aus einem Paket.

### Changed

- Renderer-App 0.4.0-tripday.1: neue Aktion `render_trip_day` und eine zweite Komposition (Titelkarte, Tagesdaten, Fotos, Abschlusskarte). Ihre Länge folgt der Anzahl der Fotos; die Renderprüfung liest sie aus der Komposition zurück, statt dieselbe Rechnung ein zweites Mal anzustellen. Der Testrender und die bestehende ffmpeg-Videopipeline bleiben unverändert.

## [4.36.0] - 2026-08-07

### Changed

- **Renderer-App 0.3.0-slim.1: das Image ist deutlich kleiner.** Die erste lauffähige Fassung war 1574 MB – nichts daran war nötig, es waren ein vollständiges Chromium, ein vollständiges ffmpeg, die Bau-Werkzeuge und der npm-Cache, die alle in die Laufzeitschicht mitgefahren sind. Gleiches Verhalten, ohne die Teile, die nie laufen:
  - **`chrome-headless-shell` statt vollem Chromium.** Dieselbe Rendermaschine, ohne Browseroberfläche, Erweiterungswirt und Synchronisierung. Einmal beim Bauen geladen, nie zur Laufzeit.
  - **Nur `ffprobe` statt des ganzen ffmpeg-Pakets.** ffprobe ist das einzige Programm, das die Validierung benutzt; es wird mit genau den Bibliotheken herausgelöst, die es selbst nennt (per `ldd` ermittelt, nicht geraten).
  - **Mehrstufiger Bau.** Bundler, React, TypeScript und der npm-Cache erzeugen das Bundle und bleiben dann in der Baustufe zurück. Von 209 npm-Paketen sind noch 27 in der Laufzeit.
- Alles bleibt gepinnt: Basis-Image per exaktem Tag, Node per Version **und** SHA-256, npm per Sperrdatei, der Browser über die Remotion-Version, die ihn holt. Während eines Auftrags wird weiterhin nichts geladen, beim Containerstart nichts installiert.

### Added

- **Harte Grenzen für den Worker.** Ein Renderer ohne sie kann eine Platte füllen oder den einzigen Arbeiter dauerhaft belegen: ein Job gleichzeitig (ausdrücklich geprüft, nicht nur durch die Schleife impliziert), 300 s Renderzeit, 420 s Gesamtdauer, 64 MB Ergebnisdatei, 512 MB Ergebnisordner, 24 h Aufbewahrung, 512 MB freier Speicher als Vorbedingung. Aufgeräumt wird jetzt auch nach Größe, nicht nur nach Alter – ein Ordner, der schneller wächst als er altert, wäre sonst unbegrenzt. Liegengebliebene `.part`-Dateien eines abgestürzten Renders werden entfernt.
- **Der Austauschordner ist als Vertrauenskanal dokumentiert, nicht als Sicherheitsgrenze.** `/share` ist von jeder App mit demselben Mount beschreibbar, und die SHA-256 liegt in derselben Datei wie das Artefakt – wer eines fälscht, fälscht beides. Die Prüfsumme belegt Transportintegrität, **nie** Herkunft. Für produktive Videos darf daher nur übernommen werden, was ffprobe als das erwartete MP4 bestätigt. Ein Vertragstest hält die Aussage fest, damit sie eine spätere Änderung überlebt.
- **Der Bau prüft den Browser selbst.** Das Image schneidet Systembibliotheken absichtlich weg, also muss es belegen, dass der Headless Shell noch läuft: `ldd` bricht den Bau ab und nennt die fehlende Bibliothek, danach wird das Programm einmal wirklich gestartet. Eine zu viel weggeschnittene Bibliothek ist ein Baufehler und gehört in den Bau – nicht in einen Render Stunden später.
- **Der Worker protokolliert die Ursache eines Fehlschlags.** Bisher stand im Log nur die Meldung, die im Panel erscheint; der eigentliche Grund steckt in `err.detail`. Er geht jetzt ins App-Log und ausdrücklich **nicht** in die Statusdatei, denn die läuft über den Austauschordner ins Panel. Die CI gibt das App-Log aus, wenn ein Render scheitert.

### Fixed

- **Der Testrender scheiterte mit „Der Browser konnte nicht gestartet werden" – für einen Browser, der die ganze Zeit im Image lag.** Das Basis-Image der Home-Assistant-Apps startet Dienste über s6-overlay, und das reicht die Docker-`ENV` nicht an den Dienst weiter. `render.mjs` fiel deshalb auf seinen eigenen Standardwert `/usr/bin/chromium` zurück. Sichtbar wurde das erst jetzt, weil jede andere Variable einen zufällig passenden Fallback hatte – das alte Image hatte seinen Browser tatsächlich dort liegen. `LD_LIBRARY_PATH` wäre als Nächstes betroffen gewesen: Der aus dem ffmpeg-Paket herausgelöste `ffprobe` findet ohne ihn keine einzige seiner Bibliotheken. Das Startskript setzt und **prüft** jetzt jeden Pfad, von dem die Laufzeit abhängt; ein kaputtes Image scheitert beim Start mit dem Namen des fehlenden Programms statt Sekunden in einen Job hinein.
- **React fehlte in der Laufzeitschicht.** `npm ci --omit=dev` warf es heraus, aber `@remotion/renderer` macht beim Laden `require("react/jsx-runtime")` für die JSX-Hilfetexte an seinen Options-Definitionen. React ist hier eine Laufzeit-, keine Bau-Abhängigkeit und steht jetzt auch dort.
- **Der Browser wurde beim Bauen an der falschen Stelle gesucht.** Remotion legt ihn nicht unter `~/.cache/remotion` ab, sondern läuft vom Arbeitsverzeichnis aufwärts bis zur nächsten `package.json` und entpackt nach `node_modules/.remotion`. Verwendet wird jetzt der Pfad, den `ensureBrowser()` selbst zurückgibt.
- Das Bundle wurde aus `/build/bundle` kopiert, obwohl der Bundler es direkt an seinen Zielort schreibt.

## [4.35.2] - 2026-08-07

### Fixed

- **Ergebnisblöcke standen in schmalen Spalten nebeneinander statt untereinander** (live nach dem ersten echten Render sichtbar: „Testvideo erzeugt", Auflösung, Zeiten und Hinweis in vier Streifen). `.notice` ist ein Flex-Container und stapelt nur, was in einem einzelnen Kind-`<div>` liegt – Text mit `<br>` direkt darin macht aus jedem Element eine eigene Spalte. Betroffen waren beide Experimentkarten; beide stapeln ihre Inhalte jetzt korrekt.
- Der Fortschritt eines Renders heißt jetzt „Testvideo wird gerendert" statt „Testauftrag läuft … running"; das englische Wort stammte aus dem rohen Jobzustand.
- Die Zeile **App** wird nach einem abgeschlossenen Auftrag aktualisiert. Sie zeigte sonst weiter die Version aus der letzten Umgebungsprüfung – nach einem App-Update also eine veraltete.
- **Die gemeldete App-Version wird jetzt von einer echten Shell aufgelöst.** Sie stand als `ENV ROADPLANNER_APP_VERSION=${APP_VERSION:-${BUILD_VERSION}}` im Dockerfile – eine *verschachtelte* Ersetzung. `ENV` beherrscht `${var:-default}`, aber ob ein weiteres `${...}` innerhalb des Standardwerts aufgelöst wird, ist nicht verlässlich. Der Build schreibt die Version jetzt mit `printf` in eine Datei, und der Start liest sie von dort. Ausgerechnet das Feld, das den laufenden Build identifizieren soll, darf nicht von einer unsicheren Ersetzung abhängen.
- Doppelter Punkt in der App-Beschreibung entfernt.

## [4.35.1] - 2026-08-07

### Fixed

- **Aufgeklappte Bereiche bleiben nach jedem Knopfdruck offen** (live: „nach Drücken auf einen der Tests schließt sich der Test immer und man muss ihn neu öffnen und runterscrollen"). Das Neuzeichnen ersetzt den gesamten Shadow-DOM, und ein `<details>` kam dabei immer zugeklappt zurück. Die erhaltene Scrollposition konnte das nicht ausgleichen – eine zugeklappte Seite ist kürzer als der Offset, der an der aufgeklappten gemessen wurde, also landete man oben in einem geschlossenen Bereich. Da jeder Knopf in einem solchen Bereich ein Neuzeichnen auslöst, waren die Karten „Renderer-App" und „Remotion-Unterprozess" praktisch unbedienbar: Knopf drücken hieß Karte verlieren. Jede aufklappbare Sektion hat jetzt eine stabile Kennung; der offene Zustand wird vor dem Neuzeichnen erfasst und **vor** dem Wiederherstellen der Scrollposition zurückgesetzt.
- **Die Karten passen wieder ins Bild** (live: „aktuell passt das Fenster auch nicht ins Bild"). Werte ohne Leerzeichen – ein vollständiger Austauschpfad, ein Dateiname – brachen nicht um und schoben das dreispaltige Raster über den Bildschirmrand, sodass die ganze Karte seitlich scrollte. Zusätzlich brachte das Testartefakt seine Eigenbreite von 640 px mit und war nie begrenzt worden. Lange Werte brechen jetzt um, das Artefakt ist auf die Kartenbreite begrenzt, und auf schmalen Schirmen hat das Raster zwei statt drei Spalten.
- **Der Remotion-Render war aus der Oberfläche gar nicht auslösbar.** Die Aktion, der Worker und die Prüfung waren vollständig, aber „Testauftrag senden" schickte weiterhin den einfachen Auftrag – ein Renderer, den niemand starten kann, ist kein Feature. Die Karte „Renderer-App" hat jetzt einen eigenen Knopf **„Testvideo rendern"**, und ein Vertragstest prüft, dass Aktion, Panel-Zweig, Knopf und Verteiler zusammenpassen.
- Die Statusabfrage im Panel überdauert jetzt einen Render. Sie war auf zwei Minuten ausgelegt, was für die kleinen Testartefakte reichte, für einen Render aber knapp gewesen wäre.
- Das fertige Video wird mit dem angezeigt, was ffprobe gemessen hat – Codec, Auflösung, Dauer, Größe und die Zeiten für Browserstart, Render und Prüfung. Die Datei selbst bleibt im Austauschordner und wird bewusst nicht ins Panel geladen.

## [4.35.0] - 2026-08-06

### Added

- **Remotion läuft in der Renderer-App** (`0.2.0-remotion.1`). Der neue Auftrag `render_remotion_test` erzeugt ein fünfsekündiges H.264-Testvideo (1280×720, 30 fps) im App-Container – schwarzer Hintergrund, Titel, ein Camper, der durchs Bild fährt. **Kein Produktivexport:** der bestehende PDF-Weg und die ffmpeg-Videopipeline sind unberührt und werden von hier aus nicht erreicht.
  - **Alles liegt im Image:** gepinnte Node-Laufzeit (per SHA-256 geprüft, bevor sie ausgepackt wird), gepinnte npm-Abhängigkeiten (Remotion 4.0.506, React 19.2.0), Chromium, Schriften und ffprobe. Während eines Auftrags wird nichts geladen, beim Containerstart nichts installiert. `REMOTION_SKIP_BROWSER_DOWNLOAD=1` und der Verzicht auf `ensureBrowser` stellen sicher, dass sich der Renderer niemals selbst einen Browser holt.
  - **Die Komposition wird einmal zur Build-Zeit gebündelt**, nicht bei jedem Render. Das spart CPU auf dem Zielsystem und verlagert einen möglichen Fehler dorthin, wo er laut auffällt.
  - **Exit-Code 0 ist eine Behauptung, ffprobe ist die Prüfung.** Codec, Container, Auflösung, Bildrate und Dauer werden zurückgelesen; erst danach wird die Datei aus ihrem temporären Namen an die endgültige Stelle verschoben. Der Austauschordner wird abgefragt, ein halb geschriebenes MP4 unter dem Zielnamen würde als fertiges Ergebnis gelesen.
  - Remotion wird **erst beim Rendern nachgeladen**. Der einfache Testauftrag beantwortet die Auslieferungsfrage weiterhin auch dort, wo der Renderer gar nicht laden kann.
  - Ein Video wird auf der Home-Assistant-Seite geprüft, aber nie in den Speicher gelesen oder ins Panel gereicht: es sind Megabyte Binärdaten, die dort nichts anzeigen. Das Panel zeigt, was ffprobe gemessen hat.

### Changed

- Das App-Image basiert jetzt auf Debian statt Alpine. Remotion liefert zwar einen musl-Compositor, aber Chromium auf glibc ist der erprobtere Weg für kopfloses Rendern – ein Spike soll aus interessanten Gründen scheitern, nicht an der Basisdistribution.

## [4.34.1] - 2026-08-06

### Fixed

- **Renderer-App-PoC 0.1.0-poc.2: Die gemeldete App-Version stimmt jetzt auch, wenn Home Assistant das Image selbst baut.** Im Live-Test meldete der Heartbeat `0.0.0-dev` – eine Version, die das veröffentlichte Image nie trägt. Das GHCR-Paket war beim Installieren noch privat, Home Assistant konnte es nicht ziehen und der Supervisor hat den Container lokal gebaut. Dabei übergibt er `BUILD_VERSION` aus der `config.yaml`, während CI `APP_VERSION` übergibt; das Dockerfile las nur letzteres und fiel auf einen Platzhalter zurück. Damit identifizierte ausgerechnet das Feld, das den laufenden Build identifizieren soll, gar nichts. Beide Wege führen jetzt zu einer echten Version, und ein Vertragstest fällt aus, sobald einer von beiden das nicht mehr tut.

### Changed

- Der Renderer-App-PoC ist auf dem echten System nachgewiesen: Supervisor vorhanden, `/share` beschreibbar, App gestartet, Testauftrag übernommen und abgeschlossen, beide Artefakte mit passenden Prüfsummen. Ressourcenbedarf der App im Leerlauf: 0 % CPU, 0,2 % RAM. Offen bleiben die Neustarttests und eine Installation über ein gezogenes statt lokal gebautes Image; dokumentiert in `docs/architecture/RENDERER_APP_POC.md`.

## [4.34.0] - 2026-08-06

### Added

- **Machbarkeitsnachweis für eine optionale Renderer-App** (`apps/roadplanner_renderer`). Der vorherige Remotion-Spike endete mit NO-GO, weil Home Assistant Core weder Node.js noch einen Browser hat und HACS nur Dateien innerhalb des Integrationsverzeichnisses ausliefert – also keine Laufzeitumgebung. Eine App ist dagegen ein eigener Container und darf eine eigene Laufzeit mitbringen. Bevor dort Remotion hineingebaut wird, beweist dieser PoC ausschließlich den Weg dorthin: Installation aus demselben Repository, Heartbeat, Auftrag, Status, Ergebnisartefakte und Neustartverhalten. **Kein Remotion, kein Browser, kein Videoexport** – der produktive PDF- und ffmpeg-Pfad ist unberührt, und ohne die App funktioniert Roadplanner unverändert.
  - **Ein Repository, zwei Konsumenten:** HACS liest `hacs.json` und `custom_components/`, der Supervisor liest die neue `repository.yaml` und `apps/`. Sie sehen einander nicht, der bestehende HACS- und Releaseweg bleibt exakt wie er war.
  - **Der Austauschkanal ist ein einziger gemeinsamer Ordner** unter `/share` – kein Port, kein Socket, kein Token, keine Supervisor-API. Jede Datei wird über einen temporären Namen im selben Verzeichnis geschrieben und dann umbenannt, damit ein Leser nie eine halbe Datei sieht. Ein Auftrag wird durch Verschieben übernommen, kann also nicht doppelt laufen. Terminale Zustände sind endgültig – ein neu gestarteter Worker kann einen fertigen Auftrag nicht wiederbeleben, was genau der Weg wäre, auf dem sonst ein ewig laufender Job entsteht.
  - **Dateinamen bestehen ausschließlich aus einer serverseitig erzeugten UUID.** Kein Pfad und kein Dateiname stammt aus Nutzertext; der Schutz gegen Traversal ist, dass die Eingabe gar nicht existiert.
  - **Die App fordert genau eine Sache über einen einfachen Container hinaus an:** Schreibzugriff auf `/share`. Keine Ports, kein Ingress, kein Host-Netzwerk, kein privilegierter Modus, kein Docker-Socket, kein Zugriff auf `/config`, keine Secrets, keine Supervisor- oder Home-Assistant-API. Ein AppArmor-Profil erzwingt die Grenze. `stage: experimental` und `boot: manual` – das Experiment startet nicht von selbst.
  - **Keine npm-Laufzeitabhängigkeiten.** Die App nutzt nur Node-Bordmittel; das ist die billigste Lieferkette für etwas, das neben einem fremden Home Assistant läuft.
  - Die Umgebungsprüfung meldet als eigenständiges Ergebnis, wenn kein Supervisor vorhanden ist: Apps gibt es nur unter Home Assistant OS oder Supervised, auf Container- oder Core-Installationen kann nie eine App installiert werden. Das ist eine Antwort, kein Fehler.

### Fixed

- **Der Repository-Validator lehnt jetzt jede `config.*` außerhalb von `apps/<slug>/` ab.** Sobald dieses Repository als App-Quelle hinzugefügt wird, durchsucht Home Assistant den gesamten Checkout rekursiv nach `**/config.*` und liest jeden Treffer als App-Manifest. Eine später aus ganz anderem Grund angelegte `config.yaml` – eine Testvorlage, die Einstellungen eines Werkzeugs – würde im App-Store des Nutzers als kaputte App auftauchen. Am Verzeichnisaufbau verhindert das nichts, also prüft es jetzt der Validator.
- Die JavaScript-Syntaxprüfung erfasst jetzt auch `.mjs`-Dateien. Der Renderer der App und der Spike-Renderer sind ES-Module und wurden bisher stillschweigend übersprungen, obwohl ein Syntaxfehler dort genauso fatal ist wie im Panel.

## [4.33.2] - 2026-08-06

### Fixed

- **Die Remotion-Diagnose behauptete Dinge, die sie nie geprüft hatte** (live gemeldet: `Status: NODE_MISSING … ffmpeg/ffprobe: nein / nein … Ausgabeordner beschreibbar: nein` – auf genau dem Home Assistant, das mit diesem ffmpeg regelmäßig Reisevideos rendert). Die Prüfung kehrte beim ersten blockierenden Befund sofort zurück, also lief ohne Node keine einzige weitere Prüfung mehr – und der Bericht machte aus jedem leeren Feld ein selbstbewusstes „nein“.
  - Die übrigen Prüfungen sind billig, nur lesend und voneinander unabhängig. Sie laufen jetzt **alle**, bevor überhaupt ein Urteil gebildet wird; die Reihenfolge des Urteils selbst bleibt unverändert, damit die Meldung weiterhin genau eine Ursache benennt statt einer Symptomliste. Nur der Node-Testaufruf bleibt daran gebunden, dass Node existiert – er ist der einzige Schritt, der einen Prozess startet.
  - Ein Feld, das das Backend gar nicht gemeldet hat, steht jetzt als **„nicht geprüft“** im Bericht, statt die Formulierung eines fehlgeschlagenen Tests zu übernehmen. Ebenso wird „Node gefunden, aber nicht startbar“ nicht mehr als „nicht gefunden“ dargestellt – das sind zwei verschiedene Ergebnisse.
  - Hintergrund: Dieser Bericht wird als Beleg für eine Go/No-Go-Entscheidung gelesen. Ein ungeprüftes Feld darf darin nie wie ein negativer Befund aussehen.

### Changed

- **Der Remotion-Spike ist abgeschlossen: NO-GO.** Auf dem realen Home Assistant sind weder Node.js noch ein Browser vorhanden, und beide können den Weg, auf den der Spike ausdrücklich beschränkt war, nicht nehmen – HACS liefert die Dateien innerhalb der Integration aus, und eine Node-Laufzeit samt plattformspezifischem Chromium sind keine solchen Dateien. Der Unterprozessweg scheitert damit an seiner Voraussetzung, nicht an seiner Mechanik.
  - **Der produktive Videoexport bleibt vollständig unberührt**; ffmpeg bleibt der einzige Renderer, der ein Reisevideo erzeugt. Es wurde nichts installiert, um zu diesem Ergebnis zu kommen – genau das macht es verwertbar.
  - Die Diagnose bleibt in der Integration: klein, nur lesend, ohne Installation – auf einer anderen Installation kann das Ergebnis durchaus anders ausfallen.

## [4.33.1] - 2026-08-06

### Fixed

- **„App aktualisieren“ umgeht jetzt wirklich den Cache** (live: „Dein Mechanismus zur Cache Aktualisierung funktioniert also nicht. Ich hatte schon auf deinen Button gedrückt als der mir das veraltete frontend zeigte. Nach ‚von oben nach unten ziehen‘ ist jetzt der neue Check da.“). Das Neuladen des **Dokuments** war nie das Problem: Home Assistant holt das Panel-Modul über eine URL, die das Backend liefert, und die stimmt nach einem Update bereits. Veraltet war der **Abruf** – der Service Worker der Oberfläche beantwortet ihn aus seinem Cache, und mehrere seiner Regeln greifen unabhängig vom Query-String. Das frische `?v=4.32.0` wurde also aus dem Eintrag für `?v=4.31.1` bedient, und ein Cache-Buster am Dokument kommt da gar nicht hin. Vor dem Neuladen werden jetzt die Roadplanner-Einträge aus allen Caches entfernt – gezielt mit `ignoreSearch`, weil die alte Kopie unter einem anderen Query liegt.
  - **Bewusst zielgerichtet:** Alle Caches von Home Assistant zu leeren oder den Service Worker abzumelden würde die gesamte App für ein Problem beeinträchtigen, das zu einem einzelnen Panel gehört. Fremde Einträge bleiben unangetastet; der Worker wird nur zur Neuprüfung aufgefordert.
  - Der Hinweis nennt zusätzlich den Handgriff, der nachweislich funktioniert: in der App einmal von oben nach unten ziehen.

## [4.33.0] - 2026-08-06

### Fixed

- **Koordinaten, die als HTML-Attribute geliefert werden, werden jetzt erkannt.** Bisher wurde nur nach JSON (`"lat": …, "lng": …`) und nach Kartenlinks gesucht. Ein Marker, der als `data-lat`/`data-lng` am Element hängt – bei Kartenwidgets, die aus dem DOM starten, sehr verbreitet – wurde gar nicht betrachtet.

### Changed

- **Der Systemcheck sagt bei fehlenden Koordinaten, woran es liegt** (live: Die Park4Night-Seite kam vollständig an, mit sauberem Seitentitel „park4night - (816 91) Ockelbo - Unnamed Road“, und lieferte trotzdem keine Position). „Ohne GPS-Angabe im Seitenquelltext“ deckt zwei völlig verschiedene Befunde ab: eine Seite **ohne jede** Koordinate – dann hilft der Seitenabruf grundsätzlich nicht weiter – oder eine Seite mit **mehreren**, die auseinanderliegen, weil sie auch die Nachbarplätze auflistet. Im zweiten Fall verweigert Roadplanner die Auswahl bewusst, statt einen der Plätze zu raten und den Stopp kilometerweit zu verschieben. Die Meldung nennt jetzt Muster, Trefferzahl und Grund – nur der zweite Fall rechtfertigt weiteren Aufwand.

## [4.32.0] - 2026-08-06

### Experimental (ausgeliefert, aber inaktiv)

- **Remotion-Unterprozess-Spike.** Reiner Machbarkeitstest zur Frage, ob eine Home-Assistant-Installation einen lokalen Node-/Remotion-Kindprozess starten, überwachen und beenden kann. Der produktive ffmpeg-Videoexport bleibt vollständig unberührt und ist weiterhin der einzige Renderer für Reisevideos.
  - **Laufzeitdiagnose**, die ausschließlich liest: Node (tatsächlich gestartet, nicht nur im Pfad gesucht), Version, npm, ffmpeg, ffprobe, beschreibbares Ausgabeverzeichnis, freier Speicher, auffindbarer Browser, konfigurierter Renderer. Sie installiert nichts und lädt nichts herunter – eine fehlende Voraussetzung ist das Ergebnis, kein still zu behebendes Problem. Jeder Befund ist ein stabiler Code mit deutscher Erklärung.
  - **Versionierter Jobvertrag** mit Weißliste für Kompositionen, serverseitig erzeugtem Ausgabepfad (gegen Traversal und Symlinks geprüft) und fest auf „aus“ verdrahteten Downloads.
  - **Minimaler Renderer** mit exakt gepinnten Abhängigkeiten und Lockfile, JSONL-Fortschritt auf stdout, eigener CI-Job mit ffprobe-Prüfung. Der CI-Job ist bewusst getrennt, damit das Experiment einen normalen Release nie blockieren kann.
  - **Überwachter Lauf** ohne `shell=True`, mit eigener Prozessgruppe (ein Timeout beendet auch den Browser), Abbruch, Statusabfrage und Ausgabeprüfung per ffprobe – Rückgabecode 0 ist eine Behauptung, ffprobe ist der Beleg.
  - **Eigener Status, eigener Ordner, eigenes Ergebnis.** Ein Testrender kann „Letztes Video“ weder überschreiben noch damit verwechselt werden.
  - Siehe [Remotion-Spike](docs/architecture/REMOTION_SPIKE.md) samt offener HACS-Paketierungsfrage und Go/No-Go-Kriterien.
  - **Warum das trotzdem ausgeliefert wird:** Der Live-Test kann nur auf der echten Home-Assistant-Installation stattfinden, und dorthin kommt Code nur über ein Release. Ausgeliefert wird deshalb ein **inaktiver** Stand: Die Diagnose liest ausschließlich, und der Renderer-Pfad ist standardmäßig leer – ohne einen bewusst eingetragenen Pfad kann nichts rendern. Über einen produktiven Einsatz wird erst nach dem Live-Ergebnis entschieden.
- **Neue Datei `THIRD_PARTY_NOTICES.md`** mit den separat lizenzierten Bestandteilen, inklusive gepinnter Remotion-Version und Prüfdatum.
- **Die Repo-Validierung überspringt installierte Abhängigkeiten** (`node_modules`, Build-Ausgaben). Ohne das würde sie tausende Fremddateien prüfen – darunter absichtlich fehlerhafte JS-/JSON-Testdaten – und an Inhalten scheitern, die das Repository gar nicht ausliefert.

## [4.31.1] - 2026-08-05

### Documentation

- **Offene Gestaltungsfrage ins Export-Dokument aufgenommen** (live gewünscht: „Nimm mal die Frage mit auf ob wir das pdf mehr als Fotobücher machen sollten vom Format her und Design. Z.b. a4 quer“). Der Abschnitt stellt Dokument und Fotobuch gegenüber, benennt was der heutige Aufbau billig macht (die Seitengeometrie ist abgeleitet, die Fotoreihen überleben ein Querformat unverändert) und was teuer wird (Titel- und Schlussseite, Kartenrahmung samt Pflicht-Attribution, Anschnitt fürs Druckhaus, Bildauflösung – randabfallende Bilder verlangen Originale, und genau die sind das HEIC-Problem). Vier konkrete Entscheidungsfragen stehen darin, unter anderem ob Format eine Exportoption werden soll wie beim Video oder ob ein Format bewusst gesetzt wird.

## [4.31.0] - 2026-08-05

### Added

- **Crew-Bilder liegen jetzt dauerhaft lokal** (live gewünscht: „Ich fände es auch sinnvoll wenn die Bilder der Crew permanent lokal gespeichert werden“). Bisher war ein Porträt nur ein Verweis nach OneDrive: bei jedem Aufbau der Oberfläche neu aufgelöst, bei jedem Export neu heruntergeladen. Ein Gesicht hing damit davon ab, dass ein Cloud-Konto erreichbar bleibt und das Quellfoto nie verschoben wird. Es wird jetzt einmal geholt, zugeschnitten, verkleinert und abgelegt.
  - **Die Datei ist bereits zugeschnitten.** Damit gibt es beim Anzeigen überhaupt keine Ausschnitts-Rechnung mehr – genau die Rechnung, die den gezeigten Bereich vom gewählten abweichen ließ.
  - Der Dateiname enthält Quellfoto **und** Ausschnitt. Ein neuer Ausschnitt ist eine neue Datei; ein veraltetes Porträt kann gar nicht mehr ausgeliefert werden, und es gibt nichts zu invalidieren.
  - Nicht mehr referenzierte Porträts werden aufgeräumt. Ein Porträt, das gerade nicht geladen werden kann, fehlt einfach – der bisherige Weg über OneDrive bleibt als Rückfall.
- **Das Fahrzeug bekommt ein Bild** (live gewünscht: „Vielleicht auch vom Fahrzeug ein Bild?“). Gleicher Bildwähler wie bei Personen, samt Ausschnitt, nur mit passendem Text – ein Camper hat kein Gesicht zu erkennen. Es erscheint in der Fahrzeugliste und auf der Crew-Seite des PDFs.

### Documentation

- **Neues Dokument [Export-Pipelines: PDF und Video](docs/architecture/EXPORT_PIPELINES.md)** (live gewünscht: „Exportiere mir später bitte das Vorgehen zur Videoerstellung und PDF-Erstellung. Ich möchte das ChatGPT zum Architekturreview übergeben“). Beschreibt beide Wege vom Panel-Klick bis zur fertigen Datei, begründet die nicht offensichtlichen Entscheidungen – warum Vorschaubilder vor Originalen kommen, warum ffmpeg ein Unterprozess und kein Executor-Job ist, warum Ken-Burns gebaut und wieder entfernt wurde, warum Zusammenfassungen gespeichert statt neu erzeugt werden – und benennt sechs bekannte Schwachstellen offen, damit ein Review daran ansetzen kann.

## [4.30.0] - 2026-08-05

### Fixed

- **Die Fährzeit wird jetzt berechnet** (live: „Die Fahrzeit scheint nicht die Fährzeit zu berücksichtigen?“). Sie wurde nicht etwa in einer Summe verschluckt – die Fährstrecke lieferte als Dauer ein wörtliches „nichts“, eine sechsstündige Überfahrt war nirgends im System eine Zahl. Grundlage ist jetzt der Fahrplan, den du ohnehin an den Terminals einträgst: Abfahrtszeit am Abfahrtsterminal, Ankunftszeit am Ankunftsterminal. Nachtüberfahrten über Mitternacht werden korrekt gerechnet.
  - **Aus der Entfernung wird nichts geschätzt.** Eine aus der Luftlinie erfundene Überfahrtszeit läse sich wie eine gemessene Tatsache.
  - **„Fahrzeit“ bleibt reine Straßenzeit** – so wie „Autofahrt“ schon immer nur Straßenkilometer waren. Die Tageskarte zeigt zusätzlich „Fährzeit“ und „Unterwegs gesamt“, die Gesamtroute die Fährzeit der ganzen Reise.
  - Fehlt der Fahrplan, steht dort „unbekannt“ mit dem Hinweis, welche zwei Felder gefüllt werden müssen – statt stillschweigend null.
  - Änderst du eine Fährzeit, gilt die gespeicherte Route als veraltet und wird neu berechnet.
- **Das Crew-Bild zeigt jetzt genau den gewählten Ausschnitt** (live: „Bildausschnitt und tatsächlicher Ausschnitt weichen etwas ab“). Die Darstellung rechnete zweimal falsch: Das quadratische Avatar-Feld beschnitt das Foto per `object-fit: cover` bereits auf einen mittigen Ausschnitt, bevor die gespeicherten Koordinaten angewendet wurden – die bezogen sich damit auf ein anderes Bild als das gezeigte. Und `transform-origin` mit `scale` vergrößert **um** einen Punkt herum, es rückt ihn nicht in die Mitte. Der Ausschnitt wird jetzt exakt auf das Feld abgebildet.

## [4.29.0] - 2026-08-05

### Added

- **Geschriebene Zusammenfassungen für Reise, Tage und Crew** (live gewünscht: „zu jeder Person eine kleine lustige Zusammenfassung … erstellt von der ki aus den Bildern. Dann zu jedem Tag eine Zusammenfassung was wir da gemacht haben und natürlich für die gesamte Reise eine Zusammenfassung“). Neuer Knopf „Zusammenfassungen schreiben“ unter „Rückblick“ in der Gesamtroute.
  - **Pro Tag** aus den Fotos **und** den Plandaten: Die Fotos zeigen, was tatsächlich erlebt wurde, die Plandaten liefern Orte, Datum und Strecke. Fotos allein erfinden Ortsnamen, Plandaten allein beschreiben nur, was vorgesehen war. Ein Tag ohne Fotos bekommt einen bewusst zurückhaltenderen Text, der nicht behauptet, was dort erlebt wurde.
  - **Pro Person** über das Referenzfoto aus „Crew & Fahrzeuge“: Nur wer darauf eindeutig wiedererkannt wird, bekommt einen Text. Ohne Referenzfoto bleiben die Bildunterschriften die einzige ehrliche Quelle — geraten wird nicht.
  - **Für die ganze Reise** als Vorwort auf einer eigenen Seite, geschrieben aus den bereits fertigen Tagestexten statt aus 200 Fotos noch einmal.
  - Der Ton ist augenzwinkernd, aber nie auf Kosten der Personen — das landet in einem gedruckten Andenken, das die Beschriebenen lesen.
  - Jeder Prompt verbietet ausdrücklich, Orte, Namen, Uhrzeiten, Wetter oder Ereignisse zu erfinden.
- **Die Texte werden gespeichert, nicht bei jedem Export neu erzeugt.** Ein Tagestext kostet einen Bildanalyse-Aufruf; 23 Tage plus Crew bei jedem PDF würden den Export auf Minuten verlängern und das Tageskontingent verbrauchen. Sie liegen jetzt am Reisetag, an der Reise und am Crew-Eintrag — das PDF liest sie nur noch, und ein misslungener Satz lässt sich von Hand korrigieren statt nur neu würfeln.
- **Die Erstellung läuft im Hintergrund** mit Fortschrittsanzeige und Statuszeile, wie schon beim Video: Eine über Minuten offene Anfrage stirbt am ersten Verbindungswechsel des Handys. Schlägt ein einzelner Tag fehl, läuft der Rest weiter — ein teilweise geschriebener Satz Texte ist besser als ein Abbruch, der nichts speichert.

## [4.28.0] - 2026-08-05

### Fixed

- **Die Routenkarte im PDF zeigt jetzt eine Route** (live: „Die Route ergibt so noch keinen Sinn“). Sie zeichnete lose Punkte ohne Verbindung – man sah, wo die Reise aufgesetzt hat, aber nichts über die Reihenfolge. Die Stationen werden jetzt in Reisereihenfolge zu einer Linie verbunden, Start und Ziel sind farblich abgesetzt.
- **Und sie zeigt die ganze Reise.** Die Kartenerzeugung hatte eine feste Obergrenze von zehn Markierungen: Aus 23 Reisetagen wurden zehn Punkte, kommentarlos. Die Grenze liegt jetzt so hoch, dass eine vollständige Reise hineinpasst; sie existiert nur noch, damit eine fehlerhafte Eingabe keine unbegrenzte Anfrage erzeugen kann.
- **Der Menüpunkt „Reisen“ hat wieder ein Symbol** (live: „Für Reisen habe ich noch immer kein Symbol“). Der hinterlegte Name `mdi:map-multiple-outline` existiert in Material Design Icons nicht, also blieb die Stelle leer, während alle anderen Einträge ihr Symbol hatten. Es ist jetzt ein Koffer – der liest sich neben „Reise“ und „Gesamtroute“ ohnehin besser als ein drittes Kartensymbol.

## [4.27.1] - 2026-08-05

### Fixed

- **Das Zeitlimit fürs Videorendern richtet sich jetzt nach der tatsächlichen Arbeit** (live: „Ich glaube die Videoerstellung hängt“, stehend bei „Video rendern (ffmpeg) … 36 Fotos und 12 Kartenbilder“ – also 48 Bildern). Das Limit war eine feste Größe von 240 Sekunden, unabhängig davon, ob drei oder fünfzig Bilder zu verarbeiten waren. Nachgemessen kostet die Überblendkette rund 2,3 Sekunden pro Bild auf vier Kernen; 48 Bilder brauchen dort knapp zwei Minuten – und auf einer kleinen Home-Assistant-Box ein Vielfaches davon, womit der Lauf zwangsläufig ins Limit lief. Das Budget wächst jetzt mit der Bildzahl, bleibt aber nach oben begrenzt: Was darüber hinausgeht, ist kein langsames Rendern mehr, sondern ein Ausreißer.
- **Die Kodierung läuft schneller.** Ohne Angabe nutzt x264 die Voreinstellung „medium“, die Detailtreue erkauft, die eine Diashow aus Standbildern nicht braucht. Gleicher 48-Bild-Lauf: 111 Sekunden mit „medium“, 96 Sekunden mit „veryfast“.
- **Die Statuszeile nennt jetzt den Umfang** („Video rendern (ffmpeg), 48 Bilder – das dauert einige Minuten“). Vorher stand dort nur „Video rendern (ffmpeg)“, was nach mehreren Minuten wie ein Stillstand aussah.
- **Ein abgebrochener Lauf bleibt nicht mehr ewig auf „läuft“ stehen.** `CancelledError` ist keine gewöhnliche Ausnahme und wurde von der Fehlerbehandlung nie erfasst: Ein Neustart von Home Assistant oder ein Neuladen der Integration mitten im Rendern hinterließ einen Status, der für immer „läuft“ meldete, während das Panel für einen längst beendeten Auftrag weiterdrehte.

## [4.27.0] - 2026-08-05

### Fixed

- **Ein Stopp mit eigenem Namen nutzt jetzt auch seine Straßenadresse für die Suche** (live: „Warum schafft er es nicht die Daten zuzuordnen. Da sind im Hintergrund vermutlich Daten die ich nicht sehen kann?“ – ja, waren da). Ein Stopp namens „Heimatort“ trug die vollständige Adresse „Neuhäuser 40, 01844 Neustadt in Sachsen-Krumhermsdorf“ – gesucht wurde aber nur „Heimatort, Neustadt in Sachsen, Deutschland“. Straße und Hausnummer wurden vor der Suche verworfen, und damit war zugleich der strukturierte Adressweg abgeschaltet, der genau diese Hausnummer gefunden hätte. Der Anbieter konnte mit „Heimatort“ nichts anfangen, der Ortsname dominierte, und heraus kam die Stadtverwaltung am Markt 1 – Treffertyp „Ort“, also „Auswahl nötig“.
  - Die Namenssuche bleibt an erster Stelle: Ein Geschäft in einem Einkaufszentrum wird über seinen Namen gefunden, nicht über die Adresse, die sich mehrere Mieter teilen. Erst wenn sie nichts Eindeutiges liefert, folgt die Adresse des Stopps als eigene Suche – und zwar als **strukturierte** Adressabfrage mit Straße, Hausnummer und Postleitzahl in getrennten Feldern. Nur so unterscheidet ein Anbieter „Neuhäuser 40“ von „die Stadt Neustadt“.
  - Diese Rückfall-Suche kann nicht durch die Obergrenze für Suchvarianten verdrängt werden – sie existiert gerade für den Fall, dass die vorherigen nichts gefunden haben.

### Note

- Die Testhilfe für Adressen im Prüflauf zerlegte eine Freitext-Adresse bisher nicht in Straße und Hausnummer und hätte diesen Fehler damit dauerhaft verdeckt. Sie tut es jetzt.

## [4.26.2] - 2026-08-05

### Fixed

- **Die Größe neben „Letztes PDF“ und „Letztes Video“ erscheint jetzt zuverlässig** (live: „zeigt er nur manchmal die Größe vom letzten Video und pdf“). Die Angabe stammt aus dem Export-Status, und den hat schlicht niemand abgerufen, wenn man die Gesamtroute nur geöffnet hat – das passierte ausschließlich beim Starten eines Exports, beim Abfragen eines laufenden Videos oder beim Klick auf den Download selbst. Nach jedem Neuladen des Panels blieben die Beschriftungen deshalb nackt, bis zufällig eines dieser Ereignisse eintrat. Der Status wird jetzt beim Öffnen der Ansicht geladen, einmal je Reise und stumm im Hintergrund: Eine fehlende Größenangabe ist ein Schönheitsfehler und nie ein Grund, eine Fehlermeldung vor die Reise zu stellen.

## [4.26.1] - 2026-08-05

### Changed

- **Die Knopfleiste der Gesamtroute ist aufgeräumt** (live: „Bisschen mehr Ordnung wär da schön“). Alles lag in einer einzigen flachen Reihe und ist auf dem Handy beliebig umgebrochen – die Auswahl der Videolänge landete direkt neben „Letztes PDF“ und las sich dadurch wie eine PDF-Einstellung. Die Aktionen stehen jetzt in zwei beschrifteten Gruppen: **Planung** (Routen berechnen, Tag hinzufügen) und **Rückblick** mit je einer eigenen Zeile für PDF und Video. Die Videolänge steht dort, wo sie hingehört: beim Video.
  - „Tag“ heißt jetzt „Tag hinzufügen“ – ein einzelnes Wort neben „Alle neu berechnen“ ließ offen, was es tut.

## [4.26.0] - 2026-08-05

### Added

- **Reisetage lassen sich jetzt im Panel löschen und verschieben** (live gefragt: „Kann ich denn Tage händisch löschen?“ – die Antwort war nein, und das war ein Versehen). Die Aktion `remove_day` gab es im Backend längst, der Klick-Handler im Panel war geschrieben, die Sicherheitsabfrage formuliert – nur hat keine Ansicht je einen Knopf dafür gezeichnet. Löschen ging deshalb ausschließlich über einen Home-Assistant-Dienstaufruf oder den Reisebegleiter. In der Tagesansicht stehen jetzt „Tag löschen“ sowie „Früher“/„Später“ zum Verschieben in der Reihenfolge.
  - Die Verschiebeknöpfe erscheinen nur, wenn es etwas zu verschieben gibt, und am ersten bzw. letzten Tag jeweils nur die mögliche Richtung. Maßgeblich ist die Gesamtzahl der Tage, nicht die gerade geladene Seite.
  - Die Sicherheitsabfrage sagt vorher, was passiert: Die Stopps eines Tages gehören zu diesem Tag und werden mitgelöscht, sie wandern nirgendwohin. Bei einem Tag ohne Stopps steht das auch so da, statt mit „0 Stopps“ zu drohen.
  - In einer schreibgeschützten Ansicht erscheint nichts davon.

## [4.25.0] - 2026-08-05

### Fixed

- **Ein abgelehnter Google-Schlüssel führt nicht mehr zu Exporten ganz ohne Karte.** Der Systemcheck zeigte es im Klartext: Google antwortet mit „This API is not activated on your API project“, während OpenStreetMap im selben Lauf grün war – und die Routenseite im PDF kam trotzdem kartenlos heraus. Ist Google als Kartenquelle eingestellt und lehnt Google ab, übernimmt jetzt OpenStreetMap. Die eingestellte Quelle wird weiterhin zuerst versucht, und der Grund der Ablehnung bleibt erhalten: Der Rückfall lässt den Export funktionieren, er räumt die Fehlkonfiguration nicht weg.
  - Scheitern beide, nennt die Meldung beide Ursachen statt nur der zuletzt aufgetretenen.
  - Die Google-Prüfung im Systemcheck ruft bewusst weiterhin direkt Google auf – sonst würde sie dank des Rückfalls ein fröhliches „OK“ melden, während Google jeden Aufruf ablehnt.
- **Ein Seitentitel wird auch ohne Open-Graph-Auszeichnung gefunden.** Der Systemcheck meldete für Park4Night „67 kB gelesen, ohne Seitentitel“ – eine Seite kann aber schlicht keine Open-Graph-Tags haben und trotzdem ein völlig normales Dokument sein. Der Rückfall auf das gewöhnliche `<title>` unterscheidet eine echte Seite von einer JavaScript-Hülle, und genau dafür ist die Zeile da.

## [4.24.1] - 2026-08-05

### Fixed

- **Der Systemcheck sagt jetzt, warum ein Kartenbild fehlt.** „Kartenbild (Google Static Maps): kein Kartenbild erhalten“ war die vollständige Diagnose (live Systemcheck) – dabei schreibt Google den Grund im Klartext in die Antwort („This API project is not authorized to use this API“). Der Grund war eine Zeile vorher schon festgehalten und wurde dann weggeworfen. Er steht jetzt in der Meldung, samt HTTP-Status und ohne HTML-Gerüst. Gilt genauso für die OpenStreetMap-Kacheln.
- **Eine Antwort mit Status 200 gilt nicht mehr automatisch als Karte.** Eine abgelehnte Static-Maps-Anfrage kann als HTML-Fehlerseite mit tadellosem Statuscode zurückkommen; die wurde bisher als Bild durchgereicht und wäre erst beim Rendern aufgefallen.
- **Google Static Maps meldet nur noch „FEHL“, wenn es auch die gewählte Kartenquelle ist.** Mit OpenStreetMap als Quelle – der Voreinstellung, die im selben Check als „OK“ dasteht – las sich das rote FEHL, als wären die Exporte kaputt, obwohl nichts davon stimmt. Es ist jetzt eine Warnung mit dem Hinweis, dass sie nur zählt, wenn du auf Google umstellst.
- **Der Park4Night-Hinweis unterscheidet die echte Seite von einer Zustimmungs-/JavaScript-Hülle.** „Seite gelesen, aber ohne GPS-Angabe“ passte auf beides. Die Meldung nennt jetzt die gelesene Seitengröße und den Seitentitel – ein paar Kilobyte ohne Titel sind etwas anderes als eine vollständig gelesene Seite, die schlicht keine Koordinaten veröffentlicht.

## [4.24.0] - 2026-08-05

### Added

- **„Reisetage aufräumen“** unter Reisequalität (live report: "Die Tage sind verwurschtelt" - drei titellose Platzhalter-Tage steckten zwischen den geplanten Tagen und haben jeden echten Tag dahinter geschoben, sodass „Heute“ auf dem falschen Tag landete). Die Funktion prüft den kompletten Reisekalender und schlägt zwei Dinge vor:
  - **Leere Platzhalter-Tage entfernen** - und zwar ausschließlich Tage ohne Stopp, ohne Notiz, ohne gefahrene Kilometer und ohne selbst vergebenen Titel (der automatische `Tag <Datum>` zählt nicht als Titel). Ein bestätigter oder abgeschlossener Tag bleibt selbst dann stehen, wenn er leer ist.
  - **Die verbliebenen Tage der Reihenfolge nach neu datieren**, sodass der Kalender wieder lückenlos und chronologisch läuft und der Reisezeitraum dazu passt.
  - **Die Reihenfolge der Tage wird dabei nie verändert.** Sie ist der Plan und das Einzige in diesen Daten, das sich nicht wieder herleiten lässt - die Daten folgen ihr, nicht umgekehrt.
  - Vor dem Vorschlag zeigt ein Dialog, was passieren würde: wie viele Tage übrig bleiben, von wann bis wann die Reise dann läuft und mit welchem Tag sie endet. So lässt sich das Ergebnis gegen die eigene Planung prüfen, bevor irgendetwas entsteht.
  - Übernommen wird nichts automatisch. Das Entfernen eines Tages ist destruktiv, also landet der Vorschlag als reiner Prüf-ChangeSet unter „Übergaben“ - dieselbe Regel wie beim Park4Night-Abgleich. Jede einzelne Änderung trägt ihre Begründung mit.
  - Fragt man zweimal, ohne dass sich am Kalender etwas geändert hat, wird der bereits wartende Vorschlag gefunden, statt einen zweiten daneben zu legen.

## [4.23.0] - 2026-08-05

### Changed

- **The exports now use exactly the picture selection the panel shows** (live report: "Die Bildauswahl im Roadplanner finde ich aber gelungener als im PDF. Kann das sein?" - yes, it could: the panel curated its highlights, the exports just sorted by quality score). PDF and video now call the same curator ("5 Highlights aus 20 Fotos · Lokal nach Qualität, Dubletten und Serien ausgewählt"), so duplicates and burst series are collapsed and different moments of a day get a place instead of five near-identical shots from one minute.
- **Photos in the PDF keep their own shape, portrait included, and there may be up to nine per day** (live request: "Wenn verfügbar sollten wir auch mehr Bilder zulassen und auch im Hochformat"). The fixed 4:3 tile grid is gone; photos are laid out in justified rows where all pictures in one row share a height and each keeps its own aspect ratio. A photo-rich day whose pictures do not quite fit shrinks them a little rather than jumping to a new page and leaving half of the current one blank.
- **The video says something now** (live report: "Das Video war jetzt ziemlich langweilig"). Every chapter's first frame carries its title, date and the story line Gemini writes for that day - that text was computed and then thrown away, so the video showed photos and nothing else. Stills are also held a little shorter (2.8 s instead of 3.5 s), so the same trip moves along instead of standing still.
  - The caption is drawn into the frame with Pillow rather than by ffmpeg, so anything Gemini writes - quotes, umlauts, colons - needs no escaping and can never break a render.

### Note

- Ken-Burns panning was built for the video and removed again: it re-renders every output frame and a three-chapter test needed over six minutes, which would run straight into the export's ffmpeg timeout on a Home Assistant box. The liveliness comes from content instead, which costs nothing at render time.

## [4.22.0] - 2026-08-05

### Added

- The trip video now has real background music (live question: "Du kannst keine lizenzfreie Musik besorgen?"). Not downloaded - **synthesised**: a slow chord pad built from plain sine tones by ffmpeg itself, looped over the whole slideshow and levelled to a predictable background loudness. Nobody else holds rights in it, so there is no licence claim to verify, no attribution to carry and no audio file in the repository.
  - The key is picked deterministically per trip, so the same trip always sounds the same.
  - A real track placed in `assets/music` still wins over the generated bed.
  - Verified end to end with real ffmpeg: video and audio stream, music at roughly -27 dB mean.

## [4.21.1] - 2026-08-05

### Fixed

- The Park4Night coordinate automation handed the same proposal over again and again (live report: "Der Änderungsvorschlag ist mit 4 solchen Änderungen voll" - four identical handoffs for one stop, every one of them stale against a trip that had moved on). Each hourly run used a fresh id, so nothing recognised the proposal already waiting under "Übergaben", and every Home Assistant restart additionally cleared the in-memory "already attempted" list. The identity is the content now - same stop, same coordinates, same handoff - so a repeated run reports "already waiting" and creates nothing. Different coordinates are still a new proposal.

### Note

- Handoffs that already piled up stay as they are; reject them and keep one, or use "Neu aufsetzen" to rebase it onto the current revision.

## [4.21.0] - 2026-08-05

### Changed

- Up to six photos per travel day in the PDF instead of two (live report: "es hat wenige Bilder pro Tag"). A day with 38 assigned photos showed two.
- Photos are no longer cropped to a page-wide strip. That strip was nearly 4:1, so every portrait photo lost its top and bottom ("die sind zu hart geschnitten"). Photos now sit in uniform 4:3 tiles, three per row - a single photo keeps the same tile size as any other instead of being blown up to page width.
- The short highlight reel uses up to three photos per chapter across twelve chapters, instead of one photo across eight. A trip with 261 photos produced a two-still video ("Video enthält zwei Bilder?").
- A generated video always carries an audio track. Without music - the bundled folder ships empty - the export produced a file with no audio stream at all ("War da Musik enthalten?"), which several players and photo libraries treat as broken. A silent track is added instead.

## [4.20.0] - 2026-08-05

### Fixed

- **Every download was silently truncated.** `await response.content.read(limit)` looks like "read up to limit bytes", and that is the trap: aiohttp returns whatever is *currently buffered*, without waiting for the rest. On a streaming response that is the first chunk. Roadplanner used that call for every download, so bodies arriving in more than one chunk lost everything after the first one - with no error anywhere. This one defect explains a whole chain of live reports that looked unrelated:
  - "8 Fotos und 0 Kartenbilder wurden geladen, aber keines davon ließ sich als Bild öffnen": the JPEG header was intact, the rest of the file was missing.
  - "0 Kartenbilder": a truncated PNG tile cannot be decoded, so the map assembly found no usable tile at all.
  - A shared place page that "contained no GPS": the coordinates sat past the first chunk.
  - Every export that produced a PDF or video without a single photo.
- All downloads now read to the end and reject an oversized body while streaming, instead of buffering it first: photos, map tiles and snapshots, shared pages, Park4Night pages, Google-Maps previews, vision images, routing and exchange-rate responses, plus the webhook and Drive-import upload bodies.
- A frame that fails to decode is now recorded with its format, size and error. Downloading and decoding are separate steps, and the second one had no record at all - which is why the export could only say "Letzter Fehler: kein konkreter Fehler erfasst" while every single frame failed.

## [4.19.1] - 2026-08-05

### Changed

- A failed video export keeps its notice until the next run. The notice now records which version produced it and says so when a newer one is running ("aus Version 4.18.2, jetzt läuft 4.19.1 - bitte erneut versuchen"). The same error text can come from a bug that is already fixed, and an old message is otherwise indistinguishable from a fresh failure.
- "OneDrive hat eine unlesbare Antwort geliefert" can no longer be raised at all. It described a transport failure while the truth was "Graph answered with a redirect" (fixed in 4.18.3), and it sent the diagnosis in the wrong direction twice. Seeing that text now means the message predates 4.18.3.

## [4.19.0] - 2026-08-04

### Added

- **Systemcheck** under "Werkzeuge & System" (live request: "Können wir zu einem Testsystem kommen mit Schnittstellen gegen livesysteme wie OneDrive und P4n und google? Das ist mir aktuell echt zu hakelig."). One button probes every live interface from inside Home Assistant - where the credentials, the network and the real photos actually are - and reports per interface what happened, with the concrete reason and duration:
  - OneDrive: connection, and a real photo resolved and downloaded through the exact path the exports use, in both the high-resolution and the standard thumbnail size.
  - Park4Night: a real place page read deterministically, reporting whether coordinates were found.
  - Map tiles from OpenStreetMap, and Google Static Maps when a key is configured.
  - The travel companion (Gemini), plus the local prerequisites Pillow (which formats can be decoded at all) and ffmpeg.
- Every probe is read-only and bounded, a failure is a result rather than an exception, and an unconfigured interface is reported as skipped rather than broken. Failures carry a concrete next step.
- "Ergebnis kopieren" puts the whole report on the clipboard as plain text, so it can be pasted into a message instead of being screenshotted line by line.

## [4.18.3] - 2026-08-04

### Fixed

- The actual reason every export stayed photo-less (live report, narrowed down by the new diagnostics): the OneDrive client read every Microsoft Graph answer with `response.json()` while redirects were switched off. A redirect has an empty body, so Graph's perfectly good answer "the rendered image is over there" became "OneDrive hat eine unlesbare Antwort geliefert" - and the photo was lost. Graph responses are now read body-first, and a redirect is treated as what it is: the thumbnail's URL.
- If the high-resolution custom thumbnail size fails for any reason, the standard "large" thumbnail is used instead of failing the photo. That size is a perfectly usable JPEG; losing the picture over the nicer variant was the worse trade.
- A Graph response that is not JSON now quotes what actually arrived instead of calling it "unlesbar".

## [4.18.2] - 2026-08-04

### Changed

- The crew list shows the person's assigned trip photo instead of a generic silhouette (live request: "Wenn ein Bild zugeordnet ist sollten wir dieses anstatt des Symbols zeigen"). If a crop was set on a group photo, the avatar zooms into exactly that region, so the right face is shown. Without an assigned photo - or if the assigned one is no longer in the trip - the icon stays.

## [4.18.1] - 2026-08-04

### Fixed

- "8 Fotos und 0 Kartenbilder wurden geladen, aber keines davon ließ sich als Bild öffnen" (live report): the format check added in 4.17.1 only covered personal media. Planning-gallery images downloaded straight past it, counted as loaded photos, and then failed in the renderer. Every download now goes through the same gate, whatever its source.
- The rejection names the format that actually arrived - HEIC, AVIF, GIF, or an HTML/JSON page behind the URL instead of an image - so the message points at the real problem.
- "0 Kartenbilder" said nothing about why. Map snapshots now record their concrete failure (no tile reachable from tile.openstreetmap.org, a Google Static Maps HTTP status, an assembly error) and the export's error message carries it.
- "OneDrive ist derzeit nicht erreichbar" covered a timeout, a network error and an unreadable response alike. Each now says which one it was.

## [4.18.0] - 2026-08-04

### Changed

- The trip PDF no longer gives every day its own A4 page (live report: "Das pdf ist untauglich"). Days flow one after another and take exactly the room their content needs, so a 28-day trip with two or three stops per day fits in a handful of dense pages instead of 33 pages of whitespace.
- The cover shows the trip's own best photo instead of an abstract circle over an empty half-page.
- Crew notes and personal summaries are wrapped over several lines instead of being cut off after one line with an ellipsis while the rest of the card stayed empty.
- Without a map image, the route page now lists the travel days with their distances instead of drawing a decorative sine wave that looked like a route, had no relation to one, and whose labels overprinted each other.

### Added

- A day that has personal photos assigned but could not load any of them now says so on the page, naming the concrete cause, instead of announcing "20 eigene Fotos" next to nothing.

## [4.17.1] - 2026-08-04

### Fixed

- "Für diese Reise wurden keine Fotos für das Video gefunden" on a trip with 261 memories, still failing after the HEIC fix (live report). The photo cache keyed entries by media id, provider item id and SIZE - but the rendered thumbnail and the untouched original are both requested under the size name "large". An iPhone HEIC original cached before the fix was therefore served to the thumbnail request that exists precisely to avoid HEIC, so every export got undecodable bytes again. The request kind is now part of the key, and the cache namespace was bumped so entries written by the old scheme are ignored.
- A downloaded photo whose format no renderer can open is no longer passed on: the fetch skips it, tries the remaining variants, and names the format as the reason. Such an image is also never written to the cache, which used to turn one bad download into a permanently failing export.
- The video error now distinguishes "nothing was there" from "everything was there and none of it could be decoded", and names the counts and the last concrete error either way. The two looked identical and sent the search in the wrong direction.

## [4.17.0] - 2026-08-04

### Added

- "Letztes PDF" next to the PDF button, matching "Letztes Video" (live question: "Sollten wir für das pdf auch einen alten Abruf ermöglichen?"). A generated summary is now also written to the export library, so it stays retrievable after its five-minute download ticket has expired - no rebuild, no waiting. The five newest PDFs are kept, older ones are pruned.
- The immediate download after generating is unchanged and still runs through its short-lived ticket.

### Changed

- The configured video library folder now holds both videos and PDFs, and is labelled "Reise-Export-Bibliothek" accordingly. An existing configured path keeps working unchanged.

## [4.16.2] - 2026-08-04

### Fixed

- A stop showed Park4Night buttons for places it has nothing to do with (live report: "Wir stehen an einem unbedeutenden Platz nicht mit p4n verlinkt sondern mit Google. Er scheint noch historische p4n links zu haben. In den Daten des Stopps finde ich nichts von p4n."). The stop card scanned the entire `details` blob for links, which includes `place_profile.source_hints` - a snapshot of an earlier enrichment run that outlives every change of the stop's identity. Links are now read from name, notes and the few fields that actually reference the current place, so what the user sees and can edit is what the card offers.
- The Park4Night coordinate automation read the same blob and could therefore have filled a stop with the position of a place it used to be. It now reads the same current references only.

## [4.16.1] - 2026-08-04

### Fixed

- "Link lesen und übernehmen" could do absolutely nothing when pressed - no error, no filled field, no note (live report). Without edit rights, or on a trip that is not the active plan, the press was swallowed by a permission check. It now says which of the two applies.
- The form shows "Link wird gelesen …" the moment the button is pressed and always ends with a verdict, including "keine Auskunft erhalten (ältere Version?)" when an older backend answers. A press that never reaches the backend is no longer indistinguishable from one that returns empty.

### Changed

- "Werkzeuge & System" now shows the loaded interface version next to the running integration version whenever they differ. A missing new function and a panel that is still the old one looked identical from a phone.

## [4.16.0] - 2026-08-04

### Added

- An overnight alternative the user names in the change basket can no longer disappear from a draft (live report: "Das ist die Alternative Übernachtungsoption heute Nacht" plus a link went through, applied cleanly - and never showed up under "Stellplätze"). After the whole draft is compiled, Roadplanner checks whether the request actually produced an overnight option for the affected day and parks one itself if it did not. The link is resolved exactly like every other link the user shares, so no coordinate is ever invented, and the option is added as a reviewable backup - never activated, never replacing an existing plan.
  - Every earlier salvage sat inside the day's `overnight_plan` branch and could therefore only repair a plan the model had already emitted; a model that answered with something else entirely slipped past all of them.
  - The check refuses to guess: it needs an unmistakable request (an alternative AND an overnight AND a link or coordinates) and an unambiguous day - the single day the draft touches, or the current travel day when the draft touches none.

## [4.15.4] - 2026-08-04

### Added

- "Link lesen und übernehmen" now shows what the read actually did, directly in the form and permanently (not just in a toast that fades): whether the page answered and how much of it was read, whether it named a position or a name, and what the AI reader made of it. A lookup that "runs without an error but does nothing" (live report) can now be diagnosed from the phone instead of from the Home Assistant log.

### Fixed

- The place read now accepts up to 3 MB of a page (was 900 kB). A map application ships its marker inside a large embedded state payload that regularly sits past the old limit - the position was cut off before it could be read.
- The success message no longer claims that name and notes were prefilled when the link in fact yielded nothing at all.

## [4.15.3] - 2026-08-04

### Fixed

- "Die Seite konnte nicht gelesen werden oder enthält keine eindeutige GPS-Angabe" when adding a Stellplatz-Option from a shared naturkartan.se link (live report). Three separate causes, all addressed:
  - Map-driven place pages ship their marker as script data (`__NEXT_DATA__`, inline widget config) or as Open-Graph place tags, not as JSON-LD. Those are now read as well, plus coordinates inside an embedded map link. The scan stays deterministic and refuses to guess: if a page names several places whose positions disagree, nothing is taken over.
  - The bare product token in the User-Agent got the request rejected outright by protective front-ends. Shared pages are now requested the way a link preview does, with the integration and its version still identifiable in the string.
  - A page whose name was readable but whose position was not left the user with a red banner and a completely empty form. The name now prefills the form as usual and only the coordinates stay open.
- The error message names the actual cause instead of lumping two together: page read but without position, request rejected (with HTTP status), timeout, or unreachable - each with the matching next step.

## [4.15.2] - 2026-08-04

### Fixed

- "Änderungsentwurf konnte nicht erstellt werden / Eine Aktualisierung benötigt Änderungen, eine Position oder place_query" (live report on assistant_prepare): the assistant regularly echoes an entity it decided NOT to touch - an update whose fields are all null and therefore collapses to an empty change. That single empty operation rejected the ENTIRE draft, throwing away every good operation next to it. The empty operation is now dropped, the rest of the draft is created as usual, and the omission is listed as an assumption in the Änderungsübersicht so nothing disappears silently.
- A draft that consisted only of such empty updates now says what to do ("Bitte formuliere im Änderungskorb, was genau geändert werden soll") instead of naming an internal field.

## [4.15.1] - 2026-08-04

### Fixed

- "Gemini hat nicht rechtzeitig geantwortet" when drafting changes for a large trip (live report on assistant_prepare): a single Gemini call was hard-capped at 40 seconds no matter how large the configured time budget was. A ChangeSet draft over dozens of days with structured output regularly needs longer, so the first attempt timed out, the automatic retry got an even smaller slice, and the whole request failed. Each call may now use the remaining budget, reserving room for one more attempt only while plenty of budget is left (e.g. 105 s for the first call of a 120 s budget instead of 40 s).
- The default time budget for assistant requests is now 120 seconds (was 75); the timeout message additionally points to that option for large trips.

## [4.15.0] - 2026-08-04

### Added

- Stops that show "Ort fehlt" while carrying a Park4Night link now fill their map point automatically (live request). A background job (90 s after start, then hourly, max 5 stops per run) reads the linked place pages deterministically - the page publishes its own GPS, no AI and no estimation involved - and parks the coordinates as ONE review ChangeSet under "Übergaben". Nothing is ever applied unattended: a wrong place can be rejected before it touches the roadbook, coordinates are stored as page-read (never provider-verified), and every attempted stop is remembered so an unreadable page can never become a retry loop. The new panel action `park4night_autofill_run` triggers a run on demand (with `force` to retry remembered stops).

## [4.14.38] - 2026-08-04

### Added

- The travel-integrity report now checks the day CALENDAR (live report: "Das Montag an der Stelle ist falsch. Weiß nicht wo er das hernimmt"). The weekday shown on a day is derived from that day's stored date, so a drifted calendar is what actually looks wrong - and nothing used to say so. Two days carrying the same date, or a day dated earlier than the day before it, are now reported as warnings naming both days and dates, with a hint that "Heute" can land on the wrong day because of it. Undated or unparsable dates never trip the check.

## [4.14.37] - 2026-08-04

### Fixed

- A stop carrying several Park4Night links (Plan A/B/C) showed three identical, indistinguishable "park4night.com" buttons (live report). Each shared link is now labelled by its place id ("Park4Night #603309") with a caravan icon, and the place id is its identity - so the same place listed under both URL shapes (/place/<id> and /lieu/<id>/) appears exactly once. Other pages keep their domain as the label.

## [4.14.36] - 2026-08-04

### Changed

- The crop box for the crew reference photo can now be MOVED, not only resized (live request): tap or drag anywhere on the enlarged photo to place it over the right face. The photo is no longer letterboxed inside its frame, so the box sits exactly over the pixels it selects, and dragging no longer scrolls the dialog.

### Added

- The panel notices when the browser still runs a pre-update interface ("Ich hatte das neue Release eingespielt. Keine Veränderung"): it compares the version it was loaded with against the version the backend reports and shows "Ältere Oberfläche geladen - Roadplanner läuft auf X, geladen ist noch Y" with a direct update button. "App aktualisieren" now also forces a fresh document (one-shot query parameter), so a service-worker cached page can no longer keep serving the old module.

## [4.14.35] - 2026-08-04

### Changed

- Photos shown in the panel no longer cost a Microsoft Graph call each time: resolving a photo's short-lived provider URL is memoized for five minutes (far below its own validity), so scrolling a trip with hundreds of memories resolves each photo once instead of on every view. Deliberately NOT changed: the view still answers with a redirect, so the image bytes keep flowing straight from the provider CDN to the browser - relaying them through Home Assistant would add load (and remote-access traffic) instead of removing it. Sizes and kinds stay separate entries, expired entries are always resolved again, and the memo is bounded.

## [4.14.34] - 2026-08-04

### Added

- Downloaded personal photo previews are cached locally (live question: "da ist ein permanentes Runterladen eigentlich übertrieben"). Every PDF export, video render and crew portrait previously re-fetched the same photos, each costing TWO Microsoft Graph round trips (resolve the short-lived URL, then download). A cache hit now skips both. The cache lives next to the other Roadplanner data (archive/media_cache), is bounded at 400 MB and pruned least-recently-used, keyed by media id + OneDrive item id + rendered size so a re-imported photo can never serve stale bytes. Only the user's OWN photos are stored - provider stock imagery stays a reference, as before. Every cache operation fails open: an unreadable or unwritable cache simply means the photo is downloaded as usual.

## [4.14.33] - 2026-08-04

### Added

- The crew reference-photo picker paginates through ALL trip photos instead of showing only the first 48 ("Ich sehe die Bilder jetzt, aber nicht alle"): 36 tiles per page with Zurück/Weiter and a "37–72 von 255" counter; the page containing the currently assigned photo opens by default.
- The selected photo is shown LARGE above the grid, and a face region can be cut out of it for the assignment ("wenn mehrere Leute drauf sind"): tap the photo to place a crop box, size it with a slider, "Ganzes Bild" resets it. The crop is stored normalized on the person, applied to the crew-card portrait and used as the Vision reference face - so a group photo can still identify exactly one person.

## [4.14.32] - 2026-08-04

### Fixed

- ROOT CAUSE of the photo-less PDF and video found: iPhone photos are stored as HEIC, and the Pillow build shipping with Home Assistant cannot decode HEIC. The original downloaded fine and was then discarded SILENTLY - which is why the app showed all 255 memories while both exports stayed empty. Both exporters now request OneDrive's rendered JPEG preview FIRST (a new custom Graph size c1920x1440 for print/video quality, then the standard large preview), and only fall back to the original as a last resort. Graph renders every preview as JPEG regardless of the source format - the same images the panel has always displayed correctly.
- An undecodable image is no longer silently dropped: PDF and video log a warning naming the detected format ("HEIC (von Pillow nicht unterstützt)", "JPEG", "PNG", ...) and the byte size, so a photo-less export is diagnosable from the log.

## [4.14.31] - 2026-08-04

### Changed

- The PDF route page now shows the REAL route on a real map (live report "Karte macht keinen Sinn"): a static map framed automatically around all travel days (OpenStreetMap or Google Static Maps, per the map_snapshot_provider option), with markers per day, attribution and a compact day legend below. The schematic zigzag remains only as the fallback when the trip has no coordinates yet.
- The vehicle icon is now a high-roof camper van (sloped windscreen, raised roof, side window) instead of the boxy caravan trailer - "Das Fahrzeugsymbol passt nicht zu einem Nugget Hochdach Plus".

### Fixed

- Crew notes and stop/day titles no longer show black boxes where a line break was ("Besitzer von Notbert[]Mag Natur"): multi-line texts are collapsed into one readable line with "·" separators before rendering.
- Photo downloads for PDF/video are far less likely to silently fail: the size cap rose from 6 MB to 30 MB (modern phone photos routinely exceed 6 MB and were dropped silently) and the timeout from 8 s to 25 s. Every failed download is now logged at WARNING with its concrete reason, and a photo-less video export names that reason in its error message instead of just "keine Fotos".

## [4.14.30] - 2026-08-03

### Fixed

- "Für diese Reise wurden keine Fotos für das Video gefunden" despite 255 memories - root cause found: both the video and the PDF export looked EXCLUSIVELY at photos linked to a stop. Photos that are assigned to a travel DAY but to no individual stop were invisible to them. Day-linked photos now fill the remaining photo slots of a chapter/day page (best quality first, never duplicating a photo already used through its stop), the highlight-day preselection counts them, and the day's photo-count chip includes them.
- The crew reference-photo picker was unusable on iOS (live report "Da kann man aber kein Bild zuordnen"): the tiles collapsed into ragged full-height columns because Safari/WebView ignores `aspect-ratio` on a `<button>`. Tiles now have a fixed height, the selected photo gets a clear ring plus a checkmark badge, and the current assignment is shown as a preview with a "Zuordnung entfernen" button.

## [4.14.29] - 2026-08-03

### Added

- "Wer ist wer" without captioning photos (live request): each crew person can be assigned ONE reference trip photo in the crew settings ("Reisefoto zuordnen" - a photo grid inside the person form, one tap to select, clearable). The assignment does two things in the trip PDF: the photo IS the person's portrait on the crew card, and Gemini Vision uses it as the reference face to recognize the person on the day photos and write the personal 2-sentence summary - bounded (reference + max 10 already-downloaded photos), strictly fail-open, and only claiming what is confidently visible. Photo captions mentioning the name keep working as an additional source and take precedence for the summary text.

## [4.14.28] - 2026-08-03

### Added

- Personal crew cards in the PDF (live request "Bilder der Leute ... bissel persönliches"): each crew member's card now shows a REAL photo and a short personal summary of what they experienced on the trip. Both come from the photo captions in "Erinnerungen": pictures whose caption mentions the person's name (whole-word match) provide the portrait (best-quality match) and the raw material for a warm 2-sentence summary written by Gemini strictly from those captions (fallback without AI: the caption snippets themselves). No captions mentioning a name → the card stays as before. The crew section paginates cleanly when it outgrows one page.
- Day pages got an at-a-glance description as keyword chips (live request "kleine Beschreibung ... als Stichpunkte"): up to three highlight stops of the day (activities, attractions, viewpoints, ferries - or the overnight place) plus the day's own photo count, deterministic and never invented.

## [4.14.27] - 2026-08-03

### Fixed

- The PDF trip summary is presentable again (live report: "eine Zumutung"):
  - Polish/Baltic place names and bullet separators no longer render as black boxes - the PDF now embeds the bundled DejaVu Sans font (full Latin coverage, license included) instead of WinAnsi-only Helvetica, with a safe Helvetica fallback.
  - Day pages are no longer near-empty: under the title a stats line shows date, kilometers and driving time, and ALL stops of the day appear as a real list with type label and arrival/departure times (with a "… und N weitere Stopps" overflow line) instead of two or three chips. Photos appear above when available - the resilient photo fetch from 4.14.26 applies to the PDF too.
  - The route page samples its nodes EVENLY across the whole trip and numbers them with real day numbers (before: the first 12 of 28 days, numbered 1-12 as if they were the whole route), and labels now actively avoid colliding with each other, the nodes and the vehicle badge.
  - Crew notes are fitted to their card with an ellipsis instead of running off the page edge.

## [4.14.26] - 2026-08-03

### Fixed

- "Für diese Reise wurden keine Fotos für das Video gefunden" despite 254 memories (live report): the photo fetch is far more resilient now. Personal photos try up to three candidates per stop, each as ORIGINAL first and then as the large thumbnail (the exact image the panel displays reliably) - one failing OneDrive download URL no longer sinks the stop. Stock galleries whose PRIMARY image is a Google photo (server-side not downloadable) fall through to the next non-Google image instead of giving up.
- When a build still ends without a single image, the error now says exactly what happened ("X Stopps haben eigene Fotos, Y Planungsbilder, alle Downloads fehlgeschlagen - Details im Log, typische Ursachen: OneDrive-Anmeldung abgelaufen / kein Internetzugriff"), and the video status shows live counts ("Bisher 12 Fotos und 6 Kartenbilder") while building. Failed photo resolutions are logged at INFO level with media id and size.

## [4.14.25] - 2026-08-03

### Fixed

- Downloaded trip videos were 17 bytes (live report): the library download view required Home Assistant session authentication, but the companion app performs the link download WITHOUT an auth token - so every download saved the literal "401: Unauthorized" body as the video. The view now works like the PDF ticket download: no session required, the unguessable 128-bit uuid4-hex filename IS the access token, strictly pattern-validated, never listed, only handed out through authenticated panel actions and the owner's notification.

## [4.14.24] - 2026-08-03

### Changed

- The trip video build is traceable now (live report: "Video erstellen" pressed, nothing observable afterwards): pressing "Reise als Video" starts a BACKGROUND build and returns immediately - a status line under the toolbar shows the current stage (loading data, chapter X/Y, ffmpeg render, saving), updates every few seconds, and announces success (with download link) or the exact error. A second parallel build is rejected with a clear message.
- The last created video is retrievable at any time: a new "Letztes Video" button next to the export fetches the newest entry of the durable video library (with size) and downloads it - no more relying on the one notification link.

## [4.14.23] - 2026-08-03

### Fixed

- The Google/map configuration options showed their raw keys without any explanation (live report): google_places_enabled, google_places_api_key, google_places_mode, map_snapshot_provider, google_places_daily_limit, google_places_request_timeout, google_photos_enabled and google_photos_daily_limit now have proper German and English labels plus descriptions that explain what each option does, what it costs and what the fallback behavior is (e.g. map snapshots only affect the trip video export; Google photo loads are billed separately and capped by their own daily limit).

## [4.14.22] - 2026-08-03

### Fixed

- The trip hero image no longer switches to photos taken outside the trip (live report: a Christmas-tree night shot became the cover of the July trip): trip-cover candidates whose timestamp provably lies outside the trip window (± 2 days) are excluded - photos without a parsable timestamp stay eligible, an explicitly user-chosen cover is never excluded.
- The sticky trip cover now truly sticks: the currently chosen cover photo is kept in the candidate set even when new photo batches push it out of the local top ranking. Before, every larger OneDrive sync re-ranked the candidates, the old cover fell out of the top set, the stickiness guarantee lapsed and Vision picked a different hero image again ("Wir haben schon wieder ein anderes Bild"). An out-of-window cover is deliberately NOT retained - it gets replaced by an in-trip photo.

## [4.14.21] - 2026-08-03

### Changed

- The pitch map now distinguishes its points intuitively (live request): the ACTIVE overnight place is a blue STAR, backup options are numbered colored markers (B1 amber, B2 violet, ...) whose ROUTE LINE uses the same color, tomorrow's first stop is a green arrow, and the day's own route stays gray dots. The legend chips under the map, the "Umwege je Kandidat" summary and each option row carry the same color, so list, summary and map read as one unit. The map caption explains the semantics in one line.

## [4.14.20] - 2026-08-03

### Fixed

- Alternatives with a naturkartan/website link and user-dictated coordinates are no longer lost (live report: "Nimm als Alternativen Stellplatz auf: Koordinaten ... Naturkartan: ..." was acknowledged in chat, then nothing was saved): the server-side salvage now covers ANY https place link (not just Park4Night/Google Maps), derives a readable option name from the URL slug (e.g. "Rastplats Storbergsudden"), and re-attaches coordinates named in the decision text as the option's reviewable position - so the option also appears on the pitch map.
- Stops created from a shared non-Google place link (naturkartan.se, campsite website ...) no longer end as "Ort fehlt": the link now goes verbatim into place_query and Roadplanner reads the position DETERMINISTICALLY from the page's own metadata (JSON-LD GeoCoordinates, geo.position/ICBM meta, og:title as name). Such a pin counts like a user-confirmed map point (provenance "user_shared_link", never provider-verified). "Link lesen und übernehmen" uses the same deterministic reader BEFORE falling back to the AI page reader.
- An overnight option whose place_query is a precise coordinate pair now gets that pair as its map position - user-dictated coordinates stay visible and reviewable instead of silently disappearing.

### Added

- The stop card now offers the links the user shared for the stop (naturkartan.se, website ...) as buttons next to Google Maps/Park4Night - "Er sollte wenigstens den Link anbieten um es nachschlagen zu können". The Reisebegleiter is also instructed to keep shared links in the stop's notes so they stay reachable.

## [4.14.19] - 2026-08-03

### Added

- Photos from a page the user shared for a stop (Park4Night, naturkartan.se, campsite website ...) are now taken over as planning-image candidates (live request: "Könnte er aus dieser Anweisung nicht auch die Bilder mitnehmen?"). A new deterministic reader extracts the page's own photos - og:image/twitter:image metadata, JSON-LD image entries and plain img tags, with logo/icon/map junk filtered out - and ranks them AHEAD of the generic image search, because they show the actual place. Reference-only like every other image source: the files stay with the linked website, every image keeps the page as source and attribution. Wired into both the planning galleries (auto + refresh) and the "Stopps anreichern" preview; Google-Maps/Wikipedia/OSM links are skipped (consent walls, already covered by Commons). Fails open - an unreachable page never breaks the gallery.

## [4.14.18] - 2026-08-02

### Fixed

- "Der Google-Maps-Link konnte nicht aufgelöst werden" / link lookup filled only the name: the resolver now handles Google's EU consent interstitial - a redirect to consent.google.com is never followed (the consent page carries no place data), instead its `continue` parameter IS the canonical maps URL and is used directly; the consent cookie is also sent while following short-link redirects. Additionally, the link-preview reader now parses the page's canonical/og:url metadata, which carries the FULL maps URL including the precise !3d/!4d marker - so coordinates are found even when the static-map preview image is missing.

### Changed

- Stellplatz options are easier to spot: a counted heading "Backup-Optionen (N)" with divider now sits above the option list.
- The pitch map now says explicitly which options are NOT shown because they have no GPS yet, with a pointer to "Bearbeiten" → "Link lesen und übernehmen" (live question: only one of two spots appeared on the map - the second had no coordinates).

## [4.14.17] - 2026-08-02

### Fixed

- A stop created from a user-shared Google-Maps POI link now adopts the POI's real NAME, not just its pin (live report: "wir haben hier gegessen" + restaurant link produced a stop called only "Essen"). The deterministic resolver reads name and coordinates TOGETHER from the canonical URL, and fetches the link-preview metadata whenever either is missing - marker-only mobile shares get their name from og:title, name-only links get their position from the static-map preview. The model's own label survives in the notes so no intent is lost; existing stops are never renamed this way.

### Changed

- "In Google Maps prüfen"/link lookup in the manual forms now prefills name AND coordinates together when the link carries both, instead of one or the other.

## [4.14.16] - 2026-08-02

### Fixed

- Overnight alternatives from the chat are now guaranteed server-side: when the model emits an overnight_plan whose merge adds ZERO new options (the repeated live failure - the chat claimed "als Alternative aufgenommen" while the draft contained options: []), the sanitizer deterministically salvages the candidate links from the raw plan and the operation reason, and as last resort from basket decisions that talk about an alternative (alternativ/zweite Option/Plan B/Backup). Salvaged candidates become reviewable backup options with the link as identity and placeholder name; the Plan-A link from an unrelated basket decision is never turned into an option.

## [4.14.15] - 2026-08-02

### Fixed

- "Der Google-Maps-Link konnte nicht aufgelöst werden" for shared POI links whose canonical URL carries nothing readable at all (cid-only shares): as a last resort the resolver now reads the page's LINK-PREVIEW metadata - the og:title and the static-map preview image, whose center/markers parameter encodes the place position. Deterministic, no AI, the same data any messenger shows for a pasted link; the rendered maps application content is never parsed. A consent interstitial or generic title still fails open to manual completion.

## [4.14.14] - 2026-08-02

### Fixed

- Shared Google-Maps POI links (maps.app.goo.gl, "?g_st=ic") are no longer silently ignored: their canonical URLs often carry NO @lat,lng segment and NO /maps/place/<name> - the precise marker position sits in the data blob as !3d<lat>!4d<lon>, or the link resolves to a q=/query= parameter form. The deterministic link resolver now reads the precise marker (preferred over the @viewport center, which could be zoomed far away), /maps/search/<name> paths and q=/query=/destination parameters (coordinates or text), each range-validated; unreadable forms (e.g. cid-only links) still fail open to the model's text query.

### Changed

- Enter in the Reisebegleiter chat now inserts a NEWLINE instead of sending the half-typed message; sending is the send button or Ctrl/Cmd+Enter. A hint under the input field explains both.

## [4.14.13] - 2026-08-02

### Fixed

- The compile prompt now explicitly covers adding ONE single overnight alternative ("nimm als Alternative/zweite Option den auf <Link>"): the model repeatedly emitted `overnight_plan` with an EMPTY options list while the chat claimed the alternative was added - the 4.14.10 merge salvage can only keep candidates the model actually includes. The prompt now states that an empty options list is always wrong for such a request, that the single candidate must appear as exactly one entry, and that a user-provided link belongs in `url` even without a name (Roadplanner then assigns the reviewable placeholder name from 4.14.10).

## [4.14.12] - 2026-08-02

### Changed

- No more scrolling past the whole chat history to reach a freshly filed change: when the Änderungskorb is non-empty, a compact quick bar appears directly under the message composer with the basket count, the same "Änderungen prüfen" action, and a "Details ansehen" jump that scrolls the full Änderungskorb into view. With an empty basket the bar stays hidden.

## [4.14.11] - 2026-08-02

### Added

- Orchestration-level simulation environment (`tests/orchestration_harness.py` + `tests/test_orchestration_simulation.py`): boots the REAL manager, handoff store and enrichment orchestrator on the real store - without Home Assistant - and replays exactly the race patterns that hurt live this week: a clean submit applies directly, a background write before ingest yields a VISIBLE conflict handoff, a background write before apply falls back to a visible review handoff with the reason, a re-submit supersedes the stale pending handoff, and seeded random interleavings of user edits, background writes and submits check the store invariants plus a new orchestration invariant after every step: a confirmed enrichment is never silently swallowed - it is either applied or visible as a pending/conflict handoff.

### Fixed

- (Found by the new orchestration simulation on its FIRST run) Superseding stale enrichment handoffs never actually worked against stored handoffs: ingest stores the NORMALIZED ChangeSet (`op`/`stop_id`), but the supersede matching only read the entity dialect (`entity_type`/`entity_id`) - so the match against stored envelopes was always empty and outdated pending confirmations for the same stop were never archived. The matcher now understands both dialects; the old unit test had masked the bug with hand-built entity-dialect envelopes and now covers the stored shape too.

## [4.14.10] - 2026-08-02

### Fixed

- "Nimm für heute als alternativen Stellplatz den auf <Link>" no longer produces an EMPTY overnight-options patch that looked like success: a candidate arriving with a link but without a name was silently dropped by the overnight-plan merge, so the chat claimed the option was added while the reviewable draft contained "options": []. A URL-only candidate now survives with a reviewable placeholder name ("Park4Night #51373" / "Stellplatz (Link)"); entirely empty candidates still vanish.

### Added

- The Stellplatz option form (add/edit) now has a "Link zum Platz" field with "Link lesen und übernehmen": Park4Night pages are read directly (AI-free), Google-Maps links are resolved deterministically, all other place pages are read by the Reisebegleiter (KI). The values only PREFILL the form (name, GPS, place query, notes with price/rating) - saving stays the explicit button, and an untouched link keeps the option's stored provenance (e.g. park4night) instead of rewriting it on every edit.

## [4.14.9] - 2026-08-02

### Fixed

- A stop created from a user-shared Google-Maps link no longer keeps demanding verification: the link's coordinates were adopted exactly, but when reverse geocoding could not attach a confident address the stop stayed at "Ort noch prüfen - GPS vorhanden, aber noch nicht bestätigt" forever - although the user had picked that exact pin themselves. Coordinates deterministically extracted from a user-provided Maps link now count as manually confirmed (provider "google_maps_link", never provider-verified), with no open review question. The provenance flag is server-set AFTER validation as a server-controlled operation field, so a model can never mark its own coordinates as user-confirmed; plain coordinate inputs without link provenance keep the review question unchanged.

## [4.14.8] - 2026-08-01

### Fixed

- Asking the assistant to delete a trip day no longer fails with "Änderungsentwurf konnte nicht erstellt werden / ChangeSet-Operation 'remove_day' ist ungültig: Der Reisetag enthält Stopps. Zum Löschen muss remove_stops=true gesetzt sein." The executor's remove_stops gate protects scripted callers - for a reviewed assistant draft the user's request to delete the day obviously includes its stops, and the loud destructive confirmation at apply time stays in place. The sanitizer now sets the flag whenever the day still carries stops, a model-provided flag survives the remove-echo cleanup (it was previously dropped along with echoed junk), and the assistant schema layer accepts the field at all (it was previously rejected as "Nicht erlaubte Felder für day").

## [4.14.7] - 2026-08-01

### Fixed

- The trip hero image no longer changes again and again: the Vision-picked trip cover was re-evaluated whenever its selection fingerprint changed - and the fingerprint includes the candidate photo list and the model name, so every synced OneDrive photo batch (and any model change) triggered a fresh Vision pick with a different hero image. A once-chosen ready trip cover is now STICKY: it stays as long as its photo remains among the cover candidates, and only disappears through an explicit re-evaluation, the photo being removed, or a manually chosen cover (which always wins anyway). Stop and day highlights keep their existing refresh behavior.

## [4.14.6] - 2026-08-01

### Fixed

- A manually CONFIRMED map point no longer keeps being offered for enrichment: the derived location status treated only provider-resolved geocoding as done, so a deliberate manual confirmation stayed "unverified" forever - right after confirming, "Stopps anreichern (1)" and the "GPS-Prüfung offen" notice reappeared for the very same stop. "manual_confirmed" now counts as resolved: the stop drops out of the enrichment offer, the day shows its location data as complete, and already-confirmed stops heal automatically on the next payload build. The stored provenance (manually confirmed, not provider-verified) remains untouched for anyone inspecting the details.

## [4.14.5] - 2026-08-01

### Added

- The manual map point form in "Stopps anreichern" has a new "In Google Maps prüfen" button: it opens the coordinates AS CURRENTLY TYPED on Google Maps, so the point can be verified BEFORE confirming it. Nothing is saved by the check; German decimal commas are accepted, empty or out-of-range values show a hint instead of opening 0,0.

## [4.14.4] - 2026-08-01

### Fixed

- Park4Night pages are now read DIRECTLY (plain page fetch + deterministic HTML parsing of the stated GPS position, title and rating) instead of exclusively through the Gemini url_context reader. Live report: with the Gemini pay-as-you-go credit exhausted, every p4n adoption failed with "Die Seite konnte nicht gelesen werden oder enthält keine eindeutige GPS-Angabe" - even though the coordinates are right there on the page. The AI reader remains only a fallback for p4n pages that fail to parse, and stays the sole reader for generic place pages (Booking, campsite websites, ...). The trust model is unchanged: page-derived coordinates only prefill the manual confirmation, never the roadbook directly. The enrichment dialog labels AI-read facts as "(KI)" and drops that label for the direct page fetch.

## [4.14.3] - 2026-08-01

### Fixed

- "Stopps anreichern" confirmed coordinates finally LAND: the enrichment submit only parked a review handoff pinned to the trip revision at submit time - background writes (automatic route refresh, photo assignment, other handoffs) bump the revision within seconds, so the parked handoff turned into a stale-revision conflict and the confirmed coordinates silently never changed ("Auch anreichern ändert nicht die Koordinaten"). Since every operation was already individually confirmed by the user in the preview dialog, the ChangeSet is now applied DIRECTLY after a clean ingest ("3 Ortsprofile übernommen und angewendet - Kartenpunkte sind aktualisiert"); only a genuine race falls back to the review handoff, now with the reason shown.
- A recognized Park4Night link whose page could not be read (AI provider not configured, unreachable, or out of quota) failed SILENTLY - the "In den manuellen Kartenpunkt übernehmen" button just never appeared and nothing said why. The enrichment dialog now shows a clear warning card ("Park4Night-Link erkannt, Seite nicht lesbar") with the reason and the manual-coordinates way out.

- Briefly leaving the app while the assistant was working showed "Änderungsentwurf konnte nicht erstellt werden / Connection lost" - even though nothing failed: backgrounding the mobile app kills the WebSocket, but assistant_prepare (like the chat, the video export and the page lookups) is shielded server-side and runs to completion; the draft/handoff arrives anyway. The scary error dialog made users retry and produced duplicate handoffs. A connection-lost failure on such a server-continuing action now shows a calm "Roadplanner arbeitet auf dem Server weiter - bitte nicht erneut starten" toast and automatically re-checks a few seconds later, so the arrived result (e.g. the new entry under Übergaben) shows up on its own. Real failures and ordinary actions keep the loud error path unchanged.

### Changed

- Gemini cost/quality tuning: the defaults are now `gemini-3.6-flash` (primary) and `gemini-3.5-flash` (fallback) - the Reisebegleiter's travel advice and multi-day route/ChangeSet compilation get the newest full Flash tier, and a rate-limited primary falls back to the previous full-quality tier instead of failing. Deliberately NOT a floating alias like `gemini-flash-latest`: the client builds requests differently per model generation (structured output combined with search/url tools, temperature handling), so an alias that silently swaps the model underneath would degrade the pipeline. Manually configured models remain untouched.
- The Gemini model configuration is now a MODE choice: "auto" (recommended, the new default) follows the Roadplanner model recommendations and updates with every release - previously the options dialog froze the then-current default as a literal model name on save, so release-side model upgrades silently never reached existing installations. "custom" exposes all three model roles (primary for advice/planning, fallback for rate-limit/error relief, lite for bounded extraction tasks). Existing entries migrate automatically: a stored model matching any historical default becomes "auto"; a genuinely hand-picked model becomes "custom" and stays untouched.
- Bounded schema-extraction tasks - receipt/document analysis (Rechnungen/Belege aus Bildern und PDFs) and vision photo curation - now run on the cheap `gemini-3.5-flash-lite` tier first, with the configured primary model as automatic in-call backup. These calls use no search and no long context, so the lite tier's quality is sufficient there at a fraction of the cost; the advisory chat, compile and research paths stay on the full tier.

## [4.14.2] - 2026-07-31

### Fixed

- Preparing an assistant change review could fail outright with "Änderungsentwurf konnte nicht erstellt werden / Nicht erlaubte Felder für stop: travelers". Since the ferry-details fix, the response schema legitimately allows travelers/vehicle/preferences inside changes (for the TRIP) - so the model sometimes echoes them on a stop or day too, and the strict per-entity field check rejected the whole multi-operation draft over that redundant echo. Such strays are now salvaged into the notes as compact text ("Reisende: Aron, Michaela, Levi"), the same approach used for stray category/text; canonical trip-level crew and vehicle data stays untouched, and an empty echo is dropped without a noise note.

## [4.14.1] - 2026-07-31

### Fixed

- (Found by the new pipeline simulation on its FIRST run) "Add a stop, then move/update it in the same draft" passed the assistant sanitizer but still failed at ChangeSet execution with "Stopp nicht gefunden": the dialect adapter translated the same-batch reference as a direct stop_id, while the executor resolves batch-added entities only through the client-id registries. Same-batch references to just-added stops AND days are now rewritten onto the ref channel during normalization, so the whole add-then-modify shape finally works end-to-end.

### Added

- Fake-provider pipeline simulation (`tests/test_pipeline_simulation.py`): a scripted "fake Gemini" emits compile responses - including exactly the malformed shapes seen live (kebab-case keys, stray category, string positions, coordinate objects in changes.location, echoed changes on remove, Plan-A/B/C handover blocks, wrong day references) - and the REAL production pipeline processes them end-to-end: batch preparation, sanitizer, review ChangeSet, apply to the real store, invariants checked after every response. No external API is ever touched.
- New simulation test environment against the REAL storage stack (no Home Assistant required): `tests/simulation_harness.py` boots the complete canonical store - repository, normalization, mutations, overnight plans, ChangeSets, revision handling - in a temp directory and defines the model's ground-rule INVARIANTS (globally unique stop ids, gapless 1..n stop order, valid option statuses and caps, intact overnight snapshots, positive monotonic revisions). `tests/test_trip_simulation.py` replays a realistic multi-day session (build days, insert/move stops, Plan-B activate and switch back, an assistant-style handover ChangeSet with overnight options, stale-revision rejection) and asserts every invariant after EVERY step. `tests/test_trip_fuzz.py` additionally hammers the stack with seeded random operation sequences - fully deterministic per seed, so any find is replayable as a one-line repro. This is exactly the environment where "Stopps sind instabil" class bugs surface as a named invariant violation instead of a confused screenshot days later; both run in the normal test suite.

## [4.14.0] - 2026-07-31

### Added

- Trip handovers with MULTIPLE overnight candidates per day ("Übernachtung - Plan A/B/C" blocks, e.g. from a ChatGPT-planned route) now land as real Stellplatz-Optionen: the assistant files Plan A as the day's overnight stop and every further candidate into the day's overnight plan - with its Park4Night link as source, the equipment list as pros/cons chips and the rest as notes. The merge happens server-side against the STORED day (the ChangeSet merges details only one level deep, so a raw model plan would have replaced existing options wholesale): user-created options always survive, the same Park4Night place is never added twice (dedup by p4n id, then url, then name), candidates are validated leniently and capped at 6, and model-provided coordinates are never accepted - the option's link is the reviewable path to real GPS. Combined with the new per-option detour routes, a pasted A/B/C plan becomes a fully comparable Stellplatz decision per day.
- The Stellplätze tab now calculates REAL road routes per candidate ("Routen je Option berechnen"): the active overnight stop and every non-rejected option with GPS get an OSRM route through the day's corridor (last GPS stop before the overnight -> candidate -> first own stop of the next day). The direct corridor route is the baseline, so every candidate shows its true detour ("+12 min · +8,4 km Umweg") as a chip on its row and in a summary line, and all routes are drawn on the day's map as colored overlays. With strategy "route_optimal", options are ranked by computed detour. Results are display-only (nothing is written to the roadbook), hash-cached (re-opening the tab costs zero provider calls until a coordinate changes), and options without GPS are named instead of silently skipped.

## [4.13.0] - 2026-07-30

### Added

- The Reisekosten overview now additionally shows ONE approximate EUR total ("≈ 3.148,22 € gesamt · EZB-Kurse vom 30.07.2026") whenever expenses span multiple currencies. Rates are the daily European Central Bank reference rates (no API key, fetched lazily, cached, last known rates keep serving on network failure); the per-currency sums and every original amount stay untouched and authoritative. A currency the ECB has no rate for is named in the label ("ohne XXX (kein Kurs)") instead of being silently dropped.
- The Erinnerungen tab no longer cuts off after the 120 newest photos - with 200+ trip photos, every older photo was simply unreachable and could never be assigned. The photo grid now has assignment filters (Alle / Zugeordnet / Zu prüfen / Ohne Tag, each with live counts) and pages of 60 with "Neuere/Ältere" navigation and a "Bilder X–Y von Z" label; every photo of the trip is reachable and editable, on any page.

### Fixed

- With own photos present on a stop, the notice "Eigene Bilder vorhanden - Nur zusätzliche externe Planungsbilder konnten noch nicht ergänzt werden" is no longer shown at all: own photos are the best possible state, and a warning about missing EXTERNAL planning images is pure noise then. Without own photos, the failure notice (and its retry button) stays unchanged.
- "Stopps anreichern" now takes a LINK: every place card has a "Link zum Ort" field (Google Maps, Park4Night, Booking, a campsite's own website, ...) with "Link lesen und übernehmen". Google-Maps links are resolved deterministically without any AI; Park4Night links use the existing specialized page reader; every other page is read by the Reisebegleiter (Gemini url_context) under the same never-guess rule (GPS only if the page literally states it). The result only prefills the manual-confirmation form - reviewing and confirming stays the user's explicit step, stored as manually confirmed, never as provider-verified.
- Routes now refresh AUTOMATICALLY after route-relevant stop changes - adding or deleting a stop, reordering, changing its GPS/transport data, activating a Stellplatz option, or applying a handoff. A burst of quick edits is debounced into one refresh a few seconds after the last change; the existing per-day route input hash then skips every day whose route did not actually change (a notes- or time-only edit never even schedules one), so only genuinely affected days reach the routing provider - including a neighbouring day whose start moved via overnight continuity. No-op when routing is not configured; failures never surface as errors, the next manual calculation stays available as always.

## [4.12.0] - 2026-07-30

### Fixed

- (Audit) Submitting a place-enrichment archived the older pending handoff BEFORE the replacement was ingested - if the ingest then failed (active trip switched in another tab, a stop edited/removed in a racing panel action), the user's only pending confirmation was silently destroyed, archived as "superseded by a newer handoff" that never came to exist. The supersede now runs strictly after a successful ingest, never archives the replacement itself, and only archives handoffs strictly older than it - two racing submits can no longer archive EACH OTHER.
- (Audit) Applying a handoff could leave it permanently wedged: the trip commit and the "mark applied" bookkeeping were not atomic, and the stored apply result embedded the FULL trip payload - beyond the 256-KiB envelope bound on a large trip, the bookkeeping raised AFTER the commit was already durable. The handoff then stayed "pending" forever: every re-apply hit a revision conflict and a rebased re-apply failed on already-existing IDs. The stored result is now compacted to the essential facts (both in the panel apply and the auto-apply path); live callers keep the full result.
- (Audit) Destination galleries from a place-enrichment were persisted and shown at submit time even when the handoff was ingested as a duplicate or as a revision conflict. Galleries are now only published for a cleanly ingested handoff.
- (Audit) Ferry details could be silently dropped: the compile response schema forbade the structured fields the prompt itself mandates (`changes.details` - including `details.transport` with the ferry role -, `travelers`, `vehicle`, `preferences`, preference `reason`). Under schema-constrained decoding the model physically could not emit them, so a booked ferry's terminals were stored without their ferry roles and the leg rendered as a car drive across the sea. The schema now allows these fields; every value is still fully re-validated server-side. A stray `changes.reason` echoed on other entity types is hoisted to the operation's reason instead of rejecting the draft.
- (Audit) Several plausible model outputs hard-rejected a whole multi-operation draft over trivially salvageable shapes: an echoed identifying `changes` object on remove/move ("changes muss bei remove/move leer sein") is now dropped; a top-level `position` as digit-string ("2") or float (2.0) is now coerced instead of silently discarded (which misplaced new stops and killed position-only reorders); `changes.location` carrying only user-supplied coordinates ({"lat":..,"lng":..} or [lat, lon]) is now salvaged into a "lat, lon" place_query (verified server-side by reverse geocoding) instead of being destroyed and then failing the add for lacking a place_query.
- (Audit) Operations referencing entities changed earlier in the SAME draft were validated against the pre-draft roadbook only: referencing a stop (or day) removed earlier in the draft passed sanitization and blew up the entire draft at changeset ingestion with a cryptic "ChangeSet-Operation N ist ungültig" - it now fails at sanitize time with a clear message naming the contradiction. Conversely, updating/moving/removing a stop ADDED earlier in the same draft (a natural compile shape the ChangeSet executor fully supports) was wrongly rejected as an unknown stop ID, discarding the whole draft - it now passes, with the day reference aligned to the add.
- (Audit) The "wir haben hier geschlafen" duplicate-overnight protection only searched the bounded compile day window: for a target day outside the window (e.g. the previous day while the basket planned days far ahead), the existing overnight stop was not found and a DUPLICATE overnight stop was added - exactly what this logic exists to prevent. The lookup now falls back to the full stored day list.
- (Audit) A stop save, delete or reorder click was SILENTLY DISCARDED whenever another panel action was still running - e.g. saving the stop form during a multi-second route calculation, or clicking the reorder arrows twice in quick succession. The busy state only changed the cursor while the UI stayed fully clickable, and the action guard simply returned without a request, toast or error - the dialog closed and every edit was lost, indistinguishable from success. Actions are now queued and run in order instead of being dropped; long-running opt-outs like the video export bypass the queue so they never stall it.
- (Audit) A background refresh queued while a form was open (any update event from copilot/media jobs) was flushed by the NEXT action even though the dialog was still open - the full re-render rebuilt the form from stale data, erasing typed input; the Park4Night prefill then wrote into the detached old form while the success toast claimed the data had been taken over. The queued refresh now waits until the dialog closes. A revision-conflict error with an open form likewise no longer triggers an immediate reload that would wipe the form.
- (Audit) The stop form closed BEFORE the save request ran, so any server-side rejection (only one coordinate entered, revision changed in between) threw away everything that had been typed, leaving just a toast. The form now closes only after the server accepted the save - the same result-gated pattern the media/archive/crew forms have always used.
- (Audit) Photos for a Stellplatz-Option could never actually be saved: the synthetic gallery key "option:<id>" failed the strict identifier check (no colon allowed), so every save and refresh of an option gallery raised a validation error, and any gallery that ever made it into storage was silently dropped on load. Option covers therefore never appeared. Gallery keys now accept the "option:" prefix (the suffix stays strictly validated); real stop ids are unchanged.
- (Audit) Activating a Stellplatz "Plan B" demoted the OLD activation-time snapshot of the previous stop instead of its current state - manual corrections made after activation (fixed GPS, renamed stop, rewritten notes) were silently thrown away, and re-activating "Plan A" restored stale data. Demotion now takes the stop's live name/GPS/notes and keeps only metadata (pros/cons, price, features, source) from the snapshot.
- (Audit) Switching to a backup pitch was completely blocked when the day's existing overnight stop didn't pass strict option validation - e.g. an address-only location without GPS or an overlong name, both legitimate roadbook states. The activation aborted with an error about a field the user never entered. Demotion now clamps overlong fields instead of rejecting, and preserves an address-only location in the backup's notes instead of dropping it.
- "Park4Night-Daten lesen (KI)" in the stop form worked exactly ONCE per stop and then failed forever with "Kein Park4Night-Verweis gefunden". The first successful lookup overwrote the stop name - which carried the only copy of the p4n reference - with the clean place name, preserving the reference nowhere. The lookup now writes the reference into the notes ("Park4Night: https://park4night.com/lieu/<id>/") before the name is replaced, so repeat lookups keep working after saving.
- A real park4night.com page link was not recognized as a Park4Night reference at all - the shared regex (frontend and backend alike) only matched shorthand forms like "p4n 506374", although the stop form's hint and the panel's error text both promise that pasting the link works. Links like https://park4night.com/lieu/506374/ and /de/place/506374 are now recognized everywhere the shorthand is.
- (Audit finding, confirmed by two independent reviewers) Assistant stop operations targeting a day OUTSIDE the compile detail window silently corrupted that day's stop order. The compile context details only a bounded day window (basket-target days plus neighbours), while ID validation accepts any day of the whole trip - so an operation like "füge am letzten Reisetag einen Stellplatz hinzu" mid-trip, or one resolving an explicit date far ahead, passed validation but saw an EMPTY position-bookkeeping list for a day that really has stops: a new stop was forced to position 1 (in front of the day's start and everything else, instead of before the overnight stop or at the day's end), and a requested move position ("verschiebe auf Position 5") was silently clamped to 1. No error was raised; the broken order only showed up later in the roadbook. Position bookkeeping is now seeded from the full stored day list instead of the context window.

## [4.11.4] - 2026-07-30

### Fixed

- Pasting a tour or booking link into the Reisebegleiter chat (Komoot, AllTrails, Booking, Park4Night, ...) could get the answer "Da ich den genauen Inhalt des Links nicht direkt live auslesen kann..." even though the assistant is fully capable of reading such pages. The chat only enabled its web tools (Google-Suche + url_context page fetch) when the message sounded like a discovery question ("suche", "empfiehl", "in der Nähe", ...) - a pasted URL matched none of those phrases, so a message like "Prüfe den Link" ran without the very tool that opens links. A pasted link now always enables the web tools; Google-Maps links remain excluded, since they are resolved deterministically from their own URL structure and never need a fetch.
- The assistant filed a mountain hike (Skuleberget) as "Stadtbesichtigung" (sightseeing). The compile prompt listed the allowed stop types as a bare enumeration with no semantics - only the sleep-place types had an explicit meaning rule, so the model guessed for everything else. The prompt now carries a short meaning rule for the experience types: `activity` for anything actively undertaken (hike, summit, kayak/bike/boat tour, swim, via ferrata, guided tour), `sightseeing` exclusively for visiting a town (stroll, old town, harbour walk), `attraction` for a single visited sight (museum, castle, church, waterfall), `viewpoint` for a short photo/scenic halt - with the explicit anchor that a mountain or hiking trail is activity, never sightseeing.

## [4.11.3] - 2026-07-30

### Fixed

- Preparing an assistant change review could fail outright with "Änderungsentwurf konnte nicht erstellt werden / Bestehende Stopp-ID ist nicht im aktuellen Reisetag vorhanden", even though the stop existed - just under a different day than the operation referenced, either because the stop moved days after the draft was written (a handoff applied in between) or because the model paired a correctly-referenced stop with the wrong day. Stop IDs are globally unique, so a single match under another day identifies the real day deterministically; the day reference is now corrected automatically (the same stale-day-ID fallback the panel has always had) instead of the whole pending change being rejected. A stop that exists on no day at all remains a hard error - it was deleted or invented, and silently guessing would write to the wrong place.
- Confirming a stop's place profile twice left two pending "Ortsprofile vervollständigen" handoffs for the same stop under Übergaben - easy to do, because the "Ortsprofil vervollständigen" badge on the stop card stays visible while the first handoff sits unapplied. The older handoff then went stale as the trip revision advanced, showing a conflict warning that was pure noise. Submitting a new place-enrichment now automatically archives older pending enrichment handoffs that touch the same stop (marked "superseded"); handoffs from other sources or for other stops are never touched.

### Changed

- The "Ortsprofil vervollständigen" notice on a stop card now says where to actually do that: via the "Stopp anreichern" button below, and that the confirmed change then lands under "Übergaben" for the final apply - previously it asked for a confirmation without naming the place to give it, and didn't mention the second step at all.

## [4.11.2] - 2026-07-29

### Fixed

- Image search failed outright with "Bildsuche fehlgeschlagen" / "Can't find variable: WS_ACTION" - for a normal stop's "Bilder verwalten" just as much as for a pitch option's "Bilder" button. `media.js` referenced the shared `WS_ACTION` WebSocket-message-type constant without importing it; each panel feature file is its own ES module, so importing it in `roadplanner-panel.js` doesn't make it available inside `media.js`. The background auto-populate call hit the same bug silently (swallowed by its own error handling), which is why this went unnoticed until a user actually triggered image search directly.
- Icon-only buttons on a Stellplatz-Option row (Bilder, Bearbeiten, Verwerfen, Löschen, Wiederherstellen) were hard to make sense of without a visible label - a tooltip doesn't help on a touchscreen. Every action now shows real text next to its icon.
- Preparing an assistant change review could fail outright with "Änderungsentwurf konnte nicht erstellt werden / Nicht erlaubte Felder für stop: category", because Gemini sometimes classifies a stop (e.g. "Camping") under a `category` field - a natural word choice for "what kind of place is this" - even though that field only ever belongs to a preference change. The strict per-entity field check rejected the whole pending change outright instead of just that one misplaced field. A stray `category` on anything other than a preference is now salvaged into the notes ("Kategorie: ...") instead, the same approach already used for a stray `text` field.

## [4.11.1] - 2026-07-29

### Fixed

- After a Roadplanner update, some panel tabs (observed on Stellplätze) could keep showing the PREVIOUS release's text and behavior indefinitely, with no error. Root cause: only the panel's entry file (`roadplanner-panel.js`) was ever cache-busted, via the `?v=<version>` query parameter Home Assistant's `panel_custom` appends to its module URL - but that entry file's own static imports (`./features/pitches.js`, `./lib/core-helpers.js`, ...) carry no such parameter and were served with no explicit cache header, so a browser (especially a mobile Companion app WebView) could keep a stale, heuristically-cached copy of a submodule around even after the entry file itself was freshly fetched. Every panel file is now served through a dedicated view that always sends `Cache-Control: no-cache`, forcing revalidation on every load regardless of the URL - a version upgrade can no longer leave part of the panel running old code silently.

### Added

- A dedicated "App aktualisieren" button next to the existing data-refresh icon (kept visible on narrow/mobile widths). A pull-to-refresh gesture doesn't reach the app shell from inside the panel's own scrollable content, so there was previously no way to force a real page/module reload from within the app - only "Neu laden", which just re-fetches data, not code. The new button reloads the page outright (with a confirmation first if a form or dialog is currently open, so in-progress typed input isn't silently discarded); combined with the cache fix above, this is now the reliable way to pick up a fresh release without leaving the app.

## [4.11.0] - 2026-07-29

### Added

- Park4Night pages are now read directly via Gemini (the same url_context fetch the assistant chat already uses during plan handover) - in three places. Park4Night has no public API, so stops like "Parkplatz am Angelteich" stayed at "Ort fehlt" forever; geocoding a generic name is hopeless. (1) "Stopp anreichern": when a stop carries a p4n reference, the review dialog shows a clearly labeled card "Von der Park4Night-Seite gelesen (KI)" with the page's stated GPS position, price and rating; one tap copies it into the existing manual-confirmation form. (2+3) Stop add and edit: the stop form has a "Park4Night-Daten lesen (KI)" button that detects a p4n ID or link in the name/notes, reads the page, and prefills GPS, city and country for review before saving. In every path, AI-read coordinates are never written to the roadbook on their own - reviewing and saving/confirming remains the user's explicit step, stored as manually confirmed rather than provider-verified.
- Planning images for pitch backup options, following the same rule as everywhere else in Roadplanner: before a place is visited its images come from internet sources, afterwards from your own OneDrive photos. The active overnight place is a normal stop and already had both behaviors; backup options now plug into the same internet-image machinery - each option row gets a "Bilder" button that opens the familiar image search (Wikimedia/Openverse, biased by the option's coordinates), the chosen image appears as the option's thumbnail and on the "Plan B" card, and when an option is activated into a real stop its planning gallery is cleaned up so the stop's normal image flow (and later your personal photos) takes over.

### Fixed

- Park4Night stops are handled properly now. Previously the internal ID stayed glued to the stop name ("Parkplatz am Angelteich (p4n #506374)") and polluted every card, map legend and export, while the reference itself did nothing useful on the stop card. Two changes: (1) when the assistant hands over a stop whose name carries a p4n ID, the sanitizer strips it from the name at ingestion and guarantees the reference survives as a real Park4Night URL in the notes (the enrichment flow's source-hint detection scans name and notes, so classification and linking keep working); (2) the stop card now displays a cleaned name for existing roadbook entries too - without mutating the roadbook - and shows a "Park4Night #506374" button that opens the place's page directly, next to Google Maps.
- The tool-tabs tray (Entscheidungen, Dokumente & Kosten, Stellplätze, Gesamtroute, ...) forced itself fully open whenever any of those tabs was active, pushing the actual tab content far down the screen - a real problem on mobile once a tool tab like Stellplätze saw daily use. It now behaves like a normal collapsible menu: closed by default, opened with a tap, and closes again once you pick a tab.

### Changed

- Reworked the Stellplätze tab based on live usage feedback: it now defaults to the current/upcoming travel day (with a dropdown to jump to any other day) instead of stacking every day's card one after another; each day shows a route-context line (where today starts, tonight's stop, tomorrow's first stop) and a small map plotting that context plus every backup option's location; and an option's pros/cons now stand out as their own green/red chips instead of being buried in one plain-text line.

## [4.10.0] - 2026-07-29

### Added

- Stellplatz-Optionen: every travel day can now hold up to six persistent overnight options in the roadbook itself (stored day-anchored in `day.details.overnight_plan` - no migration, older roadbooks are untouched). A new "Stellplätze" tool tab manages them: per-day cards with the active place and its backups, add/edit/reject/restore/delete, a per-day strategy selector (route-optimal, best-first, early-arrival - stored now, ranking logic follows in a later phase), and an editable trip-level pitch-preferences card (must-have features, weighted nice-to-haves, price/detour limits, vehicle size, free text for the assistant). Activating an option ("Plan B") is one atomic commit: the chosen option becomes the day's overnight stop (materialized if the day had none), the previous place is automatically demoted into the options list as a backup - so "Platz voll → Plan B → doch wieder Plan A" never loses a candidate - and the confirmation dialog warns when photos or documents are linked to the old stop, since they may belong to the previous physical place. The "Heute" tab shows a one-tap "Plan B aktivieren" card with the best backup, and the day route is recalculated automatically after a switch.

## [4.9.1] - 2026-07-29

### Fixed

- Starting a trip video export blocked every other panel action - including the assistant chat - for as long as the render ran (up to several minutes), because the panel's single global "busy" flag was held for the whole in-flight WebSocket call and every other action refused to start while it was set. The video export now runs without holding that flag, so the assistant (and everything else in the panel) stays usable while a video renders in the background; the export button still shows its own "Erstelle Video..." progress state so it's clear the render is underway.

## [4.9.0] - 2026-07-28

### Changed

- A finished trip video is no longer served through a short-lived, in-memory download ticket - it's written to a small durable library on disk (new "Trip video library folder" option, `.roadplanner_trip_videos` by default, oldest files pruned beyond 10 kept) and announced with a Home Assistant persistent notification containing the download link. A render can take minutes, and the app may well be closed by the time it's ready; the previous ticket was tied to that one WebSocket response and would be silently lost if the connection was gone when the export finished - the video is now safe on disk and the link keeps working (with normal Home Assistant login) whenever you come back to check.

## [4.8.1] - 2026-07-28

### Fixed

- Roadplanner could fail to load entirely right after updating to 4.8.0 ("Setup failed for custom integration 'roadplanner_mcp': Requirements for roadplanner_mcp not found: ['Pillow>=10,<12']"), because the new trip-video feature declared its own narrow `Pillow` version range in `manifest.json` - which conflicts on any host where Home Assistant Core itself already has a newer Pillow installed (as most current Core versions do), since Home Assistant won't downgrade a package shared with Core to satisfy one integration's separate pin. `reportlab` (already a Roadplanner dependency) pulls in a working Pillow on its own, so the trip-video feature now simply relies on that instead of redeclaring its own version constraint.

## [4.8.0] - 2026-07-28

### Added

- A new "Reise als Video" button next to the PDF export renders the trip as a downloadable MP4 slideshow: real personal-photo-first, stock-photo-fallback chapters (one per day), an optional map-snapshot chapter opener showing where that day's stops were (OpenStreetMap, tile-stitched with attribution, or Google Static Maps - configurable in Roadplanner's options, reusing the existing Google Places API key), crossfade transitions, and a short Gemini-written narrative per day, grounded strictly in that day's real stops/date/distance (the model is explicitly instructed never to invent details). Choose between a short highlight reel (top days only, ~2-3 min) or a full day-by-day recap at export time. Background music is supported but ships without any tracks yet - `assets/music/README.md` documents adding real royalty-free files as a manual follow-up; a missing/empty music folder simply produces a silent video. Rendering runs `ffmpeg` as an isolated subprocess (never blocking Home Assistant's shared executor pool) and needs an `ffmpeg` binary on the host - the button is disabled with an explanation if none is found. Requires the new `Pillow` dependency.

### Fixed

- The trip-summary PDF's cover title always fell back to the generic "Roadplanner-Reise", and the crew/vehicle page never rendered at all, because `trip_pdf_export.py` read the trip dict from a top-level `payload["trip"]` key that does not exist in the assistant payload - the real trip data (title, dates, travelers, vehicle) lives at `payload["summary"]["trip"]`. The export now reads from the correct location, and the previously untested `async_generate` data-gathering path (title/crew/vehicle) now has a real regression test.

## [4.7.0] - 2026-07-28

### Fixed

- Exporting the trip-summary PDF could fail outright with "Die Roadplanner-Aktion ist unerwartet fehlgeschlagen" if any single day photo had been downloaded incompletely (e.g. an interrupted OneDrive fetch). Reportlab only decodes a photo's actual pixel data inside `drawImage()`, well after the existing corrupt-photo guard's `ImageReader.getSize()` call already succeeded from just the file header - so a photo whose header was intact but body was truncated slipped past that guard and crashed the whole export instead of just that one day's one photo.
- A trip's automatically-picked cover photo (shown at the top of the "Reise" tab, and used as a Vision-curation candidate) could land on a photo taken right after leaving home, at a fuel stop, or at a border crossing, instead of anything actually representative of the trip - because both the automatic personal-photo candidate pool and the destination-gallery planning fallback picked whichever confirmed stop came first chronologically, with no regard for whether that stop was an actual destination or just logistics. Photos linked to a purely logistical stop type (waypoint, start/origin, parking, charging, fuel, service, water, waste, laundry, border, break) no longer compete for the automatic trip cover; day covers and an explicit personal trip-cover choice are unaffected.

### Changed

- A day page in the trip-summary PDF with no real, usable photo (none linked, or the only one available turned out to be corrupt/truncated) no longer shows a generic drawn camera-icon filler in its place - a personal trip retrospective shouldn't look assembled with placeholders. The photo area is simply left out for that day, and a day with one real photo now gets one full-width tile instead of one real photo plus one icon filler alongside it.

## [4.6.0] - 2026-07-28

### Added

- A "Reisezusammenfassung als PDF" button on the Gesamtroute ("Reise") tab now exports the whole trip as a downloadable PDF: a cover page, a crew page (the trip's confirmed travelers/vehicle snapshot, with a person/dog/camper icon per member), a schematic route overview, one page per day with its stops and up to two real photos, and a closing stats page (days/km/countries/stops). A stop's own personal photo (OneDrive-synced or manually uploaded, its "Erinnerungen" cover image if set) is always used first; the stock destination-gallery image (Wikimedia Commons/Openverse) is only a fallback for a stop with no personal photo of its own, and a drawn placeholder is the last resort. Google Places photos (which resolve to a browser-session-signed redirect, not a plain fetchable URL) are deliberately skipped for this server-side export either way. Rendering uses the new `reportlab` dependency; the generated PDF is served through a short-lived, few-use download ticket, never stored on disk.

## [4.5.7] - 2026-07-28

### Fixed

- The assistant chat, its review preparation, the connection test, and the daily briefing could all fail outright with "Assistent konnte nicht antworten - Connection lost" whenever the app was backgrounded (or the network briefly dropped) while one of these AI-provider calls - which can take a minute or more - was still in flight. Home Assistant cancels whichever task is awaiting a WebSocket connection that just closed; previously that was the very task running the provider call, so backgrounding the app didn't just lose the reply, it aborted the request entirely and discarded whatever the model had already produced. These four actions now run in a detached, shielded task that keeps going to completion regardless of the connection's fate - the chat's existing "war die letzte Nachricht doch beantwortet?" self-heal check then finds the real answer on the next reload instead of the conversation staying stuck forever.
- A stop with its own specific business name (e.g. "Minimani Rovaniemi") could still get resolved to a completely different, unrelated business at the same shared street address (e.g. a retail park with several tenants), because destination classification treated any parsed street/house number as a pure address lookup and searched only the bare address text - never the name - as soon as one was available. An address-only search at a shared address resolves ambiguously to whichever business a provider associates most strongly with that raw address, silently picking the wrong one even though the stop's own name would have found the right business unambiguously. A specific name is now always preferred for the search unless it's just the address written out with no distinguishing word of its own (e.g. "Krumhermsdorf Neuhäuser 40").

## [4.5.6] - 2026-07-28

### Fixed

- A new stop compiled from a link the user gave (Google Maps or otherwise) could end up with the link only inside `changes.notes`, never in the dedicated `place_query` field the prompt asks for - even though the model correctly extracted it. Since automatic geocoding enrichment only ever inspects `place_query`, that stop's enrichment never even attempted to run, silently leaving it on "Ort fehlt" until a manual "Stopp anreichern" - instead of getting resolved right away as part of the same change, the way a new stop is supposed to. A stop operation with no `place_query` now has its notes and reason text scanned for a link, lifting the first one found into `place_query` (kept in notes too, as human-readable context) so the normal geocoding path still gets a chance to run automatically.

## [4.5.5] - 2026-07-28

### Fixed

- Preparing a pending change could fail outright with "Nicht erlaubte Felder für stop: text" when Gemini's JSON-mode fallback put descriptive content (e.g. facts extracted from a resolved booking link) under `changes.text` on a stop/day/trip update - a field that only ever belongs to `entity_type=preference`. The whole ChangeSet was rejected instead of just the misplaced field, discarding genuinely useful content the model had correctly extracted. A stray `changes.text` on any non-preference entity is now salvaged into `changes.notes` (appended if notes already has content) before the strict per-entity field check runs, the same salvage-not-crash approach used for the earlier `changes.location` fix.

## [4.5.4] - 2026-07-27

### Fixed

- The assistant's compile/basket schema could get rejected by Gemini with a generic HTTP 400 ("Request contains an invalid argument.", no further detail) across several current models (confirmed live for `gemini-3.6-flash`, `gemini-3.5-flash`, and `gemini-3.5-flash-lite`), forcing every call all the way back to the unconstrained plain-JSON fallback - which can never include the `google_search`/`url_context` tools, so a booking-link stop update could never actually get fetched. Google's own guidance for this exact generic error attributes it to schema "complexity" (many properties combined with numeric/length constraints), which a bare 400 doesn't otherwise identify. `GeminiClient` already stripped `maxLength`/`minLength`/`pattern` for this reason; it now also strips `minimum`/`maximum`/`minItems`/`maxItems` from the schema sent to Gemini. None of these are needed for correctness - every value is still fully re-validated server-side regardless of what schema Gemini used during generation.

## [4.5.3] - 2026-07-27

### Fixed

- When Gemini rejects a request shape with HTTP 400 (an "invalid argument" response) and `GeminiClient` falls back to a more compatible shape, the actual provider error text was never logged anywhere - only aggregate counters (`compatibility_fallback_count`) were visible in diagnostics, even once the call ultimately succeeded via a less capable fallback (e.g. losing `google_search`/`url_context` tool access). This made a live report of persistent schema rejections across several current models impossible to diagnose without adding a log line first. Every HTTP 400 compatibility fallback now emits a debug-level log with the request mode, model, and Gemini's own error detail.

## [4.5.2] - 2026-07-27

### Fixed

- A booking-link stop update could still silently skip Gemini's `google_search`/`url_context` tools even with research correctly requested, if the same model had already answered an earlier, ordinary (non-search) call by falling back to its schema-less plain-JSON request shape. `GeminiClient` memoizes the last request shape that worked per model to skip straight to it next time, but that cache was keyed only by model name - so once a model's plain-JSON fallback got cached (very likely, since it happens whenever the primary model times out or errors and the client retries with a fallback model), every later call to that model reused it first, including ones that explicitly needed the search/`url_context` tools to fetch a booking link. The cache is now keyed by `(model, whether search was requested)`, so a plain call's cached shape can never keep a search-requiring call from actually attempting to search.

## [4.5.1] - 2026-07-27

### Fixed

- Pasting a booking link for an *existing* stop's overnight update (for example "Hier schlafen wir heute und morgen: https://booking.com/...") could get accepted into the change basket but then fail when preparing the review ("Als Entscheidungsvorlage"), with a cryptic `stops[N].location muss ein JSON-Objekt sein` error. Root cause: the assistant compile system prompt incorrectly listed `location` as a settable `changes` field for stops, even though only the server-side geocoding plugin may ever populate it (from a confirmed `place_query`); Gemini's plain-JSON fallback (more likely whenever the `url_context`/search tools are used) ignores the response schema and, following that prompt text, could put a raw place name or hand-built object straight into `changes.location`, which then reached the ChangeSet untouched and failed deep inside validation. `changes.location` is now always stripped before the operation reaches the ChangeSet - any text content it held is salvaged into `place_query` (unless one is already set) so the normal geocoding path still gets a chance to resolve it - and the prompt no longer tells the model `location` is an allowed field.

## [4.5.0] - 2026-07-27

### Added

- A pending change ("Übergabe") whose base revision has gone stale - because another change was applied first - can now be "neu aufgesetzt" (rebased): re-validated against the trip's current state and, if it still applies cleanly, re-stamped onto the current revision so it can be reviewed and applied normally. Previously the only option for a stale change was to reject it and redo the underlying request from scratch. If a referenced day or stop no longer exists (or anything else about the change is no longer applicable), rebasing fails with a clear error and the pending change is left completely untouched - there is no partial/best-effort rebase.

### Changed

- Every panel load fetched the trip/day payload, then travel-archive data, then experience data, then crew data, strictly one after another - four sequential round trips on every single click (add/update/remove a stop, apply a change, anything that triggers a refresh), even though most of them don't depend on each other. Independent subsystems (crew alongside the main payload; travel-archive alongside experience once the selected trip is known) now fetch concurrently instead.

### Fixed

- Updating an *existing* stop (as opposed to adding a new one) never enabled Gemini's search/`url_context` tools, even without a resolved `place_query` - only `add` did. A pasted booking link on a stop the chat step had already matched to a prior placeholder therefore got no fetch at all: the model had to guess a name from conversation context alone, and no location was ever resolved. Any stop `add` or `update` without a `place_query` yet, or that mentions a non-Google-Maps link anywhere in its basket text, now enables research the same way.
- Updating a stop, day, or trip's `details` (nested planning metadata - geocoding results, transport/ferry info, source attributions from a resolved booking link, etc.) silently discarded whatever wasn't part of that particular update's patch, since it was a wholesale dict replacement rather than a merge. An update meant only to change e.g. an arrival time, but that happened to also touch `details` for an unrelated reason, would wipe out previously stored `details` sub-keys with no error or warning. `details` is now merged one level deep on update; every other field still overwrites as before.
- A compiled stop `add` could get silently misattributed as last night's overnight and converted into an `update` of a completely unrelated, already-existing overnight stop - overwriting its name/notes - whenever the change basket happened to hold exactly one differently-themed stop item mentioning a past-overnight phrase ("gestern Nacht hier übernachtet") and the new operation itself had no `place_query`/name to match against. The lone-basket-item fallback that caused this is still used (as before) for the lower-stakes task of inferring which *day* an operation with a missing `day_id` belongs to, but no longer feeds the decision to silently rewrite an existing stop - that now requires the operation's own text, or an actual basket match by `place_query`/name.
- The Übergabe-Vorschau (handoff preview) dialog now actually shows what a pending change will do before you click "Übernehmen". `execute_changeset` previously only recorded bare metadata (index/op/day_id/stop_id/position) per operation result, never the requested patch or new entity content, even though the preview dialog already renders each result verbatim - so an `update_stop`/`update_day`/`update_trip`/`update_preference` preview showed no patch at all, and an `add_stop`/`add_day`/`add_preference` preview showed no content for the new entity. `remove_*` operations are unchanged, since there is nothing beyond the already-shown ID to preview.

## [4.4.0] - 2026-07-27

### Added

- Crew &amp; Fahrzeuge: a new cross-trip master-data registry for people and vehicles, managed once under the new "Crew & Fahrzeuge" panel tab instead of being retyped per trip. Retiring a person or vehicle (e.g. selling a camper) only hides it from selection for new trips - it is never deleted, so trips that already selected it keep working. The trip-edit dialog now lets you pick which people and which vehicle are along for that specific trip; the selection is stored as a point-in-time snapshot on the trip (`travelers`/`vehicle`), so later edits to a person's or vehicle's master data don't retroactively change already-planned trips.
- The assistant can now resolve a non-Google-Maps link given for a new or updated stop (Booking.com, Hotels.com, Airbnb, Park4Night, or any other booking/site link) by having Gemini fetch and read the page itself via its `url_context` tool, instead of needing a bespoke parser per booking provider. The model may only extract a place name/address for `place_query` (still verified server-side through the normal geocoding check, exactly like any other place text) plus a few concrete, attributed facts (amenities, price, rating) into notes - it never reports coordinates straight from the page, and an unresolvable page stays an open question rather than a guess. Google Maps keeps its existing, cheaper no-fetch link resolution.

## [4.3.0] - 2026-07-27

### Added

- New `GeminiClient.async_generate_image()` provider capability (dedicated image model, never falls back to the configured text/vision model) plus `vehicle_icon_service.async_generate_vehicle_icon()`, which turns a short free-text vehicle description into a flat, line-art icon image matching Roadplanner's existing icon style. Not yet wired into any user-facing flow (trip-summary PDF/video work is still prototype-only); this lands the tested building block first.

### Changed

- Google Places photos saved into a destination gallery no longer go stale. Instead of persisting Google's short-lived photo URL, Roadplanner now stores only the durable Google photo reference and resolves a fresh URL on demand through a signed redirect (`/api/roadplanner/google_photo/...`), mirroring the existing OneDrive personal-media redirect. Google's photo bytes/URL are still never written to disk. Each view spends one entry of the Google photo daily quota, since it re-resolves live - keep that in mind for the daily limit if a gallery with Google photos is opened often.

### Fixed

- External ChangeSets submitted through the handoff webhook (voice assistants, automations, other tools calling the Roadplanner LLM API) now get the same Google Maps link resolution and geocoding the in-panel assistant chat already applies to its own compiled operations. Previously only the in-panel chat's compile step resolved a pasted Google Maps link (including short `maps.app.goo.gl` links) into coordinates/a place name before geocoding it into `changes.location`; a stop added through any other ChangeSet-submitting path had no such resolution at all, so it kept only whatever bare location the external caller supplied itself.

## [4.2.0] - 2026-07-26

### Added

- Optional, off-by-default Google Places photos as a third destination-image source alongside Wikimedia Commons and Openverse. Requires an explicit new "Google-Fotos aktivieren" toggle in options (separate from the existing Google Places search toggle) plus a Google Places API key; has its own daily request quota, tracked separately from Google Places text search. Returned images show a "Foto von Google" attribution and are not cached in the normal 12-hour destination-image cache, since Google does not guarantee how long the returned image URL stays valid - treat this as a test/preview source for now. See `docs/product/EXTERNAL_SERVICES_AND_PRIVACY.md` for the full data-flow and licensing caveat.

### Changed

- Internal: completed the `frontend/roadplanner-panel.js` decomposition (6749 → 2092 lines), the last of EPIC-006's four planned module decompositions. Split into 3 `frontend/lib/*.js` infrastructure modules (styles, constants, core helpers) and 8 `frontend/features/*.js` mixins (universal import, place enrichment, archive, media, decisions/integrity, assistant, route/map, trip/day/stop), applied to the panel's prototype at load time. Also landed the panel.py/test-harness infrastructure this needed: `panel.py` now serves the whole `frontend/` directory instead of one specific file, and all 8 `import()`-capable frontend tests switched from a classic-script `vm.runInThisContext` harness to real ES-module `import()`. No functional or behavior changes; each step was validated against the full test suite.

## [4.1.0] - 2026-07-26

### Changed

- Internal: completed the `experience_manager.py` decomposition (3143 → 419 lines), splitting OneDrive photo-curation, the AI-assisted planning-photo gallery system, and the aggregated "experience" panel payload assembly into their own collaborators (`media_curation_manager.py`, `destination_gallery_manager.py`, `panel_payload_builder.py`).
- Internal: completed the `roadplanner.py` decomposition (3605 → 403 lines), splitting the store's exception hierarchy, file I/O, ID/JSON validation, routing metrics, document normalization, trip state, on-disk repository, queries, mutations, ChangeSet handling, and handoff-context export into twelve focused modules. `RoadplannerStore` is now a thin facade; the public API is unchanged. Also resolved the one circular import between `roadplanner.py` and `changeset.py`. No functional or behavior changes in either decomposition; each step was validated against the full test suite.

### Fixed

- Failed planning-image searches (Wikimedia Commons/Openverse unreachable, timed out, etc.) now write a debug-level log entry (`Destination image provider %s failed: ...`) instead of failing completely silently. Enable debug logging for `custom_components.roadplanner_mcp` to see the actual cause behind a stop's "Bilder konnten noch nicht geladen werden".

### Added

- The assistant now recognizes a Google Maps link (including `goo.gl`/`maps.app.goo.gl` short links) given for a new or updated stop and resolves it into a `place_query` deterministically from the link's own URL structure - never by fetching the Google Maps page. A coordinate pair encoded in the link takes priority and is verified through the normal GPS-Prüfung reverse-geocoding; otherwise the place name in the link is used as a search query. The result is still reviewed and confirmed like any other place before it becomes a durable stop.

## [4.0.3] - 2026-07-26

### Changed

- Internal: continued decomposing large integration modules into smaller, single-responsibility files (`assistant.py` fully split into four modules; `experience_manager.py` partially split, through the OneDrive media sync engine). No functional or behavior changes; included so the OneDrive sync path can be exercised on a live installation after the refactor.

## [4.0.2] - 2026-07-26

### Added

- Universal Import now recognizes Park4Night-style overnight-stay screenshots: visible GPS coordinates are carried into the existing GPS-Prüfung as a reverse-geocoding query (never trusted directly), and visible name/amenities/portal ID are copied into the stop notes for the existing Park4Night source-hint recognition.

### Fixed

- Pasting an image (Ctrl+V) directly into the "Reisebegleiter" chat message field now attaches it as a document/receipt, matching the existing paperclip attach flow. Previously, paste only worked inside the dedicated archive drop/paste zones.

## [4.0.1] - 2026-07-26

### Fixed

- Park4Night place IDs written without a URL – for example `Park4Night 448383`, `P4N-448383` or `Park4Night-ID: 448383` – are now recognized as source hints, classify the stop as an overnight place, are stripped from the destination name used for provider searches, and stay out of image queries.
- A recognized Park4Night place ID now outranks the AI text classification when both disagree, so such stops are always searched as camping/overnight places.
- Source hints no longer lose their provider and place ID when a confirmed Google Places discovery result is converted into the durable place profile; the Park4Night link stays labeled after confirmation.

## [4.0.0] - 2026-07-24

### Added

- Optional backend-only Google Places (New) destination discovery with fallback/preferred modes, visible Google Maps attribution, provider diagnostics, a conservative in-process daily limit, and setup documentation.
- Provider-neutral place-profile schema version 2 with structured address, durable provenance, source references, and a separate derived drivable access point for road routing.
- Explicit stop deletion from the day editor while retaining linked documents and personal media.
- Independent trip, day, and stop cover selection with manual overrides and deterministic personal/planning-image fallbacks.
- `tools/dev.py apply-series` for isolated multi-patch preflight and `context-export` for filtered AI/reviewer snapshots with Git metadata.

### Changed

- Google content is used only as transient reviewed discovery: Roadplanner keeps the Place ID reference and normalizes persistent coordinates and address data through OpenStreetMap/Nominatim or manual confirmation.
- Place search can pass bounded location and target-type hints to provider implementations while keeping the API key and provider calls server-side.
- Road routing keeps the real destination marker and can route a vehicle to a nearby derived access point instead of silently dropping an unreachable stop.
- Image status distinguishes existing personal photos from external-provider failures, and concise destination-profile queries are used even when a complete provider profile is unavailable.
- Automatic trip covers reject photos assigned only by date, preventing unrelated but technically strong images from becoming the journey hero.

### Fixed

- Gallery cards no longer report that no images are available when personal photos already exist.
- A stale day reference no longer prevents a uniquely identifiable stop gallery from being refreshed.
- Candidate provider host names are matched only as exact domains or real subdomains instead of unsafe arbitrary substrings.
- Non-drivable nature and beach destinations remain visible in the day route while navigation uses a separately explained access point.

### Security

- Google Maps Platform keys are excluded from panel data, logs, diagnostics, patches, and exported AI context packages.
- Google search responses use a short-lived in-memory cache and do not request or persist photos, reviews, ratings, or atmosphere fields.

## [3.6.0] - 2026-07-24

### Added

- Geodata-first destination intelligence that classifies addresses, ferry and transport terminals, hikes, nature centres, attractions, retail, gastronomy, camping and other stop types before provider search.
- Bounded type-aware geocoding query plans and persisted provider identifiers, destination kinds, source hints and concise image queries in confirmed place profiles.
- Recognition of Park4Night, OpenStreetMap, Wikidata, Wikipedia and Google Maps links as reviewable source hints without treating them as verified coordinates.
- Touch-friendly manual stop ordering with earlier/later controls and direct numbered positions for each Roadbook day.

### Changed

- Place enrichment now rejects surrounding locality results as automatic matches for specific POIs and falls back from reverse geocoding to bounded type-aware forward searches near existing coordinates.
- Destination image search uses the confirmed place identity, city, country and category while excluding notes and day titles; coordinates remain a separate ranking signal.
- The place-review UI is presented as “Stopps anreichern” and explains the geodata-first workflow while leaving times and confirmed stop order unchanged.

### Fixed

- Address parsing retains `Neuhäuser 40`, `01844 Neustadt in Sachsen` and `Krumhermsdorf` instead of turning aggregate category text into a city.
- German destination terms such as `Fährterminal` and hyphenated `-Wanderung` are translated into provider-friendly POI searches without losing the proper name.
- Overlong internal image queries are shortened safely instead of failing at the 400-character provider boundary.
- Gallery refreshes recover a uniquely identifiable stop after a stale day reference and continue with the canonical day and stop IDs returned by the backend.
- Manual move controls calculate their target from the canonical explicit position sequence instead of a potentially stale payload array order.

## [3.5.0] - 2026-07-23

### Added

- Structured address parsing and controlled multi-variant Nominatim searches with explicit house, street, locality and mismatch quality levels.
- Reviewable weak place candidates instead of an immediate dead end when only a street, locality or partial address can be resolved.
- Optional AI place-text cleanup that can normalize names and address fields without receiving, producing or verifying coordinates.
- Manual WGS84 place confirmation with explicit non-provider-verified provenance and separate confirmation for AI-suggested stop renames.
- Safe local `tools/dev.py` commands for repository status, full checks, reviewed patch application and binary-safe staged patch export.

### Changed

- Place completion now separates text normalization, provider geocoding and user confirmation so AI suggestions can never silently become map coordinates.
- Place-review dialogs expose match quality, search provenance, manual fallback and optional AI cleanup while preserving the existing ChangeSet review boundary.
- Technical `assistant_prepare` diagnostics remain available, while the visible dialog explains day-assignment failures in user-facing language.

### Fixed

- Existing Roadbook day IDs returned by the assistant in `day_ref`, including `day-e6c19b335d42`, are losslessly normalized to `day_id`; true new-day references remain strict.
- Place completion no longer requires an exact provider result before showing useful review candidates or allowing an intentional manual map point.

## [3.4.0] - 2026-07-23

### Added

- Reviewable full-place enrichment for incomplete stops, including candidate name, address, coordinates, category, website, phone, e-mail, opening hours, source, map link, confidence and up to three planning images.
- Direct review-only ChangeSet creation from explicitly selected place candidates, without routing the selected values through Gemini again.
- Smart local best-of selection for personal OneDrive photos with duplicate collapse, burst suppression, screenshot penalties and time-diverse highlights.
- Optional hybrid Gemini Vision curation after deterministic local preselection, with bounded candidates, structured image-ID selection and manual-cover priority.
- Semantic selection for representative planning-image covers and personal OneDrive travel-photo highlights.
- Persistent media-curation fingerprints and per-trip daily Vision limits to avoid repeated external analysis of unchanged candidate sets.

### Changed

- Travel integrity evaluates confirmed place profiles instead of treating coordinates alone as a fully complete stop.
- The former GPS-only repair flow is replaced by “Orte vervollständigen”, so users confirm the actual place rather than isolated coordinates.
- Planning-image ranking now separates relevance and technical quality, penalizes logos/maps/posters and prefers diverse representative photos.
- Stop and day presentation explicitly prefers personal `travel_images` after a visit and falls back to attributed `planning_images` before it.
- Media curation defaults to local-only for existing installations; hybrid Vision must be enabled explicitly in Roadplanner options.
- Planning-image and travel-photo galleries label whether selection is local or Vision-curated.

### Fixed

- Place-completion drafts now contain the selected coordinates and place details instead of empty update operations.
- A rejected or unavailable image provider no longer prevents another provider or the place profile from being reviewed.
- Coordinate-only stops remain routable but are visibly flagged until their place identity has been confirmed.
- Any Gemini Vision timeout, invalid output, unavailable thumbnail or exhausted daily limit now keeps the deterministic local best-of selection instead of blocking the stop or album.

## [3.2.1] - 2026-07-22

### Changed

- Assistant operation payloads now normalize lossless structured-output variants before strict Roadbook validation.
- The compile prompt explicitly requires `changes` to be one JSON object and `{}` for move/remove operations.

### Fixed

- `assistant_prepare` no longer fails when Gemini returns `changes` as a one-item object list, a list of disjoint field fragments, field/value records, simple JSON-Patch records, or a JSON-encoded object.
- Move operations with omitted, empty, or explanatory `changes` values are normalized to an empty object instead of raising `changes muss ein JSON-Objekt sein`.
- Conflicting change fragments and accidentally nested multiple operations remain rejected instead of being guessed or merged.

## [3.2.0] - 2026-07-22

### Added

- Trip-wide travel-integrity report with scores for stop order, GPS completeness, routes and visual readiness.
- Review-only bulk GPS completion for all incomplete stops in the active trip.
- Automatic bounded planning-image enrichment for the active trip, including background scheduling and provider-status diagnostics.
- Travel-quality dashboard card and a mobile-friendly detail view with direct repair actions.
- Automatic GitHub publication after a prepared release pull request is merged into `main`.

### Changed

- Planning-image enrichment prioritizes the current and upcoming travel days and skips stops that already have personal OneDrive photos.
- The panel starts only one small best-effort image batch; the backend continues enrichment without blocking the UI.
- Release preparation now documents that merging the release pull request is the publication trigger.
- `tools/release.py publish` observes or verifies the automatic workflow instead of attempting an API dispatch that Codespaces may reject.
- Missing schedule times remain informational and never change the confirmed stop order or lower the trip-integrity score.

### Fixed

- Trips with missing GPS no longer require manual day-by-day diagnosis before repair drafts can be prepared.
- Release publication no longer depends on a Codespaces token having permission to call `workflow_dispatch`.
- Existing personal travel photos are no longer displaced by unnecessary stock-image searches.

## [3.1.0] - 2026-07-22

### Added

- Canonical location states for every day-route node, including explicit missing, ambiguous and unverified GPS data.
- Review-only “GPS prüfen/ergänzen” workflow that prepares geocoding drafts for incomplete stops without inventing coordinates.
- Complete map legends and partial-route notices that keep GPS-less stops visible in their confirmed sequence.
- Two-stage release automation for Codespaces: prepare, validate, push, pull request, publish, and branch synchronization.
- Protected GitHub release workflow that validates the exact `main` commit, creates a lower-case version tag, publishes release notes from the changelog, and attaches validated manual-install artifacts.
- Canonical Roadplanner validation workflow for pull requests to `main`, with an on-demand manual trigger.

### Changed

- Stop order is independent from schedule times: complete explicit positions win; legacy days preserve their stored user-confirmed list order.
- Every stop mutation and ChangeSet operation leaves a complete gap-free one-based `position` sequence behind.
- The assistant plans stop additions and moves against the complete canonical day sequence and emits explicit positions.
- Local and GitHub release checks now use the same `tools/release.py check` entry point.
- Release preparation cuts the `[Unreleased]` changelog section and keeps `manifest.json` and `const.py` versions synchronized.
- Python caches are removed by release automation before and after tests instead of requiring repetitive manual cleanup.

### Fixed

- A timed ferry can no longer jump ahead of untimed parking, pharmacy, shopping or service stops.
- GPS-less stops no longer disappear silently from the day map; the route remains visibly partial until reviewed coordinates exist.
- GPS repair for an inherited overnight start targets the owning previous Roadbook day instead of creating a duplicate stop.

## [3.0.0] - 2026-07-22

### Added

- Canonical day view-model shared by maps, stop cards, schematic day flow, navigation, decisions and assistant context.
- Phase-oriented Roadplanner navigation: Reise, Heute, Erinnerungen and Reisebegleiter.
- Roadplanner 3.0 dashboard with planning progress, open decisions, urgent tasks, visual readiness and the next travel day.
- Deterministic local media curation with duplicate collapse, burst suppression and per-stop/per-day highlights.
- Automatic day covers that prefer personal OneDrive travel photos and fall back to attributed planning images.
- Roadplanner 3.0 Vision & UX Blueprint as the product contract for subsequent work.

### Changed

- Inherited overnight stops are displayed as a shared start marker without renumbering Roadbook-owned stops.
- Legacy `day.start` and `day.end` values remain contextual metadata but no longer appear as pseudo-stops when real stops exist.
- Decision cards prefer locally curated personal travel-photo highlights before external planning images.
- Stop cards show a curated highlight strip while the full OneDrive album remains accessible.
- Technical tools move into a secondary menu so the primary navigation follows the travel lifecycle.

### Fixed

- Map markers, route flow, stop cards, Google Maps handoff and assistant context no longer use divergent day sequences.
- Legacy targets such as a stale `Riga` day-end label no longer appear in the graphical route unless a real Roadbook stop exists.
- Personal-photo duplicates and short bursts no longer dominate stop and day covers.

## [2.8.0] - 2026-07-21

### Added

- Canonical stop ordering shared by Roadbook payloads, maps, day cards, routing, navigation, decisions, assistant context, archives, and imports.
- Automatic destination galleries with up to three planning images per stop.
- Wikimedia Commons coordinate-aware image search and Openverse fallback with source and license metadata.
- Main image selection, reordering, removal, full-screen swipe gallery, lazy loading, and inline retry states.
- Decision slides with up to three images and preference for the stop's own OneDrive travel photos.
- Tolerant structured-output parsing and one bounded Gemini repair attempt for malformed JSON responses.

### Changed

- Stop numbering and derived day routes now use one deterministic ordering contract.
- Existing explicit `position` values remain authoritative; legacy trips fall back to times, start/overnight roles, and stable storage order.
- Destination image providers run concurrently and fail independently.
- Image searches use stop name, category, place, country, coordinates, description, and day context.
- External destination images remain provider-hosted; Roadplanner stores only URLs, attribution, licensing, and selection metadata.

### Fixed

- Maps, stop cards, day flows, routing, navigation, and assistant context no longer disagree about stop order.
- A failed Wikimedia request no longer blocks a stop card, decision template, or alternative image provider.
- Assistant prepare requests can recover from JSON wrapped in Markdown, surrounding prose, a bare list, or a nested JSON string.
- OneDrive image references in persisted decisions are resolved to fresh signed URLs when the panel payload is loaded.

## [2.7.2] - 2026-07-21

### Added

- Decision templates can include the currently planned Roadbook stop as a verified baseline option.
- Current-plan decision slides are visibly labelled and require no change-basket transfer.

### Changed

- Markdown links from the assistant tolerate safe line wrapping inside long HTTPS URLs.
- The assistant review button shows a dedicated progress state and opens the handoff overview after preparation.
- Keep-or-replace decisions may contain the current plan plus up to three alternatives.

### Fixed

- Google Maps Markdown links with Unicode query values are rendered as clickable links.
- The "Änderungen prüfen" button no longer fails silently on touch devices.
- A stale "last message unanswered" banner is cleared when a later assistant reply exists.
- Decision questions that mention keeping the existing plan can no longer omit that plan from the options.

## [2.7.1] - 2026-07-21

### Added

- Assistant responses can contain safely clickable HTTPS and Google Maps links.
- Persistent, mobile-friendly error dialogs with retry and copy-details actions.
- Visible assistant loading state while requests are processed.

### Changed

- Assistant responses are rendered directly without a blocking full panel reload.
- Decision option enrichment runs concurrently with bounded timeouts.
- Missing images, routes, or geocoding results no longer invalidate an entire decision draft.
- Gemini timeout handling reserves time for a configured fallback model.

### Fixed

- Assistant and decision errors are no longer hidden or clipped at the bottom of mobile screens.

## [2.6.5] — Imported baseline

### Added and stabilized

- Native conversational assistant and change basket.
- Roadbook, routes, stops and inherited overnight starts.
- Routing and Google Maps handoff.
- Documents, expenses and daily tasks.
- Decisions and image-based option cards.
- OneDrive Personal photo synchronization and albums.
- Universal importer.
- Mobile layout and numerous assistant-normalization fixes.

This entry records the first Git-managed baseline. Detailed historical notes are preserved in `docs/legacy/2.6.5/`.
