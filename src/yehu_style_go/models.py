from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Move:
    color: str
    x: int | None
    y: int | None

    @property
    def is_pass(self) -> bool:
        return self.x is None or self.y is None

    @property
    def sgf_coord(self) -> str:
        if self.is_pass:
            return ""
        return chr(ord("a") + self.x) + chr(ord("a") + self.y)


@dataclass(slots=True)
class GameRecord:
    source_path: Path
    raw_sgf: str
    properties: dict[str, list[str]]
    moves: list[Move]
    setup_black: list[tuple[int, int]] = field(default_factory=list)
    setup_white: list[tuple[int, int]] = field(default_factory=list)
    sha256: str = ""
    database_id: int | None = None

    @property
    def board_size(self) -> int:
        value = self.first_property("SZ", "19")
        try:
            return int(value.split(":", 1)[0])
        except ValueError:
            return 19

    @property
    def black_player(self) -> str:
        return self.first_property("PB")

    @property
    def white_player(self) -> str:
        return self.first_property("PW")

    @property
    def black_rank(self) -> str:
        return self.first_property("BR")

    @property
    def white_rank(self) -> str:
        return self.first_property("WR")

    @property
    def played_at(self) -> str:
        return self.first_property("DT")

    @property
    def result(self) -> str:
        return self.first_property("RE")

    @property
    def rules(self) -> str:
        value = self.first_property("RU", "Chinese").lower()
        if "japan" in value:
            return "japanese"
        if "aga" in value:
            return "aga"
        if "new zealand" in value or value == "nz":
            return "new-zealand"
        return "chinese"

    @property
    def komi(self) -> float:
        raw = self.first_property("KM", "7.5")
        try:
            value = float(raw)
        except ValueError:
            return 7.5
        applications = " ".join(self.properties.get("AP", [])).lower()
        if "foxwq" in applications and abs(value) >= 100:
            # Fox stores Chinese-rule komi in hundredths of a stone: 375 -> 7.5 points.
            return value / 50.0
        return value

    @property
    def handicap(self) -> int:
        try:
            return int(self.first_property("HA", "0"))
        except ValueError:
            return len(self.setup_black)

    def first_property(self, name: str, default: str = "") -> str:
        values = self.properties.get(name)
        return values[0] if values else default

    def color_for(self, player_name: str) -> str | None:
        target = player_name.strip().casefold()
        if self.black_player.strip().casefold() == target:
            return "B"
        if self.white_player.strip().casefold() == target:
            return "W"
        return None

    def did_player_win(self, player_name: str) -> bool | None:
        color = self.color_for(player_name)
        result = self.result.strip().upper()
        if color is None or not result:
            return None
        unknown_or_draw = ("0", "DRAW", "VOID", "JIGO", "?", "UNKNOWN", "UNK")
        if any(result.startswith(token) for token in unknown_or_draw):
            return None
        if result.startswith(f"{color}+"):
            return True
        if result.startswith(f"{'W' if color == 'B' else 'B'}+"):
            return False
        return None

    def to_summary(self, player_name: str) -> dict[str, Any]:
        color = self.color_for(player_name)
        opponent = self.white_player if color == "B" else self.black_player
        rank = self.white_rank if color == "B" else self.black_rank
        return {
            "id": self.database_id,
            "date": self.played_at,
            "color": color,
            "opponent": opponent,
            "opponent_rank": rank,
            "result": self.result,
            "won": self.did_player_win(player_name),
            "move_count": len(self.moves),
        }
