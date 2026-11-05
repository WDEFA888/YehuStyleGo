from __future__ import annotations

import argparse
import ctypes
import csv
import hashlib
import json
import queue
import threading
import tkinter as tk
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from yehu_style_go.board import GoBoard, IllegalMove, gtp_to_xy, opposite, xy_to_gtp
from yehu_style_go.engine import (
    EngineSettings, KataGo, bundled_engine_roots, find_bundled_engine, move_report,
)
from yehu_style_go.fox_sync import MAX_SYNC_GAMES
from yehu_style_go.legends import (
    SELF_STYLE, collect_games, load_index as load_legend_index,
    load_model as load_legend_model, register as register_legend,
    remove as remove_legend,
)
from yehu_style_go.models import GameRecord, Move
from yehu_style_go.online_ui import OnlinePanel
from yehu_style_go.score_dialog import ScoringDialog
from yehu_style_go.scoring import AreaScore, PositionEstimate
from yehu_style_go.sgf import parse_sgf_file
from yehu_style_go.storage import Library, scan_folder
from yehu_style_go.strength import StrengthProfile, build_profile, load_profile, save_profile
from yehu_style_go.style import StyleModel, choose_move, train_style
from yehu_style_go import theme
from yehu_style_go.theme import apply_theme, apply_title_bar
from yehu_style_go.trial_window import TrialWindow
from yehu_style_go.widgets import BoardView, WinrateChart


def _lower_current_thread_priority() -> None:
    """Keep heavy SGF parsing/training from competing with foreground apps.

    Only the worker thread is lowered; Tk's main thread keeps normal priority.
    """
    if not hasattr(ctypes, "windll"):
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        kernel32.SetThreadPriority.restype = ctypes.c_int
        kernel32.SetThreadPriority(ctypes.c_void_p(kernel32.GetCurrentThread()), -2)
    except (AttributeError, OSError):
        pass


