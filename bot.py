import logging
from logging.handlers import RotatingFileHandler
import os
import json
import time
from pathlib import Path
from collections import Counter, defaultdict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot_runtime import (
    AttackHistoryStore,
    build_history_entry,
    current_local_date,
    filter_running_history_entries,
    refresh_map_snapshot,
)
from settlement_discord import (
    SettlementDiscordConfig,
    register_settlement_commands,
    run_settlement_rule_check,
)
from settlement_rules import (
    NameListStore,
    SettlementReportStore,
    TreasuryCoordinateStore,
)

from travian_discord_integration import (
    AttackParseError,
    build_player_link,
    build_village_link,
    explain_attack_parse_failure,
    format_guess,
    format_datetime_short,
    format_duration_hms,
    format_speed_guess,
    is_recent_standard_guess,
    parse_flexible_noted_time,
    get_previous_tp_guess,
    get_short_distance_launch_time,
    get_short_distance_speed_with_world_limit,
    load_map_payload,
    resolve_attack_message,
    split_attack_messages,
    translate,
)
from travian_kingdoms_api import build_summary
from travian_kingdoms_api import players_data
from travian_kingdoms_api import request_api_key


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
raw_command_only_channel_ids = os.getenv("WATCH_CHANNEL_IDS_ONLY_COMMANDS", "")
TRAVIAN_SERVER_URL = os.getenv("TRAVIAN_SERVER_URL", "").strip()
TRAVIAN_MAP_DATA_PATH = os.getenv("TRAVIAN_MAP_DATA_PATH", "travian-map-data.json")
TRAVIAN_MAP_DATA_YESTERDAY_PATH = os.getenv(
    "TRAVIAN_MAP_DATA_YESTERDAY_PATH",
    "travian-map-data-yesterday.json",
)
TRAVIAN_PRIVATE_API_KEY = os.getenv("TRAVIAN_PRIVATE_API_KEY", "").strip() or None
TRAVIAN_TOOL_EMAIL = os.getenv("TRAVIAN_TOOL_EMAIL", "").strip()
TRAVIAN_TOOL_NAME = os.getenv("TRAVIAN_TOOL_NAME", "").strip()
TRAVIAN_TOOL_URL = os.getenv("TRAVIAN_TOOL_URL", "").strip()
TRAVIAN_TOOL_PUBLIC = os.getenv("TRAVIAN_TOOL_PUBLIC", "false").strip().lower() == "true"
ATTACK_HISTORY_PATH = os.getenv("ATTACK_HISTORY_PATH", "attack-history.json")
KINGDOM_MEMBERS_PATH = os.getenv("KINGDOM_MEMBERS_PATH", "kingdom-members.json")
TREASURY_COORDINATES_PATH = os.getenv("TREASURY_COORDINATES_PATH", "treasury-coordinates.json")
SETTLEMENT_REPORTS_PATH = os.getenv("SETTLEMENT_REPORTS_PATH", "settlement-rule-reports.json")
BOT_LOCALE = os.getenv("BOT_LOCALE", "de").strip() or "de"
OUTPUT_CHANNEL_ID = int(os.getenv("OUTPUT_CHANNEL_ID", "0").strip() or "0")
SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID = int(
    os.getenv("SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID", "0").strip() or "0"
)
RULE_OUTPUT_CHANNEL_ID = int(
    os.getenv("RULE_OUTPUT_CHANNEL_ID", "").strip() or str(OUTPUT_CHANNEL_ID or 0)
)
SETTLEMENT_ANNOUNCEMENT_HISTORY_LIMIT = int(
    os.getenv("SETTLEMENT_ANNOUNCEMENT_HISTORY_LIMIT", "50").strip() or "50"
)
SETTLEMENT_ANNOUNCEMENT_HISTORY_LIMIT = max(
    1,
    min(SETTLEMENT_ANNOUNCEMENT_HISTORY_LIMIT, 50),
)
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "logs/bot.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "1048576"))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "6"))
UPDATE_TK_COOLDOWN_SECONDS = int(os.getenv("UPDATE_TK_COOLDOWN_SECONDS", "300"))

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Add it to your environment or .env file."
    )

if not TRAVIAN_SERVER_URL:
    raise RuntimeError(
        "TRAVIAN_SERVER_URL is missing. Add it to your environment or .env file."
    )

def parse_channel_ids(raw_value: str) -> set[int]:
    return {
        int(channel_id.strip())
        for channel_id in raw_value.split(",")
        if channel_id.strip()
    }


WATCH_CHANNEL_IDS = parse_channel_ids(raw_channel_ids)
WATCH_CHANNEL_IDS_ONLY_COMMANDS = parse_channel_ids(raw_command_only_channel_ids)

DM_REJECTION_TEXT = (
    "Ich nehme keine Direktnachrichten an. "
    "Bitte nutze einen konfigurierten Watch-Kanal auf dem Discord-Server."
)
WATCH_CHANNEL_REJECTION_TEXT = "Ich reagiere nur in den konfigurierten Bot-Kanaelen."


def build_meldeformat_text(
    *,
    include_edit_hint: bool = False,
    message_url: str | None = None,
    error_message: str | None = None,
) -> str:
    lines = []
    if include_edit_hint:
        lines.extend(
            [
                "Ich konnte deine Angriffsmeldung nicht lesen. Bitte bessere deine Nachricht über bearbeiten nach.",
                "",
            ]
        )
    if message_url:
        lines.extend(
            [
                f"Nachricht: {message_url}",
            ]
        )
    if error_message:
        lines.extend(
            [
                f"Fehler: {error_message}",
            ]
        )
    if message_url or error_message:
        lines.append("")

    lines.extend(
        [
            "Das Format für Angriffsmeldungen:",
            "```text",
            "<Dorfkoordinaten>",
            "<Angriffszeile>",
            "<Optionale weitere Angriffszeilen>",
            "```",
            "Beispiel:",
            "```text",
            "-15/7",
            "",
            "Belagerung von Leo aus Barnaba",
            "in 22:10:46 um 11:59:01",
            " Belagerung von Jon Aegon aus 1.Winterfel",
            "in 22:10:46 um 11:59:01",
            "```",
            "Koordinaten können auch mit `|` oder Leerzeichen getrennt sein, oder Kartenlinks sein.",
            "Das vorherige Meldeformat mit Dorfname statt Dorfkoordinaten wird auch weiterhin unterstützt, ist aber fehleranfälliger.",
            "Bei Dörfern, die jünger als einen Tag sind, müssen Koordinaten angegeben werden.",
        ]
    )
    return "\n".join(lines)


