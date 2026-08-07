# Roadplanner Renderer (PoC)

## Wozu diese App da ist

Der vorherige Versuch, Remotion direkt als Unterprozess der Roadplanner-
Integration zu starten, wurde mit **NO-GO** beendet: In Home Assistant Core
gibt es weder Node.js noch einen Browser, und beides kann ueber HACS nicht
geliefert werden. HACS installiert nur Dateien innerhalb des
Integrationsverzeichnisses.

Eine App ist dagegen ein eigener Container. Sie darf eine eigene Laufzeit
mitbringen. Bevor dort Remotion hineingebaut wird, beweist dieser PoC nur
den Weg dorthin:

1. Die App laesst sich aus demselben oeffentlichen Repository installieren.
2. Integration und App finden zueinander.
3. Auftrag, Status und Ergebnis kommen an.
4. Neustarts erzeugen keine haengenden oder doppelten Auftraege.
5. Der bestehende HACS-, Test- und Releaseprozess bleibt unbeschaedigt.

## Installation

1. In Home Assistant: **Einstellungen → Add-ons → Add-on Store**.
2. Ueber das Menue oben rechts **Repositories** oeffnen.
3. `https://github.com/azedler/roadplanner` hinzufuegen.
4. Die App **Roadplanner Renderer (PoC)** installieren und starten.

Es wird ein fertiges Container-Image geladen; auf dem Zielsystem wird
nichts gebaut und nichts kompiliert.

## Berechtigungen

Die App bekommt genau eine Sache ueber einen einfachen Container hinaus:
Schreibzugriff auf `/share`.

Ausdruecklich **nicht** angefordert:

| | |
|---|---|
| Ports | keine |
| Ingress | nein |
| Host-Netzwerk | nein |
| Privilegierter Modus | nein |
| Docker-Socket | nein |
| Home-Assistant-`/config` | nein |
| Secrets | nein |
| Supervisor-API | nein |
| Home-Assistant-API | nein |

Ein AppArmor-Profil (`apparmor.txt`) erzwingt zusaetzlich, dass nur der
Austauschordner beschreibbar ist.

## Kommunikationsvertrag

Beide Seiten teilen sich einen Ordner und sonst nichts:

```text
/share/roadplanner-renderer/poc-v1/
├── renderer-status.json   # Heartbeat der App, alle 5 s
├── jobs/                  # Roadplanner legt Auftraege ab
├── processing/            # uebernommene Auftraege
├── status/                # Jobstatus
├── inputs/<job_id>/       # Renderpaket eines Tagesvideos
└── results/<job_id>/      # result.json + Artefakte
```

Drei Eigenschaften tragen den Entwurf:

- **Jede Datei wird atomar geschrieben** (temporaerer Name im selben
  Verzeichnis, danach `rename`). Der Leser sieht nie eine halbe Datei.
- **Ein Auftrag wird durch Verschieben uebernommen.** `rename` kann nur
  einer gewinnen, ein Auftrag kann also nicht doppelt laufen.
- **Terminal bleibt terminal.** `completed`/`failed`/`expired` werden nie
  wieder zu `running`. Eine spaete Statusmeldung eines neu gestarteten
  Prozesses kann einen fertigen Auftrag nicht wiederbeleben.

Dateinamen bestehen ausschliesslich aus einer serverseitig erzeugten UUID.
Kein Pfad und kein Dateiname stammt aus Nutzertext.

### Das Renderpaket eines Tagesvideos

Fuer die Aktion `render_trip_day` legt Roadplanner vor dem Auftrag ein
Paket in `inputs/<job_id>/` ab: eine `package.json` mit Datum, Titel,
Tageszusammenfassung, Stoppnamen und - sofern lokal vorhanden - Strecke und
Fahrzeit, dazu bis zu fuenf verkleinerte Fotokopien als `photo-1.jpg` bis
`photo-5.jpg`.

- **Der Auftrag wird zuletzt geschrieben.** Seine Existenz belegt damit ein
  vollstaendiges Paket.
- **Das Paket nennt keine Dateinamen.** Die Bilder sind nummeriert, und die
  App baut `photo-<n>.jpg` aus der Zahl - keine Zeichenkette aus dem Paket
  kommt je in die Naehe eines Pfades.
