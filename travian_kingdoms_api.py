import argparse
import difflib
import json
import math
import os
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Village:
    village_id: str
    name: str
    x: int
    y: int
    population: int
    is_main_village: bool
    is_city: bool


@dataclass(frozen=True)
class TravelGuess:
    unit_name: str
    base_speed: int
    tp_level: int
    distance: float
    speed_per_hour: float
    is_siege: bool
    travel_time_seconds: int
    launch_time: datetime
    noted_time: datetime
    arrival_time: datetime
    note_gap_seconds: int
    starts_before_noted: bool


KATAPULT_SPEED = 3
RAM_SPEED = 4


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


def build_endpoint(server_url: str, action: str, **params: Any) -> str:
    server_url = server_url.rstrip("/")
    query = {"action": action}
    query.update({key: value for key, value in params.items() if value is not None})
    return f"{server_url}/api/external.php?{urlencode(query)}"


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DiscordBotTravianClient/1.0",
            "Accept": "application/json, text/plain, */*",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read().decode(charset)
    except HTTPError as exc:
        details = ""
        try:
            preview = exc.read(400).decode("utf-8", "ignore").strip()
            if preview:
                details = f" Response preview: {preview}"
        except Exception:
            details = ""
        raise RuntimeError(f"HTTP error {exc.code} while calling {url}.{details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while calling {url}: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The API response was not valid JSON.") from exc

    if isinstance(data, dict) and data.get("error"):
        error = data["error"]
        message = error.get("message", "Unknown API error")
        number = error.get("number", "n/a")
        raise RuntimeError(f"Travian API error {number}: {message}")

    return data


def request_api_key(
    server_url: str,
    email: str,
    site_name: str,
    site_url: str,
    is_public: bool,
) -> dict[str, Any]:
    url = build_endpoint(
        server_url,
        "requestApiKey",
        email=email,
        siteName=site_name,
        siteUrl=site_url,
        public="1" if is_public else "0",
    )
    return fetch_json(url)


def get_map_data(
    server_url: str,
    private_api_key: str,
    date: str | None = None,
) -> dict[str, Any]:
    url = build_endpoint(
        server_url,
        "getMapData",
        privateApiKey=private_api_key,
        date=date,
    )
    return fetch_json(url)


def response_data(map_payload: dict[str, Any]) -> dict[str, Any]:
    return map_payload.get("response", {})


