import argparse
import asyncio
import io
import shutil
from datetime import datetime, timezone
from pathlib import Path

import settlement_discord
from bot_runtime import filter_running_history_entries
from settlement_ocr import extract_coordinates_from_image_bytes, extract_coordinates_from_ocr_text
from settlement_rules import (
    Coordinate,
    NOT_ANNOUNCED,
    OUTSIDE_KINGDOM_NORMAL_FIELD,
    evaluate_settlement_violations,
    filter_settlements_absent_from_payload,
    find_new_member_settlements,
    is_in_kingdom_area,
)
from travian_discord_integration import extract_all_coordinates, resolve_attack_message


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deployment selftests for the bot.")
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help="Skip the live Tesseract OCR check.",
    )
    args = parser.parse_args()

    test_coordinate_parser()
    test_coordinate_only_attack_target()
    test_running_history_filter()
    test_settlement_rules()
    asyncio.run(test_discord_settlement_history_scan())
    test_ocr_text_fallback()
    if not args.skip_ocr:
        test_live_ocr()
    print("bot selftest ok")


def test_coordinate_parser() -> None:
    text = "\n".join(
        [
            "auf Verlassenes Tal (-21|4)",
            "in 01:35:45 um 09:23:34",
            "Errol / Nockerbocker / Dillon - gestern um 07:49 Uhr",
            "(\u202c\u202d-\u202d4\u202c\u202c|\u202d-\u202d6\u202c\u202c\u202d)\u202c sotw",
            "Langi - gestern um 09:18 Uhr",
            "-33/-1 sotw",
            "Smokie [~NE~], - gestern um 10:07 Uhr",
            "-19/5",
            "Kandern - gestern um 11:16 Uhr",
            "-24-5",
            "Pilgerfuchs - gestern um 12:30 Uhr",
            "-15/-14 :FoxuDino:",
            "Bladmiral/Moritz - 05.06.2026 21:38",
            "-34/-6. ich ziehe ins Kampfgebiet...",
        ]
    )
    expected = [
        (-21, 4),
        (-4, -6),
        (-33, -1),
        (-19, 5),
        (-24, -5),
        (-15, -14),
        (-34, -6),
    ]
    assert extract_all_coordinates(text) == expected
    assert extract_all_coordinates("2026-05-25") == []
    assert extract_all_coordinates("in 01:35:45 um 09:23:34") == []
    assert extract_all_coordinates("1/2.5") == []


def test_coordinate_only_attack_target() -> None:
    payload = {
        "response": {
            "gameworld": {"speedTroops": 1},
            "players": [
                {
                    "playerId": "attacker-1",
                    "name": "Attacker",
                    "villages": [
                        {
                            "villageId": "attacker-village-1",
                            "name": "Hammer",
                            "x": "0",
                            "y": "0",
                            "population": "100",
                            "isMainVillage": True,
                            "isCity": False,
                        }
                    ],
                }
            ],
            "map": {"cells": []},
        }
    }
    resolution = resolve_attack_message(
        payload,
        "-15/7\nAngriff von Attacker aus Hammer in 01:00:00 um 12:00:00",
        datetime(2026, 5, 29, 11, 0, tzinfo=timezone.utc),
    )

    assert resolution is not None
    assert resolution.defender.name == "Unbekannt"
    assert resolution.defender_village.village_id == "coordinates:-15:7"
    assert resolution.defender_village.name == "-15/7"
    assert resolution.defender_village.x == -15
    assert resolution.defender_village.y == 7


def test_running_history_filter() -> None:
    now = datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)
    future_entry = {"arrivalAt": "2026-06-08T10:00:01+00:00", "id": "future"}
    entries = [
        {"arrivalAt": "2026-06-08T09:59:59+00:00", "id": "past"},
        future_entry,
        {"arrivalAt": "not a date", "id": "broken"},
        {"id": "missing"},
    ]

    assert filter_running_history_entries(entries, now) == [future_entry]


