"""Drive the real Tk window through import, replay, training and a sparring turn."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ctypes
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from exe_smoke import capture_window

from yehu_style_go.app import Application
from yehu_style_go.board import GoBoard
from yehu_style_go.sgf import parse_sgf_file
from yehu_style_go.storage import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sgf", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--player", default="")
    parser.add_argument("--screenshots", type=Path, required=True)
    parser.add_argument("--require-analysis", action="store_true")
    parser.add_argument("--minimum-games", type=int, default=1)
    parser.add_argument("--engine-sparring", action="store_true")
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    library = Library(args.data_dir)
    if not library.setting("player"):
        if not args.player:
            raise SystemExit("请用 --player 指定测试账号，或先在影棋设置中保存用户名")
        library.set_setting("player", args.player)
    app = Application(root, library)
    root.geometry("1200x820+30+30")
    errors = []
    result = {}
    deadline = time.monotonic() + 120

    def guard(function):
        def wrapped():
            try:
                if time.monotonic() > deadline:
                    raise TimeoutError("GUI smoke timed out")
                function()
            except Exception as error:
                errors.append(repr(error))
                app.cancel.set()
                root.destroy()
        return wrapped

    def wait(next_step):
        if app.busy:
            root.after(100, guard(lambda: wait(next_step)))
        else:
            root.after(150, guard(next_step))

    def screenshot(name):
        root.update_idletasks()
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        hwnd = user32.GetParent(root.winfo_id())
        capture_window(hwnd, args.screenshots / name)

    def start():
        with patch("yehu_style_go.app.filedialog.askopenfilenames", return_value=[str(args.sgf)]):
            app.import_files()
        wait(replay)

    def replay():
        assert len(app.games) >= args.minimum_games, "Owner library is smaller than expected"
        target_hash = parse_sgf_file(args.sgf).sha256
        target = next(game for game in app.games if game.sha256 == target_hash)
        app.game_table.selection_set(str(target.database_id))
        app.open_selected()
        game = app.selected_game
        app.set_turn(len(game.moves))
        reference = GoBoard.from_game(game)
        assert app.review_board.board._position_key() == reference._position_key()
        assert game.color_for(app.player) in {"B", "W"}
        result.update(player=app.player, moves=len(game.moves), komi=game.komi,
                      game_count=len(app.games))
        root.after(200, guard(after_replay))

    def after_replay():
        app.set_turn(len(app.selected_game.moves))
        assert app.review_board.board._position_key() == GoBoard.from_game(app.selected_game)._position_key()
        root.update_idletasks()
        assert app.chart.winfo_rooty() + app.chart.winfo_height() < root.winfo_rooty() + root.winfo_height()
        assert app.review_board.winfo_height() >= 350, "Review board has been squeezed by controls"
        screenshot("review.png")
        if args.require_analysis:
            assert app.selected_analysis is not None, "Real engine cache is missing"
            assert len(app.chart.points) == len(app.selected_game.moves) + 1
            assert app.rows, "Owner analysis table is empty"
            row = max(app.rows, key=lambda item: item["loss"])
            app.move_table.selection_set(str(row["turn"]))
            app.select_report_move()
            app.show_variation()
            assert "推荐变化" in app.turn_text.get()
            screenshot("variation.png")
            app.set_turn(len(app.selected_game.moves))
            result["analyzed_positions"] = len(app.chart.points)
        app.train()
        wait(profile)

    def profile():
        assert app.model is not None
        assert app.model.move_count == sum(
            move.color == game.color_for(app.player) for game in app.games for move in game.moves
        )
        result["trained_moves"] = app.model.move_count
        root.after(200, guard(start_spar))

    def start_spar():
        screenshot("profile.png")
        app.tabs.select(app.spar_tab)
        app.use_engine_var.set(args.engine_sparring)
        if args.engine_sparring:
            app.settings.validate()
        with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
            app.new_spar()
        assert app.spar_active
        if args.engine_sparring:
            assert app.spar_mode.startswith("KataGo"), "Engine mode silently fell back"
        result["sparring_mode"] = app.spar_mode
        app.human_play(3, 3)
        wait(spar_done)

    def spar_done():
        assert len(app.spar_board.move_history) == 2
        assert app.spar_board.to_play == app.human_color
        screenshot("sparring.png")
        app.undo()
        assert len(app.spar_board.move_history) == 0
        app.human_play(15, 3)
        wait(save)

    def save():
        export = args.screenshots / "smoke-sparring.sgf"
        with patch("yehu_style_go.app.filedialog.asksaveasfilename", return_value=str(export)):
            app.save_spar()
        assert export.is_file()
        result["sparring_moves"] = len(app.spar_board.move_history)
        result["screenshots"] = str(args.screenshots)
        app.tabs.select(app.library_tab)
        root.after(200, guard(finish))

    def finish():
        screenshot("library.png")
        app.spar_active = False
        app.close()

    root.after(150, guard(start))
    root.mainloop()
    if errors:
        raise RuntimeError("; ".join(errors))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
