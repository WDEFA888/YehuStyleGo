"""Launch the packaged app and capture only its own Win32 window."""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

from PIL import Image


class ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
               ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
               ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
               ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
               ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]


def process_family(pid: int) -> set[int]:
    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise RuntimeError("Cannot verify packaged app process ownership")
    parents = {}
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.pid] = entry.parent
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    family = {pid}
    while True:
        expanded = family | {child for child, parent in parents.items() if parent in family}
        if expanded == family:
            return family
        family = expanded


class BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
        ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
        ("image_size", wintypes.DWORD), ("xppm", wintypes.LONG), ("yppm", wintypes.LONG),
        ("colors", wintypes.DWORD), ("important_colors", wintypes.DWORD),
    ]


def capture_window(hwnd: int, path: Path) -> None:
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BitmapHeader),
        wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    rectangle = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rectangle))
    width, height = rectangle.right, rectangle.bottom
    if width <= 0 or height <= 0:
        raise RuntimeError("Application client rectangle is empty")
    header = BitmapHeader()
    header.size = ctypes.sizeof(BitmapHeader)
    header.width, header.height = width, -height
    header.planes, header.bits = 1, 32
    pixels = ctypes.c_void_p()
    screen_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(header), 0,
                                     ctypes.byref(pixels), None, 0)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, 3):
            raise RuntimeError("PrintWindow failed")
        raw = ctypes.string_at(pixels, width * height * 4)
        Image.frombytes("RGB", (width, height), raw, "raw", "BGRX").save(path)
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, screen_dc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("screenshot", type=Path)
    parser.add_argument("--localappdata", type=Path,
                        help="Launch with a fresh LOCALAPPDATA to test first-run behavior")
    args = parser.parse_args()
    user32 = ctypes.windll.user32
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
    user32.ChildWindowFromPointEx.restype = wintypes.HWND
    environment = None
    if args.localappdata is not None:
        args.localappdata.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(args.localappdata.resolve())
    process = subprocess.Popen([str(args.executable.resolve())], env=environment)
    root = tk.Tk()
    root.withdraw()
    attempts = 0
    hwnd = None
    failure = []

    def callback_error(exc_type, error, traceback):
        failure.append(str(error))
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        root.destroy()
    root.report_callback_exception = callback_error

    def locate():
        nonlocal hwnd, attempts
        attempts += 1
        matches = []
        owned_pids = process_family(process.pid)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(window, param):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if owner.value not in owned_pids:
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(window, title, 256)
            if title.value == "影棋 · YehuStyleGo" and user32.IsWindowVisible(window):
                matches.append(window)
            return True
        user32.EnumWindows(callback_type(visit), 0)
        if matches:
            hwnd = matches[0]
            root.after(500, capture)
        elif attempts < 100 and process.poll() is None:
            root.after(100, locate)
        else:
            failure.append("Packaged app did not show its main window")
            root.destroy()

    def capture():
        capture_window(hwnd, args.screenshot)
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        root.after(500, root.destroy)

    root.after(100, locate)
    root.mainloop()
    if failure:
        if process.poll() is None:
            process.terminate()
        raise RuntimeError(failure[0])
    print(f"Packaged executable launched; its own main window was captured: {args.screenshot}")


if __name__ == "__main__":
    main()