def test_settlement_rules() -> None:
    previous_payload = _map_payload(
        villages=[
            {
                "villageId": "old-1",
                "name": "Old Village",
                "x": "0",
                "y": "0",
            }
        ],
        cells=[
            {"x": "0", "y": "0", "resType": "4446"},
            {"x": "5", "y": "0", "resType": "4446"},
            {"x": "9", "y": "0", "resType": "3339"},
        ],
    )
    new_payload = _map_payload(
        villages=[
            {
                "villageId": "old-1",
                "name": "Old Village",
                "x": "0",
                "y": "0",
            },
            {
                "villageId": "new-1",
                "name": "New Village",
                "x": "5",
                "y": "0",
            },
            {
                "villageId": "new-2",
                "name": "Special Village",
                "x": "9",
                "y": "0",
            },
        ],
        cells=[
            {"x": "0", "y": "0", "resType": "4446"},
            {"x": "5", "y": "0", "resType": "4446"},
            {"x": "9", "y": "0", "resType": "3339"},
        ],
    )

    settlements = find_new_member_settlements(previous_payload, new_payload, ["Tester"])
    assert [settlement.coordinate for settlement in settlements] == [
        Coordinate(5, 0),
        Coordinate(9, 0),
    ]
    assert filter_settlements_absent_from_payload(settlements, previous_payload) == settlements
    assert is_in_kingdom_area(Coordinate(4, 0), [Coordinate(0, 0)])
    assert not is_in_kingdom_area(Coordinate(5, 0), [Coordinate(0, 0)])

    violations = evaluate_settlement_violations(
        settlements,
        announced_coordinates=[Coordinate(5, 0), Coordinate(9, 0)],
        treasury_coordinates=[Coordinate(0, 0)],
    )
    assert len(violations) == 1
    assert violations[0].settlement.coordinate == Coordinate(5, 0)
    assert violations[0].reasons == (OUTSIDE_KINGDOM_NORMAL_FIELD,)

    violations = evaluate_settlement_violations(
        settlements,
        announced_coordinates=[],
        treasury_coordinates=[Coordinate(0, 0)],
    )
    assert violations[0].reasons == (NOT_ANNOUNCED, OUTSIDE_KINGDOM_NORMAL_FIELD)
    assert violations[1].reasons == (NOT_ANNOUNCED,)


async def test_discord_settlement_history_scan() -> None:
    original_ocr = settlement_discord.extract_coordinates_from_image_bytes
    settlement_discord.extract_coordinates_from_image_bytes = lambda image_bytes: [(-31, -2)]
    try:
        bot = _FakeBot()
        coordinates = await settlement_discord.fetch_announced_coordinates(
            bot,
            channel_id=123,
            before=None,
            limit=1000,
        )
    finally:
        settlement_discord.extract_coordinates_from_image_bytes = original_ocr

    assert bot.channel.history_limit == 50
    assert coordinates == {Coordinate(-24, -5), Coordinate(-31, -2)}


def test_ocr_text_fallback() -> None:
    assert extract_coordinates_from_ocr_text("1 Besiedlung\n(-31|-2)") == [(-31, -2)]
    assert (-31, -2) in extract_coordinates_from_ocr_text("1 Besiedlung\n(-31-2)")


def test_live_ocr() -> None:
    if shutil.which("tesseract") is None:
        raise AssertionError("tesseract binary not found in PATH")

    image_bytes = _build_ocr_test_image("(-31|-2)")
    coordinates = extract_coordinates_from_image_bytes(image_bytes)
    if (-31, -2) not in coordinates:
        raise AssertionError(f"OCR did not detect (-31|-2), got {coordinates}")


def _build_ocr_test_image(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    font = _load_font(ImageFont)
    scratch = Image.new("RGB", (1, 1), "white")
    draw = ImageDraw.Draw(scratch)
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0] + 80
    height = bbox[3] - bbox[1] + 60
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 30 - bbox[1]), text, fill="black", font=font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _load_font(image_font_module):
    font_paths = [
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    ]
    for font_path in font_paths:
        if Path(font_path).exists():
            return image_font_module.truetype(font_path, 56)
    return image_font_module.load_default()


def _map_payload(*, villages: list[dict], cells: list[dict]) -> dict:
    return {
        "response": {
            "gameworld": {},
            "players": [
                {
                    "playerId": "player-1",
                    "name": "Tester",
                    "villages": villages,
                }
            ],
            "map": {"cells": cells},
        }
    }


class _FakeAuthor:
    bot = False


class _FakeAttachment:
    content_type = "image/png"
    filename = "settlement.png"
    size = 10

    async def read(self) -> bytes:
        return b"fake image bytes"


class _FakeMessage:
    author = _FakeAuthor()
    content = "-24-5"
    attachments = [_FakeAttachment()]


class _FakeHistory:
    def __init__(self, messages: list[_FakeMessage]) -> None:
        self._messages = messages

    def __aiter__(self):
        self._iterator = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class _FakeChannel:
    history_limit: int | None = None

    def history(self, *, limit: int, before):
        self.history_limit = limit
        return _FakeHistory([_FakeMessage()])


class _FakeBot:
    def __init__(self) -> None:
        self.channel = _FakeChannel()

    def get_channel(self, channel_id: int):
        return self.channel


if __name__ == "__main__":
    main()