async def send_meldeformat_dm(
    user: discord.User | discord.Member,
    *,
    message_url: str | None = None,
    error_message: str | None = None,
) -> None:
    try:
        await user.send(
            build_meldeformat_text(
                include_edit_hint=True,
                message_url=message_url,
                error_message=error_message,
            )
        )
    except discord.Forbidden:
        logging.info("Konnte Meldeformat-DM nicht senden: DMs fuer %s geschlossen.", user)
    except discord.HTTPException:
        logging.exception("Konnte Meldeformat-DM an %s nicht senden.", user)


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


def is_missing_travian_key(private_api_key: str | None) -> bool:
    return not private_api_key or private_api_key.lower() == "replace_me"


def update_env_value(key: str, value: str, env_path: str = ".env") -> None:
    path = Path(env_path)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    updated = False
    new_lines = []

    for line in lines:
        if line.strip().startswith("#") or "=" not in line:
            new_lines.append(line)
            continue

        current_key, _ = line.split("=", 1)
        if current_key.strip().lstrip("\ufeff") == key:
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def request_and_store_travian_api_key() -> str | None:
    missing_fields = [
        name
        for name, value in [
            ("TRAVIAN_TOOL_EMAIL", TRAVIAN_TOOL_EMAIL),
            ("TRAVIAN_TOOL_NAME", TRAVIAN_TOOL_NAME),
            ("TRAVIAN_TOOL_URL", TRAVIAN_TOOL_URL),
        ]
        if not value or value.lower() == "replace_me"
    ]
    if missing_fields:
        logging.warning(
            "Travian API-Schluessel kann nicht automatisch angefordert werden. "
            "Fehlende Konfiguration: %s",
            ", ".join(missing_fields),
        )
        return None

    result = request_api_key(
        server_url=TRAVIAN_SERVER_URL,
        email=TRAVIAN_TOOL_EMAIL,
        site_name=TRAVIAN_TOOL_NAME,
        site_url=TRAVIAN_TOOL_URL,
        is_public=TRAVIAN_TOOL_PUBLIC,
    )
    private_api_key = str(result.get("response", {}).get("privateApiKey", "")).strip()
    if not private_api_key:
        logging.warning("Travian API-Schluessel-Anfrage lieferte keinen privateApiKey.")
        return None

    update_env_value("TRAVIAN_PRIVATE_API_KEY", private_api_key)
    logging.info("Neuer Travian API-Schluessel wurde angefordert und in .env gespeichert.")
    return private_api_key

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

_travian_map_payload: dict | None = None
_travian_map_mtime_ns: int | None = None
_last_maintenance_date: str | None = None
_pending_history_summary: list[str] = []
_startup_settlement_check_done = False
_update_tk_cooldown_until: dict[int, float] = {}
_update_tk_running_scopes: set[int] = set()
history_store = AttackHistoryStore(ATTACK_HISTORY_PATH)
kingdom_member_store = NameListStore(KINGDOM_MEMBERS_PATH)
treasury_store = TreasuryCoordinateStore(TREASURY_COORDINATES_PATH)
settlement_report_store = SettlementReportStore(SETTLEMENT_REPORTS_PATH)
for store in (kingdom_member_store, treasury_store, settlement_report_store):
    store.ensure_exists()

settlement_discord_config = SettlementDiscordConfig(
    server_url=TRAVIAN_SERVER_URL,
    settlement_announcement_channel_id=SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID,
    rule_output_channel_id=RULE_OUTPUT_CHANNEL_ID,
    announcement_history_limit=SETTLEMENT_ANNOUNCEMENT_HISTORY_LIMIT,
)
register_settlement_commands(
    bot,
    member_store=kingdom_member_store,
    treasury_store=treasury_store,
    map_payload_path=TRAVIAN_MAP_DATA_PATH,
)

