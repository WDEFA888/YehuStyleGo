from __future__ import annotations

import json
import math
import random
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from yehu_style_go.board import COLOR_TO_STONE, EMPTY, GoBoard, IllegalMove, gtp_to_xy
from yehu_style_go.models import GameRecord
from yehu_style_go.strength import StrengthProfile

FEATURE_NAMES = [
    "一线", "二线", "三线", "四线", "五线及以上", "靠近上一手", "远离上一手",
    "贴靠对方", "连接己方", "周围己方", "周围对方", "提子", "救一气棋块", "自紧气",
    "星位", "三三", "小目", "停一手",
]


def phase_for(turn: int, size: int = 19) -> int:
    scaled = turn * 361 / (size * size)
    return 0 if scaled < 40 else 1 if scaled < 140 else 2


def features(board: GoBoard, color: str, x: int | None, y: int | None) -> list[float]:
    values = [0.0] * len(FEATURE_NAMES)
    if x is None or y is None:
        values[-1] = 1.0
        return values
    own = COLOR_TO_STONE[color]
    enemy = 3 - own
    edge = min(x, y, board.size - 1 - x, board.size - 1 - y)
    values[min(edge, 4)] = 1.0
    last = board.move_history[-1] if board.move_history else None
    if last is not None and not last.is_pass:
        distance = abs(x - last.x) + abs(y - last.y)
        values[5] = math.exp(-distance / 3)
        values[6] = min(distance / max(board.size, 1), 1)
    near_own = near_enemy = 0
    captured = save = risky = 0
    checked = set()
    for nx, ny in board.neighbors(x, y):
        value = board.grid[ny][nx]
        if value == EMPTY:
            continue
        near_own += value == own
        near_enemy += value == enemy
        if (nx, ny) in checked:
            continue
        group, liberties = board._group_and_liberties(nx, ny)
        checked.update(group)
        if len(liberties) == 1 and (x, y) in liberties:
            if value == enemy:
                captured += len(group)
            else:
                save += len(group)
    values[7] = min(near_enemy, 2) / 2
    values[8] = min(near_own, 2) / 2
    friendly = hostile = 0
    for ny in range(max(0, y - 2), min(board.size, y + 3)):
        for nx in range(max(0, x - 2), min(board.size, x + 3)):
            friendly += board.grid[ny][nx] == own
            hostile += board.grid[ny][nx] == enemy
    values[9] = friendly / 24
    values[10] = hostile / 24
    values[11] = min(captured, 5) / 5
    values[12] = min(save, 5) / 5
    empty_neighbors = sum(board.grid[ny][nx] == EMPTY for nx, ny in board.neighbors(x, y))
    risky = int(empty_neighbors <= 1 and near_own == 0 and captured == 0)
    values[13] = risky
    near_x = min(x, board.size - 1 - x)
    near_y = min(y, board.size - 1 - y)
    values[14] = float(near_x == 3 and near_y == 3)
    values[15] = float(near_x == 2 and near_y == 2)
    values[16] = float(sorted((near_x, near_y)) == [2, 3])
    return values


def pattern(board: GoBoard, color: str, x: int | None, y: int | None) -> str:
    if x is None or y is None:
        return "pass"
    own = COLOR_TO_STONE[color]
    chars = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < board.size and 0 <= ny < board.size):
                chars.append("#")
            elif board.grid[ny][nx] == EMPTY:
                chars.append(".")
            else:
                chars.append("X" if board.grid[ny][nx] == own else "O")
    # Keep orientation: this small model deliberately learns the player's directional preferences.
    return "".join(chars)


