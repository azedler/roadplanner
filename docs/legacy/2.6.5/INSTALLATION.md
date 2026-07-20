# Installation – Roadplanner 2.6.5

## Upgrade von 2.6.3

1. Vollständige Home-Assistant-Sicherung erstellen.
2. `Roadplanner_MCP_v2.6.5_hotfix_files.zip` nach `/config` kopieren.
3. Im Terminal ausführen:

```bash
cd /config
unzip -o Roadplanner_MCP_v2.6.5_hotfix_files.zip
```

4. Home Assistant vollständig neu starten.
5. Browser beziehungsweise Companion App vollständig schließen und neu öffnen.
6. Im Roadplanner prüfen, dass Version `2.6.5` angezeigt wird.

Alternativ kann das vollständige Paket installiert werden:

```bash
cd /config
unzip -o Roadplanner_MCP_v2.6.5_HA_install.zip
```

## OneDrive nach dem Upgrade

Die Microsoft-Anmeldung und gespeicherten Tokens bleiben erhalten.

Öffne:

```text
Roadplanner → Fotos → OneDrive einrichten
```

Prüfe einmalig:

```text
Kameraordner: Bilder/Handy_Upload_Iphone_Aron
Unterordner rekursiv: Ja
Puffer vor/nach Reise: 3 Tage
Maximale Einträge pro Lauf: 2000
Maximale Scanzeit: 12 Sekunden
Automatische Synchronisierung: nach Bedarf
```

Diese Fotoeinrichtung ist ab 2.6.5 die einzige maßgebliche OneDrive-
Konfiguration. Die allgemeinen Integrationsoptionen enthalten diese Felder nicht
mehr.

Da die Scanstrategie verbessert wurde, startet der nächste Sync einmalig einen
neuen selektiven Erstscan. Historische Jahresordner außerhalb des
Reisezeitraums werden auf Ordnerebene übersprungen. Danach verwendet
Roadplanner wieder nur den OneDrive-Delta-Cursor.

## Abnahme

1. `Jetzt synchronisieren` drücken.
2. Prüfen, dass die Anzeige relevante und übersprungene Ordner trennt.
3. Prüfen, dass kein doppelter Kameraordnerpfad angezeigt wird.
4. Bei einer Reise im Jahr 2026 dürfen alte Jahresordner wie 2019 oder 2020
   nicht als relevante Scanordner erscheinen.
5. Im Assistenten prüfen:
   - Eingabe oben;
   - neueste Nachricht oben;
   - Tagesbriefing sichtbar;
   - `Technik & Diagnose` unten und standardmäßig geschlossen.
6. Im Kostenbuch prüfen, dass nur die neuen elf Kategorien angeboten werden.

## Rollback

Vor dem Upgrade angelegte HA-Sicherung wiederherstellen oder den gesicherten
Integrationsordner zurückkopieren. Roadplanner 2.6.5 führt keine destruktive
Migration der Reise- oder Archivdaten durch.
