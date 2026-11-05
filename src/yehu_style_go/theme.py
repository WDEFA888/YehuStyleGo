"""Shared visual language: warm paper by day, deep ink by night."""
from __future__ import annotations

import ctypes
import sys
import tkinter.font as tkfont
from dataclasses import dataclass

import tkinter as tk
from tkinter import ttk


@dataclass(frozen=True)
class Palette:
    BG: str
    SURFACE: str
    SURFACE_ALT: str
    INK: str
    MUTED: str
    FAINT: str
    BORDER: str
    ACCENT: str
    ACCENT_DARK: str
    ACCENT_BUTTON: str
    ACCENT_BUTTON_HOVER: str
    ACCENT_ON: str
    SELECTION: str
    DANGER: str
    DANGER_SOFT: str
    SUCCESS: str
    SUCCESS_SOFT: str
    WARNING: str
    WARNING_SOFT: str
    BOARD: str
    BOARD_LINE: str
    BOARD_COORD: str
    FIELD: str
    BUTTON: str
    BUTTON_HOVER: str
    BUTTON_PRESSED: str
    LOGO_BLACK: str
    LOGO_WHITE: str
    LOGO_BLACK_OUTLINE: str
    LOGO_WHITE_OUTLINE: str


LIGHT = Palette(
    BG="#f1ece2",
    SURFACE="#fdfbf6",
    SURFACE_ALT="#f7f2e8",
    INK="#24262a",
    MUTED="#6d675d",
    FAINT="#9a9488",
    BORDER="#e1d8c8",
    ACCENT="#2f5d50",
    ACCENT_DARK="#24493e",
    ACCENT_BUTTON="#2f5d50",
    ACCENT_BUTTON_HOVER="#24493e",
    ACCENT_ON="#ffffff",
    SELECTION="#dce9e2",
    DANGER="#a34536",
    DANGER_SOFT="#f7ebe7",
    SUCCESS="#2f7d4f",
    SUCCESS_SOFT="#e4f1e8",
    WARNING="#9a6a1d",
    WARNING_SOFT="#f6efdd",
    BOARD="#dbbd83",
    BOARD_LINE="#6d5433",
    BOARD_COORD="#5d482c",
    FIELD="#ffffff",
    BUTTON="#f7f2e8",
    BUTTON_HOVER="#efe8da",
    BUTTON_PRESSED="#e6ddcb",
    LOGO_BLACK="#1b1c1e",
    LOGO_WHITE="#fbfaf6",
    LOGO_BLACK_OUTLINE="#1b1c1e",
    LOGO_WHITE_OUTLINE="#cfc6b5",
)

DARK = Palette(
    BG="#111516",
    SURFACE="#1b2122",
    SURFACE_ALT="#242b2c",
    INK="#ece7dd",
    MUTED="#a9a297",
    FAINT="#6f736f",
    BORDER="#343d3d",
    ACCENT="#8fc4ae",
    ACCENT_DARK="#a9d7c1",
    ACCENT_BUTTON="#356b59",
    ACCENT_BUTTON_HOVER="#2b5849",
    ACCENT_ON="#ffffff",
    SELECTION="#2d443b",
    DANGER="#e39a8b",
    DANGER_SOFT="#3a2926",
    SUCCESS="#8fd0a6",
    SUCCESS_SOFT="#24352b",
    WARNING="#d9b26a",
    WARNING_SOFT="#3a3324",
    BOARD="#b19163",
    BOARD_LINE="#513d27",
    BOARD_COORD="#42311f",
    FIELD="#22292a",
    BUTTON="#242b2c",
    BUTTON_HOVER="#2e3738",
    BUTTON_PRESSED="#1f2627",
    LOGO_BLACK="#232c2b",
    LOGO_WHITE="#f0ebe1",
    LOGO_BLACK_OUTLINE="#93a69d",
    LOGO_WHITE_OUTLINE="#7f8d86",
)

