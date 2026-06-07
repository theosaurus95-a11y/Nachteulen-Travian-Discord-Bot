import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from travian_kingdoms_api import RAM_SPEED, KATAPULT_SPEED, gameworld_data, get_map_data, to_int


class AttackHistoryStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def clear(self) -> None:
        self.path.write_text("[]\n", encoding="utf-8")

    def list_entries(self) -> list[dict[str, Any]]:
        return self._load_entries()

    def contains(self, attacker_village_id: str, defender_village_id: str) -> bool:
        for entry in self._load_entries():
            if (
                str(entry.get("attackerVillageId", "")) == attacker_village_id
                and str(entry.get("defenderVillageId", "")) == defender_village_id
            ):
                return True
        return False

    def add(self, entry: dict[str, Any]) -> None:
        entries = self._load_entries()
        entries.append(entry)
        self._save_entries(entries)

    def set_bot_message_url(
        self,
        attacker_village_id: str,
        defender_village_id: str,
        bot_message_url: str,
    ) -> None:
        entries = self._load_entries()
        for entry in reversed(entries):
            if (
                str(entry.get("attackerVillageId", "")) == attacker_village_id
                and str(entry.get("defenderVillageId", "")) == defender_village_id
            ):
                entry["botMessageUrl"] = bot_message_url
                break
        self._save_entries(entries)

    def _load_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.warning("Historien-Datei %s war unlesbar und wird neu aufgebaut.", self.path)
            return []
        return data if isinstance(data, list) else []

    def _save_entries(self, entries: list[dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )


def refresh_map_snapshot(
    server_url: str,
    private_api_key: str | None,
    output_path: str,
    yesterday_path: str | None = None,
) -> bool:
    if not private_api_key:
        logging.warning("TRAVIAN_PRIVATE_API_KEY fehlt, Kartendaten werden nicht aktualisiert.")
        return False

    payload = get_map_data(server_url=server_url, private_api_key=private_api_key)
    target = Path(output_path)
    previous_text = target.read_text(encoding="utf-8") if target.exists() else None
    new_text = json.dumps(payload, indent=2, ensure_ascii=True)

    if yesterday_path:
        _refresh_yesterday_snapshot(
            server_url=server_url,
            private_api_key=private_api_key,
            latest_payload=payload,
            previous_text=previous_text,
            output_path=output_path,
            yesterday_path=yesterday_path,
        )

    target.write_text(new_text, encoding="utf-8")
    return True


def _refresh_yesterday_snapshot(
    *,
    server_url: str,
    private_api_key: str,
    latest_payload: dict[str, Any],
    previous_text: str | None,
    output_path: str,
    yesterday_path: str,
) -> None:
    yesterday_target = Path(yesterday_path)
    if yesterday_target.resolve() == Path(output_path).resolve():
        return

    latest_day = map_snapshot_day_key(latest_payload)
    if latest_day is None:
        logging.warning("Yesterday-Snapshot wird nicht aktualisiert: neues Kartendatum fehlt.")
        return

    expected_yesterday = latest_day - 86_400
    existing_yesterday = _load_json_file(yesterday_target)
    if map_snapshot_day_key(existing_yesterday) == expected_yesterday:
        return

    requested_date = datetime.fromtimestamp(expected_yesterday, tz=timezone.utc).strftime("%d.%m.%Y")
    try:
        yesterday_payload = get_map_data(
            server_url=server_url,
            private_api_key=private_api_key,
            date=requested_date,
        )
    except Exception:
        logging.exception("Yesterday-Snapshot fuer %s konnte nicht per API geladen werden.", requested_date)
    else:
        if map_snapshot_day_key(yesterday_payload) == expected_yesterday:
            _write_json_file(yesterday_target, yesterday_payload)
            return
        logging.warning(
            "API-Yesterday-Snapshot hat nicht den erwarteten Kartentag %s.",
            expected_yesterday,
        )

    if previous_text is not None:
        try:
            previous_payload = json.loads(previous_text)
        except json.JSONDecodeError:
            logging.warning("Alter Kartensnapshot konnte nicht als Yesterday-Fallback gelesen werden.")
            return

        previous_day = map_snapshot_day_key(previous_payload)
        if previous_day == latest_day:
            return
        if previous_day == expected_yesterday:
            _write_json_file(yesterday_target, previous_payload)
            return

    logging.warning(
        "Yesterday-Snapshot bleibt unveraendert: kein Snapshot vom erwarteten Vortag verfuegbar.",
    )


def map_snapshot_day_key(payload: dict[str, Any] | None) -> int | None:
    if not payload:
        return None

    raw_value = gameworld_data(payload).get("date")
    timestamp = to_int(raw_value)
    if timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    return timestamp - (timestamp % 86_400)


def map_snapshot_date(payload: dict[str, Any] | None) -> date | None:
    day_key = map_snapshot_day_key(payload)
    if day_key is None:
        return None
    return datetime.fromtimestamp(day_key, tz=timezone.utc).date()


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("JSON-Datei %s konnte nicht gelesen werden.", path)
        return None
    return data if isinstance(data, dict) else None


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def build_history_entry(resolution: Any, message_url: str) -> dict[str, Any]:
    return {
        "createdAt": resolution.noted_time.astimezone().isoformat(),
        "arrivalAt": resolution.arrival_time.astimezone().isoformat(),
        "messageUrl": message_url,
        "botMessageUrl": None,
        "attackerPlayer": resolution.attacker.name,
        "attackerVillage": resolution.attacker_village.name,
        "attackerVillageId": resolution.attacker_village.village_id,
        "attackerX": resolution.attacker_village.x,
        "attackerY": resolution.attacker_village.y,
        "defenderPlayer": resolution.defender.name,
        "defenderVillage": resolution.defender_village.name,
        "defenderVillageId": resolution.defender_village.village_id,
        "defenderX": resolution.defender_village.x,
        "defenderY": resolution.defender_village.y,
        "candidates": [
            _build_candidate_entry("katapult", KATAPULT_SPEED, resolution),
            _build_candidate_entry("ram", RAM_SPEED, resolution),
        ],
    }


def filter_running_history_entries(
    entries: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    return [entry for entry in entries if is_history_entry_running(entry, now)]


def is_history_entry_running(entry: dict[str, Any], now: datetime | None = None) -> bool:
    arrival_at = parse_history_datetime(entry.get("arrivalAt"))
    if arrival_at is None:
        return False

    reference_time = now or datetime.now().astimezone()
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    return arrival_at > reference_time


def parse_history_datetime(value: object) -> datetime | None:
    if not value:
        return None

    raw_value = str(value).strip()
    if not raw_value:
        return None

    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_candidate_entry(unit_name: str, speed: int, resolution: Any) -> dict[str, Any]:
    guess = resolution.guesses.get(unit_name)
    tp_level = None
    if guess is not None and guess.starts_before_noted and resolution.distance >= 20:
        tp_level = guess.tp_level

    return {
        "unit": unit_name,
        "speed": speed,
        "tp": tp_level,
    }


def current_local_date() -> str:
    return datetime.now().astimezone().date().isoformat()
