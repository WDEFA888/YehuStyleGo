from __future__ import annotations

from yehu_style_go.board import GoBoard
from yehu_style_go.models import Move


class TrialSession:
    """A private variation starting from a complete copy of the real board."""

    def __init__(self, board: GoBoard) -> None:
        self.reload(board)

    def reload(self, board: GoBoard) -> None:
        self.base = board.clone()
        self.board = self.base.clone()
        self._moves: list[Move] = []
        self.cursor = 0

    @property
    def origin_turn(self) -> int:
        return len(self.base.move_history)

    @property
    def moves(self) -> list[Move]:
        return self._moves[:self.cursor]

    @property
    def can_undo(self) -> bool:
        return self.cursor > 0

    @property
    def can_redo(self) -> bool:
        return self.cursor < len(self._moves)

    def play(self, x: int | None, y: int | None, color: str | None = None) -> None:
        result = self.board.play(color or self.board.to_play, x, y)
        # A new continuation replaces only this window's undone variation.
        self._moves[self.cursor:] = [result.move]
        self.cursor += 1

    def undo(self) -> bool:
        if not self.can_undo:
            return False
        self.cursor -= 1
        self._restore()
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        move = self._moves[self.cursor]
        self.board.play(move.color, move.x, move.y)
        self.cursor += 1
        return True

    def reset(self) -> None:
        self.board = self.base.clone()
        self._moves.clear()
        self.cursor = 0

    def _restore(self) -> None:
        self.board = self.base.clone()
        for move in self.moves:
            self.board.play(move.color, move.x, move.y)
