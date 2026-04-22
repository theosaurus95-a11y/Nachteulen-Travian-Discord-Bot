import logging
from logging.handlers import RotatingFileHandler
import os
import json
from pathlib import Path
from collections import Counter, defaultdict

import discord
from discord.ext import commands, tasks

from bot_runtime import (
    AttackHistoryStore,
    build_history_entry,
    current_local_date,
    refresh_map_snapshot,
)

from travian_discord_integration import (
    build_player_link,
    build_village_link,
    format_guess,
    format_datetime_short,
    format_duration_hms,
    get_previous_tp_guess,
    get_short_distance_launch_time,
    get_short_distance_speed,
    load_map_payload,
    resolve_attack_message,
    split_attack_messages,
    translate,
)
from travian_kingdoms_api import build_summary


def load_dotenv(env_path: str = ".env") -> None:
    """Load simple KEY=VALUE pairs from a local .env file if it exists."""
    path = Path(env_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip("'\"")

        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("COMMAND_PREFIX", "!")
raw_channel_ids = os.getenv("WATCH_CHANNEL_IDS", "")
TRAVIAN_SERVER_URL = os.getenv("TRAVIAN_SERVER_URL", "").strip()
TRAVIAN_MAP_DATA_PATH = os.getenv("TRAVIAN_MAP_DATA_PATH", "travian-map-data.json")
TRAVIAN_PRIVATE_API_KEY = os.getenv("TRAVIAN_PRIVATE_API_KEY", "").strip() or None
ATTACK_HISTORY_PATH = os.getenv("ATTACK_HISTORY_PATH", "attack-history.json")
BOT_LOCALE = os.getenv("BOT_LOCALE", "de").strip() or "de"
WATCH_ALL_CHANNELS = os.getenv("WATCH_ALL_CHANNELS", "false").strip().lower() == "true"
OUTPUT_CHANNEL_ID = int(os.getenv("OUTPUT_CHANNEL_ID", "0").strip() or "0")
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "logs/bot.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "1048576"))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "6"))

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Add it to your environment or .env file."
    )

if not TRAVIAN_SERVER_URL:
    raise RuntimeError(
        "TRAVIAN_SERVER_URL is missing. Add it to your environment or .env file."
    )

WATCH_CHANNEL_IDS = {
    int(channel_id.strip())
    for channel_id in raw_channel_ids.split(",")
    if channel_id.strip()
}


def configure_file_logging() -> None:
    log_path = Path(LOG_FILE_PATH)
    if log_path.parent != Path("."):
        log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    if any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", "") == str(log_path.resolve())
        for handler in root_logger.handlers
    ):
        return

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    root_logger.addHandler(file_handler)


configure_file_logging()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

_travian_map_payload: dict | None = None
_travian_map_mtime_ns: int | None = None
_last_maintenance_date: str | None = None
_pending_history_summary: list[str] = []
history_store = AttackHistoryStore(ATTACK_HISTORY_PATH)

HELP_LINES = [
    "Verfuegbare Befehle:",
    f"`{PREFIX}hilfe` oder `{PREFIX}help` - Zeigt diese Hilfe an",
    f"`{PREFIX}info` oder `{PREFIX}about` - Erklaert den Bot kurz",
    f"`{PREFIX}kanaele` oder `{PREFIX}channels` - Zeigt Watch- und Ausgabe-Kanaele",
    f"`{PREFIX}ping` - Zeigt die aktuelle Bot-Latenz",
    f"`{PREFIX}hallo` oder `{PREFIX}hello` - Kurzer Funktionstest",
    f"`{PREFIX}summary` - Fasst die gespeicherten Angriffe zusammen",
    f"`{PREFIX}reset` - Leert die Angriffshistorie",
    f"`{PREFIX}updateTK` - Aktualisiert die Travian-Kartendaten manuell",
    "",
    "Angriffsmeldungen in den beobachteten Kanaelen werden automatisch ausgewertet.",
]


def get_travian_map_payload() -> dict:
    global _travian_map_payload
    global _travian_map_mtime_ns

    path = Path(TRAVIAN_MAP_DATA_PATH)
    stat = path.stat()
    if _travian_map_payload is None or _travian_map_mtime_ns != stat.st_mtime_ns:
        _travian_map_payload = load_map_payload(str(path))
        _travian_map_mtime_ns = stat.st_mtime_ns
        logging.info("Loaded Travian map data from %s", path)
    return _travian_map_payload