HELP_LINES = [
    "Verfuegbare Befehle:",
    f"`{PREFIX}hilfe` oder `{PREFIX}help` - Zeigt diese Hilfe an",
    f"`{PREFIX}info` oder `{PREFIX}about` - Erklaert den Bot kurz",
    f"`{PREFIX}kanaele` oder `{PREFIX}channels` - Zeigt Watch- und Ausgabe-Kanaele",
    f"`{PREFIX}ping` - Zeigt die aktuelle Bot-Latenz",
    f"`{PREFIX}hallo` oder `{PREFIX}hello` - Kurzer Funktionstest",
    f"`{PREFIX}summary` - Fasst die gespeicherten Angriffe zusammen",
    f"`{PREFIX}summarylaufend` - Fasst aktuell noch laufende Angriffe zusammen",
    f"`{PREFIX}summarydorf` - Fasst die gespeicherten Angriffe nach Zieldorf zusammen",
    f"`{PREFIX}summarydorflaufend` - Fasst aktuell noch laufende Angriffe nach Zieldorf zusammen",
    f"`{PREFIX}angreiferliste` - Gibt Angreiferdoerfer tabellarisch zum Kopieren aus",
    f"`{PREFIX}verteidigerliste` - Gibt Zieldoerfer tabellarisch zum Kopieren aus",
    f"`{PREFIX}reset` - Leert die Angriffshistorie",
    f"`{PREFIX}updateTK` - Aktualisiert die Travian-Kartendaten manuell",
    f"`{PREFIX}meldeformat` - Zeigt das empfohlene Format fuer manuelle Meldungen",
    f"`{PREFIX}krmitglieder` - Zeigt die KR-Mitgliederliste fuer Siedelregeln",
    f"`{PREFIX}krmitglieder-setzen Name1; Name2` - Ueberschreibt die KR-Mitgliederliste",
    f"`{PREFIX}schatzkammern` - Zeigt die Schatzkammer-Koordinaten",
    f"`{PREFIX}schatzkammern-setzen 12|34; -5|8` - Ueberschreibt die Schatzkammer-Koordinaten",
    "`/melden` - Meldet einen Angriff strukturiert mit optionalem `seit`-Zeitpunkt",
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


def load_yesterday_map_snapshot_from_disk() -> dict | None:
    path = Path(TRAVIAN_MAP_DATA_YESTERDAY_PATH)
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


async def send_attack_embed_and_track(
    output_channel: discord.abc.Messageable,
    embed: discord.Embed,
    resolution: object,
    source_message_url: str,
) -> None:
    history_store.add(build_history_entry(resolution, source_message_url))
    sent_message = await output_channel.send(embed=embed)
    history_store.set_bot_message_url(
        resolution.attacker_village.village_id,
        resolution.defender_village.village_id,
        sent_message.jump_url,
    )


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


def build_defender_village_summary_chunks(
    entries: list[dict],
    title: str = "Zusammenfassung der Angriffshistorie nach Zieldorf:",
) -> list[str]:
    if not entries:
        return []

    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        target = f"{entry.get('defenderPlayer', '?')} / {entry.get('defenderVillage', '?')}"
        grouped[target].append(entry)

    lines = [title]
    for target, target_entries in sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), item[0].casefold()),
    ):
        lines.append(
            f"{target}: {len(target_entries)} Angriff(e), Gesamt-TP {build_tp_summary(target_entries)}"
        )
        for entry in target_entries:
            source = f"{entry.get('attackerPlayer', '?')} / {entry.get('attackerVillage', '?')}"
            link = entry.get("botMessageUrl") or entry.get("messageUrl") or "-"
            lines.append(f"- {source}: {link}")

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


def build_map_village_lookup(map_payload: dict) -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for player in players_data(map_payload):
        player_name = str(player.get("name", ""))
        for village in player.get("villages", []):
            village_id = str(village.get("villageId", ""))
            if not village_id:
                continue
            lookup[village_id] = {
                "player": player_name,
                "village": village.get("name", ""),
                "x": village.get("x", ""),
                "y": village.get("y", ""),
            }
    return lookup


def build_history_village_tsv_chunks(
    entries: list[dict],
    *,
    role: str,
    map_payload: dict,
) -> list[str]:
    if role == "attacker":
        village_key = "attackerVillage"
        player_key = "attackerPlayer"
        village_id_key = "attackerVillageId"
        x_key = "attackerX"
        y_key = "attackerY"
        header = "Dorf\tAngreifer\tX-Koordinate\tY-Koordinate"
    elif role == "defender":
        village_key = "defenderVillage"
        player_key = "defenderPlayer"
        village_id_key = "defenderVillageId"
        x_key = "defenderX"
        y_key = "defenderY"
        header = "Dorf\tVerteidiger\tX-Koordinate\tY-Koordinate"
    else:
        raise ValueError(f"Unknown village export role: {role}")

    village_lookup = build_map_village_lookup(map_payload)
    seen: set[str] = set()
    rows: list[tuple[str, str, str, str]] = []
    for entry in entries:
        village_id = str(entry.get(village_id_key, ""))
        map_village = village_lookup.get(village_id, {})
        village = _first_present(entry.get(village_key), map_village.get("village"))
        player = _first_present(entry.get(player_key), map_village.get("player"))
        x = _first_present(entry.get(x_key), map_village.get("x"))
        y = _first_present(entry.get(y_key), map_village.get("y"))
        unique_key = village_id or f"{player}\0{village}"
        if unique_key in seen:
            continue
        seen.add(unique_key)
        rows.append(
            (
                _format_tsv_cell(village),
                _format_tsv_cell(player),
                _format_tsv_cell(x),
                _format_tsv_cell(y),
            )
        )

    rows.sort(key=lambda row: (row[1].casefold(), row[0].casefold(), row[2], row[3]))
    return _chunk_tsv_lines([header, *["\t".join(row) for row in rows]])


def _first_present(*values: object) -> object:
    for value in values:
        if value is None:
            continue
        if str(value) == "":
            continue
        return value
    return ""


def _format_tsv_cell(value: object) -> str:
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _chunk_tsv_lines(lines: list[str], max_message_length: int = 1900) -> list[str]:
    if not lines:
        return []

    header = lines[0]
    chunks: list[str] = []
    current_lines = [header]
    for line in lines[1:]:
        candidate_lines = [*current_lines, line]
        candidate = "\n".join(candidate_lines)
        if len(candidate) > max_message_length and len(current_lines) > 1:
            chunks.append("\n".join(current_lines))
            current_lines = [header, line]
        else:
            current_lines = candidate_lines

    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks


def get_context_scope_id(ctx: commands.Context) -> int:
    if ctx.guild is not None:
        return ctx.guild.id
    return ctx.channel.id


