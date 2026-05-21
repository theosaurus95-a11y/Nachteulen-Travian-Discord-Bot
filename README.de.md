# Nachteulen Travian Discord Bot

[English](README.md) | Deutsch

Ein Discord-Bot für Travian-Kingdoms-Angriffsmeldungen. Der Bot liest Angriffsmeldungen aus konfigurierten Discord-Kanälen, gleicht Spieler und Dörfer mit den Travian-Kartendaten ab und antwortet mit Links, Laufzeiten, Distanzen und Startzeit-Schätzungen.

## Funktionen

- Erkennt deutsche und englische Travian-Angriffsmeldungen
- Gleicht Angreifer, Verteidiger und Dorfhinweise mit Kartendaten ab
- Antwortet mit Travian-Spieler- und Dorf-Links
- Berechnet Restlaufzeit, Distanz, Startzeit und Turnierplatz-Schätzungen
- Verarbeitet mehrere Angriffsmeldungen in einer Discord-Nachricht
- Unterstützt Dorfpräfixe wie `02:` und Zielüberschreibungen wie `auf mich`
- Speichert gemeldete Angriffe und ignoriert doppelte Dorf-gegen-Dorf-Meldungen
- Aktualisiert Historie und Travian-Kartendaten beim Start und taeglich um 01:00 Uhr

## Voraussetzungen

- Python 3.10 oder neuer
- Discord-Bot-Token
- Privater Travian-Kingdoms-API-Schlüssel

## Einrichtung

Setup-Skript ausführen:

```powershell
.\setup.ps1
```

Oder manuell einrichten:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Danach `.env` bearbeiten:

```env
DISCORD_TOKEN=replace_me
TRAVIAN_SERVER_URL=https://com1.kingdoms.com
TRAVIAN_PRIVATE_API_KEY=replace_me
```

Optionale Kanal- und Laufzeit-Einstellungen:

```env
COMMAND_PREFIX=!
WATCH_CHANNEL_IDS=1493215975288471607,1493215975288471608
WATCH_CHANNEL_IDS_ONLY_COMMANDS=1493215975288471609,1493215975288471610
OUTPUT_CHANNEL_ID=1493215975288471608
BOT_LOCALE=de
ATTACK_HISTORY_PATH=attack-history.json
LOG_FILE_PATH=logs/bot.log
UPDATE_TK_COOLDOWN_SECONDS=300
```

Der Bot nimmt keine Direktnachrichten an. Angriffsmeldungen werden nur in Kanaelen aus `WATCH_CHANNEL_IDS` ausgewertet; Befehle funktionieren in `WATCH_CHANNEL_IDS` und `WATCH_CHANNEL_IDS_ONLY_COMMANDS`.

## Starten

```powershell
.\start-bot.ps1
```

Oder manuell:

```powershell
.\.venv\Scripts\Activate.ps1
python bot.py
```

Beispielbefehle in Discord:

```text
!hallo
!ping
!hilfe
!summary
```

Beispiel für eine Angriffsmeldung:

```text
02:
Angriff von Pilgerfuchs aus Fuchsbau in 01:35:38 um 20:00:54
```

Mit Zielüberschreibung:

```text
auf mich 02:
Angriff von Pilgerfuchs aus Fuchsbau in 01:35:38 um 20:00:54
```

## Bot-Befehle

- `!hilfe` oder `!help` - zeigt die Befehlsübersicht
- `!info` oder `!about` - zeigt eine kurze Bot-Beschreibung
- `!kanaele` oder `!channels` - zeigt Watch- und Ausgabe-Kanäle
- `!ping` - zeigt die Latenz
- `!hallo` oder `!hello` - einfacher Funktionstest
- `!summary` - fasst gespeicherte Angriffe nach Angreifern zusammen
- `!reset` - leert die Angriffshistorie
- `!updateTK` - aktualisiert die Travian-Kartendaten

## Travian-API-Helfer

Das eigenständige API-Skript kann ein Travian-External-Tool registrieren und Kartendaten abrufen:

```powershell
python .\travian_kingdoms_api.py --help
```

API-Schlüssel anfordern:

```powershell
python .\travian_kingdoms_api.py register `
  --server-url https://com1.kingdoms.com `
  --email you@example.com `
  --site-name "Discord Bot Integration" `
  --site-url https://example.com
```

Kartendaten abrufen:

```powershell
python .\travian_kingdoms_api.py get-map-data `
  --server-url https://com1.kingdoms.com `
  --private-api-key YOUR_PRIVATE_API_KEY `
  --raw-output .\travian-map-data.json
```

## Projektdateien

- `bot.py` - Discord-Bot, Events und Befehle
- `bot_runtime.py` - Angriffshistorie und Laufzeit-Helfer
- `travian_discord_integration.py` - Parsing, Zuordnung und Discord-Formatierung
- `travian_kingdoms_api.py` - Travian-Kingdoms-API-Helfer
- `example_travian_usage.py` - lokale Beispiele mit Kartendaten
- `setup.ps1` - lokales Setup-Skript
- `start-bot.ps1` - lokales Start-Skript
- `Dockerfile` - Container-Startpunkt

## Referenzen

- [discord.py-Dokumentation](https://discordpy.readthedocs.io/en/stable/)
- [Travian-Kingdoms-API-Übersicht](https://wiki.binary-tools.de/wiki/Kingdoms_API/en)
