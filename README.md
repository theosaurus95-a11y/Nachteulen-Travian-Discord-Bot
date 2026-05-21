# Nachteulen Travian Discord Bot

English | [Deutsch](README.de.md)

A Discord bot for Travian Kingdoms attack reports. It reads attack messages from configured Discord channels, matches players and villages against Travian map data, and replies with useful links, timing, distance, and launch estimates.

## Features

- Parses German and English Travian attack messages
- Matches attackers, defenders, and village hints against map data
- Replies with Travian player and village links
- Calculates remaining travel time, distance, launch time, and tournament square estimates
- Handles multiple attack lines in one Discord message
- Supports village prefixes such as `02:` and target overrides such as `auf mich`
- Stores reported attacks and ignores duplicate village-to-village reports
- Refreshes history and Travian map data on startup and daily at 01:00

## Requirements

- Python 3.10 or newer
- A Discord bot token
- A Travian Kingdoms private API key

## Setup

Run the setup script:

```powershell
.\setup.ps1
```

Or set up the environment manually:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Then edit `.env`:

```env
DISCORD_TOKEN=replace_me
TRAVIAN_SERVER_URL=https://com1.kingdoms.com
TRAVIAN_PRIVATE_API_KEY=replace_me
```

Optional channel and runtime settings:

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

The bot rejects direct messages. It parses attack reports only in channels listed in `WATCH_CHANNEL_IDS`, and accepts commands in both `WATCH_CHANNEL_IDS` and `WATCH_CHANNEL_IDS_ONLY_COMMANDS`.

## Run

```powershell
.\start-bot.ps1
```

Or manually:

```powershell
.\.venv\Scripts\Activate.ps1
python bot.py
```

Example Discord commands:

```text
!hallo
!ping
!hilfe
!summary
```

Example attack message:

```text
02:
Angriff von Pilgerfuchs aus Fuchsbau in 01:35:38 um 20:00:54
```

With a target override:

```text
auf mich 02:
Angriff von Pilgerfuchs aus Fuchsbau in 01:35:38 um 20:00:54
```

## Bot Commands

- `!hilfe` or `!help` - show command help
- `!info` or `!about` - show a short bot description
- `!kanaele` or `!channels` - show watch and output channels
- `!ping` - show latency
- `!hallo` or `!hello` - simple smoke test
- `!summary` - summarize stored attacks by attacker
- `!reset` - clear the attack history
- `!updateTK` - refresh Travian map data

## Travian API Helper

The standalone API helper can register a Travian external tool and fetch map data:

```powershell
python .\travian_kingdoms_api.py --help
```

Request API keys:

```powershell
python .\travian_kingdoms_api.py register `
  --server-url https://com1.kingdoms.com `
  --email you@example.com `
  --site-name "Discord Bot Integration" `
  --site-url https://example.com
```

Fetch map data:

```powershell
python .\travian_kingdoms_api.py get-map-data `
  --server-url https://com1.kingdoms.com `
  --private-api-key YOUR_PRIVATE_API_KEY `
  --raw-output .\travian-map-data.json
```

## Project Files

- `bot.py` - Discord bot, event handling, and commands
- `bot_runtime.py` - attack history and runtime helpers
- `travian_discord_integration.py` - parsing, matching, and Discord formatting
- `travian_kingdoms_api.py` - Travian Kingdoms API helper
- `example_travian_usage.py` - local examples against map data
- `setup.ps1` - local setup script
- `start-bot.ps1` - local start script
- `Dockerfile` - container entry point

## References

- [discord.py documentation](https://discordpy.readthedocs.io/en/stable/)
- [Travian Kingdoms API overview](https://wiki.binary-tools.de/wiki/Kingdoms_API/en)
