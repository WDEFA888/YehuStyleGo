"""Drive real KataGo assessment and the two-pass scoring dialog in the Tk app."""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tkinter as tk
from tkinter import ttk

from exe_smoke import capture_window
from yehu_style_go.app import Application
from yehu_style_go.sgf import parse_sgf_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("screenshots", type=Path)
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    export = args.screenshots / "scored-game.sgf"
    if export.exists():
        raise FileExistsError("Use a fresh smoke output directory; no SGF will be overwritten")
    root = tk.Tk()
    app = Application(root)
    root.geometry("1280x900+30+30")
    result, errors = {}, []
    deadline = time.monotonic() + 180
    dialogs = []
    patcher = patch("yehu_style_go.app.messagebox.showinfo",
                    side_effect=lambda title, text, **kwargs: dialogs.append((title, text)))
    patcher.start()

    def capture(window, name):
        window.update_idletasks()
        user = ctypes.windll.user32
        user.GetParent.argtypes = [ctypes.c_void_p]
        user.GetParent.restype = ctypes.c_void_p
        capture_window(user.GetParent(window.winfo_id()), args.screenshots / name)

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    def invoke(text):
        button = next(w for w in walk(root)
                      if isinstance(w, ttk.Button) and w.cget("text") == text)
        button.invoke()

    def guard(function):
        def run():
            try:
                if time.monotonic() > deadline:
                    raise TimeoutError("Judgment desktop smoke timed out")
                function()
            except Exception as error:
                errors.append(repr(error))
                app.cancel.set()
                root.destroy()
        return run

    def wait(function):
        if app.busy:
            root.after(100, guard(lambda: wait(function)))
        else:
            root.after(150, guard(function))

    def start():
        app.tabs.select(app.spar_tab)
        app.use_engine_var.set(True)
        app.settings.validate()
        invoke("开始新对局")
        assert app.spar_active and app.spar_mode.startswith("KataGo")
        app.human_play(3, 3)
        wait(assess)

    def assess():
        assert len(app.spar_board.move_history) == 2
        result["before_assessment"] = len(app.spar_board.move_history)
        invoke("形势判断（KataGo）")
        wait(after_assess)

    def after_assess():
        assert app.spar_estimate is not None
        assert len(app.spar_board.move_history) == result["before_assessment"]
        assert app.spar_result == ""
        assert app.spar_estimate.uncertain_points is not None
        assert any(title.startswith("形势判断") for title, text in dialogs)
        result["black_winrate"] = app.spar_estimate.black_winrate
        result["black_lead"] = app.spar_estimate.black_lead
        result["ownership_points"] = (app.spar_estimate.black_points
                                      + app.spar_estimate.white_points
                                      + app.spar_estimate.uncertain_points)
        label = next(w for w in walk(root) if isinstance(w, ttk.Label)
                     and str(w.cget("textvariable")) == str(app.assessment_text))
        label.master.master.yview_moveto(1.0)
        root.after(200, guard(scoring))

    def scoring():
        capture(root, "assessment.png")
        # Controlled two-pass fixture: test the actual end handler and dialog,
        # without pretending that KataGo would pass in this opening position.
        app.spar_board.play("B", None, None)
        app.spar_board.play("W", None, None)
        app.spar_view.show(app.spar_board)
        assert app._check_end()
        root.after(200, guard(mark_dead))

    def mark_dead():
        dialog = app.scoring_dialog
        assert dialog.winfo_exists()
        dialog.toggle_dead(3, 3)
        assert dialog.score.dead_black == 1
        capture(dialog, "dead-marking.png")
        dialog.clear_dead()
        capture(dialog, "scoring.png")
        dialog.confirm()
        assert app.spar_result and app.spar_score is not None
        with patch("yehu_style_go.app.filedialog.asksaveasfilename", return_value=str(export)):
            invoke("导出 SGF 文件…")
        game = parse_sgf_file(export)
        assert game.result == app.spar_score.result
        assert len(game.moves) == 4
        result["scored_result"] = game.result
        result["export"] = str(export)
        capture(root, "finished.png")
        app.spar_active = False
        app.close()

    root.after(200, guard(start))
    try:
        root.mainloop()
    finally:
        patcher.stop()
    if errors:
        raise RuntimeError("; ".join(errors))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