def players_data(map_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return response_data(map_payload).get("players", [])


def gameworld_data(map_payload: dict[str, Any]) -> dict[str, Any]:
    return response_data(map_payload).get("gameworld", {})


def speed_troops_from_payload(map_payload: dict[str, Any]) -> float:
    speed_troops = gameworld_data(map_payload).get("speedTroops")
    if speed_troops is None:
        raise ValueError("speedTroops is missing from map payload.")
    return float(speed_troops)


def build_village(village_data: dict[str, Any]) -> Village:
    return Village(
        village_id=str(village_data.get("villageId", "")),
        name=str(village_data.get("name", "")),
        x=to_int(village_data.get("x")),
        y=to_int(village_data.get("y")),
        population=to_int(village_data.get("population")),
        is_main_village=bool(village_data.get("isMainVillage")),
        is_city=bool(village_data.get("isCity")),
    )


def find_player(map_payload: dict[str, Any], player_name: str) -> dict[str, Any]:
    wanted_name = player_name.casefold()
    for player in players_data(map_payload):
        name = str(player.get("name", ""))
        if name.casefold() == wanted_name:
            return player
    raise ValueError(f"Player not found: {player_name}")


def list_player_names(map_payload: dict[str, Any]) -> list[str]:
    return [str(player.get("name", "")) for player in players_data(map_payload)]


def list_player_villages(map_payload: dict[str, Any], player_name: str) -> list[Village]:
    player = find_player(map_payload, player_name)
    villages = player.get("villages", [])
    return [build_village(village_data) for village_data in villages]


def get_player_main_village(map_payload: dict[str, Any], player_name: str) -> Village | None:
    for village in list_player_villages(map_payload, player_name):
        if village.is_main_village:
            return village
    return None


def village_distance(village_a: Village, village_b: Village) -> float:
    return math.dist((village_a.x, village_a.y), (village_b.x, village_b.y))


def troop_speed_per_hour(base_speed: float, speed_troops: float) -> float:
    return base_speed * speed_troops


def travel_time_seconds(
    distance: float,
    speed_per_hour: float,
    is_siege: bool = False,
    tournament_square_level: int = 0,
) -> int:
    if distance < 0:
        raise ValueError("distance must be non-negative")
    if speed_per_hour <= 0:
        raise ValueError("speed_per_hour must be positive")
    if tournament_square_level < 0:
        raise ValueError("tournament_square_level must be non-negative")

    tp_level = min(tournament_square_level, 20)
    effective_speed = speed_per_hour / 2 if is_siege else speed_per_hour

    close_distance = min(distance, 20)
    far_distance = max(distance - 20, 0)
    tp_multiplier = 1 + (tp_level * 0.1)

    close_hours = close_distance / effective_speed
    far_hours = far_distance / (effective_speed * tp_multiplier)
    total_seconds = math.floor((close_hours + far_hours) * 3600)
    return total_seconds


def as_datetime(value: datetime | int | float) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    raise TypeError("Expected datetime, int, or float timestamp")


def guess_tp_level_for_speed(
    map_payload: dict[str, Any],
    noted_time: datetime | int | float,
    arrival_time: datetime | int | float,
    attacker_village: Village,
    defender_village: Village,
    base_speed: int,
    is_siege: bool = False,
) -> TravelGuess | None:
    noted_dt = as_datetime(noted_time)
    arrival_dt = as_datetime(arrival_time)
    distance = village_distance(attacker_village, defender_village)
    speed_troops = speed_troops_from_payload(map_payload)
    speed_per_hour = troop_speed_per_hour(base_speed, speed_troops)

    best_valid_guess: TravelGuess | None = None
    best_fallback_guess: TravelGuess | None = None

    for tp_level in range(21):
        guess = build_travel_guess(
            noted_time=noted_dt,
            arrival_time=arrival_dt,
            distance=distance,
            speed_per_hour=speed_per_hour,
            base_speed=base_speed,
            is_siege=is_siege,
            tp_level=tp_level,
        )

        if guess.starts_before_noted:
            if best_valid_guess is None or guess.note_gap_seconds < best_valid_guess.note_gap_seconds:
                best_valid_guess = guess
        else:
            if best_fallback_guess is None or abs(guess.note_gap_seconds) < abs(best_fallback_guess.note_gap_seconds):
                best_fallback_guess = guess

    return best_valid_guess or best_fallback_guess


def build_travel_guess(
    noted_time: datetime | int | float,
    arrival_time: datetime | int | float,
    distance: float,
    speed_per_hour: float,
    base_speed: int,
    tp_level: int,
    is_siege: bool = False,
) -> TravelGuess:
    noted_dt = as_datetime(noted_time)
    arrival_dt = as_datetime(arrival_time)
    seconds = travel_time_seconds(
        distance=distance,
        speed_per_hour=speed_per_hour,
        is_siege=is_siege,
        tournament_square_level=tp_level,
    )
    launch_time = arrival_dt.timestamp() - seconds
    launch_dt = datetime.fromtimestamp(launch_time, tz=arrival_dt.tzinfo)
    note_gap_seconds = math.floor(noted_dt.timestamp() - launch_dt.timestamp())
    starts_before_noted = launch_dt <= noted_dt
    return TravelGuess(
        unit_name="ram" if base_speed == RAM_SPEED else "katapult",
        base_speed=base_speed,
        tp_level=tp_level,
        distance=distance,
        speed_per_hour=speed_per_hour,
        is_siege=is_siege,
        travel_time_seconds=seconds,
        launch_time=launch_dt,
        noted_time=noted_dt,
        arrival_time=arrival_dt,
        note_gap_seconds=note_gap_seconds,
        starts_before_noted=starts_before_noted,
    )


def guess_tp_levels(
    map_payload: dict[str, Any],
    noted_time: datetime | int | float,
    arrival_time: datetime | int | float,
    attacker_village: Village,
    defender_village: Village,
    is_siege: bool = False,
) -> dict[str, TravelGuess | None]:
    return {
        "katapult": guess_tp_level_for_speed(
            map_payload=map_payload,
            noted_time=noted_time,
            arrival_time=arrival_time,
            attacker_village=attacker_village,
            defender_village=defender_village,
            base_speed=KATAPULT_SPEED,
            is_siege=is_siege,
        ),
        "ram": guess_tp_level_for_speed(
            map_payload=map_payload,
            noted_time=noted_time,
            arrival_time=arrival_time,
            attacker_village=attacker_village,
            defender_village=defender_village,
            base_speed=RAM_SPEED,
            is_siege=is_siege,
        ),
    }


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return " ".join(without_marks.casefold().split())


def candidate_name_parts(value: str) -> list[str]:
    raw_segments = [segment.strip() for segment in value.replace("/", "|").split("|")]
    normalized_segments = [normalize_name(segment) for segment in raw_segments if segment.strip()]
    normalized = normalize_name(value)
    if not normalized and not normalized_segments:
        return []

    candidates = {segment for segment in normalized_segments if segment}
    if normalized:
        candidates.add(normalized)
    for part in normalized.split():
        if len(part) >= 3 or part.isdigit():
            candidates.add(part)
    return sorted(candidates, key=len, reverse=True)


def similarity_score(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left, right).ratio()


def find_closest_player_name(map_payload: dict[str, Any], query: str) -> str:
    player_names = list_player_names(map_payload)
    if not player_names:
        raise ValueError("No players found in map payload.")

    query_candidates = candidate_name_parts(query)
    if not query_candidates:
        raise ValueError("Query string is empty after normalization.")

    best_name = player_names[0]
    best_score = -1.0

    for player_name in player_names:
        player_candidates = candidate_name_parts(player_name)
        if not player_candidates:
            continue

        score = max(
            similarity_score(query_candidate, player_candidate)
            for query_candidate in query_candidates
            for player_candidate in player_candidates
        )

        normalized_player = normalize_name(player_name)
        normalized_query = normalize_name(query)
        if normalized_player and normalized_player in normalized_query:
            score = max(score, 1.0)

        if score > best_score:
            best_name = player_name
            best_score = score

    return best_name


def get_most_similar_player_name(map_payload: dict[str, Any], query: str) -> str:
    return find_closest_player_name(map_payload, query)


def exact_token_match_score(query: str, candidate: str) -> float:
    query_tokens = set(normalize_name(query).split())
    candidate_tokens = set(normalize_name(candidate).split())
    if not query_tokens or not candidate_tokens:
        return 0.0

    overlap = query_tokens & candidate_tokens
    if not overlap:
        return 0.0

    if query_tokens <= candidate_tokens:
        return 1.0
    return len(overlap) / len(query_tokens)


def find_closest_village_of_player(
    map_payload: dict[str, Any],
    player_name: str,
    query: str,
) -> Village:
    villages = list_player_villages(map_payload, player_name)
    if not villages:
        raise ValueError(f"Player has no villages: {player_name}")

    query_candidates = candidate_name_parts(query)
    if not query_candidates:
        for village in villages:
            if village.is_main_village:
                return village
        return villages[0]

    best_village = villages[0]
    best_score = -1.0

    for village in villages:
        village_candidates = candidate_name_parts(village.name)
        if not village_candidates:
            continue

        score = max(
            similarity_score(query_candidate, village_candidate)
            for query_candidate in query_candidates
            for village_candidate in village_candidates
        )
        score = max(score, exact_token_match_score(query, village.name))

        normalized_village_name = normalize_name(village.name)
        normalized_query = normalize_name(query)
        if normalized_village_name and normalized_village_name in normalized_query:
            score = max(score, 1.0)

        if score > best_score:
            best_village = village
            best_score = score

    return best_village


def get_most_similar_village_of_player(
    map_payload: dict[str, Any],
    player_name: str,
    query: str,
) -> Village:
    return find_closest_village_of_player(map_payload, player_name, query)


def get_player_names(map_payload: dict[str, Any]) -> list[str]:
    return list_player_names(map_payload)


def get_village_names_of_player(map_payload: dict[str, Any], player_name: str) -> list[Village]:
    return list_player_villages(map_payload, player_name)


def get_main_village_of_player(map_payload: dict[str, Any], player_name: str) -> Village | None:
    return get_player_main_village(map_payload, player_name)


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_summary(map_payload: dict[str, Any]) -> dict[str, Any]:
    response = map_payload.get("response", {})
    gameworld = response.get("gameworld", {})
    players = response.get("players", [])
    kingdoms = response.get("kingdoms", [])
    map_info = response.get("map", {})
    cells = map_info.get("cells", [])

    total_villages = 0
    total_population = 0
    top_players = []

    for player in players:
        villages = player.get("villages", [])
        village_count = len(villages)
        population = sum(to_int(village.get("population")) for village in villages)
        total_villages += village_count
        total_population += population
        top_players.append(
            {
                "playerId": player.get("playerId"),
                "name": player.get("name"),
                "kingdomId": player.get("kingdomId"),
                "villageCount": village_count,
                "population": population,
                "role": player.get("role"),
            }
        )

    top_players.sort(
        key=lambda item: (item["population"], item["villageCount"], item["name"] or ""),
        reverse=True,
    )

    top_kingdoms = [
        {
            "kingdomId": kingdom.get("kingdomId"),
            "kingdomTag": kingdom.get("kingdomTag"),
            "victoryPoints": to_int(kingdom.get("victoryPoints")),
        }
        for kingdom in kingdoms
    ]
    top_kingdoms.sort(
        key=lambda item: (item["victoryPoints"], item["kingdomTag"] or ""),
        reverse=True,
    )

    occupied_cells = sum(1 for cell in cells if str(cell.get("resType", "0")) not in {"0", "1"})
    oasis_cells = sum(1 for cell in cells if str(cell.get("oasis", "0")) != "0")

    return {
        "gameworld": {
            "name": gameworld.get("name"),
            "date": gameworld.get("date"),
            "startTime": gameworld.get("startTime"),
            "lastUpdateTime": gameworld.get("lastUpdateTime"),
            "speed": gameworld.get("speed"),
            "speedTroops": gameworld.get("speedTroops"),
        },
        "counts": {
            "players": len(players),
            "kingdoms": len(kingdoms),
            "villages": total_villages,
            "mapRadius": to_int(map_info.get("radius")),
            "cells": len(cells),
            "occupiedCells": occupied_cells,
            "oases": oasis_cells,
            "totalPopulation": total_population,
        },
        "topPlayers": top_players[:10],
        "topKingdoms": top_kingdoms[:10],
    }


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=True))


