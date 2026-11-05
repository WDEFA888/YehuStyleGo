from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from yehu_style_go.board import EMPTY, GoBoard
from yehu_style_go.scoring import AreaScore, score_area
from yehu_style_go import theme
from yehu_style_go.theme import apply_title_bar
from yehu_style_go.widgets import BoardView


class ScoringDialog(tk.Toplevel):
    def __init__(
        self, parent, board: GoBoard, komi: float,
        on_confirm: Callable[[AreaScore], None], on_resume: Callable[[], None],
        on_close: Callable[[], None], dead: set[tuple[int, int]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("终局判定 · 确认死子后数子")
        self.geometry("1040x720")
        self.minsize(880, 620)
        self.transient(parent)
        self.configure(background=theme.SURFACE)
        self.board = board.clone()
        self.komi = komi
        self.dead = set(dead or ())
        self.on_confirm, self.on_resume, self.on_close = on_confirm, on_resume, on_close
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(20, 18, 20, 6))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(header, text="终局判定", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(header, text="点击棋块切换死子标记（×），再次点击可撤销。"
                  "请确认死活和中立点后再接受结果。",
                  wraplength=960, style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
        self.board_outer = tk.Frame(self, background=theme.BORDER, borderwidth=0, highlightthickness=0)
        self.board_outer.grid(row=1, column=0, sticky="nsew", padx=(20, 12), pady=(8, 20))
        board_inner = ttk.Frame(self.board_outer, style="Card.TFrame")
        board_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.view = BoardView(board_inner, self.toggle_dead)
        self.view.pack(fill="both", expand=True)
        panel = ttk.Frame(self, padding=(4, 8, 20, 20))
        panel.grid(row=1, column=1, sticky="nsew")
        self.readout = tk.StringVar()
        ttk.Label(panel, text="计分预览 · 尚未确认", style="Section.TLabel").pack(anchor="w")
        ttk.Label(panel, textvariable=self.readout, justify="left", wraplength=350,
                  style="Note.TLabel").pack(fill="x", pady=(10, 12))
        ttk.Label(panel, text="程序不会把未标记的棋块自动当作死子。若仍有死活、劫争或单官争议，"
                  "请返回继续下棋。这里只按当前确认的盘面计分，不是死活求解器。",
                  style="Muted.TLabel", wraplength=350, justify="left").pack(anchor="w", pady=(0, 16))
        ttk.Separator(panel, orient="horizontal").pack(fill="x", pady=(0, 12))
        ttk.Button(panel, text="清除死子标记", command=self.clear_dead).pack(fill="x", pady=5)
        ttk.Button(panel, text="确认死子并判定胜负", style="Accent.TButton",
                   command=self.confirm).pack(fill="x", pady=5)
        ttk.Button(panel, text="返回继续下棋", command=self.resume).pack(fill="x", pady=5)
        ttk.Button(panel, text="关闭，稍后确认", command=self.close).pack(fill="x", pady=5)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.update_idletasks()
        apply_title_bar(self, theme.is_dark())
        theme.set_button_cursors(self)
        if self.winfo_viewable():
            self.grab_set()

    def refresh_theme(self) -> None:
        self.configure(background=theme.SURFACE)
        apply_title_bar(self, theme.is_dark())
        self.board_outer.configure(background=theme.BORDER)
        self.view.refresh_theme()

    def refresh(self) -> None:
        self.score = score_area(self.board, self.dead, self.komi)
        self.view.dead_points = set(self.score.dead)
        self.view.show(self.board)
        self.readout.set(self.score.summary())

    def toggle_dead(self, x: int, y: int) -> None:
        if self.board.grid[y][x] == EMPTY:
            return
        group, _ = self.board._group_and_liberties(x, y)
        if group <= self.dead:
            self.dead.difference_update(group)
        else:
            self.dead.update(group)
        self.refresh()

    def clear_dead(self) -> None:
        self.dead.clear()
        self.refresh()

    def confirm(self) -> None:
        score = self.score
        self.destroy()
        self.on_confirm(score)

    def resume(self) -> None:
        self.destroy()
        self.on_resume()

    def close(self) -> None:
        self.destroy()
        self.on_close()
