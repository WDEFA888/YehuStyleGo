"""Guarded, local-only SGF export through FoxWQ's historical record UI.

No credentials, network protocol hooks, client memory access, or global mouse input.
The custom Save button is checked against a previously verified visual template.
"""
from __future__ import annotations

import base64
import ctypes
import os
import re
import threading
import time
import zlib
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from yehu_style_go.sgf import parse_sgf_file

_RECORD_ID = re.compile(r"(\d{15,24})\.sgf$", re.IGNORECASE)
_MOVE_COUNT = re.compile(r"总\s*(\d+)\s*手")
_SAVE_TEMPLATE = (
    "eNq1lMtqwkAUht9Ek2gSr425eoG+RRfSQqFLF13opuqilpYKbemmD+lz9IOBdMzRiRTmIwxnhjP/7xln"
    "Tp7nWZaNRqMrO6CM/ng8nk6naZpGUbR4fHrZ/9j4UMauKIrJZILR/PZ+//H9ZQeU0aciSuv3+2/vn3ub"
    "oB/HMV6dTud597oTHA6H3WXUZqLP6eV57vv+erNdC1BQo876FOfW/9hsh8MhXq1Wa7lcLY9heyWQsZlj"
    "vdVgMOA2uq67EJBcCSqxeVHS6/W4Hng9HKN+mB6XyMxL0gCvJEnwutMgsxwrixK5fi6z2+1yFfG6EbBF"
    "jRI9R5+Wu06ivBzHuRawyzAtV8pRUsnnZdE9ms3mTECyHivMaXKqE4YhT6zRaBhEVKCP//MKgoA+bPCq"
    "ONaK13rVnqFBRB61wYvWQV10+5lN0KcT0uSpK8sLq15ZVuDFc/Y8L4piq17ocw+pi97bbgdxkloyQr"
    "ntYxVSF/8ado7ju15o40MZfd4yrQNHDpOpaweU0Vdev9MPZqw="
)


class FoxSyncError(RuntimeError):
    pass


MAX_SYNC_GAMES = 1000


@dataclass(frozen=True)
class SyncResult:
    paths: list[Path]
    exported: int
    existing: int
    available_rows: int
    skipped: int = 0


def record_id_from_filename(filename: str) -> str:
    match = _RECORD_ID.search(filename)
    if not match:
        raise FoxSyncError("保存窗口没有可验证的棋谱编号；已停止，未点击保存")
    return match.group(1)


def validate_export(path: Path, player: str, expected_moves: int | None = None):
    game = parse_sgf_file(path)
    if game.color_for(player) is None:
        raise FoxSyncError("导出棋谱不包含本人账号，未导入或用于训练")
    placements = len(game.moves) + len(game.setup_black) + len(game.setup_white)
    if expected_moves is not None and expected_moves not in {len(game.moves), placements}:
        raise FoxSyncError("导出手数与历史棋谱窗口不符，可能尚未完整载入")
    return game


def matches_save_template(rgb: bytes) -> bool:
    reference = zlib.decompress(base64.b64decode(_SAVE_TEMPLATE))
    if len(rgb) != len(reference):
        return False
    difference = sum(abs(a - b) for a, b in zip(rgb, reference)) / len(reference)
    bright = [index for index in range(0, len(reference), 3)
              if min(reference[index:index + 3]) >= 180]
    if not bright:
        return False
    glyph_match = sum(min(rgb[index:index + 3]) >= 160 for index in bright) / len(bright)
    return difference <= 6 and glyph_match >= 0.95


class _BitmapHeader(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
               ("planes", wintypes.WORD), ("bits", wintypes.WORD),
               ("compression", wintypes.DWORD), ("image_size", wintypes.DWORD),
               ("xppm", wintypes.LONG), ("yppm", wintypes.LONG),
               ("colors", wintypes.DWORD), ("important", wintypes.DWORD)]