@dataclass
class StyleModel:
    player: str
    game_count: int = 0
    move_count: int = 0
    weights: list[list[float]] = field(
        default_factory=lambda: [[0.0] * len(FEATURE_NAMES) for _ in range(3)]
    )
    position_counts: dict[str, int] = field(default_factory=dict)
    pattern_counts: dict[str, int] = field(default_factory=dict)
    phase_counts: list[int] = field(default_factory=lambda: [0, 0, 0])
    statistics: dict[str, Any] = field(default_factory=dict)
    trained_hashes: list[str] = field(default_factory=list)

    def save(self, path: Path) -> None:
        payload = {
            "version": 1, "player": self.player, "game_count": self.game_count,
            "move_count": self.move_count, "weights": self.weights,
            "position_counts": self.position_counts, "pattern_counts": self.pattern_counts,
            "phase_counts": self.phase_counts, "statistics": self.statistics,
            "trained_hashes": self.trained_hashes,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> StyleModel:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("version", None)
        return cls(**payload)

    def score(self, board: GoBoard, color: str, x: int | None, y: int | None) -> float:
        phase = phase_for(len(board.move_history), board.size)
        vector = features(board, color, x, y)
        learned = sum(w * f for w, f in zip(self.weights[phase], vector))
        key = f"{board.size}:{phase}:{x}:{y}"
        location = math.log1p(self.position_counts.get(key, 0))
        local = math.log1p(self.pattern_counts.get(f"{phase}:{pattern(board, color, x, y)}", 0))
        return learned + (0.45 if phase == 0 else 0.12) * location + 0.08 * local

    def report_text(self) -> str:
        stats = self.statistics
        lines = [
            f"棋手：{self.player}",
            f"训练数据：{self.game_count} 盘，{self.move_count} 手本人落子",
            "方法：分阶段落子特征排序 + 位置/局部模式频率（非从零训练围棋神经网络）",
            "",
        ]
        if self.game_count < 20:
            lines.append("⚠ 样本不足 20 盘：当前仅为初步偏好，不代表稳定棋风或真实棋力。")
        lines.extend([
            f"已知胜负：{stats.get('wins', 0)} 胜 / {stats.get('losses', 0)} 负；"
            f"未知或和棋 {stats.get('unknown', 0)} 盘",
            f"执黑 {stats.get('black_games', 0)} 盘；执白 {stats.get('white_games', 0)} 盘",
            f"贴靠对方比例：{stats.get('contact_ratio', 0):.1%}",
            f"离上一手曼哈顿距离不超过 4 的比例：{stats.get('local_ratio', 0):.1%}",
            "（这些比例是描述统计，不等同于战斗力或棋风标签。）",
            "",
            "各阶段学习到的偏好（相对采样的合法候选手）：",
        ])
        for phase, (label, weights) in enumerate(zip(("布局", "中盘", "后半盘"), self.weights)):
            if self.phase_counts[phase] == 0:
                lines.append(f"{label}：没有该阶段的本人落子，无法推断偏好")
                continue
            preferred = sorted(range(len(weights)), key=lambda i: weights[i], reverse=True)[:4]
            lines.append(f"{label}（{self.phase_counts[phase]} 手）："
                         + "、".join(FEATURE_NAMES[index] for index in preferred))
        lines.extend([
            "", "可信度说明：目前未做跨对局留出验证，不显示虚构的“模仿准确率”。",
            "增加训练盘数可以改善偏好估计，但仍不保证复现所有判断与失误。",
        ])
        return "\n".join(lines)


def train_style(
    games: list[GameRecord],
    player: str,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> StyleModel:
    selected = [game for game in games if game.color_for(player) is not None]
    if not selected:
        raise ValueError("没有包含该棋手的棋谱")
    model = StyleModel(player)
    rng = random.Random(1847)
    location_counts: Counter[str] = Counter()
    pattern_counts: Counter[str] = Counter()
    stats = {"wins": 0, "losses": 0, "unknown": 0, "black_games": 0, "white_games": 0}
    contact = local = 0
    for game_index, game in enumerate(selected):
        if cancel and cancel.is_set():
            raise ValueError("任务已取消")
        color = game.color_for(player)
        stats["black_games" if color == "B" else "white_games"] += 1
        won = game.did_player_win(player)
        stats["unknown" if won is None else "wins" if won else "losses"] += 1
        board = GoBoard.from_game(game, 0)
        for turn, move in enumerate(game.moves):
            if move.color == color:
                try:
                    board.clone().play(color, move.x, move.y)
                except IllegalMove as error:
                    raise ValueError(f"{game.source_path} 第 {turn + 1} 手：{error}") from error
                phase = phase_for(turn, board.size)
                positive = features(board, color, move.x, move.y)
                contact += positive[7] > 0
                local += positive[5] >= math.exp(-4 / 3)
                location_counts[f"{board.size}:{phase}:{move.x}:{move.y}"] += 1
                pattern_counts[f"{phase}:{pattern(board, color, move.x, move.y)}"] += 1
                model.phase_counts[phase] += 1
                model.move_count += 1
                # Sampled pairwise logistic ranking, trained only on the account owner's moves.
                negatives: list[list[float]] = []
                for _ in range(36):
                    nx, ny = rng.randrange(board.size), rng.randrange(board.size)
                    if (nx, ny) == (move.x, move.y) or board.grid[ny][nx] != EMPTY:
                        continue
                    if board.is_legal(color, nx, ny):
                        negatives.append(features(board, color, nx, ny))
                    if len(negatives) >= 6:
                        break
                weights = model.weights[phase]
                rate = 0.045 / math.sqrt(1 + model.phase_counts[phase] / 350)
                for negative in negatives:
                    delta = [p - n for p, n in zip(positive, negative)]
                    margin = sum(w * d for w, d in zip(weights, delta))
                    gradient = 1 / (1 + math.exp(max(-30, min(30, margin))))
                    for i, difference in enumerate(delta):
                        weights[i] += rate * (gradient * difference - 0.003 * weights[i])
            try:
                board.play(move.color, move.x, move.y)
            except IllegalMove as error:
                raise ValueError(f"{game.source_path} 第 {turn + 1} 手：{error}") from error
        model.game_count += 1
        model.trained_hashes.append(game.sha256)
        if progress:
            progress(game_index + 1, len(selected))
    if model.move_count == 0:
        raise ValueError("这些棋谱中没有本人落子，无法训练")
    model.position_counts = dict(location_counts)
    model.pattern_counts = dict(pattern_counts)
    model.statistics = {
        **stats, "contact_ratio": contact / max(1, model.move_count),
        "local_ratio": local / max(1, model.move_count),
    }
    return model


def choose_move(
    board: GoBoard,
    color: str,
    model: StyleModel,
    analysis: dict[str, Any] | None = None,
    imitation: float = 0.75,
    strength: float = 0.5,
    rng: random.Random | None = None,
    profile: StrengthProfile | None = None,
) -> tuple[int | None, int | None]:
    rng = rng or random.Random()
    if analysis and analysis.get("moveInfos"):
        return _choose_among_engine_candidates(board, color, model, analysis,
                                               imitation, strength, rng, profile)
    return _choose_fallback(board, color, model, imitation, strength, rng)


def _candidate_losses(
    scored: list[tuple[tuple[int | None, int | None], int, float, dict[str, Any]]],
    color: str,
) -> dict[tuple[int | None, int | None], float] | None:
    """Approximate score loss of each engine candidate against the top move.

    KataGo reports per-move ``scoreLead`` from Black's perspective because our
    config sets ``reportAnalysisWinratesAs = BLACK``. Missing or non-finite values
    disable matching instead of inventing losses from winrate deltas.
    """
    sign = 1 if color == "B" else -1
    best: float | None = None
    for _, _, _, info in scored:  # sorted by engine order, so the first finite value is the top move
        value = info.get("scoreLead")
        if isinstance(value, (int, float)) and math.isfinite(value):
            best = float(value)
            break
    if best is None:
        return None
    losses = {}
    for point, _, _, info in scored:
        value = info.get("scoreLead")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        losses[point] = max(0.0, (best - float(value)) * sign)
    return losses


def _choose_by_profile(
    scored: list[tuple[tuple[int | None, int | None], int, float, dict[str, Any]]],
    color: str,
    imitation: float,
    strength: float,
    rng: random.Random,
    profile: StrengthProfile,
) -> tuple[int | None, int | None] | None:
    losses = _candidate_losses(scored, color)
    if losses is None:
        return None
    # Higher strength narrows both the engine candidate pool and the sampled
    # quantile; 0.5 reproduces the owner's empirical loss distribution.
    max_order = round(2 + (1 - strength) * 26)
    pool = [item for item in scored if item[1] <= max_order] or scored[:1]
    factor = max(0.15, 1 - (strength - 0.5) * 1.4)
    quantile = max(0.0, min(0.99, rng.random() * factor))
    target = profile.cap_for(quantile)
    band = [item for item in pool
            if abs(losses[item[0]] - target) <= max(1.0, target * 0.5)]
    if not band:
        # Candidate losses are discrete; fall back to the same quantile of the
        # candidate loss ranking rather than silently playing the engine's best.
        ordered = sorted(pool, key=lambda item: (losses[item[0]], -item[2], item[1]))
        index = min(len(ordered) - 1, int(quantile * len(ordered)))
        aim = losses[ordered[index][0]]
        band = [item for item in ordered
                if abs(losses[item[0]] - aim) <= max(0.5, aim * 0.25)]
    closeness = [math.exp(-abs(losses[item[0]] - target) / max(1.0, target * 0.5))
                 for item in band]
    closeness_max = max(closeness)
    style_scores = [item[2] for item in band]
    style_max = max(style_scores)
    weights = []
    for (point, _, style, _), near in zip(band, closeness):
        style_weight = math.exp((style - style_max) / 0.5)
        blend = (1 - imitation) * (near / closeness_max) + imitation * style_weight
        weights.append(max(1e-12, blend))
    return rng.choices([item[0] for item in band], weights=weights, k=1)[0]


def _choose_among_engine_candidates(
    board: GoBoard,
    color: str,
    model: StyleModel,
    analysis: dict[str, Any],
    imitation: float,
    strength: float,
    rng: random.Random,
    profile: StrengthProfile | None = None,
) -> tuple[int | None, int | None]:
    """Pick only from KataGo's searched candidates, ranked by the engine's own
    ``order`` (0 is best). Personal style breaks ties among the top moves instead
    of promoting arbitrary legal points or low-visit search noise to blunders.

    When a strength profile is available, the accepted error band comes from the
    owner's own analyzed moves instead of only the candidate rank."""
    scored: list[tuple[tuple[int | None, int | None], int, float, dict[str, Any]]] = []
    for info in analysis["moveInfos"]:
        try:
            point = gtp_to_xy(info["move"], board.size)
        except ValueError:
            continue
        if not board.is_legal(color, *point):
            continue
        order = info.get("order", 10**9)
        scored.append((point, order, model.score(board, color, *point), info))
    if not scored:
        return None, None
    scored.sort(key=lambda item: item[1])
    if profile is not None:
        chosen = _choose_by_profile(scored, color, imitation, strength, rng, profile)
        if chosen is not None:
            return chosen
    # Higher strength keeps a narrower set of top engine moves.
    max_order = round(2 + (1 - strength) * 26)
    survivors = [item for item in scored if item[1] <= max_order]
    if not survivors:
        survivors = scored[:1]
    style_scores = [item[2] for item in survivors]
    style_max = max(style_scores)
    weights = []
    for point, order, style, _ in survivors:
        quality = math.exp(-order * 0.6)
        style_weight = math.exp((style - style_max) / 0.5)
        blend = (1 - imitation) * quality + imitation * style_weight
        # Strength also sharpens the choice among the accepted candidates.
        weights.append(blend ** (1 + 2 * strength))
    return rng.choices([item[0] for item in survivors], weights=weights, k=1)[0]


def _choose_fallback(
    board: GoBoard,
    color: str,
    model: StyleModel,
    imitation: float,
    strength: float,
    rng: random.Random,
) -> tuple[int | None, int | None]:
    # The fallback is explicitly a lightweight sparring bot, not a KataGo replacement.
    candidates = []
    points = [(x, y) for y in range(board.size) for x in range(board.size)
              if board.grid[y][x] == EMPTY] + [(None, None)]
    for x, y in points:
        if not board.is_legal(color, x, y):
            continue
        vector = features(board, color, x, y)
        own_eye = False
        if x is not None and y is not None:
            own = COLOR_TO_STONE[color]
            neighbors = list(board.neighbors(x, y))
            own_eye = all(board.grid[ny][nx] == own for nx, ny in neighbors)
        baseline = 1.2 * vector[11] + 0.7 * vector[12] - vector[13] - 1.7 * own_eye
        if len(board.move_history) < 40:
            baseline += 0.6 * vector[3] + 0.3 * vector[2] + 0.3 * vector[14]
        else:
            baseline += 0.35 * vector[5] + 0.2 * vector[7]
        style_score = model.score(board, color, x, y)
        score = (1 - imitation) * baseline + imitation * style_score + 0.25 * baseline
        if x is None:
            score -= 6 if len(board.move_history) < board.size * board.size * 0.45 else 0.6
            if board.move_history and board.move_history[-1].is_pass:
                score += 1.0
        candidates.append(((x, y), score))
    if not candidates:
        return None, None
    maximum = max(score for _, score in candidates)
    temperature = 0.12 + (1 - strength) * 0.35
    probabilities = [math.exp((score - maximum) / temperature) for _, score in candidates]
    return rng.choices([point for point, _ in candidates], weights=probabilities, k=1)[0]
