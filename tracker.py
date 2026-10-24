"""Find the League client window and place the panel beside (or over) it. Windows only."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass

user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user32.GetAncestor.argtypes = [w.HWND, w.UINT]
user32.GetAncestor.restype = w.HWND
user32.GetForegroundWindow.restype = w.HWND
user32.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
user32.MonitorFromWindow.restype = w.HMONITOR
user32.GetMonitorInfoW.argtypes = [w.HMONITOR, ctypes.c_void_p]
user32.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]
user32.SetLayeredWindowAttributes.argtypes = [w.HWND, w.COLORREF, w.BYTE, w.DWORD]
user32.WindowFromPoint.argtypes = [w.POINT]
user32.WindowFromPoint.restype = w.HWND

CLIENT_CLASS = "RCLIENT"
CLIENT_TITLE = "League of Legends"
GAME_CLASS = "RiotWindowClass"  # the in-game window, "League of Legends (TM) Client"
PANEL_BASE = (300, 1080)  # panel width designed for a 1080px-tall client
MIN_WIDTH = 240
HWND_TOPMOST = w.HWND(-1)
SWP_NOACTIVATE = 0x0010
GA_ROOTOWNER = 3
MONITOR_DEFAULTTONEAREST = 2


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class Placement:
    mode: str  # "dock-right" | "dock-left" | "overlay"
    x: int
    y: int
    width: int
    height: int
    scale: float


def make_dpi_aware() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
    except (AttributeError, OSError):
        pass


def find_client() -> int:
    return user32.FindWindowW(CLIENT_CLASS, CLIENT_TITLE) or 0  # Riot Client shares the class


def game_running() -> bool:
    return bool(user32.FindWindowW(GAME_CLASS, None))


def find_window(title: str) -> int:
    return user32.FindWindowW(None, title) or 0


def window_rect(hwnd: int) -> Rect:
    r = w.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return Rect(r.left, r.top, r.right, r.bottom)


def work_area(hwnd: int) -> Rect:
    mi = _MonitorInfo()
    mi.cbSize = ctypes.sizeof(_MonitorInfo)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(mi))
    a = mi.rcWork
    return Rect(a.left, a.top, a.right, a.bottom)


def primary_work_area() -> Rect:
    r = w.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return Rect(r.left, r.top, r.right, r.bottom)


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd)) or not user32.IsWindowVisible(hwnd)


def foreground_root() -> int:
    fg = user32.GetForegroundWindow()
    return (user32.GetAncestor(fg, GA_ROOTOWNER) or fg) if fg else 0


def place(client: Rect, area: Rect) -> Placement:
    """Dock right if there's room, else dock left, else overlay the client's right side."""
    scale = client.height / PANEL_BASE[1]
    width = max(MIN_WIDTH, round(PANEL_BASE[0] * scale))
    scale = width / PANEL_BASE[0]
    if area.right - client.right >= width:
        return Placement("dock-right", client.right, client.top, width, client.height, scale)
    if client.left - area.left >= width:
        return Placement("dock-left", client.left - width, client.top, width, client.height, scale)
    # no room outside: sit over the enemy team column, below the client's top bar
    top = client.top + round(client.height * 0.12)
    return Placement("overlay", client.right - width - round(8 * scale), top, width, round(client.height * 0.7), scale)


def standalone(area: Rect) -> Placement:
    """Placement with no client (replay/demo): right edge of the primary monitor."""
    height = min(area.height, 1080)
    width = max(MIN_WIDTH, round(PANEL_BASE[0] * height / PANEL_BASE[1]))
    return Placement("dock-right", area.right - width, area.top, width, height, width / PANEL_BASE[0])


def move(hwnd: int, p: Placement) -> None:
    user32.SetWindowPos(hwnd, HWND_TOPMOST, p.x, p.y, p.width, p.height, SWP_NOACTIVATE)


INGAME_BASE = 250  # in-game panel width at 1080p: narrower, it sits over the game
GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE = 0x00080000, 0x00000020, 0x08000000
LWA_ALPHA = 0x2


def find_game() -> int:
    return user32.FindWindowW(GAME_CLASS, None) or 0


def place_ingame(game: Rect) -> Placement:
    """Right edge of the game, below the top bar and above the minimap (bottom right)."""
    scale = game.height / PANEL_BASE[1]
    width = max(200, round(INGAME_BASE * scale))
    top = game.top + round(game.height * 0.13)
    height = round(game.height * 0.56)  # the minimap starts around 72% down
    return Placement("ingame", game.right - width - round(4 * scale), top, width, height, width / PANEL_BASE[0])


def set_click_through(hwnd: int, on: bool) -> None:
    """Let clicks pass through the panel to the game (and never take focus from it)."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    bits = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    new = (style | bits) if on else (style & ~bits)
    if new != style:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new)
        if on:  # a layered window draws nothing until given an alpha: keep it fully opaque
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
