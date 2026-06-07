import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import discord
from discord.ext import commands

from settlement_rules import (
    NOT_ANNOUNCED,
    OUTSIDE_KINGDOM_NORMAL_FIELD,
    Coordinate,
    NameListStore,
    SettlementReportStore,
    SettlementViolation,
    TreasuryCoordinateStore,
    evaluate_settlement_violations,
    filter_settlements_absent_from_payload,
    find_new_member_settlements,
    map_last_update_datetime,
)
from settlement_ocr import extract_coordinates_from_image_bytes
from travian_discord_integration import extract_all_coordinates, load_map_payload
from travian_kingdoms_api import build_village_map_link, players_data


MAX_SETTLEMENT_ANNOUNCEMENT_MESSAGES = 50
MAX_OCR_ATTACHMENT_BYTES = 8 * 1024 * 1024
IMAGE_ATTACHMENT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


@dataclass(frozen=True)
class SettlementDiscordConfig:
    server_url: str
    settlement_announcement_channel_id: int
    rule_output_channel_id: int
    announcement_history_limit: int = MAX_SETTLEMENT_ANNOUNCEMENT_MESSAGES


def register_settlement_commands(
    bot: commands.Bot,
    *,
    member_store: NameListStore,
    treasury_store: TreasuryCoordinateStore,
    map_payload_path: str,
) -> None:
    @bot.hybrid_command(
        name="krmitglieder",
        aliases=["mitglieder", "members"],
        with_app_command=True,
    )
    async def kingdom_members(ctx: commands.Context) -> None:
        await _send_chunks(ctx, _format_member_list(member_store.list()))

    @bot.hybrid_command(
        name="krmitglieder-setzen",
        aliases=["krmitglieder_setzen", "setkrmitglieder", "setmembers"],
        with_app_command=True,
    )
    async def replace_kingdom_members(ctx: commands.Context, *, namen: str = "") -> None:
        names = parse_member_names(namen)
        if names:
            try:
                names, unknown_names = validate_member_names(names, map_payload_path)
            except (OSError, ValueError) as exc:
                await ctx.send(f"Ich konnte die Travian-Kartendaten nicht lesen: {exc}")
                return
            if unknown_names:
                await ctx.send(
                    "Diese Spieler stehen nicht in den aktuellen Kartendaten: "
                    + "; ".join(unknown_names)
                )
                return
        member_store.replace(names)
        await ctx.send(f"KR-Mitgliederliste aktualisiert: {len(names)} Name(n).")

    @bot.hybrid_command(
        name="schatzkammern",
        aliases=["treasuries"],
        with_app_command=True,
    )
    async def treasuries(ctx: commands.Context) -> None:
        await _send_chunks(ctx, _format_treasury_list(treasury_store.list()))

    @bot.hybrid_command(
        name="schatzkammern-setzen",
        aliases=["schatzkammern_setzen", "setschatzkammern", "settreasuries"],
        with_app_command=True,
    )
    async def replace_treasuries(ctx: commands.Context, *, koordinaten: str = "") -> None:
        coordinates = parse_coordinate_list(koordinaten)
        if koordinaten.strip() and not coordinates:
            await ctx.send("Ich konnte in der neuen Schatzkammerliste keine Koordinaten erkennen.")
            return
        treasury_store.replace(coordinates)
        await ctx.send(f"Schatzkammerliste aktualisiert: {len(coordinates)} Koordinate(n).")