def save_json(data: dict[str, Any], output_path: str) -> None:
    target = Path(output_path)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")


def env_or_value(value: str | None, env_name: str) -> str | None:
    return value or os.getenv(env_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register with the Travian Kingdoms external API and fetch map data."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser(
        "register",
        help="Request a privateApiKey/publicSiteKey for a Travian Kingdoms world.",
    )
    register_parser.add_argument(
        "--server-url",
        default=os.getenv("TRAVIAN_SERVER_URL"),
        help="Travian Kingdoms base URL, for example https://com1.kingdoms.com",
    )
    register_parser.add_argument(
        "--email",
        default=os.getenv("TRAVIAN_TOOL_EMAIL"),
        help="Contact email for the external tool registration.",
    )
    register_parser.add_argument(
        "--site-name",
        default=os.getenv("TRAVIAN_TOOL_NAME"),
        help="Human-readable name of your tool.",
    )
    register_parser.add_argument(
        "--site-url",
        default=os.getenv("TRAVIAN_TOOL_URL"),
        help="Public URL of your tool.",
    )
    register_parser.add_argument(
        "--public",
        action="store_true",
        default=os.getenv("TRAVIAN_TOOL_PUBLIC", "").lower() == "true",
        help="Mark the tool as public in the registration request.",
    )

    map_parser = subparsers.add_parser(
        "get-map-data",
        help="Download public map/player/kingdom data for a specific game world.",
    )
    map_parser.add_argument(
        "--server-url",
        default=os.getenv("TRAVIAN_SERVER_URL"),
        help="Travian Kingdoms base URL, for example https://com1.kingdoms.com",
    )
    map_parser.add_argument(
        "--private-api-key",
        default=os.getenv("TRAVIAN_PRIVATE_API_KEY"),
        help="privateApiKey returned by the register command.",
    )
    map_parser.add_argument(
        "--date",
        help="Optional DD.MM.YYYY snapshot date. Leave empty for the newest available data.",
    )
    map_parser.add_argument(
        "--raw-output",
        default="travian-map-data.json",
        help="File path for storing the full raw JSON response. Defaults to travian-map-data.json",
    )
    map_parser.add_argument(
        "--summary-output",
        help="Optional file path for storing the extracted summary JSON.",
    )
    map_parser.add_argument(
        "--print-raw",
        action="store_true",
        help="Print the full raw JSON payload instead of the extracted summary.",
    )

    return parser.parse_args()


def require_value(value: str | None, flag_name: str) -> str:
    if value:
        return value
    raise SystemExit(f"Missing required value for {flag_name}.")


def main() -> int:
    load_dotenv()
    args = parse_args()

    if args.command == "register":
        result = request_api_key(
            server_url=require_value(args.server_url, "--server-url"),
            email=require_value(args.email, "--email"),
            site_name=require_value(args.site_name, "--site-name"),
            site_url=require_value(args.site_url, "--site-url"),
            is_public=bool(args.public),
        )
        print_json(result)
        return 0

    if args.command == "get-map-data":
        raw_data = get_map_data(
            server_url=require_value(args.server_url, "--server-url"),
            private_api_key=require_value(args.private_api_key, "--private-api-key"),
            date=args.date,
        )
        summary = build_summary(raw_data)

        if args.raw_output:
            save_json(raw_data, args.raw_output)
        if args.summary_output:
            save_json(summary, args.summary_output)

        print_json(raw_data if args.print_raw else summary)
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