def invalidate_travian_map_cache() -> None:
    global _travian_map_payload
    global _travian_map_mtime_ns
    _travian_map_payload = None
    _travian_map_mtime_ns = None


def load_map_snapshot_from_disk() -> dict | None:
    path = Path(TRAVIAN_MAP_DATA_PATH)
    if not path.exists():
        return None
    return load_map_payload(str(path))


def snapshot_signature(payload: dict | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def summarize_snapshot_change(previous_payload: dict | None, new_payload: dict) -> str:
    if previous_payload is None:
        summary = build_summary(new_payload)
        counts = summary.get("counts", {})
        return (
            "Kartendaten neu geladen. "
            f"Spieler: {counts.get('players', 0)}, Doerfer: {counts.get('villages', 0)}, "
            f"Bevoelkerung: {counts.get('totalPopulation', 0)}."
        )

    old_summary = build_summary(previous_payload)
    new_summary = build_summary(new_payload)
    old_counts = old_summary.get("counts", {})
    new_counts = new_summary.get("counts", {})

    changes = []
    for key, label in [
        ("players", "Spieler"),
        ("villages", "Doerfer"),
        ("totalPopulation", "Bevoelkerung"),
    ]:
        old_value = old_counts.get(key, 0)
        new_value = new_counts.get(key, 0)
        if old_value != new_value:
            diff = new_value - old_value
            changes.append(f"{label}: {old_value} -> {new_value} ({diff:+d})")

    old_top = (old_summary.get("topPlayers") or [{}])[0]
    new_top = (new_summary.get("topPlayers") or [{}])[0]
    if old_top.get("name") != new_top.get("name"):
        changes.append(
            f"Topspieler: {old_top.get('name', '-')} -> {new_top.get('name', '-')}"
        )

    return "Aenderungen: " + "; ".join(changes) if changes else "Keine inhaltlichen Aenderungen erkannt."


def build_tp_summary(entries: list[dict]) -> str:
    counters: dict[int, Counter[int]] = {3: Counter(), 4: Counter()}
    for entry in entries:
        for candidate in entry.get("candidates", []):
            tp_value = candidate.get("tp")
            speed = int(candidate.get("speed", 0) or 0)
            if tp_value is None or speed not in counters:
                continue
            counters[speed][int(tp_value)] += 1

    parts = []
    for speed in (3, 4):
        if counters[speed]:
            tp_level, count = counters[speed].most_common(1)[0]
            parts.append(f"S{speed}: TP {tp_level} ({count}x)")
        else:
            parts.append(f"S{speed}: -")
    return ", ".join(parts)


def build_history_summary_chunks(
    entries: list[dict],
    title: str = "Zusammenfassung der Angriffshistorie:",
) -> list[str]:
    if not entries:
        return []

    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        grouped[str(entry.get("attackerPlayer", "Unbekannt"))].append(entry)

    lines = [title]
    for attacker, attacker_entries in sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), item[0].casefold()),
    ):
        lines.append(
            f"{attacker}: {len(attacker_entries)} Angriff(e), Gesamt-TP {build_tp_summary(attacker_entries)}"
        )
        for entry in attacker_entries:
            target = f"{entry.get('defenderPlayer', '?')} / {entry.get('defenderVillage', '?')}"
            link = entry.get("botMessageUrl") or entry.get("messageUrl") or "-"
            lines.append(f"- {entry.get('attackerVillage', '?')} -> {target}: {link}")

    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}".strip() if current else line
        if len(candidate) > 1900 and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def prepare_history_reset_summary(title: str) -> list[str]:
    entries = history_store.list_entries()
    if len(entries) <= 10:
        return []
    return build_history_summary_chunks(entries, title=title)


def run_startup_maintenance() -> None:
    global _pending_history_summary
    logging.info("Startup maintenance started.")
    _pending_history_summary = prepare_history_reset_summary(
        "Automatische Zusammenfassung vor dem Zuruecksetzen beim Start:"
    )
    history_store.clear()
    try:
        refreshed = refresh_map_snapshot(
            server_url=TRAVIAN_SERVER_URL,
            private_api_key=TRAVIAN_PRIVATE_API_KEY,
            output_path=TRAVIAN_MAP_DATA_PATH,
        )
    except Exception:
        logging.exception("Kartendaten konnten beim Start nicht aktualisiert werden.")
    else:
        if refreshed:
            invalidate_travian_map_cache()
            logging.info("Travian map data refreshed during startup.")
    logging.info("Startup maintenance finished.")