async def run_settlement_rule_check(
    bot: commands.Bot,
    *,
    previous_payload: dict,
    new_payload: dict,
    member_store: NameListStore,
    treasury_store: TreasuryCoordinateStore,
    report_store: SettlementReportStore,
    config: SettlementDiscordConfig,
    fallback_channel: discord.abc.Messageable | None = None,
    yesterday_payload: dict | None = None,
) -> list[SettlementViolation]:
    member_names = member_store.list()
    if not member_names:
        logging.info("Siedelregelpruefung uebersprungen: keine KR-Mitglieder konfiguriert.")
        return []

    settlements = find_new_member_settlements(previous_payload, new_payload, member_names)
    settlements = filter_settlements_absent_from_payload(settlements, yesterday_payload)
    if not settlements:
        logging.info("Siedelregelpruefung: keine neuen KR-Mitglied-Doerfer erkannt.")
        return []

    before = map_last_update_datetime(new_payload) or datetime.now(timezone.utc)
    announced_coordinates = await fetch_announced_coordinates(
        bot,
        channel_id=config.settlement_announcement_channel_id,
        before=before,
        limit=config.announcement_history_limit,
    )
    violations = evaluate_settlement_violations(
        settlements,
        announced_coordinates,
        treasury_store.list(),
    )
    unsent_violations = [
        violation for violation in violations if not report_store.contains(violation)
    ]
    if not unsent_violations:
        logging.info("Siedelregelpruefung: keine neuen Regelverstoesse zu melden.")
        return []

    output_channel = await _resolve_output_channel(
        bot,
        config.rule_output_channel_id,
        fallback_channel,
    )
    if output_channel is None:
        logging.warning("Siedelregelverstoesse gefunden, aber kein Regel-Ausgabe-Kanal ist erreichbar.")
        return []

    sent_violations: list[SettlementViolation] = []
    for violation in unsent_violations:
        try:
            await output_channel.send(format_violation_message(violation, config.server_url))
        except discord.HTTPException:
            logging.exception("Siedelregelmeldung konnte nicht gesendet werden.")
            continue
        report_store.add(violation)
        sent_violations.append(violation)

    return sent_violations


async def fetch_announced_coordinates(
    bot: commands.Bot,
    *,
    channel_id: int,
    before: datetime,
    limit: int,
) -> set[Coordinate] | None:
    if not channel_id:
        logging.warning("SETTLEMENT_ANNOUNCEMENT_CHANNEL_ID ist nicht gesetzt.")
        return None

    channel = await _fetch_channel(bot, channel_id)
    if channel is None or not hasattr(channel, "history"):
        logging.warning("Siedelkanal %s ist nicht erreichbar oder hat keine Historie.", channel_id)
        return None

    coordinates: set[Coordinate] = set()
    history_limit = max(1, min(limit, MAX_SETTLEMENT_ANNOUNCEMENT_MESSAGES))
    try:
        async for message in channel.history(limit=history_limit, before=before):
            if getattr(message.author, "bot", False):
                continue
            for x, y in extract_all_coordinates(message.content):
                coordinates.add(Coordinate(x, y))
            for x, y in await _extract_image_attachment_coordinates(message):
                coordinates.add(Coordinate(x, y))
    except discord.HTTPException:
        logging.exception("Siedelkanal-Historie konnte nicht gelesen werden.")
        return None

    return coordinates


async def _extract_image_attachment_coordinates(message: discord.Message) -> list[tuple[int, int]]:
    coordinates: list[tuple[int, int]] = []
    for attachment in getattr(message, "attachments", []):
        if not _is_image_attachment(attachment):
            continue
        attachment_size = int(getattr(attachment, "size", 0) or 0)
        if attachment_size > MAX_OCR_ATTACHMENT_BYTES:
            logging.info(
                "Siedel-Screenshot %s uebersprungen: Datei ist groesser als %s Bytes.",
                getattr(attachment, "filename", ""),
                MAX_OCR_ATTACHMENT_BYTES,
            )
            continue
        try:
            image_bytes = await attachment.read()
        except discord.HTTPException:
            logging.exception("Siedel-Screenshot konnte nicht von Discord geladen werden.")
            continue

        loop = asyncio.get_running_loop()
        try:
            image_coordinates = await loop.run_in_executor(
                None,
                extract_coordinates_from_image_bytes,
                image_bytes,
            )
        except Exception:
            logging.exception("Siedel-Screenshot konnte nicht per OCR ausgewertet werden.")
            continue
        coordinates.extend(image_coordinates)
    return coordinates


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = getattr(attachment, "content_type", "") or ""
    if content_type.casefold().startswith("image/"):
        return True
    filename = getattr(attachment, "filename", "") or ""
    return filename.casefold().endswith(IMAGE_ATTACHMENT_EXTENSIONS)


