from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from yehu_style_go.board import BLACK, EMPTY, WHITE, GoBoard


@dataclass
class AreaScore:
    black_stones: int
    white_stones: int
    black_territory: int
    white_territory: int
    neutral: int
    komi: float
    dead_black: int
    dead_white: int
    dead: frozenset[tuple[int, int]]
    territory: dict[tuple[int, int], int] = field(default_factory=dict)

    @property
    def black_total(self) -> float:
        return self.black_stones + self.black_territory

    @property
    def white_total(self) -> float:
        return self.white_stones + self.white_territory + self.komi

    @property
    def margin(self) -> float:
        return self.black_total - self.white_total

    @property
    def result(self) -> str:
        if abs(self.margin) < 1e-9:
            return "0"
        return f"{'B' if self.margin > 0 else 'W'}+{abs(self.margin):g}"

    def summary(self) -> str:
        winner = ("和棋" if self.result == "0" else
                  f"{'黑' if self.margin > 0 else '白'}胜 {abs(self.margin):g} 目")
        return (f"{winner}（{self.result}）\n"
                f"黑方：{self.black_total:g} 点 = {self.black_stones} 子 + {self.black_territory} 空点\n"
                f"白方：{self.white_total:g} 点 = {self.white_stones} 子 + {self.white_territory} 空点"
                f" + 贴目 {self.komi:g}\n"
                f"中立空点 {self.neutral}；标记死子：黑 {self.dead_black}、白 {self.dead_white}\n"
                "中国数子规则：棋子与围空一起计分，提子不另加分。")


def score_area(
    board: GoBoard, dead: Iterable[tuple[int, int]] = (), komi: float = 7.5
) -> AreaScore:
    """Chinese area count after the players have agreed which groups are dead.

    This is not a life-and-death solver. Mixed-border empty regions are neutral.
    Marking any stone removes its entire connected group, without editing the game.
    """
    if not math.isfinite(komi):
        raise ValueError("贴目必须是有限数字")
    removed: set[tuple[int, int]] = set()
    for x, y in dead:
        if not (0 <= x < board.size and 0 <= y < board.size):
            raise ValueError("死子坐标超出棋盘")
        if board.grid[y][x] == EMPTY:
            raise ValueError("不能把空点标记为死子")
        group, _ = board._group_and_liberties(x, y)
        removed.update(group)
    grid = [row[:] for row in board.grid]
    dead_black = sum(grid[y][x] == BLACK for x, y in removed)
    dead_white = len(removed) - dead_black
    for x, y in removed:
        grid[y][x] = EMPTY
    stones = {color: sum(value == color for row in grid for value in row)
              for color in (BLACK, WHITE)}
    territory: dict[tuple[int, int], int] = {}
    visited: set[tuple[int, int]] = set()
    for y in range(board.size):
        for x in range(board.size):
            if grid[y][x] != EMPTY or (x, y) in visited:
                continue
            region, borders = set(), set()
            stack = [(x, y)]
            while stack:
                point = stack.pop()
                if point in region:
                    continue
                region.add(point)
                for nx, ny in board.neighbors(*point):
                    value = grid[ny][nx]
                    if value == EMPTY:
                        if (nx, ny) not in region:
                            stack.append((nx, ny))
                    else:
                        borders.add(value)
            visited.update(region)
            owner = next(iter(borders)) if len(borders) == 1 else EMPTY
            territory.update((point, owner) for point in region)
    return AreaScore(
        stones[BLACK], stones[WHITE],
        sum(owner == BLACK for owner in territory.values()),
        sum(owner == WHITE for owner in territory.values()),
        sum(owner == EMPTY for owner in territory.values()), komi,
        dead_black, dead_white, frozenset(removed), territory,
    )


@dataclass(frozen=True)
class PositionEstimate:
    black_winrate: float
    black_lead: float
    black_points: int | None = None
    white_points: int | None = None
    uncertain_points: int | None = None

    @classmethod
    def from_response(cls, response: dict[str, Any], size: int) -> PositionEstimate:
        # The application configures all KataGo evaluation fields as BLACK.
        root = response.get("rootInfo", {})
        try:
            winrate, lead = float(root["winrate"]), float(root["scoreLead"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("KataGo 没有返回有效的胜率和目差") from error
        if not math.isfinite(lead) or not math.isfinite(winrate) or not 0 <= winrate <= 1:
            raise ValueError("KataGo 返回的形势数据不合法")
        ownership = response.get("ownership")
        black = white = uncertain = None
        if ownership is not None:
            if (len(ownership) != size * size
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                           or not -1 <= v <= 1 for v in ownership)):
                raise ValueError("KataGo 返回的归属数据不合法")
            black = sum(v >= 0.8 for v in ownership)
            white = sum(v <= -0.8 for v in ownership)
            uncertain = size * size - black - white
        return cls(winrate, lead, black, white, uncertain)

    def summary(self, human_color: str, turn: int) -> str:
        winrate = self.black_winrate if human_color == "B" else 1 - self.black_winrate
        lead = self.black_lead if human_color == "B" else -self.black_lead
        direction = "领先" if lead >= 0 else "落后"
        text = (f"第 {turn} 手 · KataGo 形势估计\n"
                f"你执{'黑' if human_color == 'B' else '白'}：胜率 {winrate:.1%}，"
                f"预计{direction} {abs(lead):.1f} 目（已含贴目）\n"
                f"黑方胜率 {self.black_winrate:.1%}；白方胜率 {1 - self.black_winrate:.1%}")
        if self.black_points is not None:
            text += (f"\n高置信度归属（含棋子）：黑 {self.black_points} 点、"
                     f"白 {self.white_points} 点、未定 {self.uncertain_points} 点")
        return text + "\n这是继续合理行棋后的估计，不是终局判决或双方实际胜率。"
