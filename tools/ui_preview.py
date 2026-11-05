"""Capture each tab and the main dialogs without changing personal data."""
from __future__ import annotations

import argparse
import ctypes
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
from yehu_style_go.theme import apply_theme


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("screenshots", type=Path)
    parser.add_argument("--geometry", default="1280x900+30+30")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = Application(root)
    root.geometry(args.geometry)
    if args.theme == "dark" and not app.dark_mode:
        app.dark_mode = True
        apply_theme(root, dark=True)
        app.theme_button_text.set("日间模式")
        app._apply_theme_colors()
    elif args.theme == "light" and app.dark_mode:
        app.dark_mode = False
        apply_theme(root, dark=False)
        app.theme_button_text.set("夜间模式")
        app._apply_theme_colors()
    user = ctypes.windll.user32

    def capture(window, name: str) -> None:
        for _ in range(4):
            root.update()
            time.sleep(0.15)
        user.GetParent.argtypes = [ctypes.c_void_p]
        user.GetParent.restype = ctypes.c_void_p
        capture_window(user.GetParent(window.winfo_id()), args.screenshots / name)

    def settle(timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.05)
        root.update()

    for index, name in enumerate(("home", "library", "review", "profile")):
        app.tabs.select(app.tabs.tabs()[index])
        capture(root, f"{name}.png")

    app.tabs.select(app.spar_tab)
    app.size_var.set("9")
    app.use_engine_var.set(False)
    with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
        app.new_spar()
    app.human_play(2, 2)
    settle()
    app.human_play(6, 6)
    settle()
    capture(root, "spar.png")
    app.control_canvas.yview_moveto(1.0)
    capture(root, "spar-save.png")
    app.control_canvas.yview_moveto(0.0)

    app.open_trial()
    capture(app.trial_window, "trial.png")
    app.trial_window.destroy()

    with patch("yehu_style_go.app.messagebox.askyesno", return_value=True):
        app.score_spar()
    capture(app.scoring_dialog, "scoring.png")
    app.scoring_dialog.close()

    app.tabs.select(app.online_tab)
    capture(root, "online.png")
    app.tabs.select(app.settings_tab)
    capture(root, "settings.png")

    app.trial_window = None
    app.scoring_dialog = None
    app.spar_active = False
    app.spar_pending_score = False
    app.spar_saved = True
    app.closed = True
    app.cancel.set()
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()


if __name__ == "__main__":
    main()
