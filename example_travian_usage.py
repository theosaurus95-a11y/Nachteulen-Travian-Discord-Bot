import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from travian_kingdoms_api import (
    KATAPULT_SPEED,
    RAM_SPEED,
    get_main_village_of_player,
    get_most_similar_player_name,
    get_most_similar_village_of_player,
    get_player_names,
    get_village_names_of_player,
    guess_tp_levels,
    travel_time_seconds,
    troop_speed_per_hour,
    village_distance,
)


def run_example_tests(payload: dict) -> None:
    print()
    print("Running example test cases:")

    similar_player = get_most_similar_player_name(payload, "Nomeless")
    print(f"  - Similar player for 'Nomeless': {similar_player}")
    assert similar_player == "Nameless"

    similar_player_with_extra_text = get_most_similar_player_name(payload, "Vkig | whatever")
    print(f"  - Similar player for 'Vkig | whatever': {similar_player_with_extra_text}")
    assert similar_player_with_extra_text == "Vkig"

    main_village = get_main_village_of_player(payload, "Vkig")
    assert main_village is not None
    print(f"  - Main village of 'Vkig': {main_village.name} at ({main_village.x}, {main_village.y})")
    assert main_village.name == "01 soup aux legumes"

    similar_village = get_most_similar_village_of_player(payload, "Vkig", "soup legumes")
    print(f"  - Similar village for 'soup legumes': {similar_village.name}")
    assert similar_village.name == "01 soup aux legumes"
    
    similar_village = get_most_similar_village_of_player(payload, "Vkig", "01")
    print(f"  - Similar village for '01': {similar_village.name}")
    assert similar_village.name == "01 soup aux legumes"

    vkig_villages = get_village_names_of_player(payload, "Vkig")
    second_village = vkig_villages[1]
    distance = village_distance(main_village, second_village)
    print(f"  - Distance from main village to second village: {distance:.3f}")
    assert round(distance, 3) == 1.414

    katapult_speed = troop_speed_per_hour(KATAPULT_SPEED, payload["response"]["gameworld"]["speedTroops"])
    ram_speed = troop_speed_per_hour(RAM_SPEED, payload["response"]["gameworld"]["speedTroops"])

    normal_travel_time = travel_time_seconds(distance=12, speed_per_hour=katapult_speed)
    print(f"  - Katapult travel time for distance 12 on this world: {normal_travel_time} seconds")
    assert normal_travel_time == 7200

    siege_travel_time = travel_time_seconds(distance=12, speed_per_hour=katapult_speed, is_siege=True)
    print(f"  - Katapult siege travel time for distance 12 on this world: {siege_travel_time} seconds")
    assert siege_travel_time == 14400

    tp_travel_time = travel_time_seconds(
        distance=30,
        speed_per_hour=ram_speed,
        tournament_square_level=10,
    )
    print(f"  - Ram travel time for distance 30 with TP 10 on this world: {tp_travel_time} seconds")
    assert tp_travel_time == 11250

    defender_village = None
    for player_name in get_player_names(payload):
        for village in get_village_names_of_player(payload, player_name):
            if village_distance(main_village, village) > 20:
                defender_village = village
                break
        if defender_village is not None:
            break

    assert defender_village is not None
    long_distance = village_distance(main_village, defender_village)
    true_tp_level = 7
    true_travel_seconds = travel_time_seconds(
        distance=long_distance,
        speed_per_hour=katapult_speed,
        is_siege=False,
        tournament_square_level=true_tp_level,
    )
    arrival_time = datetime(2026, 4, 13, 20, 0, 0)
    launch_time = arrival_time - timedelta(seconds=true_travel_seconds)
    noted_time = launch_time + timedelta(seconds=45)
    tp_guess = guess_tp_levels(
        map_payload=payload,
        noted_time=noted_time,
        arrival_time=arrival_time,
        attacker_village=main_village,
        defender_village=defender_village,
        is_siege=False,
    )
    print(
        f"  - TP guess for known katapult example at distance {long_distance:.3f}: "
        f"{tp_guess['katapult'].tp_level if tp_guess['katapult'] else 'none'}"
    )
    print(
        f"  - TP guess for  ram example at distance {long_distance:.3f}: "
        f"{tp_guess['ram'].tp_level if tp_guess['ram'] else 'none'}"
    )
    assert tp_guess["katapult"] is not None
    assert tp_guess["katapult"].tp_level == true_tp_level
    assert tp_guess["ram"] is not None
    print(
        f"    starts_before_noted={tp_guess['ram'].starts_before_noted}, "
        f"note_gap_seconds={tp_guess['ram'].note_gap_seconds}"
    )
    assert tp_guess["ram"].starts_before_noted is False

    print("All example test cases passed.")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    payload_path = Path("travian-map-data.json")
    if not payload_path.exists():
        raise SystemExit(
            "travian-map-data.json was not found. Run 'python .\\travian_kingdoms_api.py get-map-data' first."
        )

    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    player_names = get_player_names(payload)
    print(f"Loaded {len(player_names)} players.")
    print("First 10 player names:")
    for name in player_names[:10]:
        print(f"  - {name}")

    if not player_names:
        print("No players were found in the payload.")
        return

    example_player = player_names[0]
    print()
    print(f"Example player: {example_player}")

    villages = get_village_names_of_player(payload, example_player)
    print(f"Villages of {example_player}:")
    for village in villages:
        marker = " (main village)" if village.is_main_village else ""
        print(f"  - {village.name} at ({village.x}, {village.y}){marker}")

    main_village = get_main_village_of_player(payload, example_player)
    print()
    if main_village is None:
        print(f"{example_player} has no village flagged as main village.")
    else:
        print(
            f"Main village of {example_player}: "
            f"{main_village.name} at ({main_village.x}, {main_village.y})"
        )

    if len(villages) >= 2:
        distance = village_distance(villages[0], villages[1])
        print(
            f"Euclidean distance between '{villages[0].name}' and "
            f"'{villages[1].name}': {distance:.3f}"
        )

    run_example_tests(payload)


if __name__ == "__main__":
    main()
