from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from yehu_style_go.models import GameRecord, Move

EMPTY = 0
BLACK = 1
WHITE = 2
COLOR_TO_STONE = {"B": BLACK, "W": WHITE}
STONE_TO_COLOR = {BLACK: "B", WHITE: "W"}
GTP_COLUMNS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"


class IllegalMove(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlayResult:
    captured: int
    move: Move


class GoBoard:
    def __init__(self, size: int = 19, ko_rule: str = "SIMPLE") -> None:
        if not 2 <= size <= 25:
            raise ValueError("棋盘尺寸必须在 2 到 25 之间")
        if ko_rule not in {"SIMPLE", "POSITIONAL"}:
            raise ValueError("不支持的劫规则")
        self.ko_rule = ko_rule
        self._previous_position: tuple[int, ...] | None = None
        self.size = size
        self.grid = [[EMPTY for _ in range(size)] for _ in range(size)]
        self.to_play = "B"
        self.captures = {"B": 0, "W": 0}
        self.move_history: list[Move] = []
        self._positions: set[tuple[int, ...]] = {self._position_key()}

    @classmethod
    def from_game(cls, game: GameRecord, turn: int | None = None) -> GoBoard:
        board = cls(game.board_size, "SIMPLE" if game.rules in {"chinese", "japanese"} else "POSITIONAL")
        board.set_setup(game.setup_black, game.setup_white)
        limit = len(game.moves) if turn is None else max(0, min(turn, len(game.moves)))
        for move in game.moves[:limit]:
            board.play(move.color, move.x, move.y)
        return board

    def clone(self) -> GoBoard:
        board = GoBoard(self.size, self.ko_rule)
        board._previous_position = self._previous_position
        board.grid = [row[:] for row in self.grid]
        board.to_play = self.to_play
        board.captures = dict(self.captures)
        board.move_history = list(self.move_history)
        board._positions = set(self._positions)
        return board

    def clear(self) -> None:
        self.grid = [[EMPTY for _ in range(self.size)] for _ in range(self.size)]
        self.to_play = "B"
        self.captures = {"B": 0, "W": 0}
        self.move_history = []
        self._previous_position = None
        self._positions = {self._position_key()}

    def set_setup(
        self,
        black: Iterable[tuple[int, int]],
        white: Iterable[tuple[int, int]],
    ) -> None:
        self.clear()
        for color, points in ((BLACK, black), (WHITE, white)):
            for x, y in points:
                self._require_on_board(x, y)
                if self.grid[y][x] != EMPTY:
                    raise ValueError("摆放棋子位置重复")
                self.grid[y][x] = color
        self.to_play = "W" if any(stone == BLACK for row in self.grid for stone in row) else "B"
        self._positions = {self._position_key()}

    def play(self, color: str, x: int | None, y: int | None) -> PlayResult:
        color = color.upper()
        if color not in COLOR_TO_STONE:
            raise IllegalMove("棋子颜色必须是 B 或 W")
        move = Move(color, x, y)
        if move.is_pass:
            self._previous_position = self._position_key()
            self.move_history.append(move)
            self.to_play = opposite(color)
            return PlayResult(0, move)

        assert x is not None and y is not None
        self._require_on_board(x, y)
        if self.grid[y][x] != EMPTY:
            raise IllegalMove("该位置已有棋子")

        stone = COLOR_TO_STONE[color]
        opponent = WHITE if stone == BLACK else BLACK
        previous_grid = [row[:] for row in self.grid]
        self.grid[y][x] = stone
        captured = 0
        checked: set[tuple[int, int]] = set()
        for nx, ny in self.neighbors(x, y):
            if self.grid[ny][nx] != opponent or (nx, ny) in checked:
                continue
            group, liberties = self._group_and_liberties(nx, ny)
            checked.update(group)
            if not liberties:
                captured += len(group)
                for gx, gy in group:
                    self.grid[gy][gx] = EMPTY

        _, own_liberties = self._group_and_liberties(x, y)
        if not own_liberties:
            self.grid = previous_grid
            raise IllegalMove("禁入点：落子后没有气")

        position = self._position_key()
        repeated = (position == self._previous_position if self.ko_rule == "SIMPLE"
                    else position in self._positions)
        if repeated:
            self.grid = previous_grid
            message = ("打劫：不能立即回提" if self.ko_rule == "SIMPLE"
                       else "全局同形：该落子会重复之前的局面")
            raise IllegalMove(message)

        # KataGo's Chinese preset also uses SIMPLE ko, not positional superko.
        self._previous_position = tuple(stone for row in previous_grid for stone in row)
        self._positions.add(position)
        self.move_history.append(move)
        self.captures[color] += captured
        self.to_play = opposite(color)
        return PlayResult(captured, move)

    def is_legal(self, color: str, x: int | None, y: int | None) -> bool:
        board = self.clone()
        try:
            board.play(color, x, y)
        except (IllegalMove, ValueError):
            return False
        return True

    def legal_moves(self, color: str, include_pass: bool = True) -> list[tuple[int | None, int | None]]:
        moves: list[tuple[int | None, int | None]] = []
        for y in range(self.size):
            for x in range(self.size):
                if self.grid[y][x] == EMPTY and self.is_legal(color, x, y):
                    moves.append((x, y))
        if include_pass:
            moves.append((None, None))
        return moves

    def neighbors(self, x: int, y: int) -> Iterable[tuple[int, int]]:
        if x > 0:
            yield x - 1, y
        if x + 1 < self.size:
            yield x + 1, y
        if y > 0:
            yield x, y - 1
        if y + 1 < self.size:
            yield x, y + 1

    def _group_and_liberties(
        self, x: int, y: int
    ) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
        stone = self.grid[y][x]
        if stone == EMPTY:
            return set(), {(x, y)}
        group: set[tuple[int, int]] = set()
        liberties: set[tuple[int, int]] = set()
        stack = [(x, y)]
        while stack:
            point = stack.pop()
            if point in group:
                continue
            group.add(point)
            px, py = point
            for nx, ny in self.neighbors(px, py):
                value = self.grid[ny][nx]
                if value == EMPTY:
                    liberties.add((nx, ny))
                elif value == stone and (nx, ny) not in group:
                    stack.append((nx, ny))
        return group, liberties

    def _position_key(self) -> tuple[int, ...]:
        return tuple(stone for row in self.grid for stone in row)

    def _require_on_board(self, x: int, y: int) -> None:
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise IllegalMove("落子超出棋盘")


def opposite(color: str) -> str:
    return "W" if color.upper() == "B" else "B"


def xy_to_gtp(x: int | None, y: int | None, size: int = 19) -> str:
    if x is None or y is None:
        return "pass"
    if not (0 <= x < size and 0 <= y < size):
        raise ValueError("坐标超出棋盘")
    return f"{GTP_COLUMNS[x]}{size - y}"


def gtp_to_xy(coordinate: str, size: int = 19) -> tuple[int | None, int | None]:
    value = coordinate.strip().upper()
    if value in {"PASS", "PA"}:
        return None, None
    if len(value) < 2 or value[0] not in GTP_COLUMNS[:size]:
        raise ValueError(f"无效的 GTP 坐标：{coordinate}")
    x = GTP_COLUMNS.index(value[0])
    try:
        row = int(value[1:])
    except ValueError as error:
        raise ValueError(f"无效的 GTP 坐标：{coordinate}") from error
    y = size - row
    if not 0 <= y < size:
        raise ValueError(f"无效的 GTP 坐标：{coordinate}")
    return x, y