class FoxExporter:
    def __init__(self, player: str, destination: Path, cancel: threading.Event | None = None):
        if os.name != "nt":
            raise FoxSyncError("野狐客户端同步目前只支持 Windows")
        self.player = player
        self.destination = destination.resolve()
        self.cancel = cancel or threading.Event()
        self.user = ctypes.windll.user32
        self.kernel = ctypes.windll.kernel32
        self.gdi = ctypes.windll.gdi32
        self.pid = 0
        self._declare_apis()

    def _declare_apis(self):
        u, k, g = self.user, self.kernel, self.gdi
        u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
            wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = wintypes.HWND
        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowEnabled.argtypes = [wintypes.HWND]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u.GetDC.argtypes = [wintypes.HWND]
        u.GetDC.restype = wintypes.HDC
        u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BitmapHeader), wintypes.UINT,
                                      ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        g.SelectObject.restype = wintypes.HANDLE
        g.DeleteObject.argtypes = [wintypes.HANDLE]
        g.DeleteDC.argtypes = [wintypes.HDC]

    def _pid_of(self, hwnd):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _class(self, hwnd):
        value = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, value, len(value))
        return value.value

    def _caption(self, hwnd):
        value = ctypes.create_unicode_buffer(2048)
        self.user.GetWindowTextW(hwnd, value, len(value))
        return value.value

    def _windows(self):
        windows = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, param):
            if self.user.IsWindowVisible(hwnd) and (not self.pid or self._pid_of(hwnd) == self.pid):
                windows.append(hwnd)
            return True
        self.user.EnumWindows(callback_type(visit), 0)
        return windows

    def _children(self, parent, class_name=None):
        children = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, param):
            if self.user.IsWindowVisible(hwnd) and (class_name is None or self._class(hwnd) == class_name):
                children.append(hwnd)
            return True
        self.user.EnumChildWindows(parent, callback_type(visit), 0)
        return children

    def _verify_target(self, hwnd):
        if self.cancel.is_set():
            raise FoxSyncError("同步已取消；已导出的棋谱会保留")
        if not self.user.IsWindow(hwnd) or self._pid_of(hwnd) != self.pid:
            raise FoxSyncError("野狐窗口已变化，已停止发送操作")

    def _message(self, hwnd, kind, wparam=0, lparam=0):
        self._verify_target(hwnd)
        result = ctypes.c_size_t()
        if not self.user.SendMessageTimeoutW(hwnd, kind, wparam, lparam, 2, 5000,
                                            ctypes.byref(result)):
            raise FoxSyncError("野狐界面无响应；已停止同步")
        return result.value

    def _post(self, hwnd, kind, wparam=0, lparam=0):
        self._verify_target(hwnd)
        if not self.user.PostMessageW(hwnd, kind, wparam, lparam):
            raise FoxSyncError("无法向已验证的棋谱窗口发送操作")

    def _text(self, hwnd):
        length = min(self._message(hwnd, 0x000E), 8192)
        value = ctypes.create_unicode_buffer(length + 1)
        self._message(hwnd, 0x000D, len(value), ctypes.addressof(value))
        return value.value

    def _wait(self, predicate, description, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            if self.cancel.is_set():
                raise FoxSyncError("同步已取消；已导出的棋谱会保留")
            result = predicate()
            if result:
                return result
            if time.monotonic() >= deadline:
                raise FoxSyncError(f"等待{description}超时。请检查野狐弹窗后重试；未继续点击。")
            self.cancel.wait(0.12)

    def _is_fox_process(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            value = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(value))
            ok = self.kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size))
            return bool(ok) and Path(value.value).name.lower() == "foxwq.exe"
        finally:
            self.kernel.CloseHandle(handle)

    def _history_window(self):
        found = [hwnd for hwnd in self._windows()
                 if self._caption(hwnd) == "棋谱欣赏" and self._is_fox_process(self._pid_of(hwnd))]
        if len(found) != 1:
            raise FoxSyncError("请在野狐打开“棋谱欣赏 → 我的棋谱”并恢复窗口；需有且仅有一个历史列表")
        self.pid = self._pid_of(found[0])
        return found[0]

    def _record_windows(self):
        return [hwnd for hwnd in self._windows() if self._caption(hwnd).startswith("棋谱欣赏 -")]

    def _click(self, hwnd, x, y, double=False):
        rect = wintypes.RECT()
        self.user.GetClientRect(hwnd, ctypes.byref(rect))
        if not (0 <= x < rect.right and 0 <= y < rect.bottom):
            raise FoxSyncError("棋谱控件布局改变，未点击")
        point = (y << 16) | (x & 0xFFFF)
        for kind, state in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
            self._post(hwnd, kind, state, point)
        if double:
            self._post(hwnd, 0x0203, 1, point)
            self._post(hwnd, 0x0202, 0, point)

    def _open_row(self, list_view, index):
        self._message(list_view, 0x1013, index, 0)  # LVM_ENSUREVISIBLE; no remote pointers.
        top = self._message(list_view, 0x1027)
        spacing = self._message(list_view, 0x1033, 1) >> 16
        if not 10 <= spacing <= 64:
            raise FoxSyncError("无法可靠确定历史列表行高")
        headers = self._children(list_view, "SysHeader32")
        if len(headers) != 1:
            raise FoxSyncError("历史列表表头已变化")
        origin = wintypes.POINT(0, 0)
        self.user.ClientToScreen(list_view, ctypes.byref(origin))
        header = wintypes.RECT()
        self.user.GetWindowRect(headers[0], ctypes.byref(header))
        y = header.bottom - origin.y + (index - top) * spacing + spacing // 2
        self._click(list_view, 70, y)
        self._wait(lambda: self._message(list_view, 0x100C, ctypes.c_size_t(-1).value, 2) == index,
                   "历史列表选中目标行", timeout=5)
        self._post(list_view, 0x0203, 1, (y << 16) | 70)
        self._post(list_view, 0x0202, 0, (y << 16) | 70)
        def ready():
            records = self._record_windows()
            if len(records) > 1:
                raise FoxSyncError("出现多个历史棋盘，已停止以免操作错误窗口")
            if records and _MOVE_COUNT.search(self._caption(records[0])):
                return records[0]
            return None
        record = self._wait(ready, "历史棋谱载入")
        return record

    def _crop_rgb(self, hwnd, x, y, width, height):
        u, g = self.user, self.gdi
        rect = wintypes.RECT()
        u.GetClientRect(hwnd, ctypes.byref(rect))
        if x < 0 or y < 0 or x + width > rect.right or y + height > rect.bottom:
            raise FoxSyncError("保存按钮模板超出窗口范围")
        header = _BitmapHeader()
        header.size = ctypes.sizeof(header)
        header.width, header.height, header.planes, header.bits = rect.right, -rect.bottom, 1, 32
        pointer = ctypes.c_void_p()
        screen = u.GetDC(hwnd)
        memory = g.CreateCompatibleDC(screen)
        bitmap = g.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(pointer), None, 0)
        if not bitmap:
            g.DeleteDC(memory)
            u.ReleaseDC(hwnd, screen)
            raise FoxSyncError("无法检查保存按钮外观")
        old = g.SelectObject(memory, bitmap)
        try:
            if not u.PrintWindow(hwnd, memory, 3):
                raise FoxSyncError("无法读取保存按钮外观")
            rgb = bytearray()
            for row in range(y, y + height):
                raw = ctypes.string_at(pointer.value + (row * rect.right + x) * 4, width * 4)
                for index in range(0, len(raw), 4):
                    rgb.extend((raw[index + 2], raw[index + 1], raw[index]))
            return bytes(rgb)
        finally:
            g.SelectObject(memory, old)
            g.DeleteObject(bitmap)
            g.DeleteDC(memory)
            u.ReleaseDC(hwnd, screen)

    def _open_save(self, record):
        edits = self._children(record, "Edit")
        if len(edits) != 1:
            raise FoxSyncError("历史棋谱工具栏已变化，未点击任何按钮")
        edit = edits[0]
        panel = self.user.GetParent(edit)
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        self.user.GetWindowRect(edit, ctypes.byref(rect))
        self.user.ClientToScreen(panel, ctypes.byref(origin))
        x, y = rect.right - origin.x + 110, rect.top - origin.y - 4
        rgb = self._crop_rgb(panel, x, y, 36, 22)
        if not matches_save_template(rgb):
            raise FoxSyncError("保存按钮外观与已验证版本不一致，未点击（请检查主题、缩放或客户端更新）")
        self._click(panel, x + 18, y + 11)
        return self._wait(lambda: next((hwnd for hwnd in self._windows()
                                       if self._caption(hwnd) == "另存为"), None), "另存为窗口")

    def _save_controls(self, dialog):
        entries = []
        for edit in self._children(dialog, "Edit"):
            value = self._text(edit)
            if _RECORD_ID.search(value):
                entries.append((edit, value))
        if len(entries) != 1:
            raise FoxSyncError("无法唯一识别 SGF 文件名，未保存")
        buttons = self._children(dialog, "Button")
        save = [hwnd for hwnd in buttons if self._caption(hwnd).startswith("保存")]
        cancel = [hwnd for hwnd in buttons if self._caption(hwnd) == "取消"]
        if len(save) != 1 or len(cancel) != 1:
            raise FoxSyncError("保存对话框按钮已变化，未保存")
        return entries[0][0], entries[0][1], save[0], cancel[0]

    def sync(self, limit=100, progress: Callable[[int, int], None] | None = None) -> SyncResult:
        if not 1 <= limit <= MAX_SYNC_GAMES:
            raise ValueError(f"单次同步应为 1 到 {MAX_SYNC_GAMES} 盘")
        history = self._history_window()
        if self._record_windows() or any(self._caption(hwnd) == "另存为" for hwnd in self._windows()):
            raise FoxSyncError("请先关闭已打开的历史复盘棋盘或保存对话框，保留“我的棋谱”列表")
        lists = self._children(history, "SysListView32")
        if len(lists) != 1:
            raise FoxSyncError("无法唯一识别历史棋谱列表")
        list_view = lists[0]
        available = self._message(list_view, 0x1004)
        count = min(available, limit)
        if not count:
            raise FoxSyncError("历史列表没有棋谱，请先加载“我的棋谱”")
        self.destination.mkdir(parents=True, exist_ok=True)
        paths, seen = [], set()
        exported = existing = skipped = 0
        skipped_titles: list[str] = []
        for index in range(count):
            self._verify_target(history)
            record = self._open_row(list_view, index)
            title = self._caption(record)
            if self.player not in title:
                skipped += 1
                skipped_titles.append(title)
                self._post(record, 0x0010)
                self._wait(lambda: not self.user.IsWindow(record), "跳过不匹配棋谱")
                if progress:
                    progress(index + 1, count)
                continue
            expected_moves = int(_MOVE_COUNT.search(title).group(1))
            dialog = self._open_save(record)
            edit, filename, save, cancel = self._save_controls(dialog)
            record_id = record_id_from_filename(filename)
            if record_id in seen:
                raise FoxSyncError("历史列表重复打开了同一棋谱，已停止")
            seen.add(record_id)
            folder = self.destination / record_id
            destination = folder / "record.sgf"
            if folder.is_symlink():
                raise FoxSyncError("棋谱目录是符号链接，未保存或读取")
            if destination.exists():
                validate_export(destination, self.player, expected_moves)
                self._message(cancel, 0x00F5)
                existing += 1
            else:
                if folder.exists():
                    if not folder.is_dir() or any(folder.iterdir()):
                        raise FoxSyncError("目标棋谱目录已有非预期内容，未覆盖")
                else:
                    folder.mkdir(exist_ok=False)
                value = ctypes.create_unicode_buffer(str(destination))
                self._message(edit, 0x000C, 0, ctypes.addressof(value))
                if self._text(edit) != str(destination):
                    raise FoxSyncError("目标文件名验证失败，未点击保存")
                self._message(save, 0x00F5)
                def saved():
                    if not destination.is_file() or destination.stat().st_size == 0:
                        return False
                    try:
                        validate_export(destination, self.player, expected_moves)
                    except (OSError, ValueError):
                        return False
                    return True
                self._wait(saved, "完整 SGF 保存")
                exported += 1
            self._wait(lambda: not self.user.IsWindow(dialog), "保存窗口关闭")
            paths.append(destination)
            self._post(record, 0x0010)
            self._wait(lambda: not self.user.IsWindow(record), "历史棋盘关闭")
            if progress:
                progress(index + 1, count)
            self.cancel.wait(0.15)
        if not paths and skipped:
            examples = "\n".join(skipped_titles[:5])
            raise FoxSyncError(
                f"没有同步到包含当前账号“{self.player}”的棋谱，已跳过 {skipped} 盘。\n"
                "请确认“设置”中的野狐用户名与当前登录账号完全一致，然后重试。\n"
                f"跳过的窗口标题示例：\n{examples}"
            )
        return SyncResult(paths, exported, existing, available, skipped)