def parse_member_names(raw_value: str) -> list[str]:
    cleaned = _strip_code_fence(raw_value)
    parts = re.split(r"[\n;]+", cleaned)
    names: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = part.strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def validate_member_names(
    names: Iterable[str],
    map_payload_path: str,
) -> tuple[list[str], list[str]]:
    map_payload = load_map_payload(map_payload_path)
    map_names = {
        str(player.get("name", "")).strip().casefold(): str(player.get("name", "")).strip()
        for player in players_data(map_payload)
        if str(player.get("name", "")).strip()
    }

    valid_names: list[str] = []
    unknown_names: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = str(raw_name).strip()
        canonical_name = map_names.get(name.casefold())
        if canonical_name is None:
            unknown_names.append(name)
            continue
        key = canonical_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        valid_names.append(canonical_name)

    return valid_names, unknown_names


def parse_coordinate_list(raw_value: str) -> list[Coordinate]:
    cleaned = _strip_code_fence(raw_value)
    coordinates: list[Coordinate] = []
    seen: set[Coordinate] = set()
    for x, y in extract_all_coordinates(cleaned):
        coordinate = Coordinate(x, y)
        if coordinate in seen:
            continue
        seen.add(coordinate)
        coordinates.append(coordinate)
    return coordinates


def format_violation_message(violation: SettlementViolation, server_url: str) -> str:
    settlement = violation.settlement
    reason_text = _format_reason_text(violation.reasons)
    coordinate_link = build_village_map_link(
        server_url,
        settlement.coordinate.x,
        settlement.coordinate.y,
    )
    return (
        f"{settlement.player_name} hat die Regeln gebrochen und {reason_text}. "
        f"Schande ueber {settlement.player_name}! "
        f"Es geht um [{settlement.coordinate.label}]({coordinate_link})."
    )


def _format_reason_text(reasons: Iterable[str]) -> str:
    labels = []
    for reason in reasons:
        if reason == NOT_ANNOUNCED:
            labels.append("sein letztes Siedeln nicht angekuendigt")
        elif reason == OUTSIDE_KINGDOM_NORMAL_FIELD:
            labels.append("ausserhalb des KRs ein normales Feld gesiedelt")
    if not labels:
        return "gegen die Siedelregeln verstossen"
    if len(labels) == 1:
        return labels[0]
    return " und ".join(labels)


def _format_member_list(names: list[str]) -> list[str]:
    if not names:
        return ["Keine KR-Mitglieder eingetragen."]
    return _chunk_set_command_values(names, "KR-Mitglieder:")


def _format_treasury_list(coordinates: list[Coordinate]) -> list[str]:
    if not coordinates:
        return ["Keine Schatzkammern eingetragen."]
    lines = [coordinate.label for coordinate in coordinates]
    return _chunk_set_command_values(lines, "Schatzkammern:")


def _chunk_set_command_values(values: list[str], title: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for value in values:
        candidate = f"{current}; {value}" if current else value
        if current and len(candidate) > 1800:
            chunks.append(f"{title}\n```text\n{current}\n```")
            current = value
        else:
            current = candidate
    if current:
        chunks.append(f"{title}\n```text\n{current}\n```")
    return chunks


async def _send_chunks(ctx: commands.Context, chunks: list[str]) -> None:
    for chunk in chunks:
        await ctx.send(chunk)


async def _resolve_output_channel(
    bot: commands.Bot,
    channel_id: int,
    fallback_channel: discord.abc.Messageable | None,
) -> discord.abc.Messageable | None:
    if channel_id:
        channel = await _fetch_channel(bot, channel_id)
        if channel is not None and hasattr(channel, "send"):
            return channel
    return fallback_channel


async def _fetch_channel(bot: commands.Bot, channel_id: int) -> discord.abc.Messageable | None:
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        fetched_channel = await bot.fetch_channel(channel_id)
    except discord.HTTPException:
        return None
    return fetched_channel


def _strip_code_fence(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]
    return cleaned
