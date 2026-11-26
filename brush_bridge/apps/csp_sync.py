"""Windows helpers for synchronizing the standalone V-HSV color picker with CSP and Photoshop.

Clip Studio Paint (CSP) does not expose an internal plugin SDK for setting the
active foreground color.  However, CSP provides a built-in command
``applicationscreeneyecolorpicker`` ("获得屏幕颜色" / Pick screen color) whose
keyboard shortcut is stored in the user's CELSYS shortcut SQLite database
(``Shortcut/default.khc``).

When CSP is running on Windows, triggering that shortcut while CSP is active
arms CSP's screen eyedropper. Photoshop uses its built-in ``I`` eyedropper
tool. Both paths click the physical Win32 screen coordinate of our active
color swatch and leave focus inside the target application.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Optional

log = logging.getLogger(__name__)

# CSP modifier bitmask in Shortcut/default.khc:
# 1 = Alt, 2 = Shift, 4 = Ctrl
MOD_ALT = 1
MOD_SHIFT = 2
MOD_CTRL = 4

VK_MENU = 0x12     # Alt
VK_SHIFT = 0x10    # Shift
VK_CONTROL = 0x11  # Ctrl

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

SPECIAL_VK = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "SPACE": 0x20, "TAB": 0x09, "RETURN": 0x0D, "ENTER": 0x0D,
    "ESCAPE": 0x1B, "ESC": 0x1B, "BACKSPACE": 0x08, "DELETE": 0x2E,
    "INSERT": 0x2D, "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF, "-": 0xBD, "=": 0xBB, "`": 0xC0,
}


def default_khc_path() -> Optional[Path]:
    """Locate the user's CSP Shortcut/default.khc database if present."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    root = Path(appdata) / "CELSYSUserData" / "CELSYS"
    if not root.is_dir():
        return None
    for folder in sorted(root.glob("CLIPStudioPaintVer*"), reverse=True):
        candidate = folder / "Shortcut" / "default.khc"
        if candidate.is_file():
            return candidate
    return None


def read_csp_screen_picker_shortcut(khc_path: Optional[Path] = None) -> Optional[tuple[str, int]]:
    """Read the user's configured shortcut for ``applicationscreeneyecolorpicker``.

    Returns ``(key_str, modifier_mask)`` such as ``("F", 4)`` for ``Ctrl+F``,
    or ``None`` if no shortcut is assigned or the file cannot be read.
    """
    path = khc_path or default_khc_path()
    if path is None or not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as con:
            rows = con.execute(
                "SELECT shortcut, modifier FROM shortcutmenu "
                "WHERE menucommand IN ('applicationscreeneyecolorpicker', "
                "'applicationscreeneyecolorpickerwithminimize') "
                "ORDER BY CASE menucommand WHEN 'applicationscreeneyecolorpicker' THEN 0 ELSE 1 END"
            ).fetchall()
        for shortcut, modifier in rows:
            if shortcut and str(shortcut).upper() != "NULL":
                return str(shortcut).strip().upper(), int(modifier or 0)
    except Exception as exc:
        log.debug("failed to read CSP shortcut database %s: %s", path, exc)
    return None


def format_shortcut(shortcut: Optional[tuple[str, int]]) -> str:
    """Format a ``(key, modifier)`` tuple into human-readable form like ``Ctrl+F``."""
    if not shortcut:
        return "未设置"
    key, mod = shortcut
    parts = []
    if mod & MOD_CTRL:
        parts.append("Ctrl")
    if mod & MOD_SHIFT:
        parts.append("Shift")
    if mod & MOD_ALT:
        parts.append("Alt")
    parts.append(key.upper())
    return "+".join(parts)


def parse_shortcut_text(text: str) -> Optional[tuple[str, int]]:
    """Parse a user-edited shortcut string like ``Ctrl+F`` or ``Ctrl+Shift+X``."""
    cleaned = text.strip()
    if not cleaned or cleaned in ("未设置", "无", "None"):
        return None
    tokens = [t.strip().upper() for t in cleaned.replace("＋", "+").split("+") if t.strip()]
    if not tokens:
        return None
    mod = 0
    key = None
    for tok in tokens:
        if tok in ("CTRL", "CONTROL"):
            mod |= MOD_CTRL
        elif tok == "SHIFT":
            mod |= MOD_SHIFT
        elif tok == "ALT":
            mod |= MOD_ALT
        else:
            key = tok
    if not key:
        return None
    return key, mod


