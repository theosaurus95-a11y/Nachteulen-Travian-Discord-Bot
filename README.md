# Travian Angriffsbot fuer Discord

Dieses Projekt enthaelt einen Discord-Bot auf Basis von [`discord.py`](https://discordpy.readthedocs.io/en/stable/) und eine eigenstaendige Travian-Kingdoms-API-Anbindung.

Der Bot ist fuer Angriffsmeldungen in Discord gedacht und kann aktuell:

- Travian-Angriffszeilen in Deutsch und Englisch erkennen
- Angreifer, Verteidiger und Dorfhinweise unscharf gegen die Kartendaten abgleichen
- Spieler- und Dorf-Links direkt in Discord ausgeben
- Restlaufzeit, Distanz, Startzeit und TP-Schaetzungen berechnen
- mehrere Angriffsmeldungen aus einer einzigen Discord-Nachricht getrennt beantworten
- Dorfpraefixe wie `02:` oder Sammelpraefixe ueber mehreren Angriffen uebernehmen
- Sonderformen wie `auf <Spieler> <Dorf>`, `auf <Spieler>\n<Dorf>`, `auf mich` und `auf mein <Dorf>` verarbeiten
- gemeldete Angriffe in einer Historien-Datei speichern und Dubletten derselben Dorf-gegen-Dorf-Kombination ignorieren
- die Historie sowie die Travian-Kartendaten beim Start und taeglich um 01:00 Uhr automatisch zuruecksetzen bzw. aktualisieren

## 1. Voraussetzungen

- Python 3.10 or newer
- A Discord account
- A Discord bot token

If `python --version` and `py --version` do not work on Windows, install Python first from the official downloads page:

- [Python Downloads](https://www.python.org/downloads/windows/)

## 2. Discord-Bot anlegen

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application**.
3. Give your application a name.
4. Open the **Bot** tab and click **Add Bot**.
5. In the bot settings, enable:
   - **Message Content Intent**
6. Click **Reset Token** or **Copy Token** and keep it private.
7. Open **OAuth2** > **URL Generator**.
8. Select:
   - Scope: `bot`
   - Bot permissions: `Send Messages`, `Read Message History`, `View Channels`
9. Open the generated URL in your browser and invite the bot to your server.

Ein fertiger Einladungslink ist bereits vorhanden:

[Bot einladen](https://discord.com/oauth2/authorize?client_id=1493213948357640262&permissions=68608&integration_type=0&scope=bot)

## 3. Lokales Setup

The easiest way is to use the setup script from this folder:

```powershell
.\setup.ps1
```

The script will:

- create `.venv`
- install Python dependencies
- create `.env`
- ask for your bot token if needed

You can still do it manually if you prefer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Danach `.env` oeffnen und mindestens diese Werte setzen:

```env
DISCORD_TOKEN=replace_me
TRAVIAN_SERVER_URL=https://de1x3.kingdoms.com
TRAVIAN_PRIVATE_API_KEY=replace_me
```

Optional kannst du ausserdem setzen:

```env
WATCH_CHANNEL_IDS=1493215975288471607
WATCH_ALL_CHANNELS=false
OUTPUT_CHANNEL_ID=1493215975288471608
TRAVIAN_MAP_DATA_PATH=travian-map-data.json
BOT_LOCALE=de
ATTACK_HISTORY_PATH=attack-history.json
```

Wenn der Bot in allen Kanaelen reagieren soll, kannst du stattdessen setzen:

```env
WATCH_ALL_CHANNELS=true
```

Wenn `WATCH_ALL_CHANNELS=false` ist, beobachtet der Bot nur die in `WATCH_CHANNEL_IDS` eingetragenen Kanaele:

```env
WATCH_CHANNEL_IDS=1493215975288471607
```

For multiple channels, separate them with commas:

```env
WATCH_CHANNEL_IDS=1493215975288471607,123456789012345678
```

Wenn Antworten in einen separaten Kanal geschrieben werden sollen, kannst du zusaetzlich setzen:

```env
OUTPUT_CHANNEL_ID=1493215975288471608
```

Dann liest der Bot weiterhin nur aus den beobachteten Kanaelen, schreibt seine automatischen Antworten aber in den angegebenen Zielkanal.

## 4. Bot starten

For normal day-to-day use:

```powershell
.\start-bot.ps1
```

That script activates the virtual environment and starts the bot.

You can also run it manually:

```powershell
.\.venv\Scripts\Activate.ps1
python bot.py
```

If everything is correct, the terminal will show that the bot logged in.

In Discord kannst du danach zum Beispiel testen:

```text
!hallo
!ping
!hilfe
```

Beispiel fuer eine erkannte Angriffsmeldung:

```text
02:
Angriff von Pilgerfuchs aus Fuchsbau in 01:35:38 um 20:00:54
```

Oder mit Zielueberschreibung:

```text
auf mich 02:
Angriff von Pilgerfuchs aus Fuchsbau in 01:35:38 um 20:00:54
```

Mehrere Angriffe in einer Nachricht sind ebenfalls moeglich; der Bot antwortet dann auf jede Angriffszeile getrennt.

## 5. Projektdateien

- `bot.py` - Discord-Bot und Befehle
- `travian_discord_integration.py` - Parsing, Aufloesung und Formatierung der Angriffsmeldungen
- `travian_kingdoms_api.py` - Travian-Kingdoms-API-Helfer
- `example_travian_usage.py` - einfache lokale Tests gegen die Kartendaten
- `setup.ps1` - Erstinstallation
- `start-bot.ps1` - Startskript fuer den Alltag

## 6. How hosting works

A Discord bot must stay online to respond to messages. That means you need an **always-running process** somewhere:

- Your own PC or home server
- A VPS
- A cloud platform that supports long-running worker services

### Raspberry Pi im Heimnetz

Wenn ein Raspberry Pi mit SSH erreichbar ist, kannst du ihn mit den beiden PowerShell-Skripten aus diesem Repository einrichten und deployen.

Lege lokal eine Datei `raspberry-pi-info.txt` an:

```text
Hostname: christoph-rpi
Benutzername: christoph
Passwort: dein-passwort
```

Erstinitialisierung auf dem Pi:

```powershell
.\initialize-raspberry-pi.ps1
```

Das Skript installiert Python-Pakete auf dem Pi, erstellt `/home/christoph/discord-bot`, legt eine `.venv` an und richtet einen `systemd`-Service namens `travian-discord-bot` ein.

Optional, aber empfohlen: SSH-Key-Login einrichten, damit `ssh` und `scp` nicht jedes Mal nach dem Pi-Passwort fragen:

```powershell
.\setup-raspberry-ssh-key.ps1
```

Danach verwenden `initialize-raspberry-pi.ps1` und `deploy-to-raspberry-pi.ps1` den Key automatisch, sofern er unter `%USERPROFILE%\.ssh\travian_bot_rpi` liegt.

Bei Code-Aenderungen deployen und den Bot auf dem Pi neustarten:

```powershell
.\deploy-to-raspberry-pi.ps1
```

Das Deploy-Skript kopiert die notwendigen Projektdateien inklusive `.env` und `travian-map-data.json`, installiert Python-Dependencies im Pi-venv, prueft die Python-Dateien und startet den Service neu.

Der Bot schreibt wichtige Ereignisse, Warnungen und Fehler in eine rotierende Logdatei. Standard:

```env
LOG_FILE_PATH=logs/bot.log
LOG_MAX_BYTES=1048576
LOG_BACKUP_COUNT=6
```

Damit bleiben maximal etwa 7 MB Bot-Logs erhalten. Das sollte bei normalem Betrieb eher 12-24 Stunden abdecken, ohne den kleinen Raspberry-Pi-Speicher unkontrolliert zu fuellen. Die aktuelle Logdatei vom Pi kannst du lokal abrufen mit:

```powershell
.\get-raspberry-log.ps1
```

Wenn du auch rotierte Dateien wie `bot.log.1` abholen willst:

```powershell
.\get-raspberry-log.ps1 -IncludeRotated
```

For a beginner, these are the simplest options:

### Option A: Run it on your own PC

Best for learning and testing.

Pros:

- simplest setup
- no hosting bill

Cons:

- bot goes offline when your PC is off
- not great for 24/7 uptime

### Option B: Railway

A good beginner-friendly cloud option for always-on apps.

1. Push this project to GitHub.
2. Create a Railway project.
3. Create a new service from your GitHub repo.
4. Add an environment variable:
   - `DISCORD_TOKEN` = your bot token
5. Railway will use the `Dockerfile` automatically.
6. Deploy the service.

The bot should stay online as a long-running service.

### Option C: Render

Use a **Background Worker**, not a web service.

1. Push this project to GitHub.
2. In Render, create a **Background Worker**.
3. Connect your repository.
4. Build command:

```text
pip install -r requirements.txt
```

5. Start command:

```text
python bot.py
```

6. Add environment variable:
   - `DISCORD_TOKEN` = your bot token
7. Deploy.

## 7. Security notes

- Never share your bot token.
- Never commit your real `.env` file.
- If your token leaks, reset it in the Discord Developer Portal.

## 8. Bot-Befehle

- `!hilfe` oder `!help` - zeigt die Kurzuebersicht
- `!info` oder `!about` - erklaert den Zweck des Bots
- `!ping` - zeigt die aktuelle Latenz
- `!hallo` oder `!hello` - einfacher Funktionstest
- `!summary` - fasst die Angriffshistorie nach Angreifern zusammen
- `!reset` - leert die Angriffshistorie sofort
- `!updateTK` - aktualisiert die Travian-Kartendaten manuell und meldet erkannte Aenderungen

## 9. Travian-Kingdoms-API-Helfer

Fuer das Aktualisieren der Kartendaten gibt es zusaetzlich das eigenstaendige API-Skript:

```powershell
python .\travian_kingdoms_api.py --help
```

### API-Schluessel anfordern

Travian Kingdoms external tools first need to request a `privateApiKey` and `publicSiteKey`:

```powershell
python .\travian_kingdoms_api.py register `
  --server-url https://com1.kingdoms.com `
  --email you@example.com `
  --site-name "Discord Bot Integration" `
  --site-url https://example.com
```

Das Skript gibt die JSON-Antwort von Travian aus. Den `privateApiKey` solltest du sicher ablegen und in `.env` eintragen.

### Kartendaten abrufen

Once you have a `privateApiKey`, you can pull the latest map snapshot and extract a summary:

```powershell
python .\travian_kingdoms_api.py get-map-data `
  --server-url https://com1.kingdoms.com `
  --private-api-key YOUR_PRIVATE_API_KEY `
  --raw-output .\travian-map.json `
  --summary-output .\travian-summary.json
```

Der Befehl schreibt eine Zusammenfassung ins Terminal und kann optional speichern:

- die komplette rohe JSON-Antwort
- eine kleinere Zusammenfassung mit Weltinfos, Zaehlern und Ranglisten

Falls die Welt historische Snapshots anbietet, kannst du auch ein Datum angeben:

```powershell
python .\travian_kingdoms_api.py get-map-data `
  --date 31.12.2020
```

Folgende Umgebungsvariablen werden unterstuetzt:

```env
TRAVIAN_SERVER_URL=https://com1.kingdoms.com
TRAVIAN_PRIVATE_API_KEY=replace_me
TRAVIAN_TOOL_EMAIL=you@example.com
TRAVIAN_TOOL_NAME=Discord Bot Integration
TRAVIAN_TOOL_URL=https://example.com
TRAVIAN_TOOL_PUBLIC=false
```

## Referenzen

- [discord.py Introduction](https://discordpy.readthedocs.io/en/stable/intro.html)
- [discord.py Quickstart](https://discordpy.readthedocs.io/en/v2.5.2/quickstart.html)
- [discord.py Creating a Bot Account](https://discordpy.readthedocs.io/en/latest/discord.html)
- [Travian Kingdoms API overview (Binary Tools Wiki)](https://wiki.binary-tools.de/wiki/Kingdoms_API/en)
- [Community JS wrapper showing request shapes](https://github.com/JaLe29/travian-kingdoms-api)
- [Railway Services](https://docs.railway.com/guides/services)
- [Railway Build & Deploy](https://docs.railway.com/build-deploy)
- [Render Service Types](https://render.com/docs/service-types)
- [Render Background Workers](https://render.com/docs/background-workers)