def format_cooldown_remaining(seconds: float) -> str:
    total_seconds = max(1, int(seconds + 0.999))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes and seconds:
        return f"{minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def claim_update_tk_run(scope_id: int) -> str | None:
    if scope_id in _update_tk_running_scopes:
        return "Das Travian-Update laeuft bereits. Bitte warte kurz, bis es fertig ist."

    now = time.monotonic()
    cooldown_until = _update_tk_cooldown_until.get(scope_id, 0.0)
    remaining_seconds = cooldown_until - now
    if remaining_seconds > 0:
        return (
            "Das Travian-Update wurde gerade erst gestartet. "
            f"Bitte warte noch {format_cooldown_remaining(remaining_seconds)}."
        )

    _update_tk_running_scopes.add(scope_id)
    _update_tk_cooldown_until[scope_id] = now + UPDATE_TK_COOLDOWN_SECONDS
    return None


def release_update_tk_run(scope_id: int) -> None:
    _update_tk_running_scopes.discard(scope_id)


def prepare_history_reset_summary(title: str) -> list[str]:
    entries = history_store.list_entries()
    if len(entries) <= 10:
        return []
    return build_history_summary_chunks(entries, title=title)


def run_startup_maintenance() -> None:
    global _pending_history_summary
    global TRAVIAN_PRIVATE_API_KEY
    logging.info("Startup maintenance started.")
    _pending_history_summary = prepare_history_reset_summary(
        "Automatische Zusammenfassung vor dem Zuruecksetzen beim Start:"
    )
    history_store.clear()
    if is_missing_travian_key(TRAVIAN_PRIVATE_API_KEY):
        logging.info("Travian API-Schluessel fehlt oder ist ein Platzhalter, fordere neuen Schluessel an.")
        TRAVIAN_PRIVATE_API_KEY = request_and_store_travian_api_key()

    try:
        refreshed = refresh_map_snapshot(
            server_url=TRAVIAN_SERVER_URL,
            private_api_key=TRAVIAN_PRIVATE_API_KEY,
            output_path=TRAVIAN_MAP_DATA_PATH,
            yesterday_path=TRAVIAN_MAP_DATA_YESTERDAY_PATH,
        )
    except Exception:
        logging.exception(
            "Kartendaten konnten beim Start nicht aktualisiert werden. "
            "Versuche einen neuen Travian API-Schluessel anzufordern."
        )
        try:
            TRAVIAN_PRIVATE_API_KEY = request_and_store_travian_api_key()
            refreshed = refresh_map_snapshot(
                server_url=TRAVIAN_SERVER_URL,
                private_api_key=TRAVIAN_PRIVATE_API_KEY,
                output_path=TRAVIAN_MAP_DATA_PATH,
                yesterday_path=TRAVIAN_MAP_DATA_YESTERDAY_PATH,
            )
        except Exception:
            logging.exception("Kartendaten konnten auch mit neuem Travian API-Schluessel nicht aktualisiert werden.")
        else:
            if refreshed:
                invalidate_travian_map_cache()
                logging.info("Travian map data refreshed during startup after renewing API key.")
    else:
        if refreshed:
            invalidate_travian_map_cache()
            logging.info("Travian map data refreshed during startup.")
    logging.info("Startup maintenance finished.")


def run_daily_maintenance() -> tuple[dict | None, dict | None, bool]:
    global _pending_history_summary
    logging.info("Daily maintenance started.")
    previous_payload = load_map_snapshot_from_disk()
    previous_signature = snapshot_signature(previous_payload)
    _pending_history_summary = prepare_history_reset_summary(
        "Automatische Zusammenfassung vor dem naechtlichen Zuruecksetzen:"
    )
    history_store.clear()
    try:
        refreshed = refresh_map_snapshot(
            server_url=TRAVIAN_SERVER_URL,
            private_api_key=TRAVIAN_PRIVATE_API_KEY,
            output_path=TRAVIAN_MAP_DATA_PATH,
            yesterday_path=TRAVIAN_MAP_DATA_YESTERDAY_PATH,
        )
    except Exception:
        logging.exception("Kartendaten konnten in der Nachtwartung nicht aktualisiert werden.")
        logging.info("Daily maintenance finished.")
        return previous_payload, None, False
    else:
        if refreshed:
            invalidate_travian_map_cache()
            logging.info("Travian map data refreshed during daily maintenance.")
            new_payload = load_map_snapshot_from_disk()
            new_signature = snapshot_signature(new_payload)
            logging.info("Daily maintenance finished.")
            return previous_payload, new_payload, previous_signature != new_signature
    logging.info("Daily maintenance finished.")
    return previous_payload, None, False


def is_watch_channel_id(channel_id: int | None) -> bool:
    return channel_id is not None and channel_id in WATCH_CHANNEL_IDS


def is_command_channel_id(channel_id: int | None) -> bool:
    return channel_id is not None and channel_id in WATCH_CHANNEL_IDS_ONLY_COMMANDS


def is_bot_channel_id(channel_id: int | None) -> bool:
    return is_watch_channel_id(channel_id) or is_command_channel_id(channel_id)


def is_settlement_announcement_channel_id(channel_id: int | None) -> bool:
    return (
        channel_id is not None
        and SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID
        and channel_id == SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID
    )


def is_message_in_watch_channel(message: discord.Message) -> bool:
    return message.guild is not None and is_watch_channel_id(message.channel.id)


def is_message_in_bot_channel(message: discord.Message) -> bool:
    return message.guild is not None and is_bot_channel_id(message.channel.id)