def _key_to_vk(key: str) -> Optional[int]:
    k = key.strip().upper()
    if k in SPECIAL_VK:
        return SPECIAL_VK[k]
    if len(k) == 1 and ("A" <= k <= "Z" or "0" <= k <= "9"):
        return ord(k)
    return None


def client_to_physical_screen(
    hwnd: int,
    logical_x: float,
    logical_y: float,
    dpr: float = 1.0,
) -> tuple[int, int]:
    """Convert a Qt client-area logical point into physical Win32 screen pixels.

    On HiDPI Windows systems (e.g., 125% / 150% scaling), Qt6 returns logical
    DIPs from ``QWidget.mapToGlobal()`` while Win32 ``SetCursorPos()`` expects
    physical pixels.  Converting the DPR-scaled client offset through Win32
    ``ClientToScreen`` produces the exact physical pixel on any DPI or
    multi-monitor arrangement.
    """
    if sys.platform != "win32" or not hwnd:
        return round(logical_x * dpr), round(logical_y * dpr)
    user32 = ctypes.windll.user32
    pt = wintypes.POINT(round(logical_x * dpr), round(logical_y * dpr))
    if user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
        return int(pt.x), int(pt.y)
    return round(logical_x * dpr), round(logical_y * dpr)


def read_screen_pixel(x: int, y: int) -> Optional[tuple[int, int, int]]:
    """Read the physical screen RGB pixel at ``(x, y)`` via Win32 GDI."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetDC(0)
    if not hdc:
        return None
    try:
        colorref = gdi32.GetPixel(hdc, int(x), int(y))
        if colorref == 0xFFFFFFFF:
            return None
        return colorref & 0xFF, (colorref >> 8) & 0xFF, (colorref >> 16) & 0xFF
    finally:
        user32.ReleaseDC(0, hdc)


def find_csp_window(exclude_hwnd: int = 0) -> Optional[int]:
    """Return the top-level HWND of a running Clip Studio Paint window on Windows."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if hwnd == exclude_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if "CLIP STUDIO PAINT" in title.upper():
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else None


def find_photoshop_window(exclude_hwnd: int = 0) -> Optional[int]:
    """Return the top-level HWND of a running Photoshop window on Windows."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if hwnd == exclude_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.upper()
        if "PHOTOSHOP" in title:
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else None


def return_focus_to_csp(exclude_hwnd: int = 0) -> bool:
    """Bring the running CSP window to foreground without clicking."""
    if sys.platform != "win32":
        return False
    hwnd = find_csp_window(exclude_hwnd=exclude_hwnd)
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    return bool(user32.SetForegroundWindow(hwnd))


def sync_color_to_csp(
    swatch_x: int,
    swatch_y: int,
    shortcut: Optional[tuple[str, int]] = None,
    exclude_hwnd: int = 0,
    expected_rgb: Optional[tuple[int, int, int]] = None,
    freeze_seconds: float = 0.6,
) -> tuple[bool, str]:
    """Trigger CSP's screen eyedropper shortcut and sample ``(swatch_x, swatch_y)``.

    Returns ``(ok, status_message)``.
    """
    if sys.platform != "win32":
        return False, "当前系统不支持 Win32 联动（已复制 HEX）"

    hwnd = find_csp_window(exclude_hwnd=exclude_hwnd)
    if not hwnd:
        return False, "未检测到运行中的 CSP（已复制 HEX）"

    sc = shortcut or read_csp_screen_picker_shortcut()
    if not sc:
        return False, "未检测到 CSP「获得屏幕颜色」快捷键（已复制 HEX）"

    vk = _key_to_vk(sc[0])
    if vk is None:
        return False, f"不支持的快捷键 {format_shortcut(sc)}（已复制 HEX）"

    # Optional sanity check on the screen pixel before firing the eyedropper:
    # if DPI/coordinate rounding landed slightly off the swatch, try a tiny
    # local search around (swatch_x, swatch_y) to lock onto the exact swatch.
    target_x, target_y = int(swatch_x), int(swatch_y)
    if expected_rgb is not None:
        got = read_screen_pixel(target_x, target_y)
        if got is not None and max(abs(a - b) for a, b in zip(got, expected_rgb)) > 6:
            for dy in (-12, -6, 6, 12):
                for dx in (-12, -6, 6, 12):
                    probe = read_screen_pixel(target_x + dx, target_y + dy)
                    if probe is not None and max(abs(a - b) for a, b in zip(probe, expected_rgb)) <= 4:
                        target_x += dx
                        target_y += dy
                        break
                else:
                    continue
                break

    return _sync_screen_picker(
        hwnd, target_x, target_y, shortcut=sc, expected_rgb=expected_rgb,
        freeze_seconds=freeze_seconds, restore_key=None, label="CSP",
    )


def sync_color_to_photoshop(
    swatch_x: int,
    swatch_y: int,
    exclude_hwnd: int = 0,
    expected_rgb: Optional[tuple[int, int, int]] = None,
    freeze_seconds: float = 0.6,
) -> tuple[bool, str]:
    """Use Photoshop's ``I`` eyedropper and click the active color swatch."""
    if sys.platform != "win32":
        return False, "当前系统不支持 Win32 联动（已复制 HEX）"
    hwnd = find_photoshop_window(exclude_hwnd=exclude_hwnd)
    if not hwnd:
        return False, "未检测到运行中的 Photoshop（已复制 HEX）"
    return _sync_screen_picker(
        hwnd, int(swatch_x), int(swatch_y), shortcut=("I", 0), expected_rgb=expected_rgb,
        freeze_seconds=freeze_seconds, restore_key="B", label="Photoshop",
    )