def run_daily_maintenance() -> None:
    global _pending_history_summary
    logging.info("Daily maintenance started.")
    _pending_history_summary = prepare_history_reset_summary(
        "Automatische Zusammenfassung vor dem naechtlichen Zuruecksetzen:"
    )
    history_store.clear()
    try:
        refreshed = refresh_map_snapshot(
            server_url=TRAVIAN_SERVER_URL,
            private_api_key=TRAVIAN_PRIVATE_API_KEY,
            output_path=TRAVIAN_MAP_DATA_PATH,
        )
    except Exception:
        logging.exception("Kartendaten konnten in der Nachtwartung nicht aktualisiert werden.")
    else:
        if refreshed:
            invalidate_travian_map_cache()
            logging.info("Travian map data refreshed during daily maintenance.")
    logging.info("Daily maintenance finished.")


def get_defender_name_hint(message: discord.Message) -> str:
    parts = [
        getattr(message.author, "display_name", None),
        getattr(message.author, "global_name", None),
        getattr(message.author, "name", None),
    ]
    return " | ".join(part for part in parts if part)


async def get_output_channel(
    fallback_channel: discord.abc.Messageable,
) -> discord.abc.Messageable:
    if not OUTPUT_CHANNEL_ID:
        return fallback_channel

    channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    if channel is not None:
        return channel

    fetched_channel = await bot.fetch_channel(OUTPUT_CHANNEL_ID)
    return fetched_channel


async def send_history_summary_chunks(chunks: list[str], fallback_channel: discord.abc.Messageable | None = None) -> None:
    if not chunks:
        return
    if fallback_channel is not None:
        output_channel = await get_output_channel(fallback_channel)
    elif OUTPUT_CHANNEL_ID:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if output_channel is None:
            output_channel = await bot.fetch_channel(OUTPUT_CHANNEL_ID)
    else:
        logging.warning("Keine Ausgabe fuer automatische Historien-Zusammenfassung moeglich.")
        return
    for chunk in chunks:
        await output_channel.send(chunk)


async def process_attack_message(message: discord.Message) -> None:
    is_watched_channel = WATCH_ALL_CHANNELS or not WATCH_CHANNEL_IDS or message.channel.id in WATCH_CHANNEL_IDS
    if not is_watched_channel or message.author.bot:
        return

    try:
        output_channel = await get_output_channel(message.channel)
        rendered_messages: list[tuple[discord.Embed, object]] = []
        for part in split_attack_messages(message.content):
            rendered = build_attack_embed(message, part)
            if rendered is None:
                continue
            embed, resolution = rendered
            if history_store.contains(
                resolution.attacker_village.village_id,
                resolution.defender_village.village_id,
            ):
                logging.info(
                    "Duplicate attack ignored: %s/%s -> %s/%s",
                    resolution.attacker.name,
                    resolution.attacker_village.name,
                    resolution.defender.name,
                    resolution.defender_village.name,
                )
                continue
            history_store.add(build_history_entry(resolution, message.jump_url))
            rendered_messages.append((embed, resolution))
            logging.info(
                "Attack processed: %s/%s -> %s/%s",
                resolution.attacker.name,
                resolution.attacker_village.name,
                resolution.defender.name,
                resolution.defender_village.name,
            )
    except Exception:
        logging.exception("Failed to resolve attack message.")
        await message.channel.send(
            "Ich konnte diese Angriffsmeldung nicht sauber mit den Travian-Daten abgleichen."
        )
    else:
        for embed, resolution in rendered_messages:
            sent_message = await output_channel.send(embed=embed)
            history_store.set_bot_message_url(
                resolution.attacker_village.village_id,
                resolution.defender_village.village_id,
                sent_message.jump_url,
            )


