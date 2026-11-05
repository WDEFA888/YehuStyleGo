"""Drive the real save-and-review workflow against a temporary library."""
from __future__ import annotations

import ctypes
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tkinter as tk
from tkinter import ttk

from exe_smoke import capture_window
from yehu_style_go.app import Application
from yehu_style_go.board import GoBoard
from yehu_style_go.storage import Library


def main() -> None:
    screenshots = Path(sys.argv[1] if len(sys.argv) > 1 else "assets/smoke/local-review")
    screenshots.mkdir(parents=True, exist_ok=True)
    game_dir = Path(tempfile.mkdtemp(prefix="yehu-local-"))
    library = Library(game_dir / "db")
    library.set_setting("player", "测试棋手")
    root = tk.Tk()
    app = Application(root, library)
    root.geometry("1280x900+30+30")
    user = ctypes.windll.user32
    result: dict[str, object] = {}

    def capture(window, name: str) -> None:
        window.update_idletasks()
        user.GetParent.argtypes = [ctypes.c_void_p]
        user.GetParent.restype = ctypes.c_void_p
        capture_window(user.GetParent(window.winfo_id()), screenshots / name)

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    def invoke(window, text: str) -> None:
        next(w for w in walk(window)
             if isinstance(w, ttk.Button) and w.cget("text") == text).invoke()

    def wait(seconds: float = 20.0) -> None:
        deadline = time.monotonic() + seconds
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.05)
        root.update()

    app.player = "测试棋手"
    app.spar_board = GoBoard(9)
    app.human_color = "B"
    app.spar_active = True
    app.spar_result = "B+R"
    for color, x, y in (("B", 2, 2), ("W", 6, 6), ("B", 2, 6)):
        app.spar_board.play(color, x, y)
    app.spar_view.show(app.spar_board)
    app.spar_saved = False
    app.tabs.select(app.spar_tab)
    root.update()

    invoke(root, "保存并复盘")
    wait()
    saved = list((library.directory / "local-games").glob("*.sgf"))
    assert len(saved) == 1, saved
    assert app.selected_game is not None
    assert app.tabs.tab(app.tabs.select(), "text") == "复盘分析"
    capture(root, "review.png")
    result["saved_file"] = str(saved[0])
    result["review_tab"] = True
    result["library_games"] = len(library.games("测试棋手", 10))

    invoke(root, "更新棋风")
    wait()
    assert app.model is not None
    assert app.model.move_count == 2, app.model.move_count
    capture(root, "updated.png")
    result["trained_games"] = app.model.game_count
    result["trained_owner_moves"] = app.model.move_count
    result["status"] = app.status.get()

    app.closed = True
    app.cancel.set()
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
