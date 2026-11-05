"""Drive the real modeless trial window, using only the app's private test game."""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("screenshots", type=Path)
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = Application(root)
    root.geometry("1280x900+30+30")
    result, errors = {}, []
    deadline = time.monotonic() + 120

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    def invoke(window, text):
        next(w for w in walk(window) if isinstance(w, ttk.Button) and w.cget("text") == text).invoke()

    def capture(window, name):
        window.update_idletasks()
        user = ctypes.windll.user32
        user.GetParent.argtypes = [ctypes.c_void_p]
        user.GetParent.restype = ctypes.c_void_p
        capture_window(user.GetParent(window.winfo_id()), args.screenshots / name)

    def snapshot():
        b = app.spar_board
        return (b._position_key(), tuple(b.move_history), dict(b.captures), b.to_play,
                set(b._positions), b._previous_position, app.spar_saved, app.spar_result)

    def guard(function):
        def run():
            try:
                if time.monotonic() > deadline:
                    raise TimeoutError("Trial desktop smoke timed out")
                function()
            except Exception as error:
                errors.append(repr(error))
                app.cancel.set()
                root.destroy()
        return run

    def wait(function):
        if app.busy:
            root.after(50, guard(lambda: wait(function)))
        else:
            root.after(150, guard(function))

    def start():
        app.tabs.select(app.spar_tab)
        app.size_var.set("9")
        app.use_engine_var.set(False)
        with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
            invoke(root, "开始新对局")
        app.human_play(2, 2)
        wait(open_trial)

    def open_trial():
        assert len(app.spar_board.move_history) == 2
        invoke(root, "试下 / 变化推演")
        root.after(200, guard(try_moves))

    def try_moves():
        window = app.trial_window
        before = snapshot()
        assert window.grab_current() is None, "Trial must not block real play"
        for _ in range(3):
            x, y = window.session.board.legal_moves(window.session.board.to_play, include_pass=False)[0]
            ox, oy = window.view.origin
            window.view.event_generate("<Button-1>", x=round(ox + x * window.view.spacing),
                                       y=round(oy + y * window.view.spacing))
        assert window.session.cursor == 3
        assert snapshot() == before
        capture(window, "trial.png")
        invoke(window, "悔棋")
        assert window.session.cursor == 2
        invoke(window, "重做")
        assert window.session.cursor == 3
        assert snapshot() == before
        result.update(origin_turn=window.session.origin_turn, tried_moves=window.session.cursor,
                      real_moves_after_trial=len(app.spar_board.move_history), undo_redo=True)
        # Deliberately continue the private formal test game while the modeless
        # window remains open. It must keep the original trial snapshot.
        x, y = app.spar_board.legal_moves(app.human_color, include_pass=False)[0]
        app.human_play(x, y)
        wait(reload)

    def reload():
        window = app.trial_window
        assert len(app.spar_board.move_history) == 4
        assert window.session.origin_turn == 2
        before = snapshot()
        with patch("yehu_style_go.trial_window.messagebox.askyesno", return_value=True):
            invoke(window, "取最新实战局面")
        assert window.session.origin_turn == 4
        assert window.session.cursor == 0
        assert window.session.board._position_key() == app.spar_board._position_key()
        window.color_var.set("白棋")
        invoke(window, "停一手")
        assert window.session.moves[-1].color == "W"
        with patch("yehu_style_go.trial_window.messagebox.askyesno", return_value=True):
            invoke(window, "重置试下")
        assert window.session.cursor == 0
        assert snapshot() == before
        capture(window, "refreshed.png")
        invoke(window, "关闭试下，返回实战")
        assert snapshot() == before
        result.update(latest_origin=4, real_moves_at_close=4, formal_game_unchanged_by_trial=True)
        capture(root, "main.png")
        app.tabs.select(app.settings_tab)
        root.update()
        time.sleep(0.3)
        root.update()
        capture(root, "settings.png")
        with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
            app.close()

    root.after(200, guard(start))
    root.mainloop()
    if errors:
        raise RuntimeError("; ".join(errors))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