def build_attack_body(message: discord.Message, message_content: str) -> tuple[str, object] | None:
    resolution = resolve_attack_message(
        map_payload=get_travian_map_payload(),
        message_content=message_content,
        noted_time=message.created_at.astimezone(),
        defender_name_hint=get_defender_name_hint(message),
    )
    if resolution is None:
        return None

    attacker_player_link = build_player_link(TRAVIAN_SERVER_URL, resolution.attacker)
    defender_player_link = build_player_link(TRAVIAN_SERVER_URL, resolution.defender)
    attacker_village_link = build_village_link(TRAVIAN_SERVER_URL, resolution.attacker_village)
    defender_village_link = build_village_link(TRAVIAN_SERVER_URL, resolution.defender_village)
    defender_village_name = resolution.defender_village.name
    if resolution.defender_used_main_village:
        defender_village_name += " (HD)"

    ram_guess = resolution.guesses.get("ram")
    kata_guess = resolution.guesses.get("katapult")
    ram_previous = get_previous_tp_guess(ram_guess, resolution.arrival_time, resolution.noted_time)
    kata_previous = get_previous_tp_guess(kata_guess, resolution.arrival_time, resolution.noted_time)
    distance_line = (
        f"{translate(BOT_LOCALE, 'distance')}: "
        f"`{resolution.distance:.3f} {translate(BOT_LOCALE, 'fields')}`"
    )
    if resolution.distance < 20:
        distance_line += f" {translate(BOT_LOCALE, 'tp_irrelevant')}"

    if resolution.distance < 20:
        short_speed = get_short_distance_speed(
            resolution.distance,
            resolution.noted_time,
            resolution.arrival_time,
        )
        if short_speed is None:
            speed_line = f"{translate(BOT_LOCALE, 'speed')}: -"
        else:
            short_launch = get_short_distance_launch_time(
                resolution.distance,
                resolution.arrival_time,
                short_speed,
            )
            speed_line = (
                f"{translate(BOT_LOCALE, 'speed')}: `{short_speed}`, "
                f"Start `{format_datetime_short(short_launch)}`"
            )
    else:
        speed_line = (
            f"{translate(BOT_LOCALE, 'ram')}: "
            f"{format_guess(ram_guess, ram_previous, resolution.distance, BOT_LOCALE)}\n"
            f"{translate(BOT_LOCALE, 'katapult')}: "
            f"{format_guess(kata_guess, kata_previous, resolution.distance, BOT_LOCALE)}"
        )

    body = "\n".join(
        [
            translate(
                BOT_LOCALE,
                "from_to",
                attacker=resolution.attacker.name,
                attacker_link=attacker_player_link,
                attacker_village=resolution.attacker_village.name,
                attacker_village_link=attacker_village_link,
                defender=resolution.defender.name,
                defender_link=defender_player_link,
                defender_village=defender_village_name,
                defender_village_link=defender_village_link,
            ),
            f"{translate(BOT_LOCALE, 'message_link')}: {message.jump_url}",
            f"{translate(BOT_LOCALE, 'noted_time')}: `{format_datetime_short(resolution.noted_time)}`",
            f"{translate(BOT_LOCALE, 'arrival_time')}: `{format_datetime_short(resolution.arrival_time)}`",
            f"{translate(BOT_LOCALE, 'remaining_time')}: `{format_duration_hms(resolution.noted_time, resolution.arrival_time)}`",
            distance_line,
            speed_line,
        ]
    )

    return body, resolution


def build_attack_embed(
    message: discord.Message,
    message_content: str,
) -> tuple[discord.Embed, object] | None:
    rendered = build_attack_body(message, message_content)
    if rendered is None:
        return None
    body, resolution = rendered

    embed = discord.Embed(
        title=translate(
            BOT_LOCALE,
            "attack_siege" if resolution.parsed.is_siege else "attack_normal",
        ),
        color=discord.Color.orange() if resolution.parsed.is_siege else discord.Color.red(),
    )
    embed.description = body

    return embed, resolution


@bot.event
async def on_ready() -> None:
    global _pending_history_summary
    logging.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "n/a")
    if not nightly_maintenance.is_running():
        nightly_maintenance.start()
    if _pending_history_summary:
        await send_history_summary_chunks(_pending_history_summary)
        _pending_history_summary = []


@bot.command(name="hello")
async def hello(ctx: commands.Context) -> None:
    await ctx.send(f"Hallo {ctx.author.mention}, ich bin online und einsatzbereit.")


@bot.command(name="hallo")
async def hallo(ctx: commands.Context) -> None:
    await hello(ctx)


@bot.command(name="ping")
async def ping(ctx: commands.Context) -> None:
    latency_ms = round(bot.latency * 1000)
    await ctx.send(f"Pong! Aktuelle Latenz: `{latency_ms} ms`.")


@bot.command(name="about")
async def about(ctx: commands.Context) -> None:
    await ctx.send(
        "\n".join(
            [
                "Ich werte Travian Kingdoms Angriffsmeldungen in beobachteten Kanaelen aus.",
                "Dabei verlinke ich Spieler und Doerfer, berechne Restlaufzeiten und schaetze Startzeiten.",
                f"Mit `{PREFIX}hilfe` bekommst du eine kurze Uebersicht.",
            ]
        )
    )


@bot.command(name="info")
async def info(ctx: commands.Context) -> None:
    await about(ctx)


