import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from travian_kingdoms_api import (
    KATAPULT_SPEED,
    RAM_SPEED,
    TravelGuess,
    Village,
    build_travel_guess,
    build_village,
    get_main_village_of_player,
    get_most_similar_player_name,
    get_most_similar_village_of_player,
    guess_tp_levels,
    list_player_villages,
    players_data,
    speed_troops_from_payload,
    troop_speed_per_hour,
    village_distance,
)
from troop_speeds import get_possible_base_speeds_x1, get_possible_world_speeds


ATTACK_PATTERN = re.compile(
    r"(?is)"
    r"(?:\b(?:angriff|attack|belagerung|siege)\b[\s:,-]*)?"
    r"(?:von|by)\s+"
    r"(?P<attacker>.+?)\s+"
    r"(?:aus|from)\s+"
    r"(?P<attacking_village>.+?)\s*"
    r"in\s*(?P<travel_time>\d{1,2}:\d{2}:\d{2})\s+"
    r"(?:um|at)\s+(?P<arrival_time>\d{1,2}:\d{2}:\d{2})"
)
SINCE_LINE_PATTERN = re.compile(r"(?i)^\s*seit\s+(?P<value>.+?)\s*$")
TIME_ONLY_PATTERN = re.compile(r"^\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*$")
COORDINATE_PATTERN = re.compile(
    r"(?<!\d)\(?\s*(?P<x>-?\d{1,3})\s*(?:/|\|)\s*(?P<y>-?\d{1,3})\s*\)?(?!\d)"
)
MAX_STANDARD_GUESS_AGE_SECONDS = 60 * 60

@dataclass(frozen=True)
class ParsedAttackMessage:
    raw_text: str
    defender_name_hint: str | None
    defender_village_hint: str | None
    defender_coordinates_hint: tuple[int, int] | None
    use_message_author_for_defender: bool
    visible_since_text: str | None
    attacker_hint: str
    attacking_village_hint: str
    travel_time_text: str
    arrival_time_text: str
    is_siege: bool


@dataclass(frozen=True)
class PlayerMatch:
    name: str
    player_id: str


@dataclass(frozen=True)
class AttackResolution:
    parsed: ParsedAttackMessage
    noted_time: datetime
    arrival_time: datetime
    attacker: PlayerMatch
    defender: PlayerMatch
    attacker_village: Village
    defender_village: Village
    distance: float
    guesses: dict[str, TravelGuess | None]
    alternative_guess: TravelGuess | None
    defender_used_main_village: bool


TRANSLATIONS = {
    "de": {
        "attack_normal": "Angriff erkannt",
        "attack_siege": "Belagerung erkannt",
        "from_to": "Von [{attacker}]({attacker_link}) / [{attacker_village}]({attacker_village_link}) nach [{defender}]({defender_link}) / [{defender_village}]({defender_village_link})",
        "noted_time": "Meldezeit",
        "arrival_time": "Ankunft",
        "remaining_time": "Restlaufzeit",
        "message_link": "Nachrichtenlink",
        "distance": "Distanz",
        "fields": "Felder",
        "tp_irrelevant": "TP egal",
        "speed": "Geschwindigkeit",
        "ram": "Rammen",
        "katapult": "Katapulte",
        "tp_below_20": "Geschwindigkeit `{speed}`, Start `{start_time}`",
        "tp_value": "TP `{tp_level}`, Start `{start_time}`",
        "speed_without_tp": "`{speed}`, ohne TP, Start `{start_time}`",
        "tp_previous": "Mit TP `{tp_level}` haette schon um `{start_time}` gestartet werden muessen",
        "no_valid_guess": "Keine gueltige Schaetzung",
    }
}


