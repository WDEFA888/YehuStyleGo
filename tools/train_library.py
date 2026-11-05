"""Train the personal model from the latest imported owner games."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yehu_style_go.fox_sync import MAX_SYNC_GAMES
from yehu_style_go.storage import Library
from yehu_style_go.style import train_style


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_SYNC_GAMES:
        parser.error(f"--limit must be between 1 and {MAX_SYNC_GAMES}")
    library = Library()
    player = library.setting("player", "")
    if not player:
        raise SystemExit("请先在影棋设置中填写野狐用户名")
    games = library.games(player, args.limit)
    started = time.monotonic()
    def progress(done, total):
        if done == 1 or done % 10 == 0 or done == total:
            print(f"Training: {done}/{total} games", flush=True)
    model = train_style(games, player, progress)
    path = library.directory / ("style-" + hashlib.sha256(player.encode()).hexdigest()[:16] + ".json")
    model.save(path)
    print(json.dumps({"games": model.game_count, "owner_moves": model.move_count,
                      "phase_counts": model.phase_counts, "statistics": model.statistics,
                      "elapsed_seconds": time.monotonic() - started, "model_path": str(path)},
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
