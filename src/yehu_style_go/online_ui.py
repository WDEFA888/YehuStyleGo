"""Online lobby and board panel for the desktop app."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from yehu_style_go import theme
from yehu_style_go.board import GoBoard
from yehu_style_go.online_client import OnlineClient, OnlineClientError
from yehu_style_go.online_server import OnlineServer, OnlineService, local_addresses
from yehu_style_go.widgets import BoardView


class OnlinePanel(ttk.Frame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent)
        self.app = app
        self.root = app.root
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.client: OnlineClient | None = None
        self.token = ""
        self.account: dict[str, Any] | None = None
        self.room: dict[str, Any] | None = None
        self.pending = False
        self.embedded_server: OnlineServer | None = None
        self.embedded_thread: threading.Thread | None = None
        self.embedded_port = 0
        self.share_address = ""
        self._drain_id: str | None = None
        self._build()
        self._drain_id = self.root.after(100, self._drain_events)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)
        outer, inner = self.app._bordered(self)
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, 22))
        self.board_view = BoardView(inner, self._board_click, hover=self._ghost_color)
        self.board_view.pack(fill="both", expand=True)

        host = ttk.Frame(self)
        host.grid(row=0, column=1, sticky="nsew")
        self.control_canvas = tk.Canvas(host, background=theme.SURFACE,
                                        highlightthickness=0, width=420)
        self.app._themed.append((self.control_canvas, "surface_canvas"))
        scroll = ttk.Scrollbar(host, orient="vertical", command=self.control_canvas.yview)
        self.control_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.control_canvas.pack(fill="both", expand=True)
        controls = ttk.Frame(self.control_canvas, padding=(2, 2, 12, 6))
        window = self.control_canvas.create_window((0, 0), window=controls, anchor="nw")
        controls.bind("<Configure>", lambda event: self.control_canvas.configure(
            scrollregion=self.control_canvas.bbox("all")))
        self.control_canvas.bind("<Configure>", lambda event: self.control_canvas.itemconfigure(
            window, width=event.width))
        self.control_canvas.bind("<MouseWheel>", lambda event: self.control_canvas.yview_scroll(
            -int(event.delta / 120), "units"))

        def section(text: str) -> None:
            ttk.Separator(controls, orient="horizontal").pack(fill="x", pady=(14, 8))
            ttk.Label(controls, text=text, style="Section.TLabel").pack(anchor="w", pady=(0, 7))

        ttk.Label(controls, text="联机对弈", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(controls, text="账号、UID 和对局都由你选择的服务器管理；不连接野狐。",
                  style="Muted.TLabel", wraplength=390).pack(anchor="w", pady=(4, 0))

        section("服务器")
        ttk.Label(controls, text="服务器地址（主机:端口）").pack(anchor="w")
        self.server_var = tk.StringVar(value=self.app.library.setting(
            "online_server", "127.0.0.1:8765"))
        ttk.Entry(controls, textvariable=self.server_var).pack(fill="x", pady=(5, 7))
        row = ttk.Frame(controls)
        row.pack(fill="x")
        self.connect_button = ttk.Button(row, text="连接服务器", style="Accent.TButton",
                                         command=self.connect)
        self.connect_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.host_button = ttk.Button(row, text="本机开房（启动服务器）",
                                      command=self.start_host)
        self.host_button.pack(side="left", fill="x", expand=True)
        self.stop_host_button = ttk.Button(controls, text="停止本机服务器",
                                           command=self.stop_host, state="disabled")
        self.stop_host_button.pack(fill="x", pady=(7, 0))
        self.server_info = tk.StringVar(value="未连接服务器。")
        ttk.Label(controls, textvariable=self.server_info, style="Note.TLabel",
                  wraplength=390, justify="left").pack(fill="x", pady=(8, 0))

        section("账号")
        ttk.Label(controls, text="用户名").pack(anchor="w")
        self.username_var = tk.StringVar(value=self.app.library.setting("online_user", ""))
        ttk.Entry(controls, textvariable=self.username_var).pack(fill="x", pady=(5, 7))
        ttk.Label(controls, text="密码").pack(anchor="w")
        self.password_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.password_var, show="*").pack(
            fill="x", pady=(5, 7))
        ttk.Label(controls, text="确认密码（注册时）").pack(anchor="w")
        self.confirm_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.confirm_var, show="*").pack(
            fill="x", pady=(5, 7))
        row = ttk.Frame(controls)
        row.pack(fill="x")
        self.register_button = ttk.Button(row, text="注册新账号", command=self.register)
        self.register_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.login_button = ttk.Button(row, text="登录", command=self.login)
        self.login_button.pack(side="left", fill="x", expand=True)
        self.uid_text = tk.StringVar(value="尚未登录")
        ttk.Label(controls, textvariable=self.uid_text, style="Note.TLabel",
                  wraplength=390, justify="left").pack(fill="x", pady=(8, 0))

        section("房间")
        row = ttk.Frame(controls)
        row.pack(fill="x")
        size_box = ttk.Frame(row)
        size_box.pack(side="left")
        ttk.Label(size_box, text="棋盘").pack(anchor="w")
        self.size_var = tk.StringVar(value="19")
        ttk.Combobox(size_box, textvariable=self.size_var, values=("19", "13", "9"),
                     state="readonly", width=7).pack(anchor="w", pady=(5, 0))
        color_box = ttk.Frame(row)
        color_box.pack(side="left", padx=(12, 0))
        ttk.Label(color_box, text="你执").pack(anchor="w")
        self.color_var = tk.StringVar(value="执黑")
        ttk.Combobox(color_box, textvariable=self.color_var,
                     values=("执黑", "执白", "随机"), state="readonly", width=9).pack(
                         anchor="w", pady=(5, 0))
        self.create_button = ttk.Button(row, text="创建房间", command=self.create_room)
        self.create_button.pack(side="left", fill="x", expand=True, padx=(12, 0), pady=(20, 0))
        join_row = ttk.Frame(controls)
        join_row.pack(fill="x", pady=(8, 0))
        self.join_var = tk.StringVar()
        ttk.Entry(join_row, textvariable=self.join_var, width=12).pack(
            side="left", fill="x", expand=True)
        self.join_button = ttk.Button(join_row, text="加入房间", command=self.join_room)
        self.join_button.pack(side="left", padx=(6, 0))
        self.room_text = tk.StringVar(value="尚未加入房间。")
        ttk.Label(controls, textvariable=self.room_text, style="Note.TLabel",
                  wraplength=390, justify="left").pack(fill="x", pady=(8, 0))

        section("对局")
        self.game_text = tk.StringVar(value="登录后创建或加入房间。")
        ttk.Label(controls, textvariable=self.game_text, style="Note.TLabel",
                  wraplength=390, justify="left").pack(fill="x")
        row = ttk.Frame(controls)
        row.pack(fill="x", pady=(9, 0))
        self.pass_button = ttk.Button(row, text="停一手", command=self.pass_move)
        self.pass_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.resign_button = ttk.Button(row, text="认输", style="Danger.TButton",
                                        command=self.resign)
        self.resign_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.leave_button = ttk.Button(row, text="离开房间", command=self.leave_room)
        self.leave_button.pack(side="left", fill="x", expand=True)
        row = ttk.Frame(controls)
        row.pack(fill="x", pady=(7, 0))
        self.confirm_button = ttk.Button(row, text="同意数子结果", command=self.confirm_score)
        self.confirm_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.resume_button = ttk.Button(row, text="继续下棋", command=self.resume_game)
        self.resume_button.pack(side="left", fill="x", expand=True)
        self.hint = tk.StringVar(value="同一局域网可直接联机；公网房主需要端口映射或内网穿透。")
        self._hint_level = "info"
        self.hint_label = ttk.Label(controls, textvariable=self.hint, style="Note.TLabel",
                                    wraplength=390, justify="left")
        self.hint_label.pack(fill="x", pady=(10, 0))
        ttk.Label(controls, text="联机首版：账号由服务器统一管理；落子由服务端校验。"
                  "双方停一手后按当前盘面数子，未自动标死子，不认可可继续下棋。",
                  style="Muted.TLabel", wraplength=390, justify="left").pack(
                      anchor="w", pady=(9, 4))
        self._update_buttons()
        self._show_empty_board()
        theme.set_button_cursors(self)

    _HINT_STYLES = {
        "info": "Note.TLabel",
        "success": "SuccessNote.TLabel",
        "warning": "WarningNote.TLabel",
        "error": "DangerNote.TLabel",
    }

    def _set_hint(self, text: str, level: str = "info") -> None:
        """Hint message with a severity that controls its color."""
        self._hint_level = level
        self.hint.set(text)
        self.hint_label.configure(style=self._HINT_STYLES.get(level, "Note.TLabel"))

    def _ghost_color(self) -> str | None:
        """Ghost-stone preview only on your own turn while a game is live."""
        room = self.room
        if (room and room.get("status") == "playing" and not self.pending
                and room.get("viewer_color") == room.get("to_play")):
            return room.get("viewer_color")
        return None

    # ----------------------------------------------------------------- helpers
    def _run(self, work: Callable[[], Any], success: Callable[[Any], None] | None = None) -> None:
        if self.pending:
            return
        self.pending = True
        self._update_buttons()
        self.app.root.configure(cursor="watch")

        def runner() -> None:
            try:
                result = work()
            except Exception as error:
                self.events.put(("error", str(error)))
            else:
                self.events.put(("ok", (success, result)))

        threading.Thread(target=runner, daemon=True, name="online-request").start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "state":
                    self._apply_state(payload)
                elif kind == "ok":
                    self.pending = False
                    self.app.root.configure(cursor="")
                    callback, result = payload
                    if callback is not None:
                        callback(result)
                    self._update_buttons()
                elif kind == "error":
                    self.pending = False
                    self.app.root.configure(cursor="")
                    self._set_hint(str(payload), "error")
                    self._update_buttons()
                elif kind == "disconnected":
                    self._connection_lost()
                elif kind == "server":
                    self._host_started(payload)
        except queue.Empty:
            pass
        try:
            self._drain_id = self.root.after(100, self._drain_events)
        except tk.TclError:
            self._drain_id = None

    def _event(self, message: dict[str, Any]) -> None:
        if message.get("event") == "state" and isinstance(message.get("state"), dict):
            self.events.put(("state", message["state"]))
        elif message.get("event") == "disconnected":
            self.events.put(("disconnected", None))

    def _connection_lost(self) -> None:
        if self.client is not None and not self.client.closed.is_set():
            return
        if self.client is not None:
            self.client.close()
        self.client = None
        self.token = ""
        self.room = None
        self.pending = False
        self.server_info.set("连接已断开。可重新连接；本机服务器若已启动仍会继续运行。")
        self.uid_text.set("尚未登录")
        self.room_text.set("尚未加入房间。")
        self.game_text.set("连接已断开。")
        self._show_empty_board()
        self._update_buttons()

    def _show_empty_board(self) -> None:
        self.board_view.show(GoBoard())

    def _apply_state(self, state: dict[str, Any]) -> None:
        previous_status = self.room.get("status") if self.room else None
        self.room = state
        try:
            board = GoBoard(int(state["size"]), "SIMPLE")
            for move in state.get("moves", []):
                board.play(move["color"], move.get("x"), move.get("y"))
            self.board_view.show(board)
        except Exception:
            self._show_empty_board()
        players = state.get("players", [])
        me = next((p for p in players if p.get("uid") == state.get("viewer_uid")), None)
        other = next((p for p in players if p.get("uid") != state.get("viewer_uid")), None)
        mine = me.get("color") if me else None
        color_name = lambda color: "黑" if color == "B" else "白" if color == "W" else "—"
        if other:
            connected = "" if other.get("connected") else " · 已断开，等待重连"
            room_line = (f"房间 {state['room']} · 你执{color_name(mine)} · "
                         f"对手 {other['username']}（{color_name(other['color'])}）{connected}")
        else:
            room_line = f"房间 {state['room']} · 等待对手加入"
        if self.share_address:
            room_line += f"\n服务器：{self.share_address}"
        self.room_text.set(room_line)
        status = state.get("status")
        if status == "waiting":
            game = f"等待对手。把房间码 {state['room']} 发给朋友。"
        elif status == "playing":
            turn = "你" if mine == state.get("to_play") else "对手"
            game = (f"第 {int(state.get('move_count', 0)) + 1} 手 · 轮到{turn}\n"
                    f"黑提子 {state['captures'].get('B', 0)} · 白提子 {state['captures'].get('W', 0)}")
        elif status == "scoring":
            confirmed = len(state.get("confirmed") or [])
            game = (f"双方停一手，等待确认数子（已确认 {confirmed}/{len(players)}）。\n"
                    + str(state.get("score_summary") or "")
                    + "\n" + str(state.get("warning") or ""))
        elif status == "finished":
            game = f"对局结束：{state.get('result') or '—'}\n{state.get('score_summary') or ''}"
        else:
            game = str(status or "")
        self.game_text.set(game)
        if status == "waiting":
            self._set_hint("等待对手。把房间码和服务器地址发给朋友。")
        elif status == "playing":
            self._set_hint("轮到你落子。", "success") if mine == state.get("to_play") \
                else self._set_hint("等待对手落子。")
        elif status == "scoring":
            self._set_hint("双方确认数子结果；不认可可点“继续下棋”撤回最后两次停一手。")
        elif status == "finished":
            self._set_hint("对局结束，可离开房间或继续创建下一局。")
        self._update_buttons()
        if previous_status != state.get("status"):
            self.control_canvas.yview_moveto(1.0)

    def _update_buttons(self) -> None:
        connected = self.client is not None and not self.client.closed.is_set()
        logged = bool(connected and self.token)
        playing = bool(self.room and self.room.get("status") == "playing")
        scoring = bool(self.room and self.room.get("status") == "scoring")
        in_room = bool(self.room)
        idle = connected and not self.pending
        for button, enabled in (
            (self.connect_button, not self.pending and not in_room),
            (self.host_button, not self.pending and self.embedded_server is None and not in_room),
            (self.stop_host_button, self.embedded_server is not None and not self.pending
             and not in_room),
            (self.register_button, idle and not logged),
            (self.login_button, idle and not logged),
            (self.create_button, idle and logged and not in_room),
            (self.join_button, idle and logged and not in_room),
            (self.pass_button, idle and playing),
            (self.resign_button, idle and playing),
            (self.leave_button, idle and in_room),
            (self.confirm_button, idle and scoring),
            (self.resume_button, idle and scoring),
        ):
            button.configure(state="normal" if enabled else "disabled")

    def _request_room(self, kind: str, **fields: Any) -> None:
        client, token = self.client, self.token
        if client is None or not token:
            self._set_hint("请先连接服务器并登录。", "warning")
            return

        def work() -> dict[str, Any]:
            return client.request(kind, token=token, **fields)

        def success(response: dict[str, Any]) -> None:
            room = response.get("room")
            if room:
                self._apply_state(room)
            elif kind == "leave_room":
                self.room = None
                self.room_text.set("已离开房间。")
                self.game_text.set("登录后创建或加入房间。")
                self._show_empty_board()
            self._update_buttons()

        self._run(work, success)

    # ----------------------------------------------------------------- actions
    def connect(self) -> None:
        address = self.server_var.get().strip()
        self._set_hint(f"正在连接 {address}…")

        def work() -> OnlineClient:
            return OnlineClient(address, self._event)

        def success(client: OnlineClient) -> None:
            if self.client is not None:
                self.client.close()
            self.client = client
            self.token = ""
            self.account = None
            self.room = None
            self.uid_text.set("尚未登录")
            self.server_var.set(address)
            if self.embedded_server is None:
                self.share_address = address
            self.app.library.set_setting("online_server", address)
            self.server_info.set(f"已连接 {address}。请注册新账号或登录。")
            self._set_hint("连接成功。注册账号后会获得一个 UID。", "success")
            self._show_empty_board()
            self._update_buttons()

        self._run(work, success)

    def start_host(self) -> None:
        if self.embedded_server is not None:
            self.server_var.set(f"127.0.0.1:{self.embedded_port}")
            self.connect()
            return

        def work() -> tuple[OnlineServer, threading.Thread, int]:
            service = OnlineService(self.app.library.directory / "online-server.sqlite3")
            last: Exception | None = None
            for port in range(8765, 8786):
                try:
                    server = OnlineServer(("0.0.0.0", port), service)
                except OSError as error:
                    last = error
                    continue
                thread = threading.Thread(target=server.serve_forever, daemon=True,
                                          name="online-host-server")
                thread.start()
                return server, thread, port
            raise OnlineClientError(f"无法启动本机服务器：{last}")

        self._run(work, self._host_started)

    def _host_started(self, result: tuple[OnlineServer, threading.Thread, int]) -> None:
        server, _thread, port = result
        self.embedded_server, self.embedded_thread, self.embedded_port = server, _thread, port
        self.server_var.set(f"127.0.0.1:{port}")
        addresses = local_addresses(port)
        self.share_address = ("  或  ".join(addresses) if addresses
                              else f"127.0.0.1:{port}")
        self.server_info.set(
            "本机服务器已启动。朋友在“服务器地址”填：" + self.share_address
            + "\n关闭影棋后服务器停止。公网联机需要端口映射或内网穿透。")
        self.stop_host_button.configure(state="normal")
        self._set_hint("服务器已启动，正在连接本机服务器…")
        self.connect()

    def stop_host(self) -> None:
        if self.client is not None:
            self.client.close()
        self.client = None
        self.token = ""
        self.room = None
        server = self.embedded_server
        self.embedded_server = None
        self.embedded_thread = None
        self.embedded_port = 0
        if server is not None:
            def close_server() -> None:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:
                    pass
            threading.Thread(target=close_server, daemon=True).start()
        self.server_info.set("本机服务器已停止。")
        self.share_address = ""
        self.uid_text.set("尚未登录")
        self.room_text.set("尚未加入房间。")
        self.game_text.set("未连接服务器。")
        self._show_empty_board()
        self._update_buttons()

    def register(self) -> None:
        if self.client is None:
            self._set_hint("请先连接服务器。", "warning")
            return
        password = self.password_var.get()
        if password != self.confirm_var.get():
            self._set_hint("两次输入的密码不一致，请重新输入。", "error")
            return
        username = self.username_var.get().strip()
        self._set_hint("正在注册账号…")

        def work() -> dict[str, Any]:
            return self.client.request("register", username=username, password=password)

        self._run(work, self._authenticated)

    def login(self) -> None:
        if self.client is None:
            self._set_hint("请先连接服务器。", "warning")
            return
        username = self.username_var.get().strip()
        password = self.password_var.get()
        self._set_hint("正在登录…")

        def work() -> dict[str, Any]:
            return self.client.request("login", username=username, password=password)

        self._run(work, self._authenticated)

    def _authenticated(self, response: dict[str, Any]) -> None:
        self.token = str(response.get("token") or "")
        account = response.get("account") or {}
        self.account = account
        self.password_var.set("")
        self.confirm_var.set("")
        if self.username_var.get().strip():
            self.app.library.set_setting("online_user", self.username_var.get().strip())
        self.uid_text.set(f"UID {account.get('uid', '—')} · {account.get('username', '')}")
        if self.embedded_server is not None:
            self.server_info.set(f"本机服务器：{self.share_address} · "
                                 f"UID {account.get('uid', '—')}")
        else:
            self.server_info.set(f"已连接 {self.client.address} · "
                                 f"UID {account.get('uid', '—')}")
        self._set_hint("登录成功。创建房间或输入房间码加入。", "success")
        room = response.get("room")
        if room:
            self._apply_state(room)
        self._update_buttons()

    def create_room(self) -> None:
        color = {"执黑": "B", "执白": "W", "随机": "random"}.get(self.color_var.get(), "B")
        try:
            size = int(self.size_var.get())
        except ValueError:
            size = 19
        self._request_room("create_room", size=size, komi=7.5, color=color)

    def join_room(self) -> None:
        code = self.join_var.get().strip()
        if not code:
            self._set_hint("请输入房间码。", "warning")
            return
        self._request_room("join_room", room=code)

    def _board_click(self, x: int, y: int) -> None:
        if self.pending:
            return
        if self.room is None:
            self._set_hint("请先创建或加入房间。", "warning")
            return
        if self.room.get("status") != "playing":
            self._set_hint("当前不在行棋阶段。", "warning")
            return
        if self.room.get("viewer_color") != self.room.get("to_play"):
            self._set_hint("还没轮到你落子。", "warning")
            return
        client, token = self.client, self.token
        if client is None or not token:
            self._set_hint("请先登录。", "warning")
            return

        def work() -> dict[str, Any]:
            return client.request("move", token=token, x=x, y=y)

        def success(response: dict[str, Any]) -> None:
            room = response.get("room")
            if room:
                self._apply_state(room)

        self._run(work, success)

    def pass_move(self) -> None:
        self._request_room("pass")

    def resign(self) -> None:
        if self.room and self.room.get("status") in {"playing", "scoring"}:
            self._request_room("resign")

    def leave_room(self) -> None:
        self._request_room("leave_room")

    def confirm_score(self) -> None:
        self._request_room("confirm_score")

    def resume_game(self) -> None:
        self._request_room("resume_game")

    # ------------------------------------------------------------------- theme
    def refresh_theme(self) -> None:
        self.board_view.refresh_theme()
        try:
            self.control_canvas.configure(background=theme.SURFACE)
        except tk.TclError:
            pass

    def shutdown(self) -> None:
        if self._drain_id is not None:
            try:
                self.root.after_cancel(self._drain_id)
            except tk.TclError:
                pass
            self._drain_id = None
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.embedded_server is not None:
            server = self.embedded_server
            self.embedded_server = None
            try:
                threading.Thread(target=lambda: (server.shutdown(), server.server_close()),
                                 daemon=True).start()
            except Exception:
                pass
