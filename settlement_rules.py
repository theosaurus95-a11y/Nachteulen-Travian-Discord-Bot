import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from travian_kingdoms_api import (
    Village,
    build_village,
    find_map_cell,
    gameworld_data,
    players_data,
    to_int,
)


KINGDOM_TREASURY_RADIUS = 4.2
SPECIAL_SETTLEMENT_RESTYPES = {"3339", "11115"}
NOT_ANNOUNCED = "not_announced"
OUTSIDE_KINGDOM_NORMAL_FIELD = "outside_kingdom_normal_field"


@dataclass(frozen=True)
class Coordinate:
    x: int
    y: int

    @property
    def label(self) -> str:
        return f"({self.x}|{self.y})"


@dataclass(frozen=True)
class MemberSettlement:
    player_name: str
    village: Village
    coordinate: Coordinate
    res_type: str

    @property
    def report_identity(self) -> str:
        village_id = self.village.village_id or f"{self.coordinate.x}|{self.coordinate.y}"
        return f"{self.player_name.casefold()}:{village_id}"


@dataclass(frozen=True)
class SettlementViolation:
    settlement: MemberSettlement
    reasons: tuple[str, ...]
    announced: bool
    in_kingdom_area: bool
    special_res_type: bool


class NameListStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def ensure_exists(self) -> None:
        if not self.path.exists():
            self.replace([])

    def list(self) -> list[str]:
        data = _load_json(self.path, [])
        if not isinstance(data, list):
            logging.warning("Namensliste %s ist kein JSON-Array.", self.path)
            return []

        names: list[str] = []
        seen: set[str] = set()
        for item in data:
            name = str(item).strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def replace(self, names: Iterable[str]) -> None:
        unique_names: list[str] = []
        seen: set[str] = set()
        for raw_name in names:
            name = str(raw_name).strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            unique_names.append(name)
        _write_json(self.path, unique_names)


class TreasuryCoordinateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def ensure_exists(self) -> None:
        if not self.path.exists():
            self.replace([])

    def list(self) -> list[Coordinate]:
        data = _load_json(self.path, [])
        if not isinstance(data, list):
            logging.warning("Schatzkammerliste %s ist kein JSON-Array.", self.path)
            return []

        coordinates: list[Coordinate] = []
        seen: set[Coordinate] = set()
        for item in data:
            coordinate = _coerce_coordinate(item)
            if coordinate is None or coordinate in seen:
                continue
            seen.add(coordinate)
            coordinates.append(coordinate)
        return coordinates

    def replace(self, coordinates: Iterable[Coordinate]) -> None:
        unique_coordinates: list[Coordinate] = []
        seen: set[Coordinate] = set()
        for coordinate in coordinates:
            if coordinate in seen:
                continue
            seen.add(coordinate)
            unique_coordinates.append(coordinate)

        _write_json(
            self.path,
            [{"x": coordinate.x, "y": coordinate.y} for coordinate in unique_coordinates],
        )


class SettlementReportStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def ensure_exists(self) -> None:
        if not self.path.exists():
            _write_json(self.path, [])

    def contains(self, violation: SettlementViolation) -> bool:
        identity = violation.settlement.report_identity
        for report in self._load_reports():
            if str(report.get("identity", "")) == identity:
                return True
        return False

    def add(self, violation: SettlementViolation) -> None:
        reports = self._load_reports()
        settlement = violation.settlement
        reports.append(
            {
                "identity": settlement.report_identity,
                "reportedAt": datetime.now(timezone.utc).isoformat(),
                "playerName": settlement.player_name,
                "villageId": settlement.village.village_id,
                "villageName": settlement.village.name,
                "x": settlement.coordinate.x,
                "y": settlement.coordinate.y,
                "resType": settlement.res_type,
                "reasons": list(violation.reasons),
            }
        )
        _write_json(self.path, reports)

    def _load_reports(self) -> list[dict[str, Any]]:
        data = _load_json(self.path, [])
        return data if isinstance(data, list) else []


