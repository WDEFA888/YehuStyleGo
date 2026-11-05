"""Summarize the owner's per-move loss distribution from cached KataGo analysis.

The profile is descriptive, not a rank calibration: it records how large the
approximate score loss was on the owner's own moves in games analyzed with the
current engine configuration. Sparring can use it to keep candidate moves inside
a similar error band instead of always playing the engine's best move.
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path

from yehu_style_go.engine import move_report
from yehu_style_go.models import GameRecord
from yehu_style_go.storage import Library

QUANTILE_KEYS = (5, 10, 25, 50, 75, 90, 95, 99)
MIN_MOVES = 30


def _quantile(values: list[float], fraction: float) -> float:
    position = max(0.0, min(1.0, fraction)) * (len(values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


@dataclass(frozen=True)
class StrengthProfile:
    player: str
    engine_key: str
    analyzed_games: int
    move_count: int
    average_loss: float
    quantiles: dict[str, float]

    def cap_for(self, fraction: float) -> float:
        """Loss cap at a quantile of the owner's own moves (0..1)."""
        target = max(0.0, min(1.0, fraction)) * 100
        points = sorted((float(key), value) for key, value in self.quantiles.items())
        if not points:
            return 0.0
        if target <= points[0][0]:
            return points[0][1]
        for (left_quantile, left), (right_quantile, right) in zip(points, points[1:]):
            if target <= right_quantile:
                if right_quantile == left_quantile:
                    return right
                mix = (target - left_quantile) / (right_quantile - left_quantile)
                return left + (right - left) * mix
        return points[-1][1]

    def summary(self) -> str:
        median = self.cap_for(0.5)
        return (f"棋力匹配档案：{self.analyzed_games} 盘、{self.move_count} 手本人落子；"
                f"中位损失 {median:.1f} 目，平均 {self.average_loss:.1f} 目/手")


def build_profile(
    games: list[GameRecord],
    library: Library,
    player: str,
    engine_key: str,
    cancel: threading.Event | None = None,
) -> StrengthProfile | None:
    """Read cached analyses only; never starts an engine or changes the library."""
    losses: list[float] = []
    analyzed = 0
    for game in games:
        if cancel is not None and cancel.is_set():
            raise ValueError("任务已取消")
        analysis = library.analysis(game.database_id, engine_key)
        if not analysis:
            continue
        analyzed += 1
        for row in move_report(game, analysis, player):
            loss = row["loss"]
            if math.isfinite(loss) and loss >= 0:
                losses.append(float(loss))
    if len(losses) < MIN_MOVES:
        return None
    losses.sort()
    return StrengthProfile(
        player=player,
        engine_key=engine_key,
        analyzed_games=analyzed,
        move_count=len(losses),
        average_loss=sum(losses) / len(losses),
        quantiles={str(key): _quantile(losses, key / 100) for key in QUANTILE_KEYS},
    )


def save_profile(path: Path, profile: StrengthProfile) -> None:
    payload = {
        "version": 1, "player": profile.player, "engine_key": profile.engine_key,
        "analyzed_games": profile.analyzed_games, "move_count": profile.move_count,
        "average_loss": profile.average_loss, "quantiles": profile.quantiles,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_profile(path: Path, player: str, engine_key: str) -> StrengthProfile | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        quantiles = {str(key): float(value) for key, value in payload["quantiles"].items()}
        profile = StrengthProfile(
            player=str(payload["player"]),
            engine_key=str(payload["engine_key"]),
            analyzed_games=int(payload["analyzed_games"]),
            move_count=int(payload["move_count"]),
            average_loss=float(payload["average_loss"]),
            quantiles=quantiles,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if profile.player != player or profile.engine_key != engine_key:
        return None
    if profile.move_count < MIN_MOVES or not profile.quantiles:
        return None
    if any(not math.isfinite(value) or value < 0 for value in profile.quantiles.values()):
        return None
    return profile