def is_interaction_in_bot_channel(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and is_bot_channel_id(interaction.channel_id)


async def reject_interaction_outside_watch_channels(
    interaction: discord.Interaction,
) -> None:
    text = DM_REJECTION_TEXT if interaction.guild is None else WATCH_CHANNEL_REJECTION_TEXT
    ephemeral = interaction.guild is not None
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(text, ephemeral=ephemeral)
    except discord.HTTPException:
        logging.warning("Konnte Watch-Channel-Ablehnung fuer Interaction nicht senden.")


async def watch_channel_interaction_check(interaction: discord.Interaction) -> bool:
    if is_interaction_in_bot_channel(interaction):
        return True

    await reject_interaction_outside_watch_channels(interaction)
    return False


bot.tree.interaction_check = watch_channel_interaction_check


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


async def run_startup_settlement_rule_check() -> None:
    yesterday_path = Path(TRAVIAN_MAP_DATA_YESTERDAY_PATH)
    current_path = Path(TRAVIAN_MAP_DATA_PATH)
    if not yesterday_path.exists() or not current_path.exists():
        return

    try:
        previous_payload = load_map_payload(str(yesterday_path))
        new_payload = get_travian_map_payload()
    except Exception:
        logging.exception("Siedelregelpruefung aus Kartendateien konnte nicht vorbereitet werden.")
        return

    if snapshot_signature(previous_payload) == snapshot_signature(new_payload):
        return

    await run_settlement_rule_check(
        bot,
        previous_payload=previous_payload,
        new_payload=new_payload,
        member_store=kingdom_member_store,
        treasury_store=treasury_store,
        report_store=settlement_report_store,
        config=settlement_discord_config,
        yesterday_payload=previous_payload,
    )


async def process_attack_message(message: discord.Message) -> None:
    if (
        not is_message_in_watch_channel(message)
        or is_settlement_announcement_channel_id(message.channel.id)
        or message.author.bot
        or message.content.startswith(PREFIX)
    ):
        return

    try:
        output_channel = await get_output_channel(message.channel)
        rendered_messages: list[tuple[discord.Embed, object]] = []
        message_parts = split_attack_messages(message.content)
        if not message_parts:
            await send_meldeformat_dm(
                message.author,
                message_url=message.jump_url,
                error_message=explain_attack_parse_failure(message.content),
            )
            return

        resolved_count = 0
        for part in message_parts:
            rendered = build_attack_embed(message, part)
            if rendered is None:
                raise AttackParseError(explain_attack_parse_failure(part))
            resolved_count += 1
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
            rendered_messages.append((embed, resolution))
            logging.info(
                "Attack processed: %s/%s -> %s/%s",
                resolution.attacker.name,
                resolution.attacker_village.name,
                resolution.defender.name,
                resolution.defender_village.name,
            )
        if resolved_count == 0:
            await send_meldeformat_dm(
                message.author,
                message_url=message.jump_url,
                error_message="Alle erkannten Angriffe waren bereits bekannt.",
            )
            return
    except AttackParseError as exc:
        logging.info("Attack message could not be parsed: %s", exc.user_message)
        await send_meldeformat_dm(
            message.author,
            message_url=message.jump_url,
            error_message=exc.user_message,
        )
    except ValueError as exc:
        logging.info("Attack message could not be resolved: %s", exc)
        await send_meldeformat_dm(
            message.author,
            message_url=message.jump_url,
            error_message=f"Die Angriffsmeldung konnte nicht mit den Kartendaten abgeglichen werden: {exc}",
        )
    except Exception:
        logging.exception("Failed to resolve attack message.")
        await send_meldeformat_dm(
            message.author,
            message_url=message.jump_url,
            error_message="Beim Lesen der Angriffsmeldung ist ein unerwarteter Fehler passiert.",
        )
    else:
        for embed, resolution in rendered_messages:
            await send_attack_embed_and_track(
                output_channel=output_channel,
                embed=embed,
                resolution=resolution,
                source_message_url=message.jump_url,
            )


def build_attack_body_from_source(
    *,
    message_content: str,
    noted_time: object,
    message_url: str | None,
    noted_time_hint: str | None = None,
) -> tuple[str, object] | None:
    resolution = resolve_attack_message(
        map_payload=get_travian_map_payload(),
        message_content=message_content,
        noted_time=noted_time,
        noted_time_hint=noted_time_hint,
    )
    if resolution is None:
        return None

    attacker_ref = build_player_reference(TRAVIAN_SERVER_URL, resolution.attacker)
    defender_ref = build_player_reference(TRAVIAN_SERVER_URL, resolution.defender)
    attacker_village_ref = build_village_reference(TRAVIAN_SERVER_URL, resolution.attacker_village)
    defender_village_ref = build_village_reference(TRAVIAN_SERVER_URL, resolution.defender_village)

    ram_guess = resolution.guesses.get("ram")
    kata_guess = resolution.guesses.get("katapult")
    ram_previous = get_previous_tp_guess(ram_guess, resolution.arrival_time, resolution.noted_time)
    kata_previous = get_previous_tp_guess(kata_guess, resolution.arrival_time, resolution.noted_time)
    alternative_guess = resolution.alternative_guess
    distance_line = (
        f"{translate(BOT_LOCALE, 'distance')}: "
        f"`{resolution.distance:.3f} {translate(BOT_LOCALE, 'fields')}`"
    )
    if resolution.distance < 20:
        distance_line += f" {translate(BOT_LOCALE, 'tp_irrelevant')}"

    if resolution.distance < 20:
        short_speed = get_short_distance_speed_with_world_limit(
            map_payload=get_travian_map_payload(),
            distance=resolution.distance,
            noted_time=resolution.noted_time,
            arrival_time=resolution.arrival_time,
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
        speed_lines = []
        if is_recent_standard_guess(ram_guess):
            speed_lines.append(
                f"{translate(BOT_LOCALE, 'ram')}: "
                f"{format_guess(ram_guess, ram_previous, resolution.distance, BOT_LOCALE)}"
            )
        if is_recent_standard_guess(kata_guess):
            speed_lines.append(
                f"{translate(BOT_LOCALE, 'katapult')}: "
                f"{format_guess(kata_guess, kata_previous, resolution.distance, BOT_LOCALE)}"
            )
        if len(speed_lines) < 2 and alternative_guess is not None:
            formatted_alternative = format_speed_guess(alternative_guess, BOT_LOCALE)
            if formatted_alternative != "-":
                speed_lines.append(
                    f"{translate(BOT_LOCALE, 'speed')}: {formatted_alternative}"
                )
        speed_line = "\n".join(speed_lines) if speed_lines else f"{translate(BOT_LOCALE, 'speed')}: -"

    body_lines = [
        translate(
            BOT_LOCALE,
            "from_to",
            attacker_ref=attacker_ref,
            attacker_village_ref=attacker_village_ref,
            defender_ref=defender_ref,
            defender_village_ref=defender_village_ref,
        ),
    ]
    if message_url:
        body_lines.append(f"{translate(BOT_LOCALE, 'message_link')}: {message_url}")
    body_lines.extend(
        [
            f"{translate(BOT_LOCALE, 'noted_time')}: `{format_datetime_short(resolution.noted_time)}`",
            f"{translate(BOT_LOCALE, 'arrival_time')}: `{format_datetime_short(resolution.arrival_time)}`",
            f"{translate(BOT_LOCALE, 'remaining_time')}: `{format_duration_hms(resolution.noted_time, resolution.arrival_time)}`",
            distance_line,
            speed_line,
        ]
    )

    return "\n".join(body_lines), resolution


def build_player_reference(server_url: str, player: object) -> str:
    name = getattr(player, "name", "") or "Unbekannt"
    if not getattr(player, "player_id", ""):
        return name
    return f"[{name}]({build_player_link(server_url, player)})"


def build_village_reference(server_url: str, village: object) -> str:
    name = getattr(village, "name", "") or f"{getattr(village, 'x', '?')}/{getattr(village, 'y', '?')}"
    return f"[{name}]({build_village_link(server_url, village)})"


def get_message_noted_time(message: discord.Message) -> object:
    return (message.edited_at or message.created_at).astimezone()


def build_attack_body(message: discord.Message, message_content: str) -> tuple[str, object] | None:
    return build_attack_body_from_source(
        message_content=message_content,
        noted_time=get_message_noted_time(message),
        message_url=message.jump_url,
    )


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


def build_attack_embed_from_source(
    *,
    message_content: str,
    noted_time: object,
    message_url: str | None,
    noted_time_hint: str | None = None,
) -> tuple[discord.Embed, object] | None:
    rendered = build_attack_body_from_source(
        message_content=message_content,
        noted_time=noted_time,
        message_url=message_url,
        noted_time_hint=noted_time_hint,
    )
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
    global _startup_settlement_check_done
    logging.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "n/a")
    if not nightly_maintenance.is_running():
        nightly_maintenance.start()
    try:
        synced = await bot.tree.sync()
        logging.info("Synced %s global application commands.", len(synced))
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            guild_synced = await bot.tree.sync(guild=guild)
            logging.info(
                "Synced %s guild application commands for %s (%s).",
                len(guild_synced),
                guild.name,
                guild.id,
            )
    except Exception:
        logging.exception("Failed to sync application commands.")
    if _pending_history_summary:
        await send_history_summary_chunks(_pending_history_summary)
        _pending_history_summary = []
    if not _startup_settlement_check_done:
        await run_startup_settlement_rule_check()
        _startup_settlement_check_done = True


@bot.hybrid_command(name="hallo", aliases=["hello"], with_app_command=True)
async def hello(ctx: commands.Context) -> None:
    await ctx.send(f"Hallo {ctx.author.mention}, ich bin online und einsatzbereit.")


@bot.tree.command(name="hello", description="Kurzer Funktionstest")
async def slash_hello(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        f"Hallo {interaction.user.mention}, ich bin online und einsatzbereit."
    )


@bot.hybrid_command(name="ping", with_app_command=True)
async def ping(ctx: commands.Context) -> None:
    latency_ms = round(bot.latency * 1000)
    await ctx.send(f"Pong! Aktuelle Latenz: `{latency_ms} ms`.")


@bot.hybrid_command(name="info", aliases=["about"], with_app_command=True)
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


@bot.tree.command(name="about", description="Erklaert den Bot kurz")
async def slash_about(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "\n".join(
            [
                "Ich werte Travian Kingdoms Angriffsmeldungen in beobachteten Kanaelen aus.",
                "Dabei verlinke ich Spieler und Doerfer, berechne Restlaufzeiten und schaetze Startzeiten.",
                f"Mit `/{'hilfe'}` oder `/help` bekommst du eine kurze Uebersicht.",
            ]
        )
    )


def build_channel_link(guild_id: int | None, channel_id: int) -> str:
    if guild_id is None:
        return f"<#{channel_id}>"
    return f"https://discord.com/channels/{guild_id}/{channel_id}"


@bot.hybrid_command(name="kanaele", aliases=["channels"], with_app_command=True)
async def channels(ctx: commands.Context) -> None:
    guild_id = ctx.guild.id if ctx.guild else None
    watch_links = [
        build_channel_link(guild_id, channel_id)
        for channel_id in sorted(WATCH_CHANNEL_IDS)
    ]
    command_only_links = [
        build_channel_link(guild_id, channel_id)
        for channel_id in sorted(WATCH_CHANNEL_IDS_ONLY_COMMANDS)
    ]
    output_link = build_channel_link(guild_id, OUTPUT_CHANNEL_ID) if OUTPUT_CHANNEL_ID else "-"
    settlement_link = (
        build_channel_link(guild_id, SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID)
        if SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID
        else "-"
    )
    rule_output_link = build_channel_link(guild_id, RULE_OUTPUT_CHANNEL_ID) if RULE_OUTPUT_CHANNEL_ID else "-"

    lines = [
        "Aktuelle Kanal-Konfiguration:",
        "Bot-Zugriff: `nur konfigurierte Bot-Kanaele`",
        f"Watch-Kanaele fuer Meldungen: {', '.join(watch_links) if watch_links else '-'}",
        f"Nur-Befehle-Kanaele: {', '.join(command_only_links) if command_only_links else '-'}",
        f"Ausgabe-Kanal: {output_link}",
        f"Siedelkanal: {settlement_link}",
        f"Regel-Ausgabe-Kanal: {rule_output_link}",
    ]
    await ctx.send("\n".join(lines))


@bot.tree.command(name="channels", description="Zeigt Watch- und Ausgabe-Kanaele")
async def slash_channels(interaction: discord.Interaction) -> None:
    guild_id = interaction.guild.id if interaction.guild else None
    watch_links = [
        build_channel_link(guild_id, channel_id)
        for channel_id in sorted(WATCH_CHANNEL_IDS)
    ]
    command_only_links = [
        build_channel_link(guild_id, channel_id)
        for channel_id in sorted(WATCH_CHANNEL_IDS_ONLY_COMMANDS)
    ]
    output_link = build_channel_link(guild_id, OUTPUT_CHANNEL_ID) if OUTPUT_CHANNEL_ID else "-"
    settlement_link = (
        build_channel_link(guild_id, SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID)
        if SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID
        else "-"
    )
    rule_output_link = build_channel_link(guild_id, RULE_OUTPUT_CHANNEL_ID) if RULE_OUTPUT_CHANNEL_ID else "-"
    lines = [
        "Aktuelle Kanal-Konfiguration:",
        "Bot-Zugriff: `nur konfigurierte Bot-Kanaele`",
        f"Watch-Kanaele fuer Meldungen: {', '.join(watch_links) if watch_links else '-'}",
        f"Nur-Befehle-Kanaele: {', '.join(command_only_links) if command_only_links else '-'}",
        f"Ausgabe-Kanal: {output_link}",
        f"Siedelkanal: {settlement_link}",
        f"Regel-Ausgabe-Kanal: {rule_output_link}",
    ]
    await interaction.response.send_message("\n".join(lines))


@bot.hybrid_command(name="hilfe", aliases=["help"], with_app_command=True)
async def help_command(ctx: commands.Context) -> None:
    await ctx.send("\n".join(HELP_LINES))


@bot.tree.command(name="help", description="Zeigt diese Hilfe an")
async def slash_help(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("\n".join(HELP_LINES))


@bot.hybrid_command(name="reset", with_app_command=True)
async def reset_history(ctx: commands.Context) -> None:
    history_store.clear()
    logging.info("Attack history manually reset by %s.", ctx.author)
    await ctx.send("Die Angriffshistorie wurde geleert.")


@bot.hybrid_command(name="summary", with_app_command=True)
async def summary(ctx: commands.Context) -> None:
    entries = history_store.list_entries()
    if not entries:
        await ctx.send("Die Angriffshistorie ist aktuell leer.")
        return
    for chunk in build_history_summary_chunks(entries):
        await ctx.send(chunk)


@bot.hybrid_command(
    name="summarylaufend",
    aliases=["summaryaktiv", "summarylive", "summaryrunning", "summary-laufend"],
    with_app_command=True,
)
async def running_summary(ctx: commands.Context) -> None:
    entries = filter_running_history_entries(history_store.list_entries())
    if not entries:
        await ctx.send("Es laufen aktuell keine gespeicherten Angriffe.")
        return
    for chunk in build_history_summary_chunks(
        entries,
        title="Zusammenfassung der aktuell laufenden Angriffe:",
    ):
        await ctx.send(chunk)


@bot.hybrid_command(
    name="summarydorf",
    aliases=["summary-dorf", "summaryziel", "summarydef", "summaryd"],
    with_app_command=True,
)
async def summary_by_defender_village(ctx: commands.Context) -> None:
    entries = history_store.list_entries()
    if not entries:
        await ctx.send("Die Angriffshistorie ist aktuell leer.")
        return
    for chunk in build_defender_village_summary_chunks(entries):
        await ctx.send(chunk)


@bot.hybrid_command(
    name="summarydorflaufend",
    aliases=[
        "summaryziellaufend",
        "summarydeflaufend",
        "summarydlaufend",
        "summary-dorf-laufend",
    ],
    with_app_command=True,
)
async def running_summary_by_defender_village(ctx: commands.Context) -> None:
    entries = filter_running_history_entries(history_store.list_entries())
    if not entries:
        await ctx.send("Es laufen aktuell keine gespeicherten Angriffe.")
        return
    for chunk in build_defender_village_summary_chunks(
        entries,
        title="Zusammenfassung der aktuell laufenden Angriffe nach Zieldorf:",
    ):
        await ctx.send(chunk)


@bot.hybrid_command(
    name="angreiferliste",
    aliases=["angreifer", "attackerliste", "attackers"],
    with_app_command=True,
)
async def attacker_export(ctx: commands.Context) -> None:
    entries = history_store.list_entries()
    if not entries:
        await ctx.send("Die Angriffshistorie ist aktuell leer.")
        return
    for chunk in build_history_village_tsv_chunks(
        entries,
        role="attacker",
        map_payload=get_travian_map_payload(),
    ):
        await ctx.send(f"```tsv\n{chunk}\n```")


@bot.hybrid_command(
    name="verteidigerliste",
    aliases=["verteidiger", "defenderliste", "defenders"],
    with_app_command=True,
)
async def defender_export(ctx: commands.Context) -> None:
    entries = history_store.list_entries()
    if not entries:
        await ctx.send("Die Angriffshistorie ist aktuell leer.")
        return
    for chunk in build_history_village_tsv_chunks(
        entries,
        role="defender",
        map_payload=get_travian_map_payload(),
    ):
        await ctx.send(f"```tsv\n{chunk}\n```")


@bot.hybrid_command(name="updatetk", aliases=["updateTK"], with_app_command=True)
async def update_tk(ctx: commands.Context) -> None:
    scope_id = get_context_scope_id(ctx)
    cooldown_message = claim_update_tk_run(scope_id)
    if cooldown_message is not None:
        await ctx.send(cooldown_message)
        return

    try:
        previous_payload = load_map_snapshot_from_disk()
        previous_signature = snapshot_signature(previous_payload)

        try:
            refreshed = refresh_map_snapshot(
                server_url=TRAVIAN_SERVER_URL,
                private_api_key=TRAVIAN_PRIVATE_API_KEY,
                output_path=TRAVIAN_MAP_DATA_PATH,
                yesterday_path=TRAVIAN_MAP_DATA_YESTERDAY_PATH,
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
        if previous_payload is not None:
            await run_settlement_rule_check(
                bot,
                previous_payload=previous_payload,
                new_payload=new_payload,
                member_store=kingdom_member_store,
                treasury_store=treasury_store,
                report_store=settlement_report_store,
                config=settlement_discord_config,
                fallback_channel=ctx.channel,
                yesterday_payload=load_yesterday_map_snapshot_from_disk(),
            )
    finally:
        release_update_tk_run(scope_id)


@bot.hybrid_command(name="meldeformat", with_app_command=True)
async def meldeformat(ctx: commands.Context) -> None:
    await ctx.send(build_meldeformat_text())


@app_commands.describe(
    attack_string="Zielkoordinaten plus eine oder mehrere Angriffszeilen",
    seit="Optional: seit wann der Angriff sichtbar ist, z. B. 20:34 oder 2026-04-30 20:34",
)
@bot.tree.command(name="melden", description="Meldet einen Angriff strukturiert")
async def slash_melden(
    interaction: discord.Interaction,
    attack_string: str,
    seit: str | None = None,
) -> None:
    reference_time = discord.utils.utcnow().astimezone()
    if seit and parse_flexible_noted_time(reference_time, seit) is None:
        await interaction.response.send_message(
            "`seit` konnte ich nicht lesen. Beispiele: `20:34`, `20:34:12`, `2026-04-30 20:34`.",
            ephemeral=True,
        )
        return

    if interaction.channel is not None:
        output_channel = await get_output_channel(interaction.channel)
    elif OUTPUT_CHANNEL_ID:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if output_channel is None:
            output_channel = await bot.fetch_channel(OUTPUT_CHANNEL_ID)
    else:
        await interaction.response.send_message(
            "Ich habe gerade keinen erreichbaren Ausgabekanal fuer diese Meldung.",
            ephemeral=True,
        )
        return

    rendered_messages: list[tuple[discord.Embed, object]] = []
    parsed_count = 0
    duplicate_count = 0
    message_parts = split_attack_messages(attack_string)
    if not message_parts:
        await interaction.response.send_message(
            f"Ich konnte aus `attack_string` keine gueltige Angriffsmeldung lesen. Fehler: {explain_attack_parse_failure(attack_string)}",
            ephemeral=True,
        )
        return

    try:
        for part in message_parts:
            rendered = build_attack_embed_from_source(
                message_content=part,
                noted_time=reference_time,
                message_url=None,
                noted_time_hint=seit,
            )
            if rendered is None:
                raise AttackParseError(explain_attack_parse_failure(part))
            parsed_count += 1
            embed, resolution = rendered
            if history_store.contains(
                resolution.attacker_village.village_id,
                resolution.defender_village.village_id,
            ):
                duplicate_count += 1
                continue
            rendered_messages.append((embed, resolution))
    except AttackParseError as exc:
        await interaction.response.send_message(
            f"Ich konnte aus `attack_string` keine gueltige Angriffsmeldung lesen. Fehler: {exc.user_message}",
            ephemeral=True,
        )
        return
    except ValueError as exc:
        await interaction.response.send_message(
            f"Ich konnte diese Angriffsmeldung nicht mit den Kartendaten abgleichen. Fehler: {exc}",
            ephemeral=True,
        )
        return

    for embed, resolution in rendered_messages:
        await send_attack_embed_and_track(
            output_channel=output_channel,
            embed=embed,
            resolution=resolution,
            source_message_url="",
        )

    if rendered_messages:
        response_text = f"{len(rendered_messages)} Angriff(e) gemeldet."
        if duplicate_count:
            response_text += f" {duplicate_count} bekannte Dublette(n) wurden ignoriert."
    else:
        response_text = "Alle erkannten Angriffe waren bereits bekannt."
    await interaction.response.send_message(response_text, ephemeral=True)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    if message.guild is None:
        await message.channel.send(DM_REJECTION_TEXT)
        return

    if not is_message_in_bot_channel(message):
        return

    if is_message_in_watch_channel(message):
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
    should_run = now.hour == 0 and 30 <= now.minute < 35 and _last_maintenance_date != current_date
    if not should_run:
        return

    previous_payload, new_payload, map_changed = run_daily_maintenance()
    if _pending_history_summary:
        await send_history_summary_chunks(_pending_history_summary)
        _pending_history_summary = []
    if map_changed and previous_payload is not None and new_payload is not None:
        await run_settlement_rule_check(
            bot,
            previous_payload=previous_payload,
            new_payload=new_payload,
            member_store=kingdom_member_store,
            treasury_store=treasury_store,
            report_store=settlement_report_store,
            config=settlement_discord_config,
            yesterday_payload=load_yesterday_map_snapshot_from_disk(),
        )
    _last_maintenance_date = current_local_date()
    logging.info("Taegliche Wartung ausgefuehrt: Historie geleert und Kartendaten aktualisiert.")


@nightly_maintenance.before_loop
async def before_nightly_maintenance() -> None:
    await bot.wait_until_ready()


def main() -> None:
    global _last_maintenance_date
    run_startup_maintenance()
    now = discord.utils.utcnow().astimezone()
    if now.hour == 0 and now.minute >= 30:
        _last_maintenance_date = current_local_date()
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