def find_new_member_settlements(
    previous_payload: dict[str, Any] | None,
    new_payload: dict[str, Any],
    member_names: Iterable[str],
) -> list[MemberSettlement]:
    if previous_payload is None:
        return []

    member_lookup = {
        str(member_name).strip().casefold(): str(member_name).strip()
        for member_name in member_names
        if str(member_name).strip()
    }
    if not member_lookup:
        return []

    previous_players = _players_by_name(previous_payload)
    new_players = _players_by_name(new_payload)
    settlements: list[MemberSettlement] = []

    for member_key in sorted(member_lookup):
        previous_player = previous_players.get(member_key)
        new_player = new_players.get(member_key)
        if new_player is None:
            continue
        if previous_player is None:
            logging.info(
                "KR-Mitglied %s war im alten Kartensnapshot nicht vorhanden; "
                "Siedel-Diff wird fuer diesen Spieler uebersprungen.",
                new_player.get("name", member_lookup[member_key]),
            )
            continue

        previous_village_ids = {
            str(village.get("villageId", ""))
            for village in previous_player.get("villages", [])
            if str(village.get("villageId", ""))
        }
        previous_coordinates = {
            Coordinate(to_int(village.get("x")), to_int(village.get("y")))
            for village in previous_player.get("villages", [])
        }

        for village_data in new_player.get("villages", []):
            village_id = str(village_data.get("villageId", ""))
            coordinate = Coordinate(to_int(village_data.get("x")), to_int(village_data.get("y")))
            is_new_village = (
                village_id not in previous_village_ids
                if village_id
                else coordinate not in previous_coordinates
            )
            if not is_new_village:
                continue

            cell = find_map_cell(new_payload, coordinate.x, coordinate.y) or {}
            settlements.append(
                MemberSettlement(
                    player_name=str(new_player.get("name", member_lookup[member_key])),
                    village=build_village(village_data),
                    coordinate=coordinate,
                    res_type=str(cell.get("resType", "")),
                )
            )

    settlements.sort(
        key=lambda settlement: (
            settlement.player_name.casefold(),
            settlement.coordinate.x,
            settlement.coordinate.y,
        )
    )
    return settlements


def filter_settlements_absent_from_payload(
    settlements: Iterable[MemberSettlement],
    payload: dict[str, Any] | None,
) -> list[MemberSettlement]:
    if payload is None:
        return list(settlements)

    players = _players_by_name(payload)
    filtered_settlements: list[MemberSettlement] = []
    for settlement in settlements:
        player = players.get(settlement.player_name.casefold())
        if player is not None and _player_has_village(player, settlement):
            continue
        filtered_settlements.append(settlement)
    return filtered_settlements


def evaluate_settlement_violations(
    settlements: Iterable[MemberSettlement],
    announced_coordinates: Iterable[Coordinate] | None,
    treasury_coordinates: Iterable[Coordinate],
) -> list[SettlementViolation]:
    announced_set = set(announced_coordinates) if announced_coordinates is not None else None
    treasury_list = list(treasury_coordinates)
    can_check_kingdom_area = bool(treasury_list)
    violations: list[SettlementViolation] = []

    for settlement in settlements:
        announced = announced_set is not None and settlement.coordinate in announced_set
        in_kingdom_area = is_in_kingdom_area(settlement.coordinate, treasury_list)
        special_res_type = settlement.res_type in SPECIAL_SETTLEMENT_RESTYPES

        reasons: list[str] = []
        if announced_set is not None and not announced:
            reasons.append(NOT_ANNOUNCED)
        if can_check_kingdom_area and not in_kingdom_area and not special_res_type:
            reasons.append(OUTSIDE_KINGDOM_NORMAL_FIELD)
        if reasons:
            violations.append(
                SettlementViolation(
                    settlement=settlement,
                    reasons=tuple(reasons),
                    announced=announced,
                    in_kingdom_area=in_kingdom_area,
                    special_res_type=special_res_type,
                )
            )

    return violations


def is_in_kingdom_area(
    coordinate: Coordinate,
    treasury_coordinates: Iterable[Coordinate],
    radius: float = KINGDOM_TREASURY_RADIUS,
) -> bool:
    return any(
        math.dist((coordinate.x, coordinate.y), (treasury.x, treasury.y)) <= radius
        for treasury in treasury_coordinates
    )


def map_last_update_datetime(map_payload: dict[str, Any]) -> datetime | None:
    raw_value = gameworld_data(map_payload).get("lastUpdateTime") or map_payload.get("time")
    if raw_value is None:
        return None

    timestamp = to_int(raw_value)
    if timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _players_by_name(map_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    players: dict[str, dict[str, Any]] = {}
    for player in players_data(map_payload):
        name = str(player.get("name", "")).strip()
        if name:
            players[name.casefold()] = player
    return players


def _player_has_village(player: dict[str, Any], settlement: MemberSettlement) -> bool:
    settlement_village_id = settlement.village.village_id
    for village in player.get("villages", []):
        village_id = str(village.get("villageId", ""))
        if settlement_village_id and village_id == settlement_village_id:
            return True
        if (
            to_int(village.get("x")) == settlement.coordinate.x
            and to_int(village.get("y")) == settlement.coordinate.y
        ):
            return True
    return False


def _coerce_coordinate(value: object) -> Coordinate | None:
    if isinstance(value, dict):
        return Coordinate(to_int(value.get("x")), to_int(value.get("y")))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return Coordinate(to_int(value[0]), to_int(value[1]))
    return None


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("JSON-Datei %s konnte nicht gelesen werden.", path)
        return default


def _write_json(path: Path, payload: Any) -> None:
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