def _sync_screen_picker(
    hwnd: int,
    target_x: int,
    target_y: int,
    *,
    shortcut: tuple[str, int],
    expected_rgb: Optional[tuple[int, int, int]],
    freeze_seconds: float,
    restore_key: Optional[str],
    label: str,
) -> tuple[bool, str]:
    user32 = ctypes.windll.user32
    vk = _key_to_vk(shortcut[0])
    if vk is None:
        return False, f"不支持的快捷键 {format_shortcut(shortcut)}（已复制 HEX）"

    # Save current cursor and clipping state so the artist's pen/mouse returns
    # seamlessly, even when another tablet event arrives during the transfer.
    orig_pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(orig_pt))
    old_clip = wintypes.RECT()
    had_clip = bool(user32.GetClipCursor(ctypes.byref(old_clip)))

    user32.ReleaseCapture()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE

    # Bring CSP to foreground so it receives the screen eyedropper shortcut.
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.03)

    mod = shortcut[1]
    mods_down = []
    if mod & MOD_CTRL:
        mods_down.append(VK_CONTROL)
    if mod & MOD_SHIFT:
        mods_down.append(VK_SHIFT)
    if mod & MOD_ALT:
        mods_down.append(VK_MENU)

    for m_vk in mods_down:
        user32.keybd_event(m_vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.012)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    for m_vk in reversed(mods_down):
        user32.keybd_event(m_vk, 0, KEYEVENTF_KEYUP, 0)

    # Wait briefly for the target to enter screen eyedropper mode, then move to
    # the physical swatch pixel.  ClipCursor holds a pen-generated cursor at
    # the exact pixel while the synthetic click is delivered.
    try:
        time.sleep(0.045)
        user32.SetCursorPos(target_x, target_y)
        freeze_seconds = max(0.0, min(3.0, float(freeze_seconds)))
        if freeze_seconds:
            lock = wintypes.RECT(target_x, target_y, target_x + 1, target_y + 1)
            user32.ClipCursor(ctypes.byref(lock))
        time.sleep(0.015)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.012)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(freeze_seconds)
        if restore_key:
            restore_vk = _key_to_vk(restore_key)
            if restore_vk is not None:
                user32.keybd_event(restore_vk, 0, 0, 0)
                user32.keybd_event(restore_vk, 0, KEYEVENTF_KEYUP, 0)
    finally:
        if had_clip:
            user32.ClipCursor(ctypes.byref(old_clip))
        else:
            user32.ClipCursor(None)
        user32.SetCursorPos(orig_pt.x, orig_pt.y)
    return True, f"已同步到 {label}（冻结 {freeze_seconds:.1f} 秒）"