def build_channel_link(guild_id: int | None, channel_id: int) -> str:
    if guild_id is None:
        return f"<#{channel_id}>"
    return f"https://discord.com/channels/{guild_id}/{channel_id}"


@bot.command(name="channels")
async def channels(ctx: commands.Context) -> None:
    guild_id = ctx.guild.id if ctx.guild else None
    watch_links = [
        build_channel_link(guild_id, channel_id)
        for channel_id in sorted(WATCH_CHANNEL_IDS)
    ]
    output_link = build_channel_link(guild_id, OUTPUT_CHANNEL_ID) if OUTPUT_CHANNEL_ID else "-"

    lines = [
        "Aktuelle Kanal-Konfiguration:",
        f"Watch-All-Channels: `{'true' if WATCH_ALL_CHANNELS else 'false'}`",
        f"Watch-Kanaele: {', '.join(watch_links) if watch_links else '-'}",
        f"Ausgabe-Kanal: {output_link}",
    ]
    await ctx.send("\n".join(lines))


@bot.command(name="kanaele")
async def kanaele(ctx: commands.Context) -> None:
    await channels(ctx)


@bot.command(name="help")
async def help_command(ctx: commands.Context) -> None:
    await ctx.send("\n".join(HELP_LINES))


@bot.command(name="hilfe")
async def hilfe(ctx: commands.Context) -> None:
    await help_command(ctx)


@bot.command(name="reset")
async def reset_history(ctx: commands.Context) -> None:
    history_store.clear()
    logging.info("Attack history manually reset by %s.", ctx.author)
    await ctx.send("Die Angriffshistorie wurde geleert.")


@bot.command(name="summary")
async def summary(ctx: commands.Context) -> None:
    entries = history_store.list_entries()
    if not entries:
        await ctx.send("Die Angriffshistorie ist aktuell leer.")
        return
    for chunk in build_history_summary_chunks(entries):
        await ctx.send(chunk)


@bot.command(name="updateTK")
async def update_tk(ctx: commands.Context) -> None:
    previous_payload = load_map_snapshot_from_disk()
    previous_signature = snapshot_signature(previous_payload)

    try:
        refreshed = refresh_map_snapshot(
            server_url=TRAVIAN_SERVER_URL,
            private_api_key=TRAVIAN_PRIVATE_API_KEY,
            output_path=TRAVIAN_MAP_DATA_PATH,
        )
    except Exception:
        logging.exception("Manuelles Travian-Update fehlgeschlagen.")
        await ctx.send("Das Travian-Update ist fehlgeschlagen.")
        return

    if not refreshed:
        await ctx.send("Das Travian-Update wurde uebersprungen, weil kein API-Schluessel gesetzt ist.")
        return

    invalidate_travian_map_cache()
    new_payload = get_travian_map_payload()
    new_signature = snapshot_signature(new_payload)

    if previous_signature == new_signature:
        logging.info("Manual Travian update by %s completed without detected changes.", ctx.author)
        await ctx.send("Die Travian-Kartendaten wurden aktualisiert, aber es wurden keine Aenderungen erkannt.")
        return

    logging.info("Manual Travian update by %s detected changes.", ctx.author)
    await ctx.send(
        "Die Travian-Kartendaten wurden aktualisiert.\n"
        + summarize_snapshot_change(previous_payload, new_payload)
    )


@bot.event
async def on_message(message: discord.Message) -> None:
    await process_attack_message(message)
    await bot.process_commands(message)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if before.content == after.content:
        return
    await process_attack_message(after)


@tasks.loop(minutes=1)
async def nightly_maintenance() -> None:
    global _last_maintenance_date
    global _pending_history_summary

    now = discord.utils.utcnow().astimezone()
    current_date = now.date().isoformat()
    should_run = now.hour == 1 and now.minute < 5 and _last_maintenance_date != current_date
    if not should_run:
        return

    run_daily_maintenance()
    if _pending_history_summary:
        await send_history_summary_chunks(_pending_history_summary)
        _pending_history_summary = []
    _last_maintenance_date = current_local_date()
    logging.info("Taegliche Wartung ausgefuehrt: Historie geleert und Kartendaten aktualisiert.")


@nightly_maintenance.before_loop
async def before_nightly_maintenance() -> None:
    await bot.wait_until_ready()


def main() -> None:
    global _last_maintenance_date
    run_startup_maintenance()
    now = discord.utils.utcnow().astimezone()
    if now.hour == 1:
        _last_maintenance_date = current_local_date()
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
