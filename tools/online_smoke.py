"""Drive the real online panel: host, register, create, join, play and score."""
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
from yehu_style_go.online_client import OnlineClient
from yehu_style_go.storage import Library


def main() -> None:
    screenshots = Path(sys.argv[1] if len(sys.argv) > 1 else "assets/smoke/online")
    screenshots.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="yehu-online-"))
    library = Library(data_dir / "db")
    library.set_setting("player", "测试棋手")
    root = tk.Tk()
    app = Application(root, library)
    root.geometry("1280x900+30+30")
    panel = app.online_panel
    guest: OnlineClient | None = None
    result: dict[str, object] = {}

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    def invoke(text: str) -> None:
        next(w for w in walk(panel) if isinstance(w, ttk.Button)
             and w.cget("text") == text).invoke()

    def wait_for(predicate, timeout: float = 25.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                root.update()
                return
            time.sleep(0.05)
        raise TimeoutError("Online smoke timed out")

    def capture(name: str) -> None:
        root.update_idletasks()
        user = ctypes.windll.user32
        user.GetParent.argtypes = [ctypes.c_void_p]
        user.GetParent.restype = ctypes.c_void_p
        capture_window(user.GetParent(root.winfo_id()), screenshots / name)

    app.tabs.select(app.online_tab)
    invoke("本机开房（启动服务器）")
    wait_for(lambda: panel.client is not None and panel.embedded_server is not None
             and not panel.pending)
    panel.username_var.set("HostUser")
    panel.password_var.set("secret1")
    panel.confirm_var.set("secret1")
    invoke("注册新账号")
    wait_for(lambda: bool(panel.token) and not panel.pending)
    result["host_uid"] = panel.account["uid"]
    panel.size_var.set("9")
    invoke("创建房间")
    wait_for(lambda: bool(panel.room) and panel.room["status"] == "waiting"
             and not panel.pending)
    code = panel.room["room"]
    host_color = panel.room["viewer_color"]
    result["room"] = code
    result["host_color"] = host_color

    guest = OnlineClient(f"127.0.0.1:{panel.embedded_port}")
    guest_account = guest.request("register", username="GuestUser", password="secret2")
    result["guest_uid"] = guest_account["account"]["uid"]
    guest.request("join_room", token=guest_account["token"], room=code)
    wait_for(lambda: panel.room and panel.room["status"] == "playing")

    if host_color == "B":
        panel._board_click(2, 2)
    else:
        guest.request("move", token=guest_account["token"], x=2, y=2)
    wait_for(lambda: panel.room and panel.room["move_count"] == 1 and not panel.pending)
    if host_color == "B":
        guest.request("move", token=guest_account["token"], x=6, y=6)
    else:
        panel._board_click(6, 6)
    wait_for(lambda: panel.room and panel.room["move_count"] == 2 and not panel.pending)
    result["pass_state"] = str(panel.pass_button.cget("state"))
    result["resign_state"] = str(panel.resign_button.cget("state"))
    result["pending_at_playing"] = panel.pending
    capture("playing.png")

    invoke("停一手")
    wait_for(lambda: panel.room and panel.room["move_count"] == 3 and not panel.pending)
    guest.request("pass", token=guest_account["token"])
    wait_for(lambda: panel.room and panel.room["status"] == "scoring" and not panel.pending)
    capture("scoring.png")
    invoke("同意数子结果")
    wait_for(lambda: panel.room and len(panel.room["confirmed"]) == 1 and not panel.pending)
    guest.request("confirm_score", token=guest_account["token"])
    wait_for(lambda: panel.room and panel.room["status"] == "finished" and not panel.pending)
    capture("finished.png")
    result["result"] = panel.room["result"]
    result["move_count"] = panel.room["move_count"]
    result["host_uid_matches"] = panel.account["uid"] == result["host_uid"]
    result["guest_uid_matches"] = guest_account["account"]["uid"] == result["guest_uid"]

    guest.close()
    app.closed = True
    app.cancel.set()
    panel.shutdown()
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
