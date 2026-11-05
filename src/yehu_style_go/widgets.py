from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from yehu_style_go.board import BLACK, EMPTY, GTP_COLUMNS, GoBoard
from yehu_style_go import theme

_STONE_BLACK = "#232529"
_STONE_WHITE = "#efe9dd"
_STONE_BLACK_EDGE = "#101114"
_STONE_WHITE_EDGE = "#a89e8b"
_STONE_BLACK_CORE = "#41464e"
_STONE_WHITE_CORE = "#ffffff"
_TEXT_ON_BLACK = "#fbfaf6"
_TEXT_ON_WHITE = "#1b1c1e"


def _blend(color_a: str, color_b: str, ratio: float) -> str:
    """Interpolate two #rrggbb colors; ratio 0 keeps color_a, 1 gives color_b."""
    ratio = max(0.0, min(1.0, ratio))
    a = tuple(int(color_a[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(color_b[i:i + 2], 16) for i in (1, 3, 5))
    mixed = (round(a[i] + (b[i] - a[i]) * ratio) for i in range(3))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def _readable_on(color: str) -> str:
    """Pick near-black or near-white text for a #rrggbb background."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return _TEXT_ON_WHITE if 0.299 * r + 0.587 * g + 0.114 * b > 150 else _TEXT_ON_BLACK


class BoardView(tk.Canvas):
    def __init__(self, parent, on_play: Callable[[int, int], None] | None = None,
                 hover: Callable[[], str | None] | None = None,
                 on_navigate: Callable[[int], bool] | None = None,
                 on_point: Callable[[tuple[int, int] | None], None] | None = None,
                 on_key: Callable[[str], bool] | None = None, **kwargs):
        super().__init__(parent, background=theme.BOARD, highlightthickness=0, takefocus=True,
                         width=540, height=540, **kwargs)
        self.board = GoBoard()
        self.on_play = on_play
        # Optional callback returning the color ("B"/"W") allowed to preview a
        # ghost stone under the mouse, or None to hide the preview.
        self.hover = hover
        self.on_navigate = on_navigate
        # Review boards use these to preview a candidate's variation on hover
        # and to handle extra keys (numbers / Esc / Home / End).
        self.on_point = on_point
        self.on_key = on_key
        self.hover_point: tuple[int, int] | None = None
        self.numbered: dict[tuple[int, int], int] = {}
        self.marks: dict[tuple[int, int], str] = {}
        self.candidates: dict[tuple[int, int], str] = {}
        self.candidate_kinds: dict[tuple[int, int], str] = {}
        self.candidate_subs: dict[tuple[int, int], str] = {}
        self.hover_candidate: tuple[int, int] | None = None
        # Faint numbered stones shown while hovering a candidate; not a real move.
        self.preview: list[tuple[str, int | None, int | None]] = []
        # Numbering offset so a "next move" ghost continues the current step.
        self.preview_offset = 0
        self.dead_points: set[tuple[int, int]] = set()
        # Review boards navigate by turn/PV, so the keyboard placement cursor
        # (a square that can sit on top of real stones) is turned off there.
        self.show_cursor = True
        self.cursor = (3, 3)
        self.origin = (30.0, 30.0)
        self.spacing = 24.0
        self.bind("<Configure>", lambda event: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<KeyPress>", self._key)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<FocusIn>", lambda event: self.redraw())
        self.bind("<FocusOut>", lambda event: self.redraw())

    def _paint_wood(self) -> None:
        """Subtle vertical wood gradient so the board reads as a panel, not a flat fill."""
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        top = _blend(theme.BOARD, "#ffffff", 0.09)
        bottom = _blend(theme.BOARD, "#000000", 0.11)
        steps = 40
        strip = height / steps
        for index in range(steps):
            color = _blend(top, bottom, index / (steps - 1))
            y0 = index * strip
            self.create_rectangle(0, y0, width + 1, y0 + strip + 1,
                                  fill=color, outline="")

    def _draw_stone(self, cx: float, cy: float, radius: float, color: str) -> None:
        """Layered ovals fake a lit stone dome: shadow, body, off-center core, specular."""
        shadow = _blend(theme.BOARD, "#000000", 0.42)
        shift_x, shift_y = radius * 0.09, radius * 0.16
        self.create_oval(cx - radius + shift_x, cy - radius + shift_y,
                         cx + radius + shift_x + 1, cy + radius + shift_y + 1,
                         fill=shadow, outline="", stipple="gray50")
        if color == BLACK:
            base, edge, core = _STONE_BLACK, _STONE_BLACK_EDGE, _STONE_BLACK_CORE
            core_scale = 0.74
        else:
            base, edge, core = _STONE_WHITE, _STONE_WHITE_EDGE, _STONE_WHITE_CORE
            core_scale = 0.60
        self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                         fill=base, outline=edge, width=1)
        # Off-center lighter core: light arrives from the upper left.
        r2 = radius * core_scale
        dx, dy = radius * 0.10, radius * 0.12
        self.create_oval(cx - r2 - dx, cy - r2 - dy, cx + r2 - dx, cy + r2 - dy,
                         fill=core, outline="")
        if color == BLACK:
            hr = max(1.6, radius * 0.16)
            hx, hy = cx - radius * 0.34, cy - radius * 0.42
            self.create_oval(hx - hr, hy - hr * 0.72, hx + hr, hy + hr * 0.72,
                             fill="#d7dbe0", outline="", stipple="gray50")

    def show(self, board: GoBoard, numbered: dict | None = None,
             marks: dict[tuple[int, int], str] | None = None,
             candidates: dict[tuple[int, int], str] | None = None,
             candidate_kinds: dict[tuple[int, int], str] | None = None,
             candidate_subs: dict[tuple[int, int], str] | None = None) -> None:
        self.board = board
        self.numbered = numbered or {}
        self.marks = marks or {}
        self.candidates = candidates or {}
        self.candidate_kinds = candidate_kinds or {}
        self.candidate_subs = candidate_subs or {}
        self.hover_candidate = None
        self.preview = []
        self.preview_offset = 0
        self.redraw()

    def set_preview(self, moves: list[tuple[str, int | None, int | None]] | None,
                    hover: tuple[int, int] | None = None, offset: int = 0) -> None:
        """Show a faint numbered variation without changing the real position."""
        self.preview = list(moves or [])
        self.hover_candidate = hover
        self.preview_offset = offset
        self.redraw()

    def redraw(self) -> None:
        self.configure(background=theme.BOARD)
        self.delete("all")
        self._paint_wood()
        size = self.board.size
        side = min(self.winfo_width(), self.winfo_height())
        if side < 50:
            side = 540
        margin = max(28, side * 0.053, side / (size + 1) * 0.8)
        spacing = (side - 2 * margin) / (size - 1)
        axis_offset = max(17, spacing * 0.46 + 10)
        ox = (self.winfo_width() - side) / 2 + margin
        oy = (self.winfo_height() - side) / 2 + margin
        self.origin = (ox, oy)
        self.spacing = spacing
        end_x, end_y = ox + (size - 1) * spacing, oy + (size - 1) * spacing
        font_size = max(8, min(10, round(spacing * 0.38)))
        for index in range(size):
            x = ox + index * spacing
            y = oy + index * spacing
            edge = 2 if index in (0, size - 1) else 1
            self.create_line(x, oy, x, end_y, fill=theme.BOARD_LINE, width=edge)
            self.create_line(ox, y, end_x, y, fill=theme.BOARD_LINE, width=edge)
            self.create_text(x, oy - axis_offset, text=GTP_COLUMNS[index], fill=theme.BOARD_COORD,
                             font=(theme.NUM_FONT, font_size))
            self.create_text(ox - axis_offset, y, text=str(size - index), fill=theme.BOARD_COORD,
                             font=(theme.NUM_FONT, font_size))
        stars = (3, 9, 15) if size == 19 else (3, 6, 9) if size == 13 else (2, 4, 6)
        for sy in stars:
            for sx in stars:
                if size == 9 and (sx == 4) != (sy == 4):
                    continue
                x, y = ox + sx * spacing, oy + sy * spacing
                self.create_oval(x - 2.6, y - 2.6, x + 2.6, y + 2.6,
                                 fill=theme.BOARD_COORD, outline="")
        radius = spacing * 0.46
        for y, row in enumerate(self.board.grid):
            for x, stone in enumerate(row):
                if stone == EMPTY:
                    continue
                cx, cy = ox + x * spacing, oy + y * spacing
                self._draw_stone(cx, cy, radius, stone)
                if (x, y) in self.numbered:
                    self.create_text(cx, cy, text=str(self.numbered[(x, y)]),
                                     fill=_TEXT_ON_BLACK if stone == BLACK else _TEXT_ON_WHITE,
                                     font=(theme.NUM_FONT, max(8, round(spacing * 0.44)), "bold"))
        if self.board.move_history:
            last = self.board.move_history[-1]
            if not last.is_pass and (last.x, last.y) not in self.numbered:
                cx, cy = ox + last.x * spacing, oy + last.y * spacing
                color = _TEXT_ON_BLACK if last.color == "B" else _TEXT_ON_WHITE
                self.create_oval(cx - 3.2, cy - 3.2, cx + 3.2, cy + 3.2,
                                 fill=color, outline="")
        for x, y in self.dead_points:
            cx, cy = ox + x * spacing, oy + y * spacing
            color = _TEXT_ON_BLACK if self.board.grid[y][x] == BLACK else _TEXT_ON_WHITE
            arm = radius * 0.65
            self.create_line(cx - arm, cy - arm, cx + arm, cy + arm, fill=color, width=3)
            self.create_line(cx - arm, cy + arm, cx + arm, cy - arm, fill=color, width=3)
        # Hover preview: faint numbered stones for the candidate's variation.
        for index, (color, px, py) in enumerate(self.preview, 1):
            if px is None:
                continue
            cx, cy = ox + px * spacing, oy + py * spacing
            fill = _STONE_BLACK if color == "B" else _STONE_WHITE
            text_color = _readable_on(fill)
            self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                             fill=fill, outline=theme.ACCENT, width=2, stipple="gray50")
            self.create_text(cx, cy, text=str(index + self.preview_offset),
                             fill=text_color,
                             font=(theme.NUM_FONT, max(7, round(spacing * 0.34)), "bold"))
        kinds = {"best": theme.ACCENT, "good": theme.SUCCESS,
                 "warn": theme.WARNING, "bad": theme.DANGER}
        for (x, y), label in self.candidates.items():
            # While previewing, the hovered candidate is represented by preview
            # stone #1 at the same point, so skip its plain circle.
            if self.hover_candidate == (x, y) and self.preview:
                continue
            cx, cy = ox + x * spacing, oy + y * spacing
            fill = kinds.get(self.candidate_kinds.get((x, y), "best"), theme.ACCENT)
            text_color = _readable_on(fill)
            hovered = self.hover_candidate == (x, y)
            self.create_oval(cx - radius * 0.95, cy - radius * 0.95,
                             cx + radius * 0.95, cy + radius * 0.95,
                             fill=fill, outline=text_color, width=3 if hovered else 1,
                             stipple="gray50")
            sub = self.candidate_subs.get((x, y), "")
            if sub:
                self.create_text(cx, cy - spacing * 0.10, text=label, fill=text_color,
                                 font=(theme.NUM_FONT, max(8, round(spacing * 0.32)), "bold"))
                self.create_text(cx, cy + spacing * 0.20, text=sub, fill=text_color,
                                 font=(theme.NUM_FONT, max(7, round(spacing * 0.24))))
            else:
                self.create_text(cx, cy, text=label, fill=text_color,
                                 font=(theme.NUM_FONT, max(8, round(spacing * 0.36)), "bold"))
        for (x, y), kind in self.marks.items():
            cx, cy = ox + x * spacing, oy + y * spacing
            color = theme.DANGER if kind == "actual" else theme.SUCCESS
            self.create_oval(cx - radius - 3, cy - radius - 3,
                             cx + radius + 3, cy + radius + 3,
                             outline=color, width=2)
        self._draw_ghost(ox, oy, radius)
        if self.show_cursor and self.focus_get() == self and self.on_play is not None:
            x, y = self.cursor
            cx, cy = ox + x * spacing, oy + y * spacing
            self.create_rectangle(cx - radius, cy - radius, cx + radius, cy + radius,
                                  outline=theme.ACCENT, width=2)

    def _draw_ghost(self, ox: float, oy: float, radius: float) -> None:
        """Translucent preview stone under the mouse (interactive boards only)."""
        if self.hover is None or self.on_play is None or self.hover_point is None:
            return
        color = self.hover()
        if color not in {"B", "W"}:
            return
        x, y = self.hover_point
        if not (0 <= x < self.board.size and 0 <= y < self.board.size):
            return
        if self.board.grid[y][x] != EMPTY:
            return
        cx, cy = ox + x * self.spacing, oy + y * self.spacing
        fill = _STONE_BLACK if color == "B" else _STONE_WHITE
        self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                         fill=fill, outline="", stipple="gray50")

    def _motion(self, event) -> None:
        if self.hover is None and self.on_point is None:
            return
        x = round((event.x - self.origin[0]) / self.spacing)
        y = round((event.y - self.origin[1]) / self.spacing)
        point = (x, y) if 0 <= x < self.board.size and 0 <= y < self.board.size else None
        if point != self.hover_point:
            self.hover_point = point
            had_preview = bool(self.preview or self.hover_candidate)
            if self.on_point is not None:
                self.on_point(point)
            # Only repaint when hovering can actually change something; a plain
            # review board must not redraw on every cell the pointer crosses.
            if (self.hover is not None or self.candidates or had_preview
                    or self.preview or self.hover_candidate):
                self.redraw()

    def _leave(self, _event) -> None:
        if self.hover_point is not None:
            self.hover_point = None
            had_preview = bool(self.preview or self.hover_candidate)
            if self.on_point is not None:
                self.on_point(None)
            if self.hover is not None or had_preview or self.candidates:
                self.redraw()

    def refresh_theme(self) -> None:
        self.configure(background=theme.BOARD)
        self.redraw()

    def _click(self, event) -> None:
        self.focus_set()
        x = round((event.x - self.origin[0]) / self.spacing)
        y = round((event.y - self.origin[1]) / self.spacing)
        if 0 <= x < self.board.size and 0 <= y < self.board.size:
            self.cursor = x, y
            if self.on_play:
                self.on_play(x, y)
            self.redraw()

    def _key(self, event) -> str | None:
        if self.on_key is not None and self.on_key(event.keysym):
            return "break"
        x, y = self.cursor
        offsets = {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}
        if event.keysym in {"Left", "Right"} and self.on_navigate is not None:
            if self.on_navigate(-1 if event.keysym == "Left" else 1):
                return "break"
        if event.keysym in offsets:
            dx, dy = offsets[event.keysym]
            self.cursor = max(0, min(self.board.size - 1, x + dx)), max(
                0, min(self.board.size - 1, y + dy)
            )
            self.redraw()
            return "break"
        if event.keysym in {"Return", "space"} and self.on_play and self.show_cursor:
            self.on_play(x, y)
            return "break"
        return None


class WinrateChart(ttk.Frame):
    """Single sequential series with a synchronized table and pointer/keyboard readout."""

    def __init__(self, parent, on_turn: Callable[[int], None]):
        super().__init__(parent)
        self.on_turn = on_turn
        self.points: list[tuple[int, float, float]] = []
        self.selected_index = 0
        self.readout = tk.StringVar(value="尚无 KataGo 分析结果；不显示估算胜率")
        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text="本人胜率走势", style="Section.TLabel").pack(side="left")
        ttk.Button(header, text="数据表", command=self.show_table).pack(side="right")
        self.canvas = tk.Canvas(self, height=86, background=theme.SURFACE, highlightthickness=0,
                                takefocus=True, cursor="hand2")
        self.canvas.pack(fill="x", pady=(5, 0))
        ttk.Label(self, textvariable=self.readout, wraplength=650, style="Muted.TLabel").pack(
            anchor="w")
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Button-1>", self._select)
        self.canvas.bind("<Left>", lambda event: self._step(-1))
        self.canvas.bind("<Right>", lambda event: self._step(1))
        self.canvas.bind("<Return>", lambda event: self._activate())

    def set_points(self, points: list[tuple[int, float, float]]) -> None:
        self.points = points
        self.selected_index = 0
        self.readout.set("尚无 KataGo 分析结果；不显示估算胜率" if not points else
                         "移动鼠标或用左右方向键查看；回车跳转到对应手数")
        self.draw()

    def refresh_theme(self) -> None:
        self.canvas.configure(background=theme.SURFACE)
        self.draw()

    def draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(200, canvas.winfo_width())
        height = max(80, canvas.winfo_height())
        left, right, top, bottom = 43, width - 25, 12, height - 24
        for percentage in (0, 50, 100):
            y = bottom - percentage / 100 * (bottom - top)
            canvas.create_line(left, y, right, y, fill=theme.BORDER)
            canvas.create_text(left - 8, y, text=f"{percentage}%", anchor="e",
                               fill=theme.MUTED, font=(theme.NUM_FONT, 9))
        if not self.points:
            canvas.create_text((left + right) / 2, (top + bottom) / 2,
                               text="尚无分析结果；点击“分析这盘棋”",
                               fill=theme.MUTED, font=(theme.FONT, 10))
            return
        last_turn = max(1, self.points[-1][0])
        coordinates = []
        for turn, rate, _ in self.points:
            coordinates.extend([left + turn / last_turn * (right - left),
                                bottom - rate * (bottom - top)])
        if len(coordinates) >= 4:
            canvas.create_polygon([left, bottom, *coordinates, right, bottom],
                                  fill=theme.ACCENT, stipple="gray12", outline="")
            canvas.create_line(*coordinates, fill=theme.ACCENT, width=2,
                               capstyle="round", joinstyle="round")
        x, y = coordinates[-2:]
        canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=theme.ACCENT,
                           outline=theme.SURFACE, width=2)
        for turn in (0, last_turn):
            canvas.create_text(left + turn / last_turn * (right - left), bottom + 15,
                               text=f"{turn} 手", fill=theme.MUTED, font=(theme.NUM_FONT, 9))
        if self.points:
            self._crosshair()

    def _index_at(self, x: int) -> int:
        left, right = 43, max(200, self.canvas.winfo_width()) - 25
        target = (x - left) / max(1, right - left) * max(1, self.points[-1][0])
        return min(range(len(self.points)), key=lambda index: abs(self.points[index][0] - target))

    def _hover(self, event) -> None:
        if self.points:
            self.selected_index = self._index_at(event.x)
            self._crosshair()

    def _select(self, event) -> None:
        self.canvas.focus_set()
        self._hover(event)
        self._activate()

    def _step(self, direction: int) -> str:
        if self.points:
            self.selected_index = max(0, min(len(self.points) - 1, self.selected_index + direction))
            self._crosshair()
        return "break"

    def _activate(self) -> str:
        if self.points:
            self.on_turn(self.points[self.selected_index][0])
        return "break"

    def _crosshair(self) -> None:
        if not self.points:
            return
        turn, rate, score = self.points[self.selected_index]
        width = max(200, self.canvas.winfo_width())
        height = max(80, self.canvas.winfo_height())
        x = 43 + turn / max(1, self.points[-1][0]) * (width - 68)
        self.canvas.delete("crosshair")
        self.canvas.create_line(x, 12, x, height - 24, fill=theme.BORDER, tags="crosshair")
        self.readout.set(f"第 {turn} 手  ·  本人胜率 {rate:.1%}  ·  本人目差 {score:+.1f} 目")

    def show_table(self) -> None:
        window = tk.Toplevel(self)
        window.update_idletasks()
        from yehu_style_go.theme import apply_title_bar
        apply_title_bar(window, theme.is_dark())
        window.title("胜率与目差 · 全部局面数据")
        window.geometry("510x500")
        window.configure(background=theme.SURFACE)
        table = ttk.Treeview(window, columns=("turn", "rate", "score"), show="headings")
        for column, title in (("turn", "手数"), ("rate", "本人胜率"), ("score", "本人目差（目）")):
            table.heading(column, text=title)
            table.column(column, width=145, anchor="center")
        table.tag_configure("even", background=theme.SURFACE)
        table.tag_configure("odd", background=theme.SURFACE_ALT)
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        table.pack(fill="both", expand=True)
        for index, (turn, rate, score) in enumerate(self.points):
            table.insert("", "end", values=(turn, f"{rate:.1%}", f"{score:+.1f}"),
                         tags=("odd" if index % 2 else "even",))
