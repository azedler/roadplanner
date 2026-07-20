# Sicherheit und Datenschutz – Roadplanner 2.6.5

## OneDrive Personal

Roadplanner verwendet weiterhin ausschließlich delegierten lesenden Zugriff auf
das verbundene OneDrive-Konto. Es werden keine OneDrive-Dateien verschoben,
verändert oder gelöscht.

Beim selektiven Erstscan werden zunächst nur Metadaten gelesen:

- Datei- und Ordner-ID;
- Name und MIME-Typ;
- Aufnahme-, Erstellungs- und Änderungszeit;
- vorhandene GPS-Metadaten;
- Hash- und Größenangaben, soweit Microsoft sie liefert.

Originalbilder werden erst für eine konkrete Vorschau oder Anzeige abgerufen.
Historische Bilder außerhalb des Reisezeitfensters werden nicht in den
Roadplanner-Medienindex übernommen.

Die Microsoft-Anwendungs-ID ist kein Geheimnis. OAuth-Zugriffs- und
Aktualisierungstokens bleiben im privaten Home-Assistant-Speicher und werden
nicht im Panel, Roadbook, Export oder Diagnoseprotokoll ausgegeben.

## Einzige Konfigurationsquelle

OneDrive wird ab 2.6.5 ausschließlich unter
`Roadplanner → Fotos → OneDrive einrichten` konfiguriert. Alte Config-Entry-Werte
werden einmalig als Migrationsquelle verwendet, danach aber nicht mehr als
aktive Benutzereinstellung angezeigt.

Ordnerpfade werden normalisiert. Pfad-Traversal, Nullbytes und absolute
Dateisystempfade werden abgewiesen.

## Kostenmigration

Bestehende Ausgaben werden nicht gelöscht. Veraltete Kategorien werden beim
Laden auf die neue, kleinere Kategorieliste abgebildet. Es findet keine
ungeprüfte Währungsumrechnung statt.

## Assistent

Die geänderte Reihenfolge des Chats betrifft nur die Darstellung. Der
serverseitige Gesprächsverlauf, die Rechteprüfung, der Änderungskorb und die
Review-Pflicht bleiben unverändert. Diagnoseangaben bleiben standardmäßig
zugeklappt und enthalten keine API-Schlüssel oder vollständigen Prompts.