_active = LIGHT
# Module-level names stay available for callers that prefer plain constants.
BG = SURFACE = SURFACE_ALT = INK = MUTED = FAINT = BORDER = ""
ACCENT = ACCENT_DARK = ACCENT_BUTTON = ACCENT_BUTTON_HOVER = ACCENT_ON = ""
SELECTION = DANGER = DANGER_SOFT = SUCCESS = SUCCESS_SOFT = WARNING = WARNING_SOFT = ""
BOARD = BOARD_LINE = BOARD_COORD = ""
FIELD = BUTTON = BUTTON_HOVER = BUTTON_PRESSED = ""
LOGO_BLACK = LOGO_WHITE = LOGO_BLACK_OUTLINE = LOGO_WHITE_OUTLINE = ""

# --------------------------------------------------------------------------
# Fonts: resolve once against the real display so the UI looks consistent on
# Windows, macOS and Linux instead of depending on a single hardcoded family.
# --------------------------------------------------------------------------
_UI_CANDIDATES = (
    "Microsoft YaHei UI", "微软雅黑", "PingFang SC", "Hiragino Sans GB",
    "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
    "Segoe UI", "Helvetica Neue", "Arial",
)
_NUM_CANDIDATES = (
    "Segoe UI", "SF Mono", "Cascadia Mono", "Menlo", "Consolas",
    "DejaVu Sans Mono", "Helvetica Neue", "Arial",
)

FONT = "Microsoft YaHei UI"  # UI font family (resolved on first apply_theme)
NUM_FONT = "Segoe UI"        # digits / coordinates font family
UI_FONT = SMALL_FONT = TITLE_FONT = PAGE_TITLE_FONT = SECTION_FONT = STAT_FONT = ()
HERO_FONT = HERO_SUB_FONT = ()


def _build_fonts() -> None:
    global UI_FONT, SMALL_FONT, TITLE_FONT, PAGE_TITLE_FONT, SECTION_FONT, STAT_FONT
    global HERO_FONT, HERO_SUB_FONT
    UI_FONT = (FONT, 10)
    SMALL_FONT = (FONT, 9)
    TITLE_FONT = (FONT, 24, "bold")
    PAGE_TITLE_FONT = (FONT, 19, "bold")
    SECTION_FONT = (FONT, 11, "bold")
    STAT_FONT = (FONT, 18, "bold")
    HERO_FONT = (FONT, 27, "bold")
    HERO_SUB_FONT = (FONT, 12)


_build_fonts()
_fonts_resolved = False


def resolve_fonts(root: tk.Misc | None = None) -> None:
    """Pick the best available font families for the current platform."""
    global FONT, NUM_FONT, _fonts_resolved
    if _fonts_resolved:
        return
    _fonts_resolved = True
    try:
        families = {name.lower() for name in tkfont.families(root)}
    except tk.TclError:
        return

    def pick(candidates: tuple[str, ...], fallback: str) -> str:
        for name in candidates:
            if name.lower() in families:
                return name
        return fallback

    FONT = pick(_UI_CANDIDATES, FONT)
    NUM_FONT = pick(_NUM_CANDIDATES, NUM_FONT)
    _build_fonts()


