import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from travian_kingdoms_api import RAM_SPEED, KATAPULT_SPEED, save_json, get_map_data


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
) -> bool:
    if not private_api_key:
        logging.warning("TRAVIAN_PRIVATE_API_KEY fehlt, Kartendaten werden nicht aktualisiert.")
        return False

    payload = get_map_data(server_url=server_url, private_api_key=private_api_key)
    save_json(payload, output_path)
    return True


def build_history_entry(resolution: Any, message_url: str) -> dict[str, Any]:
    return {
        "createdAt": resolution.noted_time.astimezone().isoformat(),
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
