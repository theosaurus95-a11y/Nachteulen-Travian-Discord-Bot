from dataclasses import dataclass


@dataclass(frozen=True)
class TroopSpeed:
    tribe: str
    unit: str
    base_speed_x1: int


TROOP_SPEEDS_X1 = [
    TroopSpeed("Römer", "Legionär", 6),
    TroopSpeed("Römer", "Prätorianer", 5),
    TroopSpeed("Römer", "Imperianer", 7),
    TroopSpeed("Römer", "Equites Legati", 16),
    TroopSpeed("Römer", "Equites Imperatoris", 14),
    TroopSpeed("Römer", "Equites Caesaris", 10),
    TroopSpeed("Römer", "Rammbock", 4),
    TroopSpeed("Römer", "Feuerkatapult", 3),
    TroopSpeed("Römer", "Senator", 4),
    TroopSpeed("Teutonen", "Keulenschwinger", 7),
    TroopSpeed("Teutonen", "Speerkämpfer", 7),
    TroopSpeed("Teutonen", "Axtkämpfer", 6),
    TroopSpeed("Teutonen", "Kundschafter", 9),
    TroopSpeed("Teutonen", "Paladin", 10),
    TroopSpeed("Teutonen", "Teutonen Reiter", 9),
    TroopSpeed("Teutonen", "Ramme", 4),
    TroopSpeed("Teutonen", "Katapult", 3),
    TroopSpeed("Teutonen", "Stammesführer", 4),
    TroopSpeed("Gallier", "Phalanx", 7),
    TroopSpeed("Gallier", "Schwertkämpfer", 6),
    TroopSpeed("Gallier", "Späher", 17),
    TroopSpeed("Gallier", "Theutates Blitz", 19),
    TroopSpeed("Gallier", "Druidenreiter", 16),
    TroopSpeed("Gallier", "Haeduaner", 13),
    TroopSpeed("Gallier", "Rammholz", 4),
    TroopSpeed("Gallier", "Kriegskatapult", 3),
    TroopSpeed("Gallier", "Häuptling", 5),
]


def get_possible_world_speeds(speed_troops: float) -> list[int]:
    return sorted(
        {
            int(troop.base_speed_x1 * speed_troops)
            for troop in TROOP_SPEEDS_X1
        }
    )


def get_max_world_speed(speed_troops: float) -> int:
    return max(get_possible_world_speeds(speed_troops))


def get_min_world_speed(speed_troops: float) -> int:
    return min(get_possible_world_speeds(speed_troops))


def get_possible_base_speeds_x1() -> list[int]:
    return sorted({troop.base_speed_x1 for troop in TROOP_SPEEDS_X1})
