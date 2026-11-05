from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from yehu_style_go.board import COLOR_TO_STONE, GoBoard, IllegalMove, xy_to_gtp
from yehu_style_go import theme
from yehu_style_go.theme import apply_title_bar
from yehu_style_go.trial import TrialSession
from yehu_style_go.widgets import BoardView


class TrialWindow(tk.Toplevel):
    def __init__(self, parent, board: GoBoard, current_board: Callable[[], GoBoard]) -> None:
        super().__init__(parent)
        self.title("试下 / 变化推演 · 不影响实战")
        self.geometry("1080x780")
        self.minsize(900, 650)
        self.transient(parent)
        self.configure(background=theme.SURFACE)
        self.session = TrialSession(board)
        self.current_board = current_board
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(20, 18, 20, 6))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(header, text="试下 / 变化推演", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(header, text="独立副本 · 只在当前窗口推演，不写回正式对局",
                  style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
        self.board_outer = tk.Frame(self, background=theme.BORDER, borderwidth=0, highlightthickness=0)
        self.board_outer.grid(row=1, column=0, sticky="nsew", padx=(20, 12), pady=(8, 20))
        board_inner = ttk.Frame(self.board_outer, style="Card.TFrame")
        board_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.view = BoardView(board_inner, self.play)
        self.view.pack(fill="both", expand=True)
        panel = ttk.Frame(self, padding=(4, 8, 20, 20))
        panel.grid(row=1, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(7, weight=1)
        self.status = tk.StringVar()
        ttk.Label(panel, textvariable=self.status, wraplength=360, justify="left",
                  style="Note.TLabel").grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(panel, text="由你手动替黑白双方落子，不会自动启动陪练。\n"
                  "实战可继续进行，本窗口不会自动跟随实战。",
                  wraplength=360, style="Muted.TLabel",
                  justify="left").grid(row=1, column=0, sticky="w", pady=(0, 14))
        color_row = ttk.Frame(panel)
        color_row.grid(row=2, column=0, sticky="ew")
        ttk.Label(color_row, text="落子方").pack(side="left", padx=(0, 10))
        self.color_var = tk.StringVar(value="自动交替")
        ttk.Combobox(color_row, textvariable=self.color_var,
                     values=("自动交替", "黑棋", "白棋"), state="readonly", width=14).pack(side="left")
        row = ttk.Frame(panel)
        row.grid(row=3, column=0, sticky="ew", pady=12)
        self.undo_button = ttk.Button(row, text="悔棋", command=self.undo)
        self.undo_button.pack(side="left")
        self.redo_button = ttk.Button(row, text="重做", command=self.redo)
        self.redo_button.pack(side="left", padx=6)
        ttk.Button(row, text="停一手", command=lambda: self.play(None, None)).pack(side="left")
        row = ttk.Frame(panel)
        row.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(row, text="重置试下", command=self.reset).pack(side="left")
        ttk.Button(row, text="取最新实战局面", command=self.reload).pack(side="left", padx=6)
        self.number_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(panel, text="标出试下手数（从 1 开始）", variable=self.number_var,
                        command=self.refresh).grid(row=5, column=0, sticky="w", pady=(0, 10))
        self.message = tk.StringVar(value="点击棋盘即可试下；方向键移动、回车落子。")
        self.message_label = ttk.Label(panel, textvariable=self.message, wraplength=360,
                                       style="Note.TLabel", justify="left")
        self.message_label.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        sequence = ttk.Frame(panel)
        sequence.grid(row=7, column=0, sticky="nsew")
        ttk.Label(sequence, text="变化手顺", style="Section.TLabel").pack(anchor="w", pady=(0, 7))
        self.sequence_outer = tk.Frame(sequence, background=theme.BORDER, borderwidth=0, highlightthickness=0)
        self.sequence_outer.pack(fill="both", expand=True)
        sequence_inner = ttk.Frame(self.sequence_outer, style="Card.TFrame")
        sequence_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.move_table = ttk.Treeview(sequence_inner, columns=("turn", "color", "move"),
                                      show="headings", height=7)
        for column, title, width in (("turn", "试下手数", 90), ("color", "执色", 65),
                                     ("move", "落点", 100)):
            self.move_table.heading(column, text=title)
            self.move_table.column(column, width=width, minwidth=45, anchor="center")
        self.move_table.tag_configure("even", background=theme.SURFACE)
        self.move_table.tag_configure("odd", background=theme.SURFACE_ALT)
        scroll = ttk.Scrollbar(sequence_inner, orient="vertical", command=self.move_table.yview)
        self.move_table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.move_table.pack(fill="both", expand=True)
        ttk.Button(panel, text="关闭试下，返回实战", style="Accent.TButton",
                   command=self.destroy).grid(row=8, column=0, sticky="ew", pady=(12, 0))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.refresh()
        self.update_idletasks()
        apply_title_bar(self, theme.is_dark())
        theme.set_button_cursors(self)

    def _set_message(self, text: str, error: bool = False) -> None:
        self.message.set(text)
        self.message_label.configure(style="DangerNote.TLabel" if error else "Note.TLabel")

    def refresh_theme(self) -> None:
        self.configure(background=theme.SURFACE)
        apply_title_bar(self, theme.is_dark())
        self.board_outer.configure(background=theme.BORDER)
        self.sequence_outer.configure(background=theme.BORDER)
        self.move_table.tag_configure("even", background=theme.SURFACE)
        self.move_table.tag_configure("odd", background=theme.SURFACE_ALT)
        self.view.refresh_theme()

    def refresh(self) -> None:
        board = self.session.board
        numbered = {}
        if self.number_var.get():
            for turn, move in enumerate(self.session.moves, 1):
                if not move.is_pass and board.grid[move.y][move.x] == COLOR_TO_STONE[move.color]:
                    numbered[(move.x, move.y)] = turn
        self.view.show(board, numbered)
        color = "黑" if board.to_play == "B" else "白"
        self.status.set(f"起点：实战第 {self.session.origin_turn} 手\n"
                        f"已试下 {self.session.cursor} 手；自动交替时下一手：{color}\n"
                        f"当前提子：黑 {board.captures['B']}、白 {board.captures['W']}")
        self.undo_button.configure(state="normal" if self.session.can_undo else "disabled")
        self.redo_button.configure(state="normal" if self.session.can_redo else "disabled")
        self.move_table.delete(*self.move_table.get_children())
        for turn, move in enumerate(self.session.moves, 1):
            self.move_table.insert("", "end", iid=str(turn), values=(
                turn, "黑" if move.color == "B" else "白", xy_to_gtp(move.x, move.y, board.size)),
                tags=("odd" if turn % 2 else "even",))
        if self.session.cursor:
            self.move_table.see(str(self.session.cursor))

    def play(self, x: int | None, y: int | None) -> None:
        selected = self.color_var.get()
        color = {"黑棋": "B", "白棋": "W"}.get(selected)
        try:
            self.session.play(x, y, color)
        except IllegalMove as error:
            self._set_message(str(error), error=True)
            return
        self._set_message("试下落子仅保存在本窗口，正式对局未改变。")
        self.refresh()

    def undo(self) -> None:
        if self.session.undo():
            self._set_message("已撤回一手试下，不会撤回实战落子。")
        self.refresh()

    def redo(self) -> None:
        self.session.redo()
        self.refresh()

    def _discard_confirmed(self, text: str) -> bool:
        return not (self.session.can_undo or self.session.can_redo) or messagebox.askyesno(
            "替换当前试下", text + "\n只影响试下窗口，不改变正式对局。", parent=self)

    def reset(self) -> None:
        if not self._discard_confirmed("清空试下变化，回到打开时的局面？"):
            return
        self.session.reset()
        self.color_var.set("自动交替")
        self._set_message("已回到本次试下的起点。")
        self.refresh()

    def reload(self) -> None:
        if not self._discard_confirmed("清空试下变化，复制最新的实战局面？"):
            return
        self.session.reload(self.current_board())
        self.color_var.set("自动交替")
        self._set_message("已复制最新实战局面；后续试下仍与实战独立。")
        self.refresh()