def load_map_payload(path: str = "travian-map-data.json") -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _extract_coordinates(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = COORDINATE_PATTERN.search(value)
    if match is None:
        return None
    return int(match.group("x")), int(match.group("y"))


def _strip_coordinates(value: str) -> str:
    return _clean_segment(COORDINATE_PATTERN.sub(" ", value).strip())


def _strip_optional_coordinates(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = _strip_coordinates(value)
    return stripped or None


def _extract_leading_defender_hint(content: str) -> tuple[str | None, str] | None:
    match = ATTACK_PATTERN.search(content)
    if match is None or match.start() == 0:
        return None

    lines = [
        line.strip().rstrip(":")
        for line in content[: match.start()].splitlines()
        if line.strip()
    ]
    if not lines:
        return None

    return _strip_optional_coordinates(lines[-1]), content[match.start() :].strip()


def _extract_defender_coordinates(
    content: str,
    *hints: str | None,
) -> tuple[int, int] | None:
    for hint in hints:
        coordinates = _extract_coordinates(hint)
        if coordinates is not None:
            return coordinates

    match = ATTACK_PATTERN.search(content)
    if match is None:
        return _extract_coordinates(content)

    for segment in (content[: match.start()], content[match.end() :]):
        coordinates = _extract_coordinates(segment)
        if coordinates is not None:
            return coordinates
    return None


def parse_attack_message(content: str) -> ParsedAttackMessage | None:
    compact = content.strip()
    if not compact:
        return None

    defender_name_hint: str | None = None
    defender_village_hint: str | None = None
    use_message_author_for_defender = False
    override = _extract_defender_override(compact)
    if override is not None:
        defender_name_hint, defender_village_hint, compact, use_message_author_for_defender = override

    visible_since_text, compact = _extract_visible_since_hint(compact)
    coordinate_source = compact

    prefix_match = re.match(r"(?s)^\s*([^:\n]{1,80})\s*:\s*(.+)$", compact)
    if prefix_match:
        possible_hint = prefix_match.group(1).strip()
        remaining = prefix_match.group(2).strip()
        if ATTACK_PATTERN.search(remaining):
            defender_village_hint = _strip_coordinates(possible_hint)
            compact = remaining
    else:
        prefixed_hint = _extract_leading_defender_hint(compact)
        if prefixed_hint is not None:
            defender_village_hint, compact = prefixed_hint

    match = ATTACK_PATTERN.search(compact)
    if not match:
        return None

    defender_coordinates_hint = _extract_defender_coordinates(
        coordinate_source,
        defender_village_hint,
    )
    defender_name_hint = _strip_optional_coordinates(defender_name_hint)
    defender_village_hint = _strip_optional_coordinates(defender_village_hint)
    lowered = compact.casefold()
    return ParsedAttackMessage(
        raw_text=content,
        defender_name_hint=defender_name_hint,
        defender_village_hint=defender_village_hint,
        defender_coordinates_hint=defender_coordinates_hint,
        use_message_author_for_defender=use_message_author_for_defender,
        visible_since_text=visible_since_text,
        attacker_hint=_clean_segment(match.group("attacker")),
        attacking_village_hint=_clean_segment(match.group("attacking_village")),
        travel_time_text=match.group("travel_time"),
        arrival_time_text=match.group("arrival_time"),
        is_siege=("belagerung" in lowered) or ("siege" in lowered),
    )


def split_attack_messages(content: str) -> list[str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return []

    attack_indices = [index for index, line in enumerate(lines) if ATTACK_PATTERN.search(line)]
    if len(attack_indices) <= 1:
        return [content]

    shared_prefix = "\n".join(lines[:attack_indices[0]]).strip()
    messages: list[str] = []
    for index in attack_indices:
        line = lines[index]
        if shared_prefix and not line.casefold().startswith("auf "):
            messages.append(f"{shared_prefix}\n{line}")
        else:
            messages.append(line)
    return messages


def resolve_attack_message(
    map_payload: dict[str, Any],
    message_content: str,
    noted_time: datetime,
    defender_name_hint: str,
    defender_name_override: str | None = None,
    defender_village_override: str | None = None,
    noted_time_hint: str | None = None,
) -> AttackResolution | None:
    parsed = parse_attack_message(message_content)
    if parsed is None:
        return None

    effective_noted_time = parse_flexible_noted_time(
        noted_time,
        noted_time_hint or parsed.visible_since_text,
    ) or noted_time
    defender_coordinates = (
        _extract_coordinates(defender_village_override)
        or parsed.defender_coordinates_hint
    )
    attacker_name = get_most_similar_player_name(map_payload, parsed.attacker_hint)

    if defender_coordinates is not None:
        defender_match = _find_village_match_by_coordinates(
            map_payload,
            defender_coordinates[0],
            defender_coordinates[1],
        )
        if defender_match is None:
            raise ValueError(
                "No village found at coordinates: "
                f"{defender_coordinates[0]}/{defender_coordinates[1]}"
            )
        defender = defender_match[0]
        defender_name = defender.name
        defender_village = defender_match[1]
        used_main_village = False
    else:
        effective_defender_hint = (
            defender_name_override
            or (
                defender_name_hint
                if parsed.use_message_author_for_defender
                else (parsed.defender_name_hint or defender_name_hint)
            )
        )
        defender_name = get_most_similar_player_name(map_payload, effective_defender_hint)
        defender = _find_player_match(map_payload, defender_name)
        defender_village, used_main_village = _resolve_defender_village(
            map_payload=map_payload,
            defender_name=defender_name,
            defender_village_hint=defender_village_override or parsed.defender_village_hint,
        )

    attacker_village = get_most_similar_village_of_player(
        map_payload,
        attacker_name,
        parsed.attacking_village_hint,
    )

    arrival_time = _resolve_arrival_time(effective_noted_time, parsed.arrival_time_text)
    guesses = guess_tp_levels(
        map_payload=map_payload,
        noted_time=effective_noted_time,
        arrival_time=arrival_time,
        attacker_village=attacker_village,
        defender_village=defender_village,
        is_siege=parsed.is_siege,
    )
    alternative_guess = None
    if _should_guess_alternative_speed(
        distance=village_distance(attacker_village, defender_village),
        guesses=guesses,
    ):
        alternative_guess = guess_alternative_speed(
            map_payload=map_payload,
            noted_time=effective_noted_time,
            arrival_time=arrival_time,
            attacker_village=attacker_village,
            defender_village=defender_village,
            is_siege=parsed.is_siege,
        )

    return AttackResolution(
        parsed=parsed,
        noted_time=effective_noted_time,
        arrival_time=arrival_time,
        attacker=_find_player_match(map_payload, attacker_name),
        defender=defender,
        attacker_village=attacker_village,
        defender_village=defender_village,
        distance=village_distance(attacker_village, defender_village),
        guesses=guesses,
        alternative_guess=alternative_guess,
        defender_used_main_village=used_main_village,
    )


def build_player_link(server_url: str, player: PlayerMatch) -> str:
    return (
        f"{server_url.rstrip('/')}/#/page:map/window:playerProfile/"
        f"playerId:{player.player_id}"
    )


def build_village_link(server_url: str, village: Village) -> str:
    return (
        f"{server_url.rstrip('/')}/#/page:map/x:{village.x}/y:{village.y}/window:sendTroops"
    )


def translate(locale: str, key: str, **values: Any) -> str:
    template = TRANSLATIONS.get(locale, TRANSLATIONS["de"])[key]
    return template.format(**values)


def format_guess(
    guess: TravelGuess | None,
    previous_tp_guess: TravelGuess | None,
    distance: float,
    locale: str = "de",
) -> str:
    if guess is None or not guess.starts_before_noted:
        return "-"

    start_time = format_time_short(guess.launch_time) if distance >= 20 else format_datetime_short(guess.launch_time)
    if distance < 20:
        speed = math.ceil((guess.speed_per_hour / 2) if guess.is_siege else guess.speed_per_hour)
        text = translate(locale, "tp_below_20", speed=speed, start_time=start_time)
    else:
        text = translate(locale, "tp_value", tp_level=guess.tp_level, start_time=start_time)

    if previous_tp_guess is not None and previous_tp_guess.starts_before_noted:
        text += ", " + translate(
            locale,
            "tp_previous",
            tp_level=previous_tp_guess.tp_level,
            start_time=format_time_short(previous_tp_guess.launch_time),
        )
    return text


def format_speed_guess(
    guess: TravelGuess | None,
    locale: str = "de",
) -> str:
    if guess is None or not guess.starts_before_noted:
        return "-"

    return translate(
        locale,
        "speed_without_tp",
        speed=_display_speed_for_guess(guess),
        start_time=format_time_short(guess.launch_time),
    )


def _display_speed_for_guess(guess: TravelGuess) -> int:
    speed = (guess.speed_per_hour / 2) if guess.is_siege else guess.speed_per_hour
    return math.ceil(speed)


def is_recent_standard_guess(guess: TravelGuess | None) -> bool:
    return (
        guess is not None
        and guess.starts_before_noted
        and 0 <= guess.note_gap_seconds <= MAX_STANDARD_GUESS_AGE_SECONDS
    )


def _clean_segment(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())


def _extract_visible_since_hint(content: str) -> tuple[str | None, str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return None, content.strip()

    since_index = None
    since_value: str | None = None
    for index, line in enumerate(lines):
        match = SINCE_LINE_PATTERN.fullmatch(line)
        if match is None:
            continue
        since_index = index
        since_value = _clean_segment(match.group("value"))
        break

    if since_index is None or not since_value:
        return None, content.strip()

    remaining_lines = lines[:since_index] + lines[since_index + 1 :]
    return since_value, "\n".join(remaining_lines).strip()


def _extract_defender_override(content: str) -> tuple[str, str, str, bool] | None:
    stripped = content.lstrip()
    if not stripped.casefold().startswith("auf "):
        return None

    lines = stripped.splitlines()
    if not lines:
        return None

    first_line = lines[0].strip()
    after_auf = first_line[4:].strip()
    remainder_lines = lines[1:]

    if remainder_lines:
        first_content_index = next(
            (index for index, line in enumerate(remainder_lines) if line.strip()),
            None,
        )
        if first_content_index is None:
            return None

        second_line = remainder_lines[first_content_index].strip()
        if ATTACK_PATTERN.search(second_line):
            defender = _clean_segment(after_auf)
            remaining = "\n".join(remainder_lines[first_content_index:]).strip()
            if defender and remaining:
                return defender, "", remaining, _is_self_reference(defender)

        if second_line and ATTACK_PATTERN.search(second_line) is None:
            defender = _clean_segment(after_auf)
            village = _clean_segment(second_line.rstrip(":"))
            remaining = "\n".join(remainder_lines[first_content_index + 1 :]).strip()
            if defender and village and remaining:
                return defender, village, remaining, _is_self_reference(defender)

    resolved = _split_inline_override_hint(after_auf)
    if resolved is None or not remainder_lines:
        return None

    defender, village = resolved
    remaining = "\n".join(remainder_lines).strip()
    if not remaining:
        return None
    return defender, village.rstrip(":"), remaining, _is_self_reference(defender)


def _split_inline_override_hint(value: str) -> tuple[str, str] | None:
    cleaned = value.rstrip(":").strip()
    if not cleaned:
        return None
    parts = cleaned.rsplit(None, 1)
    if len(parts) != 2:
        return None
    defender, village = parts[0].strip(), parts[1].strip()
    if not defender or not village:
        return None
    return defender, village


def _is_self_reference(value: str) -> bool:
    normalized = _clean_segment(value).casefold()
    return normalized in {"mich", "mein", "uns", "unser"}


def parse_flexible_noted_time(reference_time: datetime, raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None

    cleaned = " ".join(raw_value.strip().replace("T", " ").split())
    if not cleaned:
        return None
    if cleaned.casefold().endswith(" uhr"):
        cleaned = cleaned[:-4].strip()

    for time_format in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            parsed = datetime.strptime(cleaned, time_format)
        except ValueError:
            continue

        candidate = parsed.replace(tzinfo=reference_time.tzinfo)
        if candidate > reference_time:
            return None
        return candidate

    match = TIME_ONLY_PATTERN.fullmatch(cleaned)
    if match is None:
        return None

    hours = int(match.group("hour"))
    minutes = int(match.group("minute"))
    seconds = int(match.group("second") or "0")
    if hours > 23 or minutes > 59 or seconds > 59:
        return None

    candidate = datetime.combine(
        reference_time.date(),
        time(hour=hours, minute=minutes, second=seconds),
        tzinfo=reference_time.tzinfo,
    )
    if candidate > reference_time:
        candidate -= timedelta(days=1)
    return candidate


def _resolve_defender_village(
    map_payload: dict[str, Any],
    defender_name: str,
    defender_village_hint: str | None,
) -> tuple[Village, bool]:
    if defender_village_hint:
        return (
            get_most_similar_village_of_player(map_payload, defender_name, defender_village_hint),
            False,
        )

    main_village = get_main_village_of_player(map_payload, defender_name)
    if main_village is not None:
        return main_village, True

    villages = list_player_villages(map_payload, defender_name)
    if not villages:
        raise ValueError(f"Player has no villages: {defender_name}")
    return villages[0], True


def _find_player_match(map_payload: dict[str, Any], player_name: str) -> PlayerMatch:
    for player in players_data(map_payload):
        if str(player.get("name", "")).casefold() == player_name.casefold():
            return PlayerMatch(
                name=str(player.get("name", "")),
                player_id=str(player.get("playerId", "")),
            )
    raise ValueError(f"Player not found: {player_name}")


def _find_village_match_by_coordinates(
    map_payload: dict[str, Any],
    x: int,
    y: int,
) -> tuple[PlayerMatch, Village] | None:
    for player in players_data(map_payload):
        player_match = PlayerMatch(
            name=str(player.get("name", "")),
            player_id=str(player.get("playerId", "")),
        )
        for village_data in player.get("villages", []):
            village = build_village(village_data)
            if village.x == x and village.y == y:
                return player_match, village
    return None


def _resolve_arrival_time(noted_time: datetime, arrival_text: str) -> datetime:
    hours, minutes, seconds = (int(part) for part in arrival_text.split(":"))
    arrival = datetime.combine(
        noted_time.date(),
        time(hour=hours, minute=minutes, second=seconds),
        tzinfo=noted_time.tzinfo,
    )
    if arrival < noted_time:
        arrival += timedelta(days=1)
    return arrival


def format_datetime_short(value: datetime) -> str:
    localized = value.astimezone()
    timezone_name = localized.tzname() or ""
    abbreviations = {
        "Mitteleuropaeische Sommerzeit": "MESZ",
        "Mitteleuropäische Sommerzeit": "MESZ",
        "Mitteleuropaeische Zeit": "MEZ",
        "Mitteleuropäische Zeit": "MEZ",
        "Central European Summer Time": "CEST",
        "Central European Standard Time": "CET",
    }
    timezone_text = abbreviations.get(timezone_name, timezone_name)
    return localized.strftime("%Y-%m-%d %H:%M:%S").strip() + (f" {timezone_text}" if timezone_text else "")


def format_time_short(value: datetime) -> str:
    localized = value.astimezone()
    timezone_name = localized.tzname() or ""
    abbreviations = {
        "Mitteleuropaeische Sommerzeit": "MESZ",
        "Mitteleuropäische Sommerzeit": "MESZ",
        "Mitteleuropaeische Zeit": "MEZ",
        "Mitteleuropäische Zeit": "MEZ",
        "Central European Summer Time": "CEST",
        "Central European Standard Time": "CET",
    }
    timezone_text = abbreviations.get(timezone_name, timezone_name)
    return localized.strftime("%H:%M:%S").strip() + (f" {timezone_text}" if timezone_text else "")


def format_duration_hms(start: datetime, end: datetime) -> str:
    total_seconds = max(0, int((end - start).total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def get_short_distance_speed(distance: float, noted_time: datetime, arrival_time: datetime) -> int | None:
    total_seconds = (arrival_time - noted_time).total_seconds()
    if total_seconds <= 0:
        return None
    hours = total_seconds / 3600
    if hours <= 0:
        return None
    exact_speed = distance / hours
    candidate_speed = max(1, math.ceil(exact_speed))
    while candidate_speed > 1:
        launch_time = get_short_distance_launch_time(distance, arrival_time, candidate_speed)
        if launch_time <= noted_time:
            return candidate_speed
        candidate_speed -= 1

    launch_time = get_short_distance_launch_time(distance, arrival_time, 1)
    if launch_time <= noted_time:
        return 1
    return None


def get_short_distance_speed_with_world_limit(
    map_payload: dict[str, Any],
    distance: float,
    noted_time: datetime,
    arrival_time: datetime,
) -> int | None:
    speed_troops = speed_troops_from_payload(map_payload)
    possible_speeds = get_possible_world_speeds(speed_troops)
    valid_speeds = [
        speed
        for speed in possible_speeds
        if get_short_distance_launch_time(distance, arrival_time, speed) <= noted_time
    ]
    if not valid_speeds:
        return None
    return max(valid_speeds)


def get_short_distance_launch_time(
    distance: float,
    arrival_time: datetime,
    speed: int,
) -> datetime:
    travel_seconds = math.floor((distance / speed) * 3600)
    launch_time = arrival_time.timestamp() - travel_seconds
    return datetime.fromtimestamp(launch_time, tz=arrival_time.tzinfo)


def get_previous_tp_guess(
    guess: TravelGuess | None,
    arrival_time: datetime,
    noted_time: datetime,
) -> TravelGuess | None:
    if guess is None or guess.tp_level <= 0:
        return None
    return build_travel_guess(
        noted_time=noted_time,
        arrival_time=arrival_time,
        distance=guess.distance,
        speed_per_hour=guess.speed_per_hour,
        base_speed=guess.base_speed,
        tp_level=guess.tp_level - 1,
        is_siege=guess.is_siege,
    )


def _should_guess_alternative_speed(
    distance: float,
    guesses: dict[str, TravelGuess | None],
) -> bool:
    if distance <= 20:
        return False

    return not all(
        is_recent_standard_guess(guesses.get(unit_name))
        for unit_name in ("ram", "katapult")
    )


def guess_alternative_speed(
    map_payload: dict[str, Any],
    noted_time: datetime,
    arrival_time: datetime,
    attacker_village: Village,
    defender_village: Village,
    is_siege: bool,
) -> TravelGuess | None:
    best_valid_guess: TravelGuess | None = None
    best_fallback_guess: TravelGuess | None = None
    speed_troops = speed_troops_from_payload(map_payload)
    distance = village_distance(attacker_village, defender_village)

    for base_speed in get_possible_base_speeds_x1():
        if base_speed in {RAM_SPEED, KATAPULT_SPEED}:
            continue
        guess = build_travel_guess(
            noted_time=noted_time,
            arrival_time=arrival_time,
            distance=distance,
            speed_per_hour=troop_speed_per_hour(base_speed, speed_troops),
            base_speed=base_speed,
            tp_level=0,
            is_siege=is_siege,
        )

        if guess.starts_before_noted:
            if best_valid_guess is None or guess.note_gap_seconds < best_valid_guess.note_gap_seconds:
                best_valid_guess = guess
        else:
            if best_fallback_guess is None or abs(guess.note_gap_seconds) < abs(best_fallback_guess.note_gap_seconds):
                best_fallback_guess = guess

    return best_valid_guess or best_fallback_guess
