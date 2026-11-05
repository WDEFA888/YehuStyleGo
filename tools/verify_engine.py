"""Exercise the installed real engine, cache imported games and persist tested settings."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yehu_style_go.board import GoBoard, xy_to_gtp
from yehu_style_go.engine import EngineSettings, KataGo, move_report
from yehu_style_go.storage import Library
from yehu_style_go.style import StyleModel, choose_move


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--visits", type=int, default=120)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    settings = EngineSettings(manifest["executable"], manifest["model_path"], args.visits)
    library = Library(args.data_dir)
    player = library.setting("player", "")
    if not player:
        raise SystemExit("请先在影棋设置中填写野狐用户名")
    started = time.monotonic()
    reports = []
    games = library.games(player, args.limit)
    for game in games:
        GoBoard.from_game(game)
    print(f"Replay preflight passed for {len(games)} games", flush=True)
    with KataGo(settings, library.directory / "engine-work") as engine:
        print("Starting real KataGo GPU test; first run may tune OpenCL kernels...", flush=True)
        request = engine.position_request([])
        request["maxVisits"] = 8
        test = engine.query(request, timeout=600)[0]
        assert test["moveInfos"], "Engine returned no candidates"
        assert len(test["policy"]) == 362, "Unexpected 19x19 policy shape"
        print(f"Real position test passed in {time.monotonic() - started:.1f}s", flush=True)
        print("\n".join(engine.errors), flush=True)
        library.set_setting("engine", asdict(settings))
        for index, game in enumerate(games):
            print(f"Analyzing game {index + 1}/{len(games)}, {len(game.moves) + 1} positions...", flush=True)
            last = [-1]
            def progress(done, total):
                bucket = int(done / max(1, total) * 10)
                if bucket != last[0]:
                    print(f"  {done}/{total} positions", flush=True)
                    last[0] = bucket
            payload = library.analysis(game.database_id, settings.key)
            if not payload:
                payload = engine.analyze_game(game, progress)
                library.save_analysis(game.database_id, settings.key, payload)
            rows = move_report(game, payload, player)
            assert len(rows) == sum(move.color == game.color_for(player) for move in game.moves)
            reports.append({
                "game_id": game.database_id, "date": game.played_at,
                "positions": len(payload["positions"]), "owner_moves": len(rows),
                "average_loss": sum(row["loss"] for row in rows) / max(1, len(rows)),
                "large_losses": sorted(rows, key=lambda row: row["loss"], reverse=True)[:5],
            })
        board = GoBoard(19)
        board.play("B", 3, 3)
        response = engine.analyze_position(board.move_history)
        move = choose_move(board, "W", StyleModel(player), response)
        assert board.is_legal("W", *move)
        print(f"Engine-assisted sparring returned a legal move: {xy_to_gtp(*move)}", flush=True)
    report_path = library.directory / "verified-engine-report.json"
    report = {"engine": asdict(settings), "engine_key": settings.key,
              "elapsed_seconds": time.monotonic() - started, "games": reports}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "analyzed_games": len(reports),
                      "elapsed_seconds": report["elapsed_seconds"]}, indent=2))


if __name__ == "__main__":
    main()