def enable_dpi_awareness() -> None:
    """Opt into per-monitor DPI scaling on Windows for crisp text rendering."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _export(palette: Palette) -> None:
    global _active, BG, SURFACE, SURFACE_ALT, INK, MUTED, FAINT, BORDER
    global ACCENT, ACCENT_DARK, ACCENT_BUTTON, ACCENT_BUTTON_HOVER, ACCENT_ON
    global SELECTION, DANGER, DANGER_SOFT, SUCCESS, SUCCESS_SOFT, WARNING, WARNING_SOFT
    global BOARD, BOARD_LINE, BOARD_COORD
    global FIELD, BUTTON, BUTTON_HOVER, BUTTON_PRESSED
    global LOGO_BLACK, LOGO_WHITE, LOGO_BLACK_OUTLINE, LOGO_WHITE_OUTLINE
    _active = palette
    BG, SURFACE, SURFACE_ALT = palette.BG, palette.SURFACE, palette.SURFACE_ALT
    INK, MUTED, FAINT, BORDER = palette.INK, palette.MUTED, palette.FAINT, palette.BORDER
    ACCENT, ACCENT_DARK = palette.ACCENT, palette.ACCENT_DARK
    ACCENT_BUTTON, ACCENT_BUTTON_HOVER = palette.ACCENT_BUTTON, palette.ACCENT_BUTTON_HOVER
    ACCENT_ON = palette.ACCENT_ON
    SELECTION, DANGER, DANGER_SOFT = palette.SELECTION, palette.DANGER, palette.DANGER_SOFT
    SUCCESS, SUCCESS_SOFT = palette.SUCCESS, palette.SUCCESS_SOFT
    WARNING, WARNING_SOFT = palette.WARNING, palette.WARNING_SOFT
    BOARD, BOARD_LINE, BOARD_COORD = palette.BOARD, palette.BOARD_LINE, palette.BOARD_COORD
    FIELD, BUTTON = palette.FIELD, palette.BUTTON
    BUTTON_HOVER, BUTTON_PRESSED = palette.BUTTON_HOVER, palette.BUTTON_PRESSED
    LOGO_BLACK, LOGO_WHITE = palette.LOGO_BLACK, palette.LOGO_WHITE
    LOGO_BLACK_OUTLINE, LOGO_WHITE_OUTLINE = (
        palette.LOGO_BLACK_OUTLINE, palette.LOGO_WHITE_OUTLINE)


def is_dark() -> bool:
    return _active is DARK


def apply_title_bar(window: tk.Misc, dark: bool) -> None:
    """Match the native Windows title bar to the selected theme when supported."""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        handle = int(window.wm_frame(), 16)
        if not handle:
            return
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                handle, attribute, ctypes.byref(value), ctypes.sizeof(value))
            if result == 0:
                break
    except (OSError, ValueError, tk.TclError):
        pass


def apply_theme(root: tk.Misc, dark: bool = False) -> None:
    resolve_fonts(root)
    palette = DARK if dark else LIGHT
    _export(palette)
    p = palette
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=p.BG)
    style.configure(".", font=UI_FONT, background=p.SURFACE, foreground=p.INK)

    # Surfaces
    style.configure("TFrame", background=p.SURFACE)
    style.configure("Page.TFrame", background=p.SURFACE)
    style.configure("Card.TFrame", background=p.SURFACE)
    style.configure("Outside.TFrame", background=p.BG)
    style.configure("TLabel", background=p.SURFACE, foreground=p.INK)
    style.configure("Muted.TLabel", background=p.SURFACE, foreground=p.MUTED)
    style.configure("Outside.TLabel", background=p.BG, foreground=p.INK)
    style.configure("OutsideMuted.TLabel", background=p.BG, foreground=p.MUTED)
    style.configure("Title.TLabel", background=p.SURFACE, foreground=p.INK, font=TITLE_FONT)
    style.configure("Hero.TLabel", background=p.SURFACE, foreground=p.INK, font=HERO_FONT)
    style.configure("HeroSub.TLabel", background=p.SURFACE, foreground=p.MUTED, font=HERO_SUB_FONT)
    style.configure("HeroAccent.TLabel", background=p.SURFACE, foreground=p.ACCENT, font=HERO_FONT)
    style.configure("PageTitle.TLabel", background=p.SURFACE, foreground=p.INK,
                    font=PAGE_TITLE_FONT)
    style.configure("Section.TLabel", background=p.SURFACE, foreground=p.INK,
                    font=SECTION_FONT)
    style.configure("Stat.TLabel", background=p.SURFACE, foreground=p.INK, font=STAT_FONT)
    style.configure("Note.TLabel", background=p.SURFACE_ALT, foreground=p.MUTED,
                    padding=(12, 10), font=SMALL_FONT)
    style.configure("SuccessNote.TLabel", background=p.SUCCESS_SOFT, foreground=p.SUCCESS,
                    padding=(12, 10), font=SMALL_FONT)
    style.configure("WarningNote.TLabel", background=p.WARNING_SOFT, foreground=p.WARNING,
                    padding=(12, 10), font=SMALL_FONT)
    style.configure("DangerNote.TLabel", background=p.DANGER_SOFT, foreground=p.DANGER,
                    padding=(12, 10), font=SMALL_FONT)
    style.configure("Accent.TLabel", background=p.SURFACE, foreground=p.ACCENT)
    style.configure("Danger.TLabel", background=p.SURFACE, foreground=p.DANGER)
    style.configure("Success.TLabel", background=p.SURFACE, foreground=p.SUCCESS)
    style.configure("Warning.TLabel", background=p.SURFACE, foreground=p.WARNING)
    style.configure("Tile.TFrame", background=p.SURFACE_ALT)
    style.configure("TileMuted.TLabel", background=p.SURFACE_ALT, foreground=p.MUTED,
                    font=SMALL_FONT)
    style.configure("TileStat.TLabel", background=p.SURFACE_ALT, foreground=p.INK,
                    font=(FONT, 15, "bold"))

    # Tabs
    style.configure("TNotebook", background=p.BG, borderwidth=0,
                    tabmargins=(6, 4, 6, 0))
    style.configure("TNotebook.Tab", background=p.BG, foreground=p.MUTED,
                    padding=(22, 11), borderwidth=0, font=UI_FONT,
                    lightcolor=p.BG, darkcolor=p.BG, bordercolor=p.BG)
    style.map("TNotebook.Tab",
              background=[("selected", p.SURFACE), ("active", p.SURFACE_ALT)],
              foreground=[("selected", p.ACCENT), ("active", p.INK)],
              font=[("selected", (FONT, 10, "bold"))],
              lightcolor=[("selected", p.SURFACE), ("active", p.SURFACE_ALT)],
              bordercolor=[("selected", p.SURFACE), ("active", p.SURFACE_ALT)],
              darkcolor=[("selected", p.SURFACE), ("active", p.SURFACE_ALT)])

    # Buttons: slightly larger hit area reads as calmer and is easier to click.
    style.configure("TButton", background=p.BUTTON, foreground=p.INK,
                    bordercolor=p.BORDER, lightcolor=p.BUTTON, darkcolor=p.BORDER,
                    relief="flat", padding=(14, 9), font=UI_FONT)
    style.map("TButton",
              background=[("active", p.BUTTON_HOVER), ("pressed", p.BUTTON_PRESSED)],
              foreground=[("disabled", p.FAINT)],
              bordercolor=[("focus", p.ACCENT)],
              lightcolor=[("active", p.BUTTON_HOVER), ("pressed", p.BUTTON_PRESSED)],
              darkcolor=[("active", p.BORDER)])
    style.configure("Accent.TButton", background=p.ACCENT_BUTTON, foreground=p.ACCENT_ON,
                    bordercolor=p.ACCENT_BUTTON, lightcolor=p.ACCENT_BUTTON,
                    darkcolor=p.ACCENT_BUTTON, relief="flat", padding=(16, 10),
                    font=(FONT, 10, "bold"))
    style.map("Accent.TButton",
              background=[("active", p.ACCENT_BUTTON_HOVER),
                          ("pressed", p.ACCENT_BUTTON_HOVER)],
              foreground=[("disabled", p.MUTED)],
              bordercolor=[("active", p.ACCENT_BUTTON_HOVER)],
              lightcolor=[("active", p.ACCENT_BUTTON_HOVER)],
              darkcolor=[("active", p.ACCENT_BUTTON_HOVER)])
    style.configure("Danger.TButton", background=p.BUTTON, foreground=p.DANGER,
                    bordercolor=p.DANGER_SOFT, lightcolor=p.BUTTON,
                    darkcolor=p.DANGER_SOFT, relief="flat", padding=(14, 9),
                    font=UI_FONT)
    style.map("Danger.TButton",
              background=[("active", p.DANGER_SOFT), ("pressed", p.DANGER_SOFT)],
              foreground=[("disabled", p.FAINT)])

    # Inputs
    style.configure("TEntry", fieldbackground=p.FIELD, foreground=p.INK,
                    bordercolor=p.BORDER, lightcolor=p.BORDER, darkcolor=p.BORDER,
                    padding=(9, 7))
    style.map("TEntry",
              bordercolor=[("focus", p.ACCENT)],
              lightcolor=[("focus", p.ACCENT)],
              darkcolor=[("focus", p.ACCENT)])
    style.configure("TCombobox", fieldbackground=p.SURFACE, background=p.SURFACE,
                    foreground=p.INK, arrowcolor=p.MUTED, bordercolor=p.BORDER,
                    lightcolor=p.BORDER, darkcolor=p.BORDER, padding=(7, 5))
    style.map("TCombobox",
              fieldbackground=[("readonly", p.SURFACE_ALT)],
              foreground=[("readonly", p.INK)],
              bordercolor=[("focus", p.ACCENT)],
              arrowcolor=[("active", p.ACCENT)])
    style.configure("TCheckbutton", background=p.SURFACE, foreground=p.INK,
                    indicatorbackground=p.SURFACE, indicatorforeground=p.ACCENT)
    style.map("TCheckbutton",
              background=[("active", p.SURFACE)],
              indicatorcolor=[("selected", p.ACCENT), ("active", p.ACCENT)],
              foreground=[("disabled", p.FAINT)])
    style.configure("Horizontal.TScale", background=p.SURFACE, troughcolor=p.BORDER,
                    bordercolor=p.SURFACE, lightcolor=p.ACCENT, darkcolor=p.ACCENT,
                    sliderlength=18, gripcount=0)
    style.configure("TSeparator", background=p.BORDER)

    # Tables: a little more breathing room improves scan speed.
    style.configure("Treeview", background=p.SURFACE, fieldbackground=p.SURFACE,
                    foreground=p.INK, borderwidth=0, relief="flat",
                    rowheight=36, font=UI_FONT)
    style.configure("Treeview.Heading", background=p.SURFACE_ALT, foreground=p.MUTED,
                    borderwidth=0, relief="flat", padding=(10, 10),
                    font=(FONT, 9, "bold"))
    style.map("Treeview.Heading", background=[("active", p.SURFACE_ALT)])
    style.map("Treeview", background=[("selected", p.SELECTION)],
              foreground=[("selected", p.INK)])

    # Scrolling and progress
    style.configure("Vertical.TScrollbar", background=p.BORDER, troughcolor=p.SURFACE,
                    bordercolor=p.SURFACE, arrowcolor=p.MUTED, width=11,
                    lightcolor=p.SURFACE, darkcolor=p.SURFACE)
    style.map("Vertical.TScrollbar",
              background=[("disabled", p.BORDER), ("pressed", p.MUTED),
                          ("active", p.MUTED)],
              troughcolor=[("disabled", p.SURFACE)],
              arrowcolor=[("disabled", p.MUTED)],
              lightcolor=[("disabled", p.SURFACE), ("active", p.SURFACE)],
              darkcolor=[("disabled", p.SURFACE), ("active", p.SURFACE)])
    style.configure("Horizontal.TScrollbar", background=p.BORDER, troughcolor=p.SURFACE,
                    bordercolor=p.SURFACE, arrowcolor=p.MUTED,
                    lightcolor=p.SURFACE, darkcolor=p.SURFACE)
    style.map("Horizontal.TScrollbar",
              background=[("disabled", p.BORDER), ("pressed", p.MUTED),
                          ("active", p.MUTED)],
              troughcolor=[("disabled", p.SURFACE)],
              arrowcolor=[("disabled", p.MUTED)],
              lightcolor=[("disabled", p.SURFACE), ("active", p.SURFACE)],
              darkcolor=[("disabled", p.SURFACE), ("active", p.SURFACE)])
    style.configure("Horizontal.TProgressbar", background=p.ACCENT,
                    troughcolor=p.SURFACE_ALT, bordercolor=p.SURFACE_ALT,
                    lightcolor=p.ACCENT, darkcolor=p.ACCENT, thickness=8)


def set_button_cursors(widget: tk.Misc, cursor: str = "hand2") -> None:
    """Give every button/combobox under *widget* a pointer cursor.

    ttk styles cannot set cursors, so walk the widget tree once after a
    window has been built. Safe to call again for late-created widgets.
    """
    try:
        children = widget.winfo_children()
    except tk.TclError:
        return
    for child in children:
        try:
            if child.winfo_class() in {"TButton", "TCombobox"}:
                child.configure(cursor=cursor)
        except tk.TclError:
            continue
        set_button_cursors(child, cursor)


_export(LIGHT)
