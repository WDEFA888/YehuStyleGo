from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yehu_style_go.models import GameRecord
from yehu_style_go.sgf import parse_sgf_file
from yehu_style_go.style import StyleModel

LEGEND_INDEX = "legend-styles.json"
SELF_STYLE = "我的棋风"


def model_filename(name: str) -> str:
    digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()[:16]
    return f"legend-{digest}.json"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def load_index(directory: Path) -> list[dict[str, Any]]:
    path = directory / LEGEND_INDEX
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, list):
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        filename = Path(str(item.get("model", ""))).name
        if not name or not filename or name in seen:
            continue
        seen.add(name)
        entries.append({
            "name": name,
            "model": filename,
            "folder": str(item.get("folder", "")),
            "game_count": _int(item.get("game_count", 0)),
            "move_count": _int(item.get("move_count", 0)),
            "updated_at": str(item.get("updated_at", "")),
        })
    entries.sort(key=lambda item: item["name"].casefold())
    return entries


def save_index(directory: Path, entries: list[dict[str, Any]]) -> None:
    path = directory / LEGEND_INDEX
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def register(directory: Path, model: StyleModel, folder: Path,
             entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    model.save(directory / model_filename(model.player))
    updated = [item for item in entries if item.get("name") != model.player]
    updated.append({
        "name": model.player,
        "model": model_filename(model.player),
        "folder": str(folder),
        "game_count": model.game_count,
        "move_count": model.move_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    updated.sort(key=lambda item: item["name"].casefold())
    save_index(directory, updated)
    return updated


def load_model(directory: Path, entry: dict[str, Any]) -> StyleModel:
    filename = Path(str(entry["model"])).name
    return StyleModel.load(directory / filename)


def remove(directory: Path, entry: dict[str, Any],
           entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    model = directory / Path(str(entry["model"])).name
    try:
        model.unlink(missing_ok=True)
    except OSError:
        pass
    updated = [item for item in entries if item.get("name") != entry.get("name")]
    save_index(directory, updated)
    return updated


def collect_games(paths: list[Path], player: str) -> tuple[list[GameRecord], list[str]]:
    games: list[GameRecord] = []
    errors: list[str] = []
    seen: set[str] = set()
    for path in paths:
        try:
            game = parse_sgf_file(path)
        except (OSError, ValueError, RecursionError) as error:
            errors.append(f"{path.name}: {error}")
            continue
        if game.color_for(player) is not None and game.sha256 not in seen:
            seen.add(game.sha256)
            games.append(game)
    return games, errors