class Application:
    def __init__(self, root: tk.Tk, library: Library | None = None) -> None:
        self.root = root
        self.library = library or Library()
        self.player = str(self.library.setting("player", "") or "")
        self.settings = EngineSettings(**self.library.setting("engine", {}))
        self.bundled_engine = False
        self._configure_bundled_engine()
        self.game_limit = self._clamp_limit(self.library.setting("game_limit", 100))
        self.dark_mode = self.library.setting("theme_mode", "light") == "dark"
        self._themed: list[tuple[tk.Misc, str]] = []
        self.strength_profile: StrengthProfile | None = None
        self.strength_profile_key = ""
        self.model: StyleModel | None = None
        self.legend_styles: list[dict[str, Any]] = load_legend_index(self.library.directory)
        self.legend_model: StyleModel | None = None
        self.spar_model: StyleModel | None = None
        self.spar_legend_name: str | None = None
        self.spar_opponent_label = ""
        self.games: list[GameRecord] = []
        self.selected_game: GameRecord | None = None
        self.selected_analysis: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []
        self.review_variation: dict[str, Any] | None = None
        self.review_candidates: dict[str, Any] | None = None
        # In-memory per-branch KataGo results, keyed by engine + game + PV.
        self.variation_cache: dict[str, dict[int, dict[str, float]]] = {}
        self.turn = 0
        self.busy = False
        self.cancel = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.finished: Callable[[Any], None] | None = None
        self.closed = False
        self.spar_board = GoBoard()
        self.human_color = "B"
        self.spar_active = False
        self.spar_pending_score = False
        self.spar_saved = True
        self.spar_result = ""
        self.spar_score: AreaScore | None = None
        self.spar_dead: set[tuple[int, int]] = set()
        self.spar_estimate: PositionEstimate | None = None
        self.spar_komi = 7.5
        self.scoring_dialog: ScoringDialog | None = None
        self.trial_window: TrialWindow | None = None
        self.spar_mode = ""
        self.watch_signature: tuple | None = None
        self.root.title("影棋 · YehuStyleGo")
        self.root.geometry("1280x900")
        self.root.minsize(1060, 760)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build()
        self._load_model()
        self._load_strength_profile()
        self.refresh_library()
        if not self.player:
            self.tabs.select(self.settings_tab)
            suffix = "；随包 KataGo 已自动配置。" if self.bundled_engine else "；引擎可选。"
            self._notify("首次使用：请在设置中填写你的野狐用户名并保存" + suffix, "warning")
        elif self.bundled_engine:
            self._notify("就绪 · 已自动配置随包 KataGo；所有数据仅保存在本机", "success")
        self.root.after(50, lambda: apply_title_bar(self.root, self.dark_mode))
        self.root.after(100, self._drain_events)
        self.root.after(30000, self._watch_folder)

    def _configure_bundled_engine(self) -> None:
        def present(value: str) -> bool:
            return bool(value) and Path(value).is_file()

        if present(self.settings.executable) and present(self.settings.model):
            return
        found = find_bundled_engine(bundled_engine_roots())
        if found is None:
            return
        visits = self.settings.visits if 1 <= self.settings.visits <= 10000 else 120
        self.settings = EngineSettings(str(found[0]), str(found[1]), visits)
        self.bundled_engine = True

    def _style(self) -> None:
        apply_theme(self.root, dark=self.dark_mode)

    def _bordered(self, parent) -> tuple[tk.Frame, ttk.Frame]:
        outer = tk.Frame(parent, background=theme.BORDER, borderwidth=0, highlightthickness=0)
        self._themed.append((outer, "border"))
        inner = ttk.Frame(outer, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return outer, inner

    def _draw_logo(self) -> None:
        self.logo_canvas.configure(background=theme.BG)
        self.logo_canvas.delete("all")
        self.logo_canvas.create_oval(4, 6, 28, 30, fill=theme.LOGO_BLACK,
                                     outline=theme.LOGO_BLACK_OUTLINE, width=1.2)
        self.logo_canvas.create_oval(10, 10, 17, 17, fill=theme.LOGO_WHITE,
                                     outline="", stipple="gray25")
        self.logo_canvas.create_oval(17, 16, 41, 40, fill=theme.LOGO_WHITE,
                                     outline=theme.LOGO_WHITE_OUTLINE, width=1.2)
        self.logo_canvas.create_oval(23, 20, 29, 26, fill=theme.LOGO_WHITE,
                                     outline="")

    def _apply_theme_colors(self) -> None:
        """Update the plain Tk widgets that ttk styles cannot restyle."""
        self.root.configure(background=theme.BG)
        live = []
        for widget, role in self._themed:
            try:
                if not widget.winfo_exists():
                    continue
            except tk.TclError:
                continue
            live.append((widget, role))
            if role == "border":
                widget.configure(background=theme.BORDER)
            elif role == "chip":
                widget.configure(background=theme.SURFACE, foreground=theme.INK,
                                 highlightbackground=theme.BORDER)
            elif role == "logo":
                self._draw_logo()
            elif role == "surface_canvas":
                widget.configure(background=theme.SURFACE)
            elif role == "outside_canvas":
                widget.configure(background=theme.BG)
            elif role == "text":
                widget.configure(background=theme.SURFACE, foreground=theme.INK,
                                 insertbackground=theme.INK,
                                 highlightbackground=theme.BORDER,
                                 highlightcolor=theme.BORDER)
            elif role == "scale":
                widget.configure(background=theme.SURFACE, troughcolor=theme.BORDER,
                                 activebackground=theme.ACCENT,
                                 highlightbackground=theme.SURFACE)
        self._themed = live
        self.review_board.refresh_theme()
        self.spar_view.refresh_theme()
        self.chart.refresh_theme()
        for table in (self.game_table, self.move_table):
            table.tag_configure("even", background=theme.SURFACE)
            table.tag_configure("odd", background=theme.SURFACE_ALT)
            table.tag_configure("empty", foreground=theme.FAINT)
        self._apply_status_style()
        if self.trial_window is not None and self.trial_window.winfo_exists():
            self.trial_window.refresh_theme()
        if self.scoring_dialog is not None and self.scoring_dialog.winfo_exists():
            self.scoring_dialog.refresh_theme()
        if hasattr(self, "online_panel"):
            self.online_panel.refresh_theme()
        self._configure_pv_tags()
        # Per-move tags carry theme colors, so rebuild an open variation list.
        variation = getattr(self, "review_variation", None)
        if variation is not None:
            self._render_variation(variation["step"])

    def _configure_pv_tags(self) -> None:
        """Color candidate rows by severity; called on build and theme change."""
        for tag, color in (("heading", theme.INK), ("best", theme.SUCCESS),
                           ("good", theme.ACCENT), ("warn", theme.WARNING),
                           ("bad", theme.DANGER)):
            try:
                self.pv_text.tag_configure(tag, foreground=color)
            except tk.TclError:
                return
        try:
            self.pv_text.tag_configure("cand_hover", background=theme.SELECTION)
        except tk.TclError:
            pass

    def toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self.library.set_setting("theme_mode", "dark" if self.dark_mode else "light")
        apply_theme(self.root, dark=self.dark_mode)
        apply_title_bar(self.root, self.dark_mode)
        self.theme_button_text.set("日间模式" if self.dark_mode else "夜间模式")
        self._apply_theme_colors()
        self._notify("已切换到夜间模式" if self.dark_mode else "已切换到日间模式", "success")

    def _notify(self, text: str, level: str = "info") -> None:
        """Status-bar message with a severity: info/busy/success/warning/error."""
        self._status_level = level
        self.status.set(text)
        self._apply_status_style()

    def _apply_status_style(self) -> None:
        colors = {
            "info": (theme.MUTED, theme.FAINT),
            "busy": (theme.INK, theme.ACCENT),
            "success": (theme.SUCCESS, theme.SUCCESS),
            "warning": (theme.WARNING, theme.WARNING),
            "error": (theme.DANGER, theme.DANGER),
        }
        foreground, dot = colors.get(self._status_level, colors["info"])
        try:
            self.status_label.configure(foreground=foreground)
            self.status_dot.itemconfigure(self._status_dot_item, fill=dot)
        except tk.TclError:
            pass

    def _build(self) -> None:
        header = ttk.Frame(self.root, padding=(28, 20, 28, 14), style="Outside.TFrame")
        header.pack(fill="x")
        self.logo_canvas = tk.Canvas(header, width=46, height=46, background=theme.BG,
                                     highlightthickness=0, bd=0)
        self.logo_canvas.pack(side="left", padx=(0, 13))
        self._themed.append((self.logo_canvas, "logo"))
        self._draw_logo()
        title = ttk.Frame(header, style="Outside.TFrame")
        title.pack(side="left")
        ttk.Label(title, text="影棋", style="Outside.TLabel",
                  font=(theme.FONT, 23, "bold")).pack(anchor="w")
        ttk.Label(title, text="YehuStyleGo  ·  与另一个自己对弈",
                  style="OutsideMuted.TLabel").pack(anchor="w", pady=(2, 0))
        self.identity = tk.StringVar(value=f"棋手 · {self.player or '未设置'}")
        self.identity_label = tk.Label(
            header, textvariable=self.identity, background=theme.SURFACE,
            foreground=theme.INK, font=(theme.FONT, 9, "bold"), padx=14, pady=6,
            highlightbackground=theme.BORDER, highlightthickness=1)
        self.identity_label.pack(side="right")
        self._themed.append((self.identity_label, "chip"))
        self.theme_button_text = tk.StringVar(
            value="日间模式" if self.dark_mode else "夜间模式")
        ttk.Button(header, textvariable=self.theme_button_text,
                   command=self.toggle_theme).pack(side="right", padx=(0, 10))

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=23, pady=(0, 2))

        footer = ttk.Frame(self.root, padding=(28, 10, 28, 12), style="Outside.TFrame")
        footer.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value="就绪 · 所有数据仅保存在本机")
        self._status_level = "info"
        self.status_dot = tk.Canvas(footer, width=12, height=12, background=theme.BG,
                                    highlightthickness=0, bd=0)
        self._status_dot_item = self.status_dot.create_oval(2, 2, 10, 10,
                                                            fill=theme.FAINT, outline="")
        self.status_dot.pack(side="left", padx=(0, 8))
        self._themed.append((self.status_dot, "outside_canvas"))
        self.status_label = ttk.Label(footer, textvariable=self.status, wraplength=780,
                                      style="OutsideMuted.TLabel")
        self.status_label.pack(side="left")
        self.progress_text = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.progress_text, width=5,
                  style="OutsideMuted.TLabel").pack(side="right")
        self.progress = ttk.Progressbar(footer, length=170, mode="determinate")
        self.progress.pack(side="right", padx=(12, 0))
        self.cancel_button = ttk.Button(footer, text="取消任务", command=self.cancel.set,
                                        state="disabled")
        self.cancel_button.pack(side="right")
        ttk.Separator(self.root, orient="horizontal").pack(side="bottom", fill="x", padx=23)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=23, pady=(8, 10))
        self.home_tab = ttk.Frame(self.tabs, padding=24, style="Page.TFrame")
        self.library_tab = ttk.Frame(self.tabs, padding=20, style="Page.TFrame")
        self.review_tab = ttk.Frame(self.tabs, padding=16, style="Page.TFrame")
        self.profile_tab = ttk.Frame(self.tabs, padding=20, style="Page.TFrame")
        self.spar_tab = ttk.Frame(self.tabs, padding=16, style="Page.TFrame")
        self.online_tab = ttk.Frame(self.tabs, padding=16, style="Page.TFrame")
        self.settings_tab = ttk.Frame(self.tabs, padding=22, style="Page.TFrame")
        for frame, name in ((self.home_tab, "首页"), (self.library_tab, "棋谱库"),
                            (self.review_tab, "复盘分析"), (self.profile_tab, "我的棋风"),
                            (self.spar_tab, "模拟对弈"), (self.online_tab, "联机对弈"),
                            (self.settings_tab, "设置")):
            self.tabs.add(frame, text=name)
        self._build_home()
        self._build_library()
        self._build_review()
        self._build_profile()
        self._build_spar()
        self._build_online()
        self._build_settings()
        theme.set_button_cursors(self.root)

    def _stat_card(self, parent, label: str, variable: tk.StringVar) -> tk.Frame:
        """A bordered stat card: small caption over a large value."""
        outer = tk.Frame(parent, background=theme.BORDER, borderwidth=0, highlightthickness=0)
        self._themed.append((outer, "border"))
        inner = ttk.Frame(outer, style="Tile.TFrame", padding=(18, 14))
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        ttk.Label(inner, text=label, style="TileMuted.TLabel").pack(anchor="w")
        ttk.Label(inner, textvariable=variable, style="TileStat.TLabel").pack(
            anchor="w", pady=(4, 0))
        return outer

    def _build_home(self) -> None:
        hero = ttk.Frame(self.home_tab)
        hero.pack(fill="x", pady=(6, 2))
        ttk.Label(hero, text="欢迎回来，", style="Hero.TLabel").pack(side="left")
        self.home_hero_name = tk.StringVar(value=self.player or "棋手")
        ttk.Label(hero, textvariable=self.home_hero_name, style="HeroAccent.TLabel").pack(
            side="left")
        ttk.Label(self.home_tab,
                  text="从这里快速开始：同步棋谱、训练棋风，再和另一个自己对弈。",
                  style="HeroSub.TLabel").pack(anchor="w", pady=(4, 24))

        self.home_stat_games = tk.StringVar(value="—")
        self.home_stat_winrate = tk.StringVar(value="—")
        self.home_stat_model = tk.StringVar(value="—")
        self.home_stat_engine = tk.StringVar(value="—")
        stats_row = ttk.Frame(self.home_tab)
        stats_row.pack(fill="x", pady=(0, 26))
        cards = (("棋谱", self.home_stat_games), ("本人胜率", self.home_stat_winrate),
                 ("棋风模型", self.home_stat_model), ("引擎", self.home_stat_engine))
        for index, (label, variable) in enumerate(cards):
            card = self._stat_card(stats_row, label, variable)
            card.pack(side="left", fill="x", expand=True,
                      padx=(0, 12 if index < len(cards) - 1 else 0))

        ttk.Label(self.home_tab, text="快捷入口", style="Section.TLabel").pack(
            anchor="w", pady=(0, 10))
        actions = ttk.Frame(self.home_tab)
        actions.pack(fill="x")
        quick = (("同步棋谱", "Accent.TButton", self._home_sync),
                 ("导入 SGF", "TButton", self._home_import),
                 ("开始对弈", "Accent.TButton", self._home_spar),
                 ("训练棋风", "TButton", self._home_train))
        for index, (text, style_name, command) in enumerate(quick):
            ttk.Button(actions, text=text, style=style_name, command=command).pack(
                side="left", fill="x", expand=True,
                padx=(0, 12 if index < len(quick) - 1 else 0))

        ttk.Label(self.home_tab,
                  text="所有棋谱、模型和分析只保存在本机；联机对弈使用独立账号，不连接野狐。",
                  style="Note.TLabel").pack(fill="x", pady=(24, 0))

    def refresh_home(self) -> None:
        self.home_hero_name.set(self.player or "棋手")
        self.home_stat_games.set(f"{len(self.games)} / {self.game_limit}")
        known = wins = 0
        for game in self.games:
            won = game.to_summary(self.player)["won"]
            if won is not None:
                known += 1
                wins += won
        self.home_stat_winrate.set(
            f"{wins / known:.1%} · {known} 盘有胜负" if known else "暂无已知胜负")
        self.home_stat_model.set(
            f"已训练 · {self.model.game_count} 盘" if self.model else "未训练")
        try:
            self.settings.validate()
            engine = "KataGo 已配置"
        except RuntimeError:
            engine = "轻量陪练"
        self.home_stat_engine.set(engine)

    def _home_sync(self) -> None:
        self.tabs.select(self.library_tab)
        self.sync_fox_records()

    def _home_import(self) -> None:
        self.tabs.select(self.library_tab)
        self.import_files()

    def _home_spar(self) -> None:
        self.tabs.select(self.spar_tab)
        self.new_spar()

    def _home_train(self) -> None:
        self.train()

    def _build_library(self) -> None:
        toolbar = ttk.Frame(self.library_tab)
        toolbar.pack(fill="x", pady=(0, 18))
        ttk.Button(toolbar, text="同步棋谱", style="Accent.TButton", padding=(9, 7),
                   command=self.sync_fox_records).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="导入 SGF", padding=(9, 7), command=self.import_files).pack(
            side="left", padx=(0, 8))
        ttk.Button(toolbar, text="扫描文件夹", padding=(9, 7), command=self.import_folder).pack(
            side="left", padx=(0, 8))
        ttk.Button(toolbar, text="分析选中", padding=(9, 7),
                   command=lambda: self.analyze_games(False)).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar, text="分析列表", padding=(9, 7),
                   command=lambda: self.analyze_games(True)).pack(
            side="left", padx=(0, 8)
        )
        self.delete_games_button = ttk.Button(
            toolbar, text="删除选中", style="Danger.TButton", padding=(9, 7),
            command=self.delete_selected_games, state="disabled")
        self.delete_games_button.pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="清空棋谱", style="Danger.TButton", padding=(9, 7),
                   command=self.clear_library).pack(side="left")
        ttk.Button(toolbar, text="训练棋风", padding=(9, 7),
                   command=self.train).pack(side="right")
        self.library_summary = tk.StringVar()
        ttk.Label(self.library_tab, textvariable=self.library_summary,
                  style="Stat.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(self.library_tab,
                  text="按对局日期选取本人最近棋谱，盘数上限可在设置中修改。双击一盘进入复盘；"
                       "可多选后删除；文件夹可在设置中开启增量扫描。",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 16))
        list_header = ttk.Frame(self.library_tab)
        list_header.pack(fill="x", pady=(0, 8))
        ttk.Label(list_header, text="最近对局", style="Section.TLabel").pack(side="left")
        ttk.Label(list_header, text="双击进入复盘 · 可多选删除",
                  style="Muted.TLabel").pack(side="right")
        outer, inner = self._bordered(self.library_tab)
        outer.pack(fill="both", expand=True)
        columns = ("date", "color", "opponent", "rank", "result", "moves", "analysis")
        self.game_table = ttk.Treeview(inner, columns=columns, show="headings",
                                       selectmode="extended")
        headings = ("日期", "本人执色", "对手", "对手段位", "结果", "手数", "分析状态")
        widths = (125, 90, 230, 100, 120, 75, 140)
        for column, title, width in zip(columns, headings, widths):
            self.game_table.heading(column, text=title)
            self.game_table.column(column, width=width, anchor="center", minwidth=60)
        self.game_table.tag_configure("even", background=theme.SURFACE)
        self.game_table.tag_configure("odd", background=theme.SURFACE_ALT)
        self.game_table.tag_configure("empty", foreground=theme.FAINT)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.game_table.yview)
        self.game_table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.game_table.pack(fill="both", expand=True)
        self.game_table.bind("<Double-1>", lambda event: self.open_selected())
        self.game_table.bind("<Return>", lambda event: self.open_selected())
        self.game_table.bind("<<TreeviewSelect>>", self._on_game_selection)

    def _build_review(self) -> None:
        self.review_tab.columnconfigure(0, weight=3)
        self.review_tab.columnconfigure(1, weight=2)
        self.review_tab.rowconfigure(0, weight=1)
        left = ttk.Frame(self.review_tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1, minsize=260)
        self.review_title = tk.StringVar(value="先从棋谱库选择一盘棋")
        ttk.Label(left, textvariable=self.review_title, style="Section.TLabel",
                  wraplength=650).grid(row=0, column=0, sticky="w", pady=(0, 10))
        board_outer, board_inner = self._bordered(left)
        board_outer.grid(row=1, column=0, sticky="nsew")
        self.review_board = BoardView(
            board_inner, on_play=self._review_board_click,
            on_navigate=self._review_navigate,
            on_point=self._review_board_hover, on_key=self._review_board_key)
        self.review_board.pack(fill="both", expand=True)
        # Review navigation is turn/PV based; hide the placement cursor square.
        self.review_board.show_cursor = False
        self.review_board.bind("<MouseWheel>", self._review_wheel)
        navigation = ttk.Frame(left)
        navigation.grid(row=2, column=0, sticky="ew", pady=(10, 6))
        for label, command in (("|◀", lambda: self.set_turn(0)),
                               ("◀", lambda: self.set_turn(self.turn - 1)),
                               ("▶", lambda: self.set_turn(self.turn + 1)),
                               ("▶|", lambda: self.set_turn(100000))):
            ttk.Button(navigation, text=label, width=4, command=command).pack(side="left", padx=(0, 4))
        self.turn_text = tk.StringVar(value="0 / 0 手")
        ttk.Label(navigation, textvariable=self.turn_text, style="Muted.TLabel").pack(side="right")
        self.turn_slider = tk.Scale(left, from_=0, to=0, orient="horizontal", showvalue=False,
                                    command=self._slider_turn, background=theme.SURFACE,
                                    troughcolor=theme.BORDER, activebackground=theme.ACCENT,
                                    highlightthickness=0, borderwidth=0, sliderrelief="flat",
                                    width=10)
        self.turn_slider.grid(row=3, column=0, sticky="ew")
        self._themed.append((self.turn_slider, "scale"))
        self.variation_bar = ttk.Frame(left)
        self.variation_bar.grid(row=4, column=0, sticky="w", pady=(7, 0))
        self.variation_prev_button = ttk.Button(
            self.variation_bar, text="变化上一手",
            command=lambda: self.step_variation(-1), state="disabled")
        self.variation_prev_button.pack(side="left")
        self.variation_step_text = tk.StringVar(value="")
        ttk.Label(self.variation_bar, textvariable=self.variation_step_text,
                  style="Muted.TLabel").pack(side="left", padx=10)
        self.variation_next_button = ttk.Button(
            self.variation_bar, text="变化下一手",
            command=lambda: self.step_variation(1), state="disabled")
        self.variation_next_button.pack(side="left")
        self.variation_analyze_button = ttk.Button(
            self.variation_bar, text="分析此变化", style="Accent.TButton",
            command=self.analyze_variation, state="disabled")
        self.variation_analyze_button.pack(side="left", padx=(12, 0))
        self.variation_bar.grid_remove()
        right = ttk.Frame(self.review_tab)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1, minsize=120)
        review_header = ttk.Frame(right)
        review_header.grid(row=0, column=0, sticky="ew")
        review_header.columnconfigure(0, weight=1)
        ttk.Label(review_header, text="本人落子 · 关键转折", style="Section.TLabel").grid(
            row=0, column=0, sticky="w")
        self.analyze_current_button = ttk.Button(
            review_header, text="分析这盘棋", style="Accent.TButton",
            command=self.analyze_current_game, state="disabled")
        self.analyze_current_button.grid(row=0, column=1, sticky="e")
        self.analysis_summary = tk.StringVar(value="先从棋谱库选择一盘棋。")
        ttk.Label(right, textvariable=self.analysis_summary, wraplength=430,
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 9))
        table_outer, table_inner = self._bordered(right)
        table_outer.grid(row=2, column=0, sticky="nsew")
        self.move_table = ttk.Treeview(table_inner, columns=("turn", "move", "best", "loss"),
                                       show="headings", height=12)
        for column, title in (("turn", "手数"), ("move", "实战"), ("best", "建议"),
                              ("loss", "损失/目")):
            self.move_table.heading(column, text=title)
            self.move_table.column(column, width=80, anchor="center", minwidth=55)
        self.move_table.tag_configure("even", background=theme.SURFACE)
        self.move_table.tag_configure("odd", background=theme.SURFACE_ALT)
        self.move_table.tag_configure("empty", foreground=theme.FAINT)
        move_scroll = ttk.Scrollbar(table_inner, orient="vertical", command=self.move_table.yview)
        self.move_table.configure(yscrollcommand=move_scroll.set)
        move_scroll.pack(side="right", fill="y")
        self.move_table.pack(fill="both", expand=True)
        self.move_table.bind("<<TreeviewSelect>>", lambda event: self.select_report_move())
        buttons = ttk.Frame(right)
        buttons.grid(row=3, column=0, sticky="ew", pady=8)
        ttk.Button(buttons, text="查看推荐变化", command=self.show_candidates).pack(side="left")
        ttk.Button(buttons, text="返回实战", command=self.return_to_actual).pack(
            side="left", padx=7
        )
        ttk.Button(buttons, text="更新棋风", command=lambda: self.train(False)).pack(
            side="left")
        ttk.Button(buttons, text="导出 CSV", command=self.export_analysis).pack(side="right")
        right.rowconfigure(4, minsize=80)
        text_host = ttk.Frame(right)
        text_host.grid(row=4, column=0, sticky="new", pady=(0, 8))
        text_host.columnconfigure(0, weight=1)
        self.pv_text = tk.Text(
            text_host, height=7, wrap="word", font=(theme.FONT, 9),
            background=theme.SURFACE, foreground=theme.INK, insertbackground=theme.INK,
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground=theme.BORDER, highlightcolor=theme.BORDER,
            padx=8, pady=6, spacing1=2, spacing3=2,
        )
        self._themed.append((self.pv_text, "text"))
        text_scroll = ttk.Scrollbar(text_host, orient="vertical", command=self.pv_text.yview)
        self.pv_text.configure(yscrollcommand=text_scroll.set)
        text_scroll.pack(side="right", fill="y")
        self.pv_text.pack(side="left", fill="both", expand=True)
        self._configure_pv_tags()
        self._set_pv_text("损失为前后局面目差变化的近似值，受搜索深度影响；不是绝对真值。")
        self.chart = WinrateChart(right, self.set_turn)
        self.chart.grid(row=5, column=0, sticky="ew")

    def _build_profile(self) -> None:
        toolbar = ttk.Frame(self.profile_tab)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="从你的实战中，学习你的选择", style="PageTitle.TLabel").pack(
            side="left")
        ttk.Button(toolbar, text="重新训练棋风模型", style="Accent.TButton",
                   command=self.train).pack(side="right")
        ttk.Label(self.profile_tab, text="只学习本人落子；报告是描述统计，不是棋力评级。",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 14))
        self.profile_stat_games = tk.StringVar(value="—")
        self.profile_stat_moves = tk.StringVar(value="—")
        self.profile_stat_record = tk.StringVar(value="—")
        self.profile_stat_colors = tk.StringVar(value="—")
        stats_row = ttk.Frame(self.profile_tab)
        stats_row.pack(fill="x", pady=(0, 14))
        for index, (label, variable) in enumerate((
                ("训练盘数", self.profile_stat_games),
                ("本人落子", self.profile_stat_moves),
                ("胜负", self.profile_stat_record),
                ("执黑 / 执白", self.profile_stat_colors))):
            outer = tk.Frame(stats_row, background=theme.BORDER, borderwidth=0, highlightthickness=0)
            self._themed.append((outer, "border"))
            outer.pack(side="left", fill="x", expand=True, padx=(0, 10 if index < 3 else 0))
            inner = ttk.Frame(outer, style="Tile.TFrame", padding=(14, 9))
            inner.pack(fill="both", expand=True, padx=1, pady=1)
            ttk.Label(inner, text=label, style="TileMuted.TLabel").pack(anchor="w")
            ttk.Label(inner, textvariable=variable, style="TileStat.TLabel").pack(
                anchor="w", pady=(2, 0))
        text_host = ttk.Frame(self.profile_tab)
        text_host.pack(fill="both", expand=True)
        self.profile_text = tk.Text(
            text_host, wrap="word", font=(theme.FONT, 10), background=theme.SURFACE,
            foreground=theme.INK, insertbackground=theme.INK, relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.BORDER,
            padx=26, pady=22, spacing1=6, spacing3=10,
        )
        profile_scroll = ttk.Scrollbar(text_host, orient="vertical",
                                       command=self.profile_text.yview)
        self.profile_text.configure(yscrollcommand=profile_scroll.set)
        self._themed.append((self.profile_text, "text"))
        profile_scroll.pack(side="right", fill="y")
        self.profile_text.pack(side="left", fill="both", expand=True)
        self._replace_text(self.profile_text, "尚未训练。导入棋谱后点击“重新训练棋风模型”。\n\n"
                           "仅学习本人落子，不混入对手数据。只有 1 盘时只能形成初步偏好；"
                           "不会将它描述为稳定的个人模型。")

    def _build_spar(self) -> None:
        self.spar_tab.columnconfigure(0, weight=3)
        self.spar_tab.columnconfigure(1, weight=2)
        self.spar_tab.rowconfigure(0, weight=1)
        board_outer, board_inner = self._bordered(self.spar_tab)
        board_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 23))
        self.spar_view = BoardView(board_inner, self.human_play, hover=self._spar_ghost)
        self.spar_view.pack(fill="both", expand=True)
        control_host = ttk.Frame(self.spar_tab)
        control_host.grid(row=0, column=1, sticky="nsew")
        control_canvas = tk.Canvas(control_host, background=theme.SURFACE, highlightthickness=0,
                                   width=410)
        self.control_canvas = control_canvas
        self._themed.append((control_canvas, "surface_canvas"))
        control_scroll = ttk.Scrollbar(control_host, orient="vertical", command=control_canvas.yview)
        control_canvas.configure(yscrollcommand=control_scroll.set)
        control_scroll.pack(side="right", fill="y")
        control_canvas.pack(fill="both", expand=True)
        controls = ttk.Frame(control_canvas, padding=(2, 2, 12, 6))
        control_window = control_canvas.create_window((0, 0), window=controls, anchor="nw")
        controls.bind("<Configure>", lambda event: control_canvas.configure(
            scrollregion=control_canvas.bbox("all")))
        control_canvas.bind("<Configure>", lambda event: control_canvas.itemconfigure(
            control_window, width=event.width))
        control_canvas.bind("<MouseWheel>", lambda event: control_canvas.yview_scroll(
            -int(event.delta / 120), "units"))

        def section(text: str) -> None:
            ttk.Separator(controls, orient="horizontal").pack(fill="x", pady=(12, 7))
            ttk.Label(controls, text=text, style="Section.TLabel").pack(anchor="w", pady=(0, 6))

        self.opponent_title = tk.StringVar(value="对手：另一个你")
        ttk.Label(controls, textvariable=self.opponent_title,
                  style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(controls, text="仅在本软件中对弈，不连接野狐在线棋局。",
                  style="Muted.TLabel", wraplength=380).pack(anchor="w", pady=(4, 0))

        section("对弈风格")
        self.opponent_var = tk.StringVar(value=SELF_STYLE)
        style_row = ttk.Frame(controls)
        style_row.pack(fill="x")
        style_row.columnconfigure(0, weight=1)
        self.opponent_combo = ttk.Combobox(
            style_row, textvariable=self.opponent_var, values=self._legend_choices(),
            state="readonly", width=18)
        self.opponent_combo.grid(row=0, column=0, sticky="ew")
        self.opponent_combo.bind("<<ComboboxSelected>>",
                                 lambda _event: self._on_opponent_change())
        ttk.Button(style_row, text="导入名手…", command=self.import_legend).grid(
            row=0, column=1, padx=(8, 0))
        self.opponent_note = tk.StringVar(value="使用你的个人棋风模型。")
        ttk.Label(controls, textvariable=self.opponent_note, wraplength=380,
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Label(controls, text="请只导入你有权使用的 SGF；软件不会自动下载职业棋谱。",
                  wraplength=380, style="Note.TLabel", justify="left").pack(
                      anchor="w", pady=(4, 0))
        ttk.Button(controls, text="批量导入多个名手…",
                   command=self.import_legends_batch).pack(fill="x", pady=(6, 0))
        ttk.Label(controls, text="批量训练在后台低优先级运行，可以继续用电脑；底部可取消。",
                  wraplength=380, style="Muted.TLabel", justify="left").pack(
                      anchor="w", pady=(4, 0))
        self.delete_legend_button = ttk.Button(
            controls, text="删除当前名手模型", style="Danger.TButton",
            command=self.delete_current_legend, state="disabled")
        self.delete_legend_button.pack(fill="x", pady=(6, 0))

        section("对局设置")
        setup = ttk.Frame(controls)
        setup.pack(fill="x")
        color_box = ttk.Frame(setup)
        color_box.pack(side="left", fill="x", expand=True)
        ttk.Label(color_box, text="你的执色").pack(anchor="w")
        self.color_var = tk.StringVar(value="执黑（先行）")
        ttk.Combobox(color_box, textvariable=self.color_var, values=("执黑（先行）", "执白"),
                     state="readonly", width=17).pack(anchor="w", pady=(6, 0))
        size_box = ttk.Frame(setup)
        size_box.pack(side="left", padx=(14, 0))
        ttk.Label(size_box, text="棋盘").pack(anchor="w")
        self.size_var = tk.StringVar(value="19")
        ttk.Combobox(size_box, textvariable=self.size_var, values=("19", "13", "9"),
                     state="readonly", width=7).pack(anchor="w", pady=(6, 0))

        self.imitation_value = tk.StringVar(value="55%")
        head = ttk.Frame(controls)
        head.pack(fill="x", pady=(10, 2))
        ttk.Label(head, text="模仿程度").pack(side="left")
        ttk.Label(head, textvariable=self.imitation_value, style="Muted.TLabel").pack(side="right")
        self.imitation_var = tk.DoubleVar(value=0.55)
        ttk.Scale(controls, from_=0, to=1, variable=self.imitation_var,
                  command=lambda value: self.imitation_value.set(f"{float(value):.0%}")).pack(
                      fill="x", pady=(3, 2))
        ttk.Label(controls, text="左偏引擎棋力，右偏个人习惯", style="Muted.TLabel").pack(anchor="w")

        self.match_strength_var = tk.BooleanVar(value=True)
        self.match_strength_check = ttk.Checkbutton(
            controls, text="棋力匹配：按你的实战损失分布限制选点（需已完成分析）",
            variable=self.match_strength_var)
        self.match_strength_check.pack(anchor="w", pady=(8, 0))
        self.strength_value = tk.StringVar(value="0.50")
        head = ttk.Frame(controls)
        head.pack(fill="x", pady=(7, 2))
        ttk.Label(head, text="选点强度").pack(side="left")
        ttk.Label(head, textvariable=self.strength_value, style="Muted.TLabel").pack(side="right")
        self.strength_var = tk.DoubleVar(value=0.5)
        ttk.Scale(controls, from_=0, to=1, variable=self.strength_var,
                  command=lambda value: self.strength_value.set(f"{float(value):.2f}")).pack(
                      fill="x", pady=(3, 2))
        ttk.Label(controls, text="0.5 约等于你的实战水平，越高越强（非段位标定）",
                  style="Muted.TLabel").pack(anchor="w")
        self.use_engine_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="使用 KataGo（未配置时使用轻量陪练）",
                        variable=self.use_engine_var).pack(anchor="w", pady=(8, 0))

        section("行棋")
        ttk.Button(controls, text="开始新对局", style="Accent.TButton",
                   command=self.new_spar).pack(fill="x", pady=(0, 10))
        row = ttk.Frame(controls)
        row.pack(fill="x")
        for index, (label, command, button_style) in enumerate((
                ("停一手", lambda: self.human_play(None, None), "TButton"),
                ("悔棋", self.undo, "TButton"),
                ("认输", self.resign, "Danger.TButton"))):
            row.columnconfigure(index, weight=1)
            ttk.Button(row, text=label, command=command, style=button_style).grid(
                row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0))
        ttk.Button(controls, text="试下 / 变化推演", command=self.open_trial).pack(
            fill="x", pady=(10, 0))

        section("形势与终局")
        judgment = ttk.Frame(controls)
        judgment.pack(fill="x")
        judgment.columnconfigure(0, weight=1)
        judgment.columnconfigure(1, weight=1)
        ttk.Button(judgment, text="形势判断（KataGo）", command=self.assess_spar).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(judgment, text="终局判定", command=self.score_spar).grid(
            row=0, column=1, sticky="ew")
        self.assessment_text = tk.StringVar(value="可在自己回合查看形势；估计值不等于终局结果。")
        ttk.Label(controls, textvariable=self.assessment_text, wraplength=380,
                  justify="left", style="Note.TLabel").pack(fill="x", pady=(11, 0))

        section("保存与状态")
        ttk.Button(controls, text="保存并复盘", style="Accent.TButton",
                   command=self.save_and_review).pack(fill="x")
        ttk.Button(controls, text="导出 SGF 文件…", command=self.save_spar).pack(
            fill="x", pady=(8, 0))
        ttk.Label(controls, text="保存后自动加入棋谱库并打开复盘；"
                  "在复盘页点“更新棋风”即可把本局纳入模型。",
                  wraplength=380, style="Muted.TLabel", justify="left").pack(
                      anchor="w", pady=(7, 0))
        self.spar_status = tk.StringVar(value="尚未开始。先导入棋谱并训练棋风模型。")
        ttk.Label(controls, textvariable=self.spar_status, wraplength=380,
                  style="Note.TLabel", justify="left").pack(fill="x", pady=(10, 0))
        ttk.Label(controls, text="键盘：点击棋盘后，用方向键移动，回车落子。\n"
                  "双方连续停一手后确认死子，按中国数子规则判定胜负。"
                  "形势判断需要 KataGo，仅作参考，不替代终局确认。",
                  wraplength=380, style="Muted.TLabel", justify="left").pack(anchor="w", pady=(10, 4))
        self._apply_opponent_selection(initial=True)

    def _build_online(self) -> None:
        self.online_panel = OnlinePanel(self.online_tab, self)
        self.online_panel.pack(fill="both", expand=True)

    def _build_settings(self) -> None:
        page = self.settings_tab
        canvas = tk.Canvas(page, background=theme.SURFACE, highlightthickness=0)
        self._themed.append((canvas, "surface_canvas"))
        scroll = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        body = ttk.Frame(canvas, padding=(0, 0, 14, 6))
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(
            -int(event.delta / 120), "units"))
        frame = body
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="设置", style="PageTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="账号、棋谱数据与本地 KataGo 引擎", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))
        self.player_var = tk.StringVar(value=self.player)
        self.engine_var = tk.StringVar(value=self.settings.executable)
        self.model_var = tk.StringVar(value=self.settings.model)
        self.visits_var = tk.StringVar(value=str(self.settings.visits))
        self.limit_var = tk.StringVar(value=str(self.game_limit))
        self.folder_var = tk.StringVar(value=self.library.setting("watch_folder", ""))
        self.watch_var = tk.BooleanVar(value=self.library.setting("watch_enabled", False))

        def section(title: str, row: int) -> int:
            ttk.Separator(frame, orient="horizontal").grid(
                row=row, column=0, columnspan=3, sticky="ew", pady=(16, 10))
            ttk.Label(frame, text=title, style="Section.TLabel").grid(
                row=row + 1, column=0, columnspan=3, sticky="w")
            return row + 2

        def field(row: int, label: str, variable: tk.Variable, kind: str | None = None) -> None:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w",
                                              padx=(0, 18), pady=9)
            if kind == "limit":
                ttk.Combobox(frame, textvariable=variable, width=12, state="readonly",
                             values=("100", "300", "500", "1000")).grid(
                    row=row, column=1, sticky="w")
            else:
                ttk.Entry(frame, textvariable=variable, width=65).grid(
                    row=row, column=1, sticky="ew")
            if kind in {"exe", "model", "folder"}:
                ttk.Button(frame, text="浏览",
                           command=lambda v=variable, k=kind: self._browse(v, k)).grid(
                    row=row, column=2, padx=(8, 0))

        row = section("账号与棋谱", 2)
        field(row, "野狐用户名", self.player_var)
        field(row + 1, "同步/训练盘数上限", self.limit_var, "limit")
        field(row + 2, "棋谱文件夹", self.folder_var, "folder")
        ttk.Checkbutton(frame, text="每 30 秒扫描一次文件夹，自动导入新增或变更的 SGF",
                        variable=self.watch_var).grid(
            row=row + 3, column=1, sticky="w", pady=(8, 2))

        row = section("KataGo 引擎", row + 4)
        field(row, "KataGo 程序", self.engine_var, "exe")
        field(row + 1, "KataGo 网络模型", self.model_var, "model")
        field(row + 2, "每局面访问次数", self.visits_var)

        actions = ttk.Frame(frame)
        actions.grid(row=row + 3, column=1, sticky="w", pady=(16, 0))
        ttk.Button(actions, text="保存设置", style="Accent.TButton",
                   command=self.save_settings).pack(side="left")
        ttk.Button(actions, text="测试 KataGo", command=self.test_engine).pack(
            side="left", padx=10)
        engine_note = (
            "已自动使用随包 KataGo；如需更换，可浏览选择其他引擎或模型。"
            "许可见上级目录 引擎/LICENSE-KataGo.txt 和 LICENSE-Model.txt。"
            if self.bundled_engine else
            "KataGo 引擎和模型不随程序分发；请从官方来源下载后在设置中选择。"
        )
        notes = (
            engine_note + "\n"
            "引擎：https://github.com/lightvector/KataGo/releases     "
            "模型：https://katagotraining.org/\n"
            "数据目录：" + str(self.library.directory) + "\n"
            "不收集野狐密码、Cookie 或登录令牌；切换引擎、模型或访问次数后需要重新分析。"
        )
        ttk.Label(frame, text=notes, wraplength=940, style="Note.TLabel",
                  justify="left").grid(
            row=row + 4, column=0, columnspan=3, sticky="new", pady=(18, 0))

    @staticmethod
    def _replace_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _model_path(self) -> Path:
        digest = hashlib.sha256(self.player.encode()).hexdigest()[:16]
        return self.library.directory / f"style-{digest}.json"

    def _load_model(self) -> None:
        self.model = None
        path = self._model_path()
        if path.is_file():
            try:
                model = StyleModel.load(path)
                if model.player == self.player:
                    self.model = model
                    self._replace_text(self.profile_text, model.report_text())
            except (ValueError, TypeError, OSError):
                self._notify("棋风模型无法读取，请重新训练", "error")
        if self.model is None:
            self._replace_text(self.profile_text, "尚未训练该棋手的模型。请导入棋谱后点击训练。")
        self._update_profile_stats()

    def _update_profile_stats(self) -> None:
        model = self.model
        if model is None:
            for variable in (self.profile_stat_games, self.profile_stat_moves,
                             self.profile_stat_record, self.profile_stat_colors):
                variable.set("—")
            return
        stats = model.statistics
        self.profile_stat_games.set(str(model.game_count))
        self.profile_stat_moves.set(f"{model.move_count:,}")
        self.profile_stat_record.set(f"{stats.get('wins', 0)} 胜 / {stats.get('losses', 0)} 负")
        self.profile_stat_colors.set(
            f"{stats.get('black_games', 0)} / {stats.get('white_games', 0)}")

    @staticmethod
    def _clamp_limit(value: Any) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return 100
        return max(1, min(MAX_SYNC_GAMES, limit))

    def _load_strength_profile(self) -> None:
        path = self.library.directory / "strength-profile.json"
        self.strength_profile = load_profile(path, self.player, self.settings.key)
        self.strength_profile_key = self.settings.key if self.strength_profile else ""

    def _active_profile(self) -> StrengthProfile | None:
        if self.strength_profile_key and self.strength_profile_key == self.settings.key:
            return self.strength_profile
        return None

    def _idle(self) -> bool:
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前任务完成，或先点击底部的“取消任务”。")
            return False
        return True

    def _run(self, description: str, work: Callable, finished: Callable | None = None,
             low_priority: bool = False) -> None:
        if not self._idle():
            return
        self.busy = True
        self.cancel = threading.Event()
        self.finished = finished
        self._notify(description, "busy")
        self.progress["value"] = 0
        self.progress_text.set("0%")
        self.cancel_button.configure(state="normal")
        self.root.configure(cursor="watch")

        def progress(done: int, total: int) -> None:
            self.events.put(("progress", (done, total)))

        def worker() -> None:
            if low_priority:
                _lower_current_thread_priority()
            try:
                result = work(progress, self.cancel)
                self.events.put(("done", result))
            except Exception as error:
                self.events.put(("error", (str(error), traceback.format_exc())))
        threading.Thread(target=worker, daemon=True).start()

    def _settle_task(self) -> None:
        """Common UI reset when a background task ends (done or error)."""
        self.busy = False
        self.finished = None
        self.cancel_button.configure(state="disabled")
        self.root.configure(cursor="")
        if getattr(self, "review_variation", None) is not None:
            self._update_variation_controls()

    def _drain_events(self) -> None:
        if self.closed:
            return
        for _ in range(200):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                done, total = payload
                percent = done / max(1, total) * 100
                self.progress["value"] = percent
                self.progress_text.set(f"{percent:.0f}%")
            elif kind == "done":
                callback = self.finished
                self._settle_task()
                self.progress["value"] = 100
                self.progress_text.set("100%")
                self._notify("任务完成 · 数据已保存在本机", "success")
                if callback:
                    try:
                        callback(payload)
                    except Exception as error:
                        messagebox.showerror("界面更新失败", str(error))
            elif kind == "error":
                self._settle_task()
                self.progress_text.set("")
                message, detail = payload
                if self.cancel.is_set():
                    self._notify("任务已取消 · 已完成的部分已保留", "warning")
                else:
                    self._notify("任务失败 · 详情见弹窗与错误日志", "error")
                    log = self.library.directory / "last-error.log"
                    log.write_text(detail, encoding="utf-8")
                    messagebox.showerror("任务未完成", message + f"\n\n错误日志：{log}")
                if self.spar_active and self.spar_board.to_play != self.human_color:
                    self.spar_active = False
                    self.spar_status.set("陪练任务中断。可保存棋谱或重新开始。")
        self.root.after(100, self._drain_events)

    def refresh_library(self) -> None:
        previous = self.game_table.selection()
        self.games = self.library.games(self.player, self.game_limit) if self.player else []
        analyzed = self.library.analyzed_game_ids(self.settings.key)
        self.game_table.delete(*self.game_table.get_children())
        known = wins = 0
        for index, game in enumerate(self.games):
            summary = game.to_summary(self.player)
            if summary["won"] is not None:
                known += 1
                wins += summary["won"]
            self.game_table.insert("", "end", iid=str(game.database_id), values=(
                summary["date"], "黑" if summary["color"] == "B" else "白", summary["opponent"],
                summary["opponent_rank"], summary["result"] or "未知", summary["move_count"],
                "已分析" if game.database_id in analyzed else "待分析",
            ), tags=("odd" if index % 2 else "even",))
        ratio = f"{wins / known:.1%}（{known} 盘有胜负）" if known else "暂无已知胜负"
        self.library_summary.set(f"最近 {len(self.games)} / {self.game_limit} 盘  ·  本人胜率 {ratio}")
        children = self.game_table.get_children()
        if self.model is not None:
            current_hashes = {game.sha256 for game in self.games}
            stale = current_hashes != set(self.model.trained_hashes)
            note = "\n\n⚠ 棋谱库已更新，当前模型尚未重新训练。" if stale else ""
            self._replace_text(self.profile_text,
                               self.model.report_text() + note + self._analysis_profile())
        self._update_profile_stats()
        if children:
            selection = previous[0] if previous and previous[0] in children else children[0]
            self.game_table.selection_set(selection)
            self.load_selected()
        else:
            self.selected_game = None
            self.selected_analysis = None
            self.rows = []
            self.review_variation = None
            self.review_candidates = None
            self._hide_variation_controls()
            self._set_pv_text(self._pv_intro_text())
            self.review_title.set("没有该棋手的棋谱")
            self.analysis_summary.set("先从棋谱库选择一盘棋。")
            self.analyze_current_button.configure(text="分析这盘棋", state="disabled")
            self.review_board.show(GoBoard())
            self.chart.set_points([])
            self.move_table.delete(*self.move_table.get_children())
            self.game_table.insert("", "end", iid="__empty__", values=(
                "—", "—", "暂无棋谱：点击上方“同步野狐棋谱”或“导入 SGF”开始",
                "—", "—", "—", "—",
            ), tags=("empty",))
        self._update_library_actions()
        self.refresh_home()

    def _on_game_selection(self, event: tk.Event | None = None) -> None:
        self._update_library_actions()
        selection = self.game_table.selection()
        # A queued selection event for the game already on screen must not tear
        # down an open candidate/PV overlay or a running branch analysis.
        if (selection and selection[0].isdigit() and self.selected_game is not None
                and int(selection[0]) == self.selected_game.database_id
                and (self.review_candidates is not None or self.review_variation is not None)):
            return
        self.load_selected()

    def _update_library_actions(self) -> None:
        selected = any(iid != "__empty__" for iid in self.game_table.selection())
        self.delete_games_button.configure(state="normal" if selected else "disabled")

    def delete_selected_games(self) -> None:
        if not self._idle():
            return
        ids = sorted({int(iid) for iid in self.game_table.selection() if iid.isdigit()})
        if not ids:
            messagebox.showinfo("没有选中棋谱", "请先在棋谱列表中选择要删除的棋谱。")
            return
        if not messagebox.askyesno(
            "删除选中棋谱",
            f"从棋谱库删除选中的 {len(ids)} 盘棋谱及对应分析缓存？\n\n"
            "不会删除原始 SGF 文件，也不会删除已训练的棋风模型。\n"
            "如果开启了自动扫描，重启后原 SGF 可能被重新导入；"
            "要交接电脑，建议使用“清空棋谱”。",
            icon="warning",
        ):
            return
        try:
            deleted = self.library.delete_games(ids)
        except Exception as error:
            messagebox.showerror("删除失败", str(error))
            return
        self.refresh_library()
        self._notify(f"已从棋谱库删除 {deleted} 盘棋谱", "success")

    def clear_library(self) -> None:
        if not self._idle():
            return
        total = self.library.game_count()
        if total == 0:
            messagebox.showinfo("棋谱库为空", "当前棋谱库没有可删除的棋谱。")
            return
        if not messagebox.askyesno(
            "清空棋谱库",
            f"将删除棋谱库中的全部 {total} 盘棋谱，以及所有 KataGo 分析缓存。\n\n"
            "原始 SGF 文件、引擎设置和联机账号不会被删除。\n"
            "为避免旧棋谱在下次启动时被自动扫描重新导入，清空时会同时关闭"
            "“自动扫描”并清除原棋谱文件夹设置。\n"
            "清空后，请在“设置”中改成下一个使用者的野狐用户名，"
            "再同步他的棋谱并训练。\n\n是否继续？",
            icon="warning",
        ):
            return
        if self.spar_active or self.spar_pending_score or (
                self.spar_board.move_history and not self.spar_saved):
            messagebox.showinfo("先结束对局", "请先结束并保存当前陪练对局，再清空棋谱库。")
            return
        clean_models = messagebox.askyesno(
            "同时清理棋风数据？",
            "是否同时删除本机所有棋风模型和棋力匹配档案？\n\n"
            "选择“是”：下一位使用者必须用自己的棋谱重新训练，适合交接电脑。\n"
            "选择“否”：只删除棋谱和分析缓存，旧模型文件保留。",
            icon="warning",
        )
        try:
            removed = self.library.clear_games()
        except Exception as error:
            messagebox.showerror("清空失败", str(error))
            return
        if clean_models:
            self._clear_style_data()
        self.library.set_setting("watch_enabled", False)
        self.library.set_setting("watch_folder", "")
        self.watch_var.set(False)
        self.folder_var.set("")
        self.selected_game = None
        self.selected_analysis = None
        self.rows = []
        self.refresh_library()
        detail = (f"棋谱 {removed['games']} 盘 · 分析缓存 {removed['analyses']} 条"
                  + (" · 棋风模型和棋力档案已清理" if clean_models else " · 棋风模型保留")
                  + " · 自动扫描已关闭")
        self._notify(f"棋谱库已清空：{detail}", "success")

    def _clear_style_data(self) -> None:
        for path in self.library.directory.glob("style-*.json"):
            try:
                path.unlink()
            except OSError:
                pass
        for path in self.library.directory.glob("legend-*.json"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            (self.library.directory / "strength-profile.json").unlink(missing_ok=True)
        except OSError:
            pass
        try:
            (self.library.directory / "legend-styles.json").unlink(missing_ok=True)
        except OSError:
            pass
        self.model = None
        self.legend_styles = []
        self.legend_model = None
        self.spar_model = None
        self.spar_legend_name = None
        self.strength_profile = None
        self.strength_profile_key = ""
        self._replace_text(self.profile_text, "尚未训练该棋手的模型。请导入棋谱后点击训练。")
        self._update_profile_stats()
        if hasattr(self, "opponent_combo"):
            self.opponent_combo.configure(values=(SELF_STYLE,))
            self.opponent_var.set(SELF_STYLE)
            self._apply_opponent_selection()

    def _pv_intro_text(self) -> str:
        if self.rows:
            return "点击本人落子行或绿圈查看推荐变化。"
        return "损失为前后局面目差变化的近似值，受搜索深度影响；不是绝对真值。"

    def _set_pv_text(self, text: str) -> None:
        self.pv_text.configure(state="normal")
        self.pv_text.delete("1.0", "end")
        self.pv_text.insert("1.0", text)
        self.pv_text.configure(state="disabled")
        self.pv_text.yview_moveto(0)

    def load_selected(self) -> None:
        selection = self.game_table.selection()
        if not selection or selection[0] == "__empty__":
            return
        self.load_game(self.library.game(int(selection[0])))

    def load_game(self, game: GameRecord) -> None:
        self.selected_game = game
        self.selected_analysis = self.library.analysis(game.database_id, self.settings.key)
        self.rows = move_report(game, self.selected_analysis, self.player) if self.selected_analysis else []
        self.review_title.set(f"{game.played_at}  {game.black_player}（黑） vs {game.white_player}（白）")
        self.turn_slider.configure(to=len(game.moves))
        self.move_table.delete(*self.move_table.get_children())
        for index, row in enumerate(self.rows):
            self.move_table.insert("", "end", iid=str(row["turn"]), values=(
                row["turn"], row["move"], row["best"], f"{row['loss']:.1f}"
            ), tags=("odd" if index % 2 else "even",))
        if not self.rows:
            self.move_table.insert("", "end", iid="__empty__", values=(
                "—", "尚未分析", "—", "—",
            ), tags=("empty",))
        if self.rows:
            average = sum(row["loss"] for row in self.rows) / len(self.rows)
            serious = sum(row["loss"] >= 8 for row in self.rows)
            self.analysis_summary.set(f"{len(self.rows)} 手 · 平均损失 {average:.2f} 目/手 · "
                                      f"严重失误 {serious} 次（≥8 目）")
            self.analyze_current_button.configure(text="重新分析", state="normal")
        elif self.selected_analysis:
            self.analysis_summary.set(
                "这盘棋已有分析，但没有可统计的本人落子；请检查设置中的野狐用户名。")
            self.analyze_current_button.configure(text="重新分析", state="normal")
        elif self._engine_configured():
            self.analysis_summary.set(
                f"这盘棋尚未分析（{len(game.moves)} 手）。点击右上角“分析这盘棋”开始。")
            self.analyze_current_button.configure(text="分析这盘棋", state="normal")
        else:
            self.analysis_summary.set(
                "这盘棋尚未分析。请先在“设置”中配置 KataGo，再点“分析这盘棋”。")
            self.analyze_current_button.configure(text="分析这盘棋", state="normal")
        points = []
        if self.selected_analysis:
            sign = 1 if game.color_for(self.player) == "B" else -1
            for position in self.selected_analysis["positions"]:
                info = position["rootInfo"]
                rate = info.get("winrate", 0.5)
                points.append((position["turnNumber"], rate if sign == 1 else 1 - rate,
                               info.get("scoreLead", 0) * sign))
        self.chart.set_points(points)
        self.set_turn(0)

    def open_selected(self) -> None:
        selection = self.game_table.selection()
        if not selection or selection[0] == "__empty__":
            return
        self.load_selected()
        self.tabs.select(self.review_tab)

    def _slider_turn(self, value: str) -> None:
        turn = round(float(value))
        if turn != self.turn:
            self.set_turn(turn)

    def set_turn(self, turn: int) -> None:
        game = self.selected_game
        if game is None:
            return
        self.review_variation = None
        self.review_candidates = None
        self._hide_variation_controls()
        self._set_pv_text(self._pv_intro_text())
        self.turn = max(0, min(len(game.moves), turn))
        try:
            self.review_board.show(GoBoard.from_game(game, self.turn))
        except (IllegalMove, ValueError) as error:
            self._notify(f"无法重现第 {self.turn} 手：{error}", "error")
            return
        self.turn_slider.set(self.turn)
        self.turn_text.set(f"{self.turn} / {len(game.moves)} 手")

    @staticmethod
    def _gtp_point(coordinate: str, size: int) -> tuple[int, int] | None:
        if not coordinate or coordinate.strip().lower() in {"", "pass"}:
            return None
        try:
            return gtp_to_xy(coordinate.strip(), size)
        except ValueError:
            return None

    def select_report_move(self) -> None:
        selection = self.move_table.selection()
        if not selection or selection[0] == "__empty__" or self.selected_game is None:
            return
        turn = int(selection[0])
        # A queued <<TreeviewSelect>> must not tear down an active candidate/PV
        # overlay for the very same move (programmatic selection does this).
        for state in (self.review_candidates, self.review_variation):
            if state is not None and state["turn"] == turn:
                return
        self.set_turn(turn)
        row = next((item for item in self.rows if item["turn"] == turn), None)
        if row:
            pv = " → ".join(row["pv"][:12]) or "无可用变化"
            self._set_pv_text(
                f"第 {turn} 手 · {row['label']} · 近似损失 {row['loss']:.1f} 目\n"
                f"推荐变化：{pv}\n"
                "绿圈=KataGo 首选；点击绿圈或“查看推荐变化”显示候选点，"
                "再点棋盘上的编号进入变化，用 ←/→ 或下方按钮逐手查看。")
            best = self._gtp_point(row["best"], self.selected_game.board_size)
            if best is not None:
                self.review_board.show(self.review_board.board,
                                       marks={best: "best"})

    def _hide_variation_controls(self) -> None:
        if hasattr(self, "variation_bar"):
            self.variation_bar.grid_remove()
            self.variation_prev_button.configure(state="disabled")
            self.variation_next_button.configure(state="disabled")
            self.variation_analyze_button.configure(state="disabled")
            self.variation_step_text.set("")
        self._clear_board_preview()

    def _clear_board_preview(self) -> None:
        """Drop a hover/next-move ghost without touching the real position."""
        board = getattr(self, "review_board", None)
        if board is not None and (board.preview or board.hover_candidate):
            board.preview = []
            board.hover_candidate = None
            board.redraw()

    def _update_variation_controls(self) -> None:
        state = self.review_variation
        if not state:
            self.variation_prev_button.configure(state="disabled")
            self.variation_next_button.configure(state="disabled")
            self.variation_step_text.set("")
            return
        total = len(state["moves"])
        step = state["step"]
        self.variation_prev_button.configure(state="normal" if step > 0 else "disabled")
        self.variation_next_button.configure(state="normal" if step < total else "disabled")
        self.variation_analyze_button.configure(
            state="disabled" if self.busy else "normal",
            text="重新分析此变化" if state.get("analysis") else "分析此变化")
        self.variation_step_text.set(f"变化 {step} / {total}")

    def step_variation(self, delta: int) -> None:
        state = self.review_variation
        if state is None:
            return
        self._render_variation(state["step"] + delta)

    def _review_report_row(self) -> dict[str, Any] | None:
        if not self.rows:
            return None
        selection = self.move_table.selection()
        if selection and selection[0] != "__empty__":
            row = next((item for item in self.rows if item["turn"] == int(selection[0])), None)
            if row is not None and row["turn"] == self.turn:
                return row
        return next((item for item in self.rows if item["turn"] == self.turn),
                    self.rows[0])

    def _review_navigate(self, delta: int) -> bool:
        if self.review_variation is not None:
            self.step_variation(delta)
            return True
        if self.review_candidates is not None:
            return False
        if self.selected_game is not None:
            self.set_turn(self.turn + delta)
            return True
        return False

    def _review_wheel(self, event) -> str | None:
        """Mouse wheel steps the variation, like KaTrain/Lizzie."""
        if self.review_variation is None:
            return None
        self.step_variation(-1 if event.delta > 0 else 1)
        return "break"

    def _review_board_key(self, keysym: str) -> bool:
        if keysym == "Escape":
            if self.review_variation is not None or self.review_candidates is not None:
                self.return_to_actual()
                return True
            return False
        if self.review_variation is not None:
            if keysym == "Home":
                self._render_variation(0)
                return True
            if keysym == "End":
                self._render_variation(len(self.review_variation["moves"]))
                return True
            return False
        if self.review_candidates is not None and len(keysym) == 1 and keysym in "12345":
            ordered = self.review_candidates.get("ordered") or []
            index = int(keysym) - 1
            if 0 <= index < len(ordered):
                self.start_candidate_variation(ordered[index])
                return True
        return False

    def _review_board_hover(self, point: tuple[int, int] | None) -> None:
        """Preview a candidate's variation while the pointer rests on it."""
        if self.review_variation is not None:
            # Keep the next-move ghost set by _render_variation; hover is only
            # meaningful while the candidate list is on screen.
            return
        board = self.review_board
        state = self.review_candidates
        self._highlight_candidate_row(point)
        candidate = state["points"].get(point) if state and point is not None else None
        if candidate is None or self.selected_game is None:
            board.preview = []
            board.hover_candidate = None
            return
        color = self.selected_game.moves[state["turn"] - 1].color
        moves, _coords = self._pv_sequence(state["start"], candidate, color, limit=12)
        board.preview = moves
        board.preview_offset = 0
        board.hover_candidate = point

    def _highlight_candidate_row(self, point: tuple[int, int] | None) -> None:
        """Mirror the hovered candidate on the matching line of the side list."""
        widget = getattr(self, "pv_text", None)
        if widget is None:
            return
        try:
            widget.tag_remove("cand_hover", "1.0", "end")
        except tk.TclError:
            return
        state = self.review_candidates
        if state is None or point is None:
            return
        label = state.get("labels", {}).get(point)
        if label is None:
            return
        line = 3 + int(label)  # header, hint, legend, then candidate rows
        try:
            widget.tag_add("cand_hover", f"{line}.0", f"{line}.end")
        except tk.TclError:
            pass

    @staticmethod
    def _pv_sequence(start: GoBoard, candidate: dict[str, Any], color: str,
                     limit: int = 16) -> tuple[list[tuple[str, int | None, int | None]],
                                               list[str]]:
        """Replay a candidate's PV on a clone; an unknown token stops the sequence."""
        board = start.clone()
        pv = list(candidate.get("pv") or [])
        move = candidate.get("move")
        if not pv and move:
            pv = [move]
        moves: list[tuple[str, int | None, int | None]] = []
        coords: list[str] = []
        for coordinate in pv[:limit]:
            text = str(coordinate).strip()
            if text == "" or text.lower() == "pass":
                point = None
            else:
                try:
                    point = gtp_to_xy(text, board.size)
                except ValueError:
                    break
            x, y = point if point is not None else (None, None)
            try:
                board.play(color, x, y)
            except IllegalMove:
                break
            moves.append((color, x, y))
            coords.append("pass" if point is None else text)
            color = opposite(color)
        return moves, coords

    def _review_board_click(self, x: int, y: int) -> None:
        if self.review_variation is not None or self.selected_game is None:
            return
        if self.review_candidates is not None:
            candidate = self.review_candidates["points"].get((x, y))
            if candidate is not None:
                self.start_candidate_variation(candidate)
            return
        row = self._review_report_row()
        if row is None:
            return
        best = self._gtp_point(row["best"], self.selected_game.board_size)
        if best == (x, y):
            self.show_candidates()
            self.review_board.focus_set()

    def _render_variation(self, step: int) -> None:
        state = self.review_variation
        if state is None:
            return
        total = len(state["moves"])
        step = max(0, min(total, step))
        state["step"] = step
        board = state["start"].clone()
        numbered: dict[tuple[int, int], int] = {}
        for index, (color, x, y) in enumerate(state["moves"][:step], 1):
            try:
                board.play(color, x, y)
            except IllegalMove:
                break
            if x is not None:
                numbered[(x, y)] = index
        self.review_board.show(board, numbered, state["marks"])
        # Ghost the next move so the user sees where the variation continues.
        next_move = state["moves"][step] if step < total else None
        self.review_board.preview = [next_move] if next_move else []
        self.review_board.preview_offset = step
        self.review_board.hover_candidate = None
        if next_move:
            self.review_board.redraw()
        self._update_variation_controls()
        self.turn_text.set(
            f"第 {state['turn']} 手前 · 变化 {step}/{total}（非实战）")
        row = state["row"]
        choice = state.get("choice") or {}
        actual_text = row["move"] if state["actual"] is not None else "停一手"
        progress = ("当前显示起点局面；点“变化下一手”或按 → 逐手显示。"
                    if step == 0 else f"当前显示变化第 {step} 手。")
        choice_move = choice.get("move", row["best"])
        winrate = choice.get("winrate")
        score = choice.get("score")
        metrics = ""
        if isinstance(winrate, (int, float)):
            metrics += f" · 胜率 {winrate:.1%}"
        if isinstance(score, (int, float)):
            metrics += f" · 目差 {score:+.1f}"
        analysis = state.get("analysis") or {}
        for index in range(total + 1):
            name = f"varmove{index}"
            current = index == step
            self.pv_text.tag_configure(
                name,
                foreground=self._variation_line_color(analysis, index, current),
                background=theme.SELECTION if current else theme.SURFACE,
                font=(theme.FONT, 9, "bold") if current else (theme.FONT, 9))
        start_info = analysis.get(0)
        start_metrics = (f" · 本人胜率 {start_info['winrate']:.1%} · "
                         f"目差 {start_info['score']:+.1f}" if start_info else "")
        lines: list[tuple[str, str | None]] = [
            (f"第 {row['turn']} 手：实战 {actual_text} · 已选候选 "
             f"{choice_move}{metrics}", "heading"),
            (progress, None),
            ("变化手顺（点行跳转，←/→ 或滚轮逐手）：", None),
            (f"{'▶' if step == 0 else '　'} 起点{start_metrics}", "varmove0"),
        ]
        binds: list[tuple[str, Callable[[tk.Event], None]]] = [
            ("varmove0", lambda event: self._render_variation(0))]
        for index, (color, _x, _y) in enumerate(state["moves"], 1):
            coord = state["pv_coords"][index - 1]
            text = "停一手" if coord == "pass" else coord
            side = "黑" if color == "B" else "白"
            mark = "▶" if index == step else "　"
            row_metrics = ""
            info = analysis.get(index)
            if info:
                row_metrics = (f" · 胜率 {info['winrate']:.1%} · "
                               f"目差 {info['score']:+.1f}")
                before = analysis.get(index - 1)
                if before:
                    row_metrics += f" · 较上步 {info['score'] - before['score']:+.1f}"
            lines.append((f"{mark} {index} · {side} {text}{row_metrics}", f"varmove{index}"))
            binds.append((f"varmove{index}",
                          lambda event, jump=index: self._render_variation(jump)))
        if analysis:
            lines.append(("红圈=实战落点，绿圈=所选候选；颜色=对本人形势的升降。", None))
        else:
            lines.append(("红圈=实战落点，绿圈=所选候选；点“分析此变化”显示逐手胜率。", None))
        self._set_rich_pv_text(lines, binds, see_tag=f"varmove{step}")

    def _variation_cache_key(self, state: dict[str, Any]) -> str:
        game = self.selected_game
        game_hash = game.sha256 if game is not None else ""
        return "|".join((self.settings.key, game_hash, str(state["turn"]),
                         ",".join(str(coord) for coord in state["pv_coords"])))

    def _variation_query(self, state: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
        """KataGo request covering the start position and every PV move."""
        game = self.selected_game
        assert game is not None
        start_turn = state["turn"] - 1
        moves = list(game.moves[:start_turn]) + [
            Move(color, x, y) for color, x, y in state["moves"]
        ]
        request = {
            "moves": [[move.color, xy_to_gtp(move.x, move.y, game.board_size)]
                      for move in moves],
            "initialStones": [
                [color, xy_to_gtp(x, y, game.board_size)]
                for color, points in (("B", game.setup_black), ("W", game.setup_white))
                for x, y in points
            ],
            "initialPlayer": game.first_property("PL", "W" if game.setup_black else "B"),
            "boardXSize": game.board_size,
            "boardYSize": game.board_size,
            "rules": game.rules,
            "komi": game.komi,
            "maxVisits": self.settings.visits,
            "includePolicy": True,
            "analyzeTurns": list(range(start_turn, len(moves) + 1)),
        }
        return request, start_turn, len(state["moves"])

    def _apply_variation_analysis(self, state: dict[str, Any],
                                  responses: list[dict[str, Any]],
                                  start_turn: int) -> dict[int, dict[str, float]]:
        """Turn BLACK-perspective engine output into owner-perspective per step."""
        game = self.selected_game
        color = game.color_for(self.player) if game is not None else "B"
        sign = -1 if color == "W" else 1
        by_step: dict[int, dict[str, float]] = {}
        for response in responses:
            try:
                step = int(response.get("turnNumber", -1)) - start_turn
            except (TypeError, ValueError):
                continue
            if not 0 <= step <= len(state["moves"]):
                continue
            info = response.get("rootInfo") or {}
            winrate = info.get("winrate")
            score = info.get("scoreLead")
            if not isinstance(winrate, (int, float)) or not isinstance(score, (int, float)):
                continue
            by_step[step] = {
                "winrate": float(winrate) if sign == 1 else 1 - float(winrate),
                "score": float(score) * sign,
            }
        return by_step

    def analyze_variation(self) -> None:
        """Analyze just the selected variation branch, never the main game cache."""
        state = self.review_variation
        if state is None or self.selected_game is None:
            return
        if not self._idle() or not self._engine_ready():
            return
        key = self._variation_cache_key(state)
        cached = self.variation_cache.get(key)
        if cached is not None:
            state["analysis"] = cached
            self._render_variation(state["step"])
            self._notify("已载入该变化上次的分析结果", "success")
            return
        request, start_turn, _count = self._variation_query(state)
        expected = len(state["moves"]) + 1
        settings = self.settings

        def work(progress, cancel):
            with KataGo(settings, self.library.directory / "engine-work") as engine:
                return engine.query(request, expected, progress, cancel=cancel)

        def finished(responses):
            analysis = self._apply_variation_analysis(state, responses, start_turn)
            if not analysis:
                self._notify("变化分析没有返回可用结果", "warning")
                return
            state["analysis"] = analysis
            self.variation_cache[key] = analysis
            if self.review_variation is state:
                self._render_variation(state["step"])
            self._notify(f"变化分析完成：{len(analysis)} 个局面", "success")

        self.variation_analyze_button.configure(state="disabled")
        self._run("分析当前变化 · 首次加载模型可能较慢…", work, finished)

    @staticmethod
    def _variation_line_color(analysis: dict[int, dict[str, float]],
                              index: int, current: bool) -> str:
        """Green when the owner's position improved, red when it worsened."""
        if not analysis:
            return theme.INK if current else theme.MUTED
        info = analysis.get(index)
        before = analysis.get(index - 1)
        if info is None or before is None:
            return theme.INK if current else theme.MUTED
        delta = info["score"] - before["score"]
        if delta <= -3:
            return theme.DANGER
        if delta <= -1:
            return theme.WARNING
        if delta >= 0.5:
            return theme.SUCCESS
        return theme.INK if current else theme.MUTED

    def show_candidates(self) -> None:
        if self.selected_game is None:
            messagebox.showinfo("没有棋谱", "请先从棋谱库打开一盘棋。")
            return
        if not self.rows:
            messagebox.showinfo("尚无分析", "请先分析这盘棋，再查看推荐变化。")
            return
        row = self._review_report_row()
        if row is None:
            return
        game = self.selected_game
        try:
            start = GoBoard.from_game(game, row["turn"] - 1)
        except (IllegalMove, ValueError) as error:
            messagebox.showinfo("无法显示候选", str(error))
            return
        candidates = list(row.get("candidates") or [])
        if not candidates and self._gtp_point(row["best"], start.size) is not None:
            candidates = [{
                "move": row["best"], "pv": row["pv"], "winrate": row.get("winrate"),
                "score": row.get("score"), "loss": 0.0,
            }]
        candidates = candidates[:5]
        points: dict[tuple[int, int], dict[str, Any]] = {}
        labels: dict[tuple[int, int], str] = {}
        kinds: dict[tuple[int, int], str] = {}
        subs: dict[tuple[int, int], str] = {}
        for index, candidate in enumerate(candidates, 1):
            point = self._gtp_point(str(candidate.get("move", "")), start.size)
            if point is None:
                continue
            labels[point] = str(index)
            points[point] = candidate
            kinds[point] = self._candidate_kind(candidate, index)
            winrate = candidate.get("winrate")
            if isinstance(winrate, (int, float)):
                subs[point] = f"{winrate:.0%}"
        if not points:
            messagebox.showinfo("没有候选", "该手没有可显示的 KataGo 候选点。")
            return
        # Keep digit shortcuts in the same order as the board numbers.
        ordered = [points[point] for point in
                   sorted(points, key=lambda point: int(labels[point]))]
        actual = game.moves[row["turn"] - 1]
        actual_point = (actual.x, actual.y) if actual.x is not None else None
        marks = {actual_point: "actual"} if actual_point is not None else {}
        self.review_candidates = {
            "turn": row["turn"], "row": row, "start": start,
            "candidates": candidates, "points": points, "actual": actual_point,
            "ordered": ordered, "labels": labels,
        }
        self.review_variation = None
        self._hide_variation_controls()
        self.turn = row["turn"] - 1
        self.turn_slider.set(self.turn)
        self.turn_text.set(f"第 {row['turn']} 手前 · KataGo 候选点（非实战）")
        self.review_board.show(start, marks=marks, candidates=labels,
                               candidate_kinds=kinds, candidate_subs=subs)
        lines: list[tuple[str, str | None]] = [
            (f"第 {row['turn']} 手 · 实战 {row['move']}（红圈） · "
             f"损失 {row['loss']:.1f} 目", "heading"),
            ("KataGo 候选：悬停编号预览变化；点编号或按 1–5 进入变化。", None),
            ("绿=一选 · 青=接近 · 橙=小失 · 红=较大损失", None),
        ]
        for index, candidate in enumerate(candidates, 1):
            winrate = candidate.get("winrate")
            score = candidate.get("score")
            loss = candidate.get("loss")
            visits = candidate.get("visits")
            win = f"{winrate:.1%}" if isinstance(winrate, (int, float)) else "—"
            lead = f"{score:+.1f}" if isinstance(score, (int, float)) else "—"
            drop = f"{loss:.1f}" if isinstance(loss, (int, float)) else "—"
            extra = f" · 访问 {visits}" if isinstance(visits, int) else ""
            kind = self._candidate_kind(candidate, index)
            text = (f"{index}  {candidate.get('move', '—')} · 胜率 {win} · "
                    f"目差 {lead} · 损失 {drop}{extra}")
            lines.append((text, kind))
        self._set_rich_pv_text(lines)
        self.review_board.focus_set()

    @staticmethod
    def _candidate_kind(candidate: dict[str, Any], index: int) -> str:
        """Severity bucket used for candidate color: best/good/warn/bad."""
        if index == 1:
            return "best"
        loss = candidate.get("loss")
        if not isinstance(loss, (int, float)):
            return "good"
        if loss < 2:
            return "good"
        if loss < 5:
            return "warn"
        return "bad"

    def _set_rich_pv_text(self, lines: list[tuple[str, str | None]],
                          binds: list[tuple[str, Callable[["tk.Event"], None]]] | None = None,
                          see_tag: str | None = None) -> None:
        widget = self.pv_text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for text, tag in lines:
            widget.insert("end", text + "\n", tag or ())
        widget.configure(state="disabled")
        for name in getattr(self, "_pv_bind_tags", []):
            try:
                widget.tag_unbind(name, "<Button-1>")
            except tk.TclError:
                pass
        self._pv_bind_tags: list[str] = []
        for name, callback in (binds or []):
            widget.tag_bind(name, "<Button-1>", callback)
            self._pv_bind_tags.append(name)
        if see_tag:
            try:
                widget.see(f"{see_tag}.first")
                return
            except tk.TclError:
                pass
        widget.yview_moveto(0)

    def start_candidate_variation(self, candidate: dict[str, Any]) -> None:
        state = self.review_candidates
        if state is None:
            return
        game = self.selected_game
        if game is None:
            return
        row = state["row"]
        start = state["start"]
        move = candidate.get("move")
        color = game.moves[row["turn"] - 1].color
        moves, pv_coords = self._pv_sequence(start, candidate, color)
        actual_point = state["actual"]
        marks = {actual_point: "actual"} if actual_point is not None else {}
        chosen_point = self._gtp_point(str(move or ""), start.size)
        if chosen_point is not None and chosen_point != actual_point:
            marks[chosen_point] = "best"
        self.review_candidates = None
        self.review_variation = {
            "turn": row["turn"], "row": row, "start": start,
            "moves": moves, "marks": marks, "actual": actual_point,
            "pv_coords": pv_coords, "step": 0, "choice": candidate,
        }
        cached = self.variation_cache.get(self._variation_cache_key(self.review_variation))
        if cached is not None:
            self.review_variation["analysis"] = cached
        self.turn = row["turn"] - 1
        self.turn_slider.set(self.turn)
        self.variation_bar.grid()
        self._render_variation(1 if moves else 0)
        self.review_board.focus_set()

    def show_variation(self) -> None:
        self.show_candidates()

    def return_to_actual(self) -> None:
        state = self.review_variation or self.review_candidates
        turn = state["turn"] if state else self.turn
        self.review_variation = None
        self.review_candidates = None
        self.set_turn(turn)

    def sync_fox_records(self) -> None:
        if not self.player:
            messagebox.showinfo("先设置用户名", "请先在设置中填写你的野狐用户名并保存。")
            self.tabs.select(self.settings_tab)
            return
        if not self._idle():
            return
        if self.spar_active or self.spar_pending_score:
            messagebox.showinfo("先结束陪练", "同步期间需要操作野狐历史窗口，请先结束并确认当前陪练对局。")
            return
        destination = Path(self.library.setting(
            "fox_export_folder", str(Path.home() / "Documents" / "YehuStyleGo" / "FoxRecords")))
        limit = self.game_limit
        if not messagebox.askyesno(
            f"同步最近 {limit} 盘",
            "请先在野狐打开“棋谱欣赏 → 我的棋谱”，并关闭历史复盘棋盘（保留列表）。\n\n"
            "接下来会自动打开历史棋谱、保存 SGF 并关闭这些复盘窗口，不操作在线棋局。"
            "同步期间请勿操作野狐；可点击影棋底部“取消任务”。\n\n"
            f"本次最多同步 {limit} 盘，实际以野狐列表已载入的盘数为限；盘数越多耗时越长。\n"
            f"保存到：{destination}\n已有文件不覆盖；同步后自动重新训练棋风。是否开始？",
        ):
            return
        player, model_path = self.player, self._model_path()
        def work(progress, cancel):
            from yehu_style_go.fox_sync import FoxExporter

            result = FoxExporter(player, destination, cancel).sync(
                limit, lambda done, total: progress(done / max(1, total) * 80, 100))
            imported = self.library.import_paths(result.paths)
            if imported["errors"]:
                raise RuntimeError("部分 SGF 导入失败：" + "\n".join(imported["errors"][:5]))
            self.library.set_setting("fox_export_folder", str(destination))
            model = train_style(self.library.games(player, limit), player,
                                lambda done, total: progress(80 + done / max(1, total) * 20, 100),
                                cancel)
            model.save(model_path)
            return result, imported, model
        def finished(payload):
            result, imported, model = payload
            self.model = model
            self.refresh_library()
            visible = (f"；野狐列表本次仅载入 {result.available_rows} 盘"
                       if result.available_rows < limit else "")
            skipped = (f"；跳过 {result.skipped} 盘不含当前姓名的棋谱"
                       if result.skipped else "")
            self._notify(f"已同步 {len(result.paths)} 盘：新导出 {result.exported}、"
                         f"已有 {result.existing}、新导入 {imported['added']} · 棋风已更新"
                         f"{visible}{skipped}",
                         "warning" if result.skipped else "success")
            self.tabs.select(self.library_tab)
        self._run("通过野狐官方历史界面同步棋谱 · 请勿操作野狐窗口…", work, finished)

    def import_files(self) -> None:
        if not self._idle():
            return
        paths = filedialog.askopenfilenames(title="选择棋谱（可多选）", filetypes=[("SGF 棋谱", "*.sgf")])
        if paths:
            self._import([Path(path) for path in paths])

    def import_folder(self) -> None:
        if not self._idle():
            return
        folder = filedialog.askdirectory(title="选择从野狐保存棋谱的文件夹")
        if folder:
            self.folder_var.set(folder)
            self.library.set_setting("watch_folder", folder)
            self._run("扫描并导入棋谱…", lambda progress, cancel:
                      self.library.import_paths(scan_folder(Path(folder))), self._import_done,
                      low_priority=True)

    def _import(self, paths: list[Path]) -> None:
        self._run("导入棋谱…", lambda progress, cancel: self.library.import_paths(paths), self._import_done)

    def _import_done(self, result: dict) -> None:
        self.refresh_library()
        self._notify(f"已导入 {result['added']} 盘 · 重复 {result['duplicates']} 盘 · "
                     f"失败 {len(result['errors'])} 盘",
                     "warning" if result["errors"] else "success")
        if result["errors"]:
            detail = "\n".join(result["errors"][:15])
            log_path = self.library.directory / "last-import.log"
            try:
                log_path.write_text("\n".join(result["errors"]) + "\n", encoding="utf-8")
                detail += f"\n\n完整错误记录：{log_path}"
            except OSError:
                pass
            messagebox.showwarning("部分棋谱导入失败", detail)

    def _watch_folder(self) -> None:
        if self.closed:
            return
        folder = self.library.setting("watch_folder", "")
        if (not self.busy and not self.spar_active and not self.spar_pending_score
                and folder and self.library.setting("watch_enabled", False)):
            def scan(progress, cancel):
                paths = scan_folder(Path(folder))
                signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
                if signature == self.watch_signature:
                    return None
                self.watch_signature = signature
                return self.library.import_paths(paths)
            self._run("检查新增棋谱…", scan,
                      lambda result: self._import_done(result) if result else None)
        self.root.after(30000, self._watch_folder)

    def _engine_configured(self) -> bool:
        try:
            self.settings.validate()
            return True
        except RuntimeError:
            return False

    def _engine_ready(self) -> bool:
        try:
            self.settings.validate()
            return True
        except RuntimeError as error:
            messagebox.showinfo("需要配置引擎", str(error))
            self.tabs.select(self.settings_tab)
            return False

    def analyze_current_game(self) -> None:
        """Analyze the game currently open in the review tab."""
        if self.selected_game is None:
            messagebox.showinfo("没有棋谱", "请先从棋谱库选择一盘棋。")
            return
        self.analyze_games(False, force=self.selected_analysis is not None)

    def analyze_games(self, all_games: bool, force: bool = False) -> None:
        if not self._idle() or not self._engine_ready():
            return
        games = list(self.games) if all_games else ([self.selected_game] if self.selected_game else [])
        if not games:
            messagebox.showinfo("没有棋谱", "请先导入该棋手的 SGF 棋谱。")
            return
        settings = self.settings
        def work(progress, cancel):
            completed = cached = 0
            with KataGo(settings, self.library.directory / "engine-work") as engine:
                for index, game in enumerate(games):
                    if cancel.is_set():
                        raise RuntimeError("任务已取消；已完成的分析会保留")
                    if not force and self.library.analysis(game.database_id, settings.key):
                        cached += 1
                        progress(index + 1, len(games))
                        continue
                    def position_progress(done, total):
                        progress(index * 1000 + done * 1000 / total, len(games) * 1000)
                    analysis = engine.analyze_game(game, position_progress, cancel)
                    self.library.save_analysis(game.database_id, settings.key, analysis)
                    completed += 1
            profile = build_profile(games, self.library, self.player, settings.key, cancel)
            if profile is not None:
                save_profile(self.library.directory / "strength-profile.json", profile)
            return completed, cached, profile
        def finished(result):
            completed, cached, profile = result
            if profile is not None:
                self.strength_profile = profile
                self.strength_profile_key = settings.key
            self.refresh_library()
            self._notify(f"KataGo 分析完成：新分析 {completed} 盘 · 使用缓存 {cached} 盘"
                         + (" · 已更新棋力匹配档案" if profile is not None
                            else " · 分析手数不足，棋力匹配暂用引擎排名"), "success")
            self.tabs.select(self.review_tab)
        self._run("KataGo 批量分析中 · 首次加载模型可能较慢…", work, finished)

    def train(self, select_tab: bool = True) -> None:
        if not self.player:
            messagebox.showinfo("先设置用户名", "请先在设置中填写你的野狐用户名并保存。")
            self.tabs.select(self.settings_tab)
            return
        if not self._idle():
            return
        games = list(self.games)
        if (self.selected_game is not None
                and all(game.database_id != self.selected_game.database_id for game in games)):
            games.append(self.selected_game)
        if not games:
            messagebox.showinfo("没有棋谱", "请先同步或导入至少一盘包含本人账号的棋谱，再训练棋风。")
            return
        player, path = self.player, self._model_path()
        def work(progress, cancel):
            model = train_style(games, player, progress, cancel)
            model.save(path)
            return model
        def finished(model):
            self.model = model
            self._update_profile_stats()
            self.refresh_home()
            self._replace_text(self.profile_text, model.report_text() + self._analysis_profile())
            if select_tab:
                self.tabs.select(self.profile_tab)
            else:
                self._notify(f"棋风模型已更新：{model.game_count} 盘、"
                             f"{model.move_count} 手本人落子；陪练已使用新模型", "success")
        self._run("学习本人落子偏好…", work, finished, low_priority=True)

    def _analysis_profile(self) -> str:
        rows = []
        analyzed = 0
        cached_ids = self.library.analyzed_game_ids(self.settings.key)
        for game in self.games:
            if game.database_id not in cached_ids:
                continue
            analysis = self.library.analysis(game.database_id, self.settings.key)
            if analysis:
                analyzed += 1
                rows.extend(move_report(game, analysis, self.player))
        if not rows:
            return "\n\n深度分析：尚无 KataGo 数据。请配置引擎后执行批量分析。"
        average = sum(row["loss"] for row in rows) / len(rows)
        largest = sorted(rows, key=lambda row: row["loss"], reverse=True)[:5]
        profile = self._active_profile()
        matching = ("\n" + profile.summary()) if profile else (
            "\n棋力匹配档案：尚无足够分析（至少需要 30 手本人落子），陪练暂按引擎排名选点。")
        return (f"\n\nKataGo 汇总：已分析 {analyzed} 盘；本人 {len(rows)} 手；"
                f"平均近似损失 {average:.2f} 目/手。\n"
                f"严重失误（≥8 目）{sum(row['loss'] >= 8 for row in rows)} 手；"
                f"失误（3–8 目）{sum(3 <= row['loss'] < 8 for row in rows)} 手。\n"
                + "较大损失手数：" + "、".join(f"第 {row['turn']} 手（{row['loss']:.1f} 目）" for row in largest)
                + matching)

    def _browse(self, variable: tk.StringVar, kind: str) -> None:
        if kind == "folder":
            path = filedialog.askdirectory()
        else:
            types = [("KataGo", "*.exe")] if kind == "exe" else [("网络模型", "*.bin.gz *.txt.gz *.bin")]
            path = filedialog.askopenfilename(filetypes=types + [("所有文件", "*.*")])
        if path:
            variable.set(path)

    def save_settings(self) -> bool:
        if not self._idle():
            return False
        try:
            visits = int(self.visits_var.get())
            if not 1 <= visits <= 10000:
                raise ValueError("访问次数应在 1 到 10000 之间")
            limit = int(self.limit_var.get())
            if not 1 <= limit <= MAX_SYNC_GAMES:
                raise ValueError(f"盘数上限应在 1 到 {MAX_SYNC_GAMES} 之间")
            player = self.player_var.get().strip()
            if not player:
                raise ValueError("用户名不能为空")
        except ValueError as error:
            messagebox.showerror("设置无效", str(error))
            return False
        self.player = player
        self.game_limit = limit
        self.settings = EngineSettings(self.engine_var.get().strip(), self.model_var.get().strip(), visits)
        self.library.set_setting("player", player)
        self.library.set_setting("game_limit", limit)
        self.library.set_setting("engine", asdict(self.settings))
        self.library.set_setting("watch_folder", self.folder_var.get().strip())
        self.library.set_setting("watch_enabled", self.watch_var.get())
        self.identity.set(f"棋手 · {player}")
        self.spar_active = False
        self._load_model()
        self._load_strength_profile()
        self.refresh_library()
        self._notify("设置已保存", "success")
        return True

    def test_engine(self) -> None:
        if not self.save_settings() or not self._engine_ready():
            return
        settings = self.settings
        def work(progress, cancel):
            with KataGo(settings, self.library.directory / "engine-work") as engine:
                request = engine.position_request([], 19)
                request["maxVisits"] = 8
                response = engine.query(request, cancel=cancel)[0]
                return response
        def finished(response):
            messagebox.showinfo("KataGo 测试通过", "引擎成功完成真实局面分析。\n"
                                f"候选手数：{len(response.get('moveInfos', []))}")
        self._run("测试 KataGo · 正在加载网络模型…", work, finished)

    def _legend_choices(self) -> tuple[str, ...]:
        return (SELF_STYLE, *(str(entry["name"]) for entry in self.legend_styles))

    def _legend_entry(self, name: str) -> dict[str, Any] | None:
        return next((entry for entry in self.legend_styles if entry["name"] == name), None)

    def _using_legend(self) -> bool:
        return (getattr(self, "legend_model", None) is not None
                and getattr(self, "opponent_var", None) is not None
                and self.opponent_var.get() != SELF_STYLE)

    def _spar_style_model(self) -> StyleModel | None:
        return self.legend_model if self._using_legend() else self.model

    def _opponent_sgf_label(self) -> str:
        if self._using_legend() and self.legend_model is not None:
            return f"影棋-{self.legend_model.player}"
        return f"影棋-{self.player}"

    def _on_opponent_change(self) -> None:
        self._apply_opponent_selection()

    def _apply_opponent_selection(self, initial: bool = False) -> None:
        saved = str(self.library.setting("spar_style", SELF_STYLE))
        choices = self._legend_choices()
        if saved not in choices:
            saved = SELF_STYLE
        if initial:
            self.opponent_var.set(saved)
        choice = self.opponent_var.get()
        self.legend_model = None
        if choice == SELF_STYLE:
            self.opponent_title.set("对手：另一个你")
            self.opponent_note.set("使用你的个人棋风模型。")
            self.match_strength_check.configure(state="normal")
            self.delete_legend_button.configure(state="disabled")
            self.library.set_setting("spar_style", SELF_STYLE)
            return
        entry = self._legend_entry(choice)
        if entry is None:
            self.opponent_var.set(SELF_STYLE)
            self._apply_opponent_selection()
            return
        try:
            model = load_legend_model(self.library.directory, entry)
        except (OSError, ValueError, TypeError) as error:
            if not initial:
                messagebox.showwarning("名手模型无法读取", f"{choice}：{error}")
            self.opponent_var.set(SELF_STYLE)
            self._apply_opponent_selection()
            return
        self.legend_model = model
        self.opponent_title.set(f"对手：{choice}（名手棋风）")
        self.opponent_note.set(
            f"{choice} · {model.game_count} 盘 / {model.move_count} 手落子；"
            "名手模式不使用你的棋力损失档案，默认选点强度 0.82，可用下方滑条调整。")
        self.match_strength_check.configure(state="disabled")
        self.delete_legend_button.configure(state="normal")
        if self.strength_var.get() < 0.6:
            self.strength_var.set(0.82)
            self.strength_value.set("0.82")
        if self.imitation_var.get() < 0.5:
            self.imitation_var.set(0.65)
            self.imitation_value.set("65%")
        self.library.set_setting("spar_style", choice)

    def import_legend(self) -> None:
        if not self._idle():
            return
        name = simpledialog.askstring(
            "导入名手棋风",
            "输入名手姓名，必须与 SGF 的 PB 或 PW 完全一致：",
            parent=self.root,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showinfo("需要姓名", "名手姓名不能为空。")
            return
        folder = filedialog.askdirectory(title="选择包含该名手 SGF 的文件夹")
        if not folder:
            return
        try:
            paths = scan_folder(Path(folder))
        except ValueError as error:
            messagebox.showinfo("没有 SGF", str(error))
            return
        if not paths:
            messagebox.showinfo("没有 SGF", "该文件夹及其子文件夹中没有找到 .sgf 文件。")
            return
        directory, existing = self.library.directory, list(self.legend_styles)

        def work(progress, cancel):
            games, errors = collect_games(paths, name)
            if not games:
                detail = ("\n" + "\n".join(errors[:5])) if errors else ""
                raise ValueError(
                    f"没有找到包含“{name}”作为黑方或白方的 SGF 棋谱。{detail}")
            model = train_style(games, name, progress, cancel)
            if cancel.is_set():
                raise RuntimeError("任务已取消")
            entries = register_legend(directory, model, Path(folder), existing)
            return model, entries, errors

        def finished(result):
            model, entries, errors = result
            self.legend_styles = entries
            self.opponent_combo.configure(values=self._legend_choices())
            self.opponent_var.set(model.player)
            self._apply_opponent_selection()
            self.tabs.select(self.spar_tab)
            self._notify(f"名手棋风已训练：{model.player} · {model.game_count} 盘 · "
                         f"{model.move_count} 手落子", "success")
            if errors:
                messagebox.showwarning(
                    "部分棋谱未导入",
                    "\n".join(errors[:8]) + f"\n\n共 {len(errors)} 个文件未读取",
                )

        self._run(f"读取{name}棋谱并训练名手模型…", work, finished, low_priority=True)

    @staticmethod
    def _legend_names(text: str) -> list[str]:
        parts = text
        for separator in (",", "，", "、", ";", "；", "\n", "\r", "\t"):
            parts = parts.replace(separator, "\n")
        names: list[str] = []
        for part in parts.split("\n"):
            name = part.strip()
            if name and name not in names:
                names.append(name)
        return names

    def import_legends_batch(self) -> None:
        if not self._idle():
            return
        text = simpledialog.askstring(
            "批量导入名手",
            "输入多个名手姓名，用逗号或顿号分隔：\n"
            "姓名必须与各自 SGF 的 PB 或 PW 完全一致。",
            parent=self.root,
        )
        if text is None:
            return
        names = self._legend_names(text)
        if not names:
            messagebox.showinfo("需要姓名", "请至少输入一个名手姓名。")
            return
        folder = filedialog.askdirectory(title="选择包含这些名手 SGF 的文件夹")
        if not folder:
            return
        try:
            paths = scan_folder(Path(folder))
        except ValueError as error:
            messagebox.showinfo("没有 SGF", str(error))
            return
        if not paths:
            messagebox.showinfo("没有 SGF", "该文件夹及其子文件夹中没有找到 .sgf 文件。")
            return
        directory, existing = self.library.directory, list(self.legend_styles)

        def work(progress, cancel):
            all_games = []
            errors: list[str] = []
            seen_hashes: set[str] = set()
            for path in paths:
                if cancel.is_set():
                    raise RuntimeError("任务已取消")
                try:
                    game = parse_sgf_file(path)
                except (OSError, ValueError, RecursionError) as error:
                    errors.append(f"{path.name}: {error}")
                    continue
                if game.sha256 in seen_hashes:
                    continue
                seen_hashes.add(game.sha256)
                all_games.append(game)
            jobs = []
            failures: list[str] = []
            for name in names:
                games = [game for game in all_games if game.color_for(name) is not None]
                if games:
                    jobs.append((name, games))
                else:
                    failures.append(f"{name}: 没有找到包含该姓名的棋谱")
            if not jobs:
                detail = ("\n" + "\n".join((failures + errors)[:8])
                          if (failures or errors) else "")
                raise ValueError(f"没有可训练的名手。{detail}")
            total = sum(len(games) for _, games in jobs)
            trained = 0
            results: list[tuple[str, int, int]] = []
            entries = existing
            for name, games in jobs:
                if cancel.is_set():
                    raise RuntimeError("任务已取消")
                base = trained

                def report(done, total_for_name, base=base):
                    progress(base + done, total)

                try:
                    model = train_style(games, name, report, cancel)
                    entries = register_legend(directory, model, Path(folder), entries)
                except Exception as error:
                    if cancel.is_set():
                        raise
                    failures.append(f"{name}: {error}")
                else:
                    results.append((name, model.game_count, model.move_count))
                trained += len(games)
            if not results:
                detail = "\n" + "\n".join(failures[:8]) if failures else ""
                raise ValueError(f"没有名手模型训练成功。{detail}")
            return results, entries, errors, failures

        def finished(result):
            results, entries, errors, failures = result
            self.legend_styles = entries
            self.opponent_combo.configure(values=self._legend_choices())
            self.opponent_var.set(results[0][0])
            self._apply_opponent_selection()
            self.tabs.select(self.spar_tab)
            detail = "、".join(f"{name}（{games} 盘）" for name, games, _moves in results)
            self._notify(f"批量训练完成：{detail}", "warning" if (errors or failures) else "success")
            notes = errors[:6] + failures[:6]
            if notes:
                messagebox.showwarning(
                    "批量导入部分完成",
                    "\n".join(notes)
                    + f"\n\n成功 {len(results)} 位名手，未完成 {len(failures)} 位。",
                )

        self._run("读取批量棋谱并训练名手模型 · 低优先级后台运行…",
                  work, finished, low_priority=True)

    def delete_current_legend(self) -> None:
        if not self._using_legend() or self.legend_model is None:
            return
        name = self.legend_model.player
        if not messagebox.askyesno(
            "删除名手模型",
            f"删除“{name}”的本地名手模型？\n\n原始 SGF 文件不会被删除。",
            icon="warning",
        ):
            return
        entry = self._legend_entry(name)
        if entry is None:
            return
        try:
            self.legend_styles = remove_legend(self.library.directory, entry,
                                               self.legend_styles)
        except OSError as error:
            messagebox.showerror("删除失败", str(error))
            return
        self.legend_model = None
        self.opponent_combo.configure(values=self._legend_choices())
        self.opponent_var.set(SELF_STYLE)
        self._apply_opponent_selection()
        self._notify(f"已删除名手模型：{name}", "success")

    def new_spar(self) -> None:
        if not self._idle():
            return
        model = self._spar_style_model()
        if model is None:
            if self._using_legend():
                messagebox.showinfo("名手模型不可用", "当前名手模型没有加载成功，请重新导入或切换对手。")
            else:
                messagebox.showinfo("需要棋风模型", "请先导入棋谱并训练你的棋风模型。")
            return
        if (self.spar_active or self.spar_pending_score
                or (self.spar_board.move_history and not self.spar_saved)) and not messagebox.askyesno(
            "开始新对局", "当前对局将被替换。是否继续？"
        ):
            return
        if model.game_count < 20 and not messagebox.askyesno(
            "样本较少", f"当前仅有 {model.game_count} 盘，模仿仍很粗略。继续体验？"
        ):
            return
        self.spar_model = model
        self.spar_legend_name = model.player if self._using_legend() else None
        self.spar_opponent_label = self._opponent_sgf_label()
        self.human_color = "B" if self.color_var.get().startswith("执黑") else "W"
        self.spar_board = GoBoard(int(self.size_var.get()))
        self.spar_active = True
        self.spar_pending_score = False
        self.spar_saved = False
        self.spar_result = ""
        self.spar_score = None
        self.spar_dead.clear()
        self.spar_komi = 7.5
        self._invalidate_assessment()
        if self.spar_legend_name:
            self.spar_mode = f"轻量名手风格：{self.spar_legend_name}（无引擎，不代表真实棋力）"
            if self.use_engine_var.get():
                try:
                    self.settings.validate()
                    self.spar_mode = f"KataGo + 名手风格：{self.spar_legend_name}"
                except RuntimeError:
                    pass
        else:
            self.spar_mode = "轻量棋风陪练（无引擎，不代表真实棋力）"
            if self.use_engine_var.get():
                try:
                    self.settings.validate()
                    self.spar_mode = "KataGo + 个人落子偏好"
                except RuntimeError:
                    pass
        self.spar_view.show(self.spar_board)
        self._spar_info()
        if self.human_color == "W":
            self.bot_play()

    def _spar_ghost(self) -> str | None:
        """Ghost-stone preview only while it is the human's turn to play."""
        if (self.spar_active and not self.busy
                and self.spar_board.to_play == self.human_color):
            return self.human_color
        return None

    def human_play(self, x: int | None, y: int | None) -> None:
        if not self.spar_active or self.busy or self.spar_board.to_play != self.human_color:
            return
        try:
            self.spar_board.play(self.human_color, x, y)
        except IllegalMove as error:
            self._notify(str(error), "warning")
            return
        self.spar_saved = False
        self._invalidate_assessment()
        self.spar_view.show(self.spar_board)
        if not self._check_end():
            self.bot_play()

    def bot_play(self) -> None:
        board = self.spar_board.clone()
        model = self.spar_model or self.model
        if model is None:
            self.spar_active = False
            self.spar_status.set("没有可用的棋风模型。请重新训练后再开始对局。")
            return
        settings = self.settings
        use_engine = self.spar_mode.startswith("KataGo")
        imitation = self.imitation_var.get()
        strength = self.strength_var.get()
        legend_name = self.spar_legend_name
        matching = bool(use_engine and self.match_strength_var.get() and not legend_name)
        profile = self._active_profile() if matching else None
        games, library, player = list(self.games), self.library, self.player
        self.spar_status.set(self.spar_mode + "\n对手正在思考…")
        if legend_name:
            self.spar_status.set(
                self.spar_mode + f"\n名手：{legend_name} · 对手正在思考…")
        if matching and profile is None:
            self.spar_status.set(self.spar_mode + "\n尚无棋力匹配档案，正在统计已缓存的实战分析…")
        def work(progress, cancel):
            analysis = None
            if use_engine:
                with KataGo(settings, self.library.directory / "engine-work") as engine:
                    request = engine.position_request(board.move_history, board.size)
                    request["maxVisits"] = max(settings.visits,
                                               int(settings.visits * (0.6 + strength)))
                    analysis = engine.query(request, cancel=cancel)[0]
            if cancel.is_set():
                raise RuntimeError("任务已取消")
            profile_used = profile
            if matching and profile_used is None:
                profile_used = build_profile(games, library, player, settings.key, cancel)
                if profile_used is not None:
                    save_profile(library.directory / "strength-profile.json", profile_used)
            if cancel.is_set():
                raise RuntimeError("任务已取消")
            return (choose_move(board, board.to_play, model, analysis, imitation, strength,
                                profile=profile_used), profile_used)
        def finished(result):
            point, profile_used = result
            if profile_used is not None and matching:
                self.strength_profile = profile_used
                self.strength_profile_key = settings.key
            self.spar_board.play(self.spar_board.to_play, *point)
            self.spar_saved = False
            self._invalidate_assessment()
            self.spar_view.show(self.spar_board)
            if not self._check_end():
                self._spar_info()
        self._run("模拟对手正在选择落点…", work, finished)

    def _spar_info(self) -> None:
        color = "黑" if self.spar_board.to_play == "B" else "白"
        detail = ""
        if self.spar_legend_name:
            detail = "\n名手模式：不使用你的棋力损失档案；用选点强度滑条调节。"
            if not self.spar_mode.startswith("KataGo"):
                detail += "\n未配置 KataGo：名手风格只能做轻量近似。"
        elif self.spar_mode.startswith("KataGo") and self.match_strength_var.get():
            profile = self._active_profile()
            detail = ("\n" + profile.summary()) if profile else "\n棋力匹配：尚无足够分析，暂按引擎排名选点"
        self.spar_status.set(f"{self.spar_mode}{detail}\n"
                             f"第 {len(self.spar_board.move_history) + 1} 手 · {color}方行棋\n"
                             f"黑提子 {self.spar_board.captures['B']} · 白提子 {self.spar_board.captures['W']}")

    def open_trial(self) -> None:
        if self.trial_window is not None and self.trial_window.winfo_exists():
            self.trial_window.lift()
            return
        if not (self.spar_active or self.spar_pending_score or self.spar_board.move_history):
            messagebox.showinfo("先开始对局", "请先开始模拟对弈，再从当前局面打开试下窗口。")
            return
        # Modeless and snapshot-based: even a thinking opponent cannot change the copy.
        self.trial_window = TrialWindow(self.root, self.spar_board,
                                        lambda: self.spar_board.clone())

    def _invalidate_assessment(self) -> None:
        self.spar_estimate = None
        self.assessment_text.set("棋盘已变化，点击“形势判断”更新（需要 KataGo）。")

    def assess_spar(self) -> None:
        if not self._idle():
            return
        if not (self.spar_active or self.spar_pending_score or self.spar_board.move_history):
            messagebox.showinfo("先开始对局", "请先开始一盘模拟对弈，再查看形势。")
            return
        if not self._engine_ready():
            return
        board, settings, komi = self.spar_board.clone(), self.settings, self.spar_komi
        turn, human_color = len(board.move_history), self.human_color
        def work(progress, cancel):
            with KataGo(settings, self.library.directory / "engine-work") as engine:
                request = engine.position_request(board.move_history, board.size, komi)
                request["includeOwnership"] = True
                request["maxVisits"] = max(400, settings.visits)
                response = engine.query(request, cancel=cancel)[0]
            return PositionEstimate.from_response(response, board.size)
        def finished(estimate):
            self.spar_estimate = estimate
            text = estimate.summary(human_color, turn)
            self.assessment_text.set(text)
            self.tabs.select(self.spar_tab)
            self._notify(f"第 {turn} 手形势判断完成 · 仅作参考，不作为终局判决", "success")
            messagebox.showinfo("形势判断 · 估计值", text, parent=self.root)
        self._run("KataGo 正在判断当前形势…", work, finished)

    def _check_end(self) -> bool:
        history = self.spar_board.move_history
        if len(history) >= 2 and history[-1].is_pass and history[-2].is_pass:
            self.spar_active = False
            self.spar_pending_score = True
            self.spar_status.set("双方连续停一手，等待终局确认。请标记死子后确认胜负。")
            self.score_spar()
            return True
        return False

    def score_spar(self) -> None:
        if not self._idle():
            return
        if self.scoring_dialog is not None and self.scoring_dialog.winfo_exists():
            self.scoring_dialog.lift()
            return
        if not self.spar_board.move_history:
            messagebox.showinfo("尚无对局", "请先开始并完成一盘对局。")
            return
        if self.spar_result.endswith("+R"):
            messagebox.showinfo("认输结果", f"该局已认输，结果为 {self.spar_result}，无需数子。")
            return
        if self.spar_active and not messagebox.askyesno(
            "提前终局确认", "尚未连续停一手。确定暂停对局、确认死子并数子？\n"
            "仍有争议时可在计分窗口返回继续下棋。"
        ):
            return
        self.spar_active = False
        self.spar_pending_score = True
        self.spar_saved = False
        self.spar_result = ""
        dead = set(self.spar_score.dead) if self.spar_score else set(self.spar_dead)
        self.spar_score = None
        self.scoring_dialog = ScoringDialog(
            self.root, self.spar_board, self.spar_komi,
            self._accept_score, self._resume_after_scoring, self._defer_score, dead,
        )

    def _accept_score(self, score: AreaScore) -> None:
        self.spar_score = score
        self.spar_saved = False
        self.spar_dead = set(score.dead)
        self.spar_result = score.result
        self.spar_pending_score = False
        self.spar_active = False
        self.spar_status.set("对局结束 · 死子已确认\n" + score.summary())
        self._notify(f"终局判定：{score.result} · 点击“保存并复盘”加入棋谱库", "success")

    def _defer_score(self) -> None:
        if self.scoring_dialog is not None:
            self.spar_dead = set(self.scoring_dialog.dead)
        self.spar_pending_score = True
        self.spar_status.set("终局尚未确认，不写入确定胜负。点击“终局判定”可继续确认死子。")

    def _resume_after_scoring(self) -> None:
        history = list(self.spar_board.move_history)
        if len(history) >= 2 and history[-1].is_pass and history[-2].is_pass:
            history = history[:-2]
        board = GoBoard(self.spar_board.size)
        for move in history:
            board.play(move.color, move.x, move.y)
        self.spar_board = board
        self.spar_result = ""
        self.spar_saved = False
        self.spar_score = None
        self.spar_dead.clear()
        self.spar_pending_score = False
        self.spar_active = True
        self._invalidate_assessment()
        self.spar_view.show(board)
        self._spar_info()
        if board.to_play != self.human_color:
            self.bot_play()

    def undo(self) -> None:
        if not self._idle() or not self.spar_active or not self.spar_board.move_history:
            return
        history = self.spar_board.move_history
        remove = 2 if len(history) >= 2 else 1
        board = GoBoard(self.spar_board.size)
        for move in history[:-remove]:
            board.play(move.color, move.x, move.y)
        self.spar_board = board
        self.spar_saved = False
        self._invalidate_assessment()
        self.spar_view.show(board)
        self._spar_info()
        if board.to_play != self.human_color:
            self.bot_play()

    def resign(self) -> None:
        if not self._idle() or not self.spar_active:
            return
        if messagebox.askyesno("确认认输", "是否结束当前对局并认输？"):
            self.spar_active = False
            self.spar_saved = False
            self.spar_result = opposite(self.human_color) + "+R"
            self.spar_status.set("你已认输。点击“保存并复盘”加入棋谱库，或开始新对局。")

    def _spar_sgf_text(self) -> str:
        def escape(text: str) -> str:
            return text.replace("\\", "\\\\").replace("]", "\\]")
        opponent = self.spar_opponent_label or self._opponent_sgf_label()
        black = self.player if self.human_color == "B" else opponent
        white = self.player if self.human_color == "W" else opponent
        result = getattr(self, "spar_result", "")
        sgf = (f"(;GM[1]FF[4]CA[UTF-8]AP[YehuStyleGo:0.1]SZ[{self.spar_board.size}]"
               f"PB[{escape(black)}]PW[{escape(white)}]KM[{self.spar_komi:g}]RU[Chinese]"
               f"DT[{datetime.now():%Y-%m-%d}]RE[{result}]"
               + "".join(f";{move.color}[{move.sgf_coord}]" for move in self.spar_board.move_history))
        if self.spar_score is not None and result:
            score = self.spar_score
            sgf += f";C[{escape('死子已由使用者确认。' + score.summary())}]"
            for name, stone in (("TB", 1), ("TW", 2)):
                points = [point for point, owner in sorted(score.territory.items()) if owner == stone]
                if points:
                    sgf += name + "".join(f"[{Move('B', x, y).sgf_coord}]" for x, y in points)
            if score.dead:
                sgf += "MA" + "".join(f"[{Move('B', x, y).sgf_coord}]" for x, y in sorted(score.dead))
        return sgf + ")"

    def save_spar(self) -> None:
        if not self.spar_board.move_history:
            return
        path = filedialog.asksaveasfilename(defaultextension=".sgf", filetypes=[("SGF 棋谱", "*.sgf")],
                                          initialfile=f"影棋陪练-{datetime.now():%Y%m%d-%H%M%S}.sgf")
        if not path:
            return
        try:
            Path(path).write_text(self._spar_sgf_text(), encoding="utf-8")
            self.spar_saved = True
            self._notify(f"陪练棋谱已导出：{path}", "success")
        except OSError as error:
            messagebox.showerror("保存失败", str(error))

    def save_and_review(self) -> None:
        """Save the local game into the library and open it for immediate review."""
        if not self.spar_board.move_history:
            messagebox.showinfo("还没有对局", "先下一盘棋，再保存到棋谱库。")
            return
        if not self.player:
            messagebox.showinfo("先设置用户名", "请先在设置中填写你的野狐用户名并保存。")
            self.tabs.select(self.settings_tab)
            return
        folder = self.library.directory / "local-games"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = folder / f"影棋陪练-{stamp}.sgf"
            counter = 2
            while path.exists():
                path = folder / f"影棋陪练-{stamp}-{counter}.sgf"
                counter += 1
            path.write_text(self._spar_sgf_text(), encoding="utf-8")
        except OSError as error:
            messagebox.showerror("保存失败", str(error))
            return
        self.spar_saved = True
        imported = self.library.import_paths([path])
        try:
            saved = self.library.game_by_sha(parse_sgf_file(path).sha256)
        except (OSError, ValueError, RecursionError):
            saved = None
        self.refresh_library()
        if saved is not None:
            iid = str(saved.database_id)
            if iid in self.game_table.get_children():
                self.game_table.selection_set(iid)
            self.load_game(saved)
            self.tabs.select(self.review_tab)
        if imported["errors"]:
            messagebox.showwarning("已保存，但导入失败",
                                   "\n".join(imported["errors"][:5]) + f"\n\n文件：{path}")
            self._notify(f"陪练棋谱已保存：{path}", "warning")
        else:
            self._notify(
                f"已保存并导入棋谱库：{path.name}；可立即复盘。"
                "点击复盘页的“更新棋风”或“我的棋风 → 重新训练棋风模型”把本局纳入训练。",
                "success")

    def export_analysis(self) -> None:
        if not self.rows:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("分析表", "*.csv")])
        if path:
            try:
                with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
                    writer = csv.writer(file)
                    writer.writerow(["手数", "实战", "建议", "近似损失/目", "本人胜率", "本人目差/目", "变化"])
                    for row in self.rows:
                        writer.writerow([row["turn"], row["move"], row["best"], f"{row['loss']:.3f}",
                                         f"{row['winrate']:.5f}", f"{row['score']:.3f}", " ".join(row["pv"])])
                self._notify(f"分析已导出：{path}", "success")
            except OSError as error:
                messagebox.showerror("导出失败", str(error))

    def close(self) -> None:
        if self.busy and not messagebox.askyesno("退出", "任务尚未完成。取消任务并退出？"):
            return
        if (self.spar_board.move_history and not self.spar_saved) and not messagebox.askyesno(
            "退出", "当前陪练棋谱尚未保存。仍要退出？"
        ):
            return
        self.cancel.set()
        if hasattr(self, "online_panel"):
            self.online_panel.shutdown()
        self.closed = True
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="影棋 · 本地棋风分析与模拟对弈")
    parser.add_argument("--data-dir", type=Path, help="使用独立的数据目录")
    parser.add_argument("--import-sgf", type=Path, action="append", default=[])
    args = parser.parse_args()
    library = Library(args.data_dir)
    if args.import_sgf:
        result = library.import_paths(args.import_sgf)
        print(json.dumps(result, ensure_ascii=False))
    theme.enable_dpi_awareness()
    root = tk.Tk()
    # Build the themed UI while the window is hidden, otherwise the default
    # white Tk window can flash before the selected light/dark palette is applied.
    root.withdraw()
    app = Application(root, library)
    root.update_idletasks()
    root.deiconify()
    app.root.mainloop()


if __name__ == "__main__":
    main()