- **Jedes Bild wird gegen den angekuendigten SHA-256 geprueft**, bevor es
  benutzt wird, und ein Symlink wird abgelehnt statt verfolgt. Beides
  geschieht, bevor ein Browser startet.
- **Die Fotos tragen keine Aufnahmedaten.** Roadplanner codiert sie aus den
  Bilddaten neu; EXIF und damit auch die GPS-Position sind entfernt.
- **Das Paket ueberlebt seinen Auftrag nicht.** Die App loescht es nach
  jedem Job, ob er gelungen ist oder nicht.

## Der Austauschordner ist ein Vertrauenskanal, keine Sicherheitsgrenze

`/share` ist von Home Assistant und von jeder App beschreibbar, die
denselben Mount anfordert. Nichts dort ist authentifiziert: Die SHA-256 in
`result.json` liegt in derselben Datei wie das Artefakt, das sie
beschreibt. Wer eines faelschen kann, faelscht beides.

**Die Pruefsumme belegt, dass die Bytes unversehrt angekommen sind - nie,
dass sie vom Renderer stammen.**

Daraus folgt:

- Eine zurueckgelieferte Datei gilt als nicht vertrauenswuerdige Eingabe.
  Sie ist groessenbegrenzt, ihr Typ wird geprueft, und sie wird nie als
  Markup, Code oder Pfad interpretiert.
- **Fuer produktive Videos darf nur uebernommen werden, was ffprobe als das
  erwartete MP4 bestaetigt** - Container, Codec, Aufloesung und Dauer, aus
  den Bytes zurueckgelesen statt einem Manifest geglaubt.
- Der Kanal ist angemessen, weil beide Enden lokal sind und demselben
  Betreiber gehoeren. Ueber Maschinen- oder Vertrauensgrenzen hinweg waere
  er es nicht; das braeuchte Signaturen oder einen Transport mit Identitaet,
  nicht ein strengeres Schema.

## Grenzen

| | |
|---|---|
| Gleichzeitige Jobs | 1 |
| Renderdauer | 300 s |
| Gesamtdauer eines Jobs | 420 s |
| Ergebnisdatei | 64 MB |
| Ergebnisordner gesamt | 512 MB |
| Aufbewahrung Ergebnisse | 24 h |
| Aufbewahrung verwaister Renderpakete | 1 h |
| Bilder je Renderpaket | 5 |
| Bildgroesse | 400 kB |
| Renderpaket gesamt | 3 MB |
| Auftragsdatei | 64 kB |
| Freier Speicher vor dem Render | 512 MB |

Alle Werte sind ueber Umgebungsvariablen einstellbar, aber so gewaehlt, dass
sie im Normalbetrieb nie greifen und nur bei etwas tatsaechlich Falschem
ausloesen.

## Neustartverhalten

| Fall | Verhalten |
|---|---|
| App startet nach Home Assistant | Roadplanner zeigt zuerst „nicht erreichbar", erkennt die App nach dem ersten gueltigen Heartbeat automatisch |
| Home Assistant startet nach der App | Heartbeat und terminale Jobstatus werden von der Platte gelesen; nichts geht verloren |
| App-Neustart waehrend eines Jobs | Der unterbrochene Auftrag wird beim Start als `failed` mit Code `INTERRUPTED` markiert, nicht stillschweigend liegen gelassen |
| Home-Assistant-Neustart waehrend eines Jobs | Der Auftrag bleibt ueber seine Job-ID auffindbar; es entsteht kein zweiter Auftrag |
| Beschaedigte JSON-Datei | Wird als Protokollfehler gemeldet, die App stuerzt nicht ab |
| App gestoppt oder nicht installiert | Kein Fehler in Roadplanner, keine Startverzoegerung |

## Was diese App nicht tut

Kein Remotion, kein Chromium, kein React, keine TravelStoryManifest, keine
Kartenanimation, kein PDF, kein Videoexport, keine Cloud, keine Telemetrie,
keine Netzwerkzugriffe zur Laufzeit, keine Uebertragung von Reisedaten oder
Familienfotos.
