"""Find installed / running Half-Life and Portal games, and their windows.

All Win32 access is explicit ``ctypes`` with ``argtypes`` / ``restype`` where it
matters.  Two Windows details are easy to get wrong and are handled here:

* ``int(ctypes.c_void_p)`` is not portable across Python versions (3.12 returns
  the raw bytes), so every handle goes through :func:`as_hwnd`.
* A minimised game is parked near ``(-32000, -32000)`` with an icon-sized
  rectangle.  Cropping to that rectangle yields a 1x1 "bright" image, so such a
  window must be rejected explicitly rather than trusted because it is non-black.

Steam discovery is read-only: registry -> ``libraryfolders.vdf`` ->
``appmanifest_<id>.acf``.  Nothing here writes to the game or to Steam.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import catalog

__all__ = [
    "RunningGame",
    "WindowInfo",
    "active_game",
    "as_hwnd",
    "client_rect_on_screen",
    "cursor_visible",
    "enable_dpi_awareness",
    "find_game_window",
    "find_running_games",
    "focus_window",
    "foreground_window",
    "installed_games",
    "is_foreground",
    "list_processes",
    "menu_active",
    "process_image_path",
    "steam_libraries",
    "steam_root",
]

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
else:  # pragma: no cover - the plugin is Windows-only in practice
    _user32 = None
    _kernel32 = None
    _advapi32 = None


# ---------------------------------------------------------------------------
# Handles / DPI
# ---------------------------------------------------------------------------


def as_hwnd(value: Any) -> int:
    """Coerce a ctypes handle, bytes or int to a plain Python ``int``."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, bytes):
        return int.from_bytes(value[:8], "little", signed=False)
    raw = getattr(value, "value", None)
    if isinstance(raw, int):
        return raw
    try:
        return int(ctypes.cast(value, ctypes.c_void_p).value or 0)
    except Exception:
        return 0


_DPI_SET = False


def enable_dpi_awareness() -> bool:
    """Best-effort per-monitor DPI awareness for accurate window rectangles."""
    global _DPI_SET
    if _DPI_SET or not _IS_WINDOWS:
        return _DPI_SET
    try:
        # Windows 10 1703+: per-monitor v2.
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _DPI_SET = True
            return True
    except Exception:
        pass
    try:
        if _user32.SetProcessDPIAware():
            _DPI_SET = True
    except Exception:
        _DPI_SET = False
    return _DPI_SET


# ---------------------------------------------------------------------------
# Process enumeration
# ---------------------------------------------------------------------------

TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


def list_processes() -> list[tuple[int, str]]:
    """Return ``[(pid, exe_name), ...]`` for every running process."""
    if not _IS_WINDOWS:
        return []
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return []
    entries: list[tuple[int, str]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            entries.append((int(entry.th32ProcessID), str(entry.szExeFile)))
            ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return entries


def process_image_path(pid: int) -> str:
    """Full image path for ``pid`` (empty string when not readable)."""
    if not _IS_WINDOWS:
        return ""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return str(buffer.value)
        return ""
    finally:
        _kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# Steam libraries / installed manifests
# ---------------------------------------------------------------------------


def _registry_string(root, subkey: str, name: str) -> str:
    try:
        import winreg  # noqa: PLC0415 - Windows-only, imported lazily

        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except Exception:
        return ""


def steam_root() -> Path | None:
    """Locate the Steam installation, preferring the registry."""
    if _IS_WINDOWS:
        candidates = [
            _registry_string(0x80000001, r"Software\Valve\Steam", "SteamPath"),
            _registry_string(0x80000002, r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
            _registry_string(0x80000001, r"Software\Valve\Steam", "InstallPath"),
            os.environ.get("STEAM_PATH", ""),
            r"C:\Program Files (x86)\Steam",
            r"D:\Steam",
            r"D:\steam",
        ]
    else:  # pragma: no cover
        candidates = []
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        if (path / "steamapps").is_dir():
            return path
        if path.name.lower() == "steamapps" and path.is_dir():
            return path.parent
    return None


def _vdf_paths(text: str) -> list[Path]:
    return [Path(match) for match in re.findall(r'"path"\s*"([^"]+)"', text)]


def steam_libraries() -> list[Path]:
    """Every Steam library root (the install itself plus ``libraryfolders.vdf``)."""
    root = steam_root()
    if root is None:
        return []
    libraries: list[Path] = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for path in _vdf_paths(text):
        if path.is_dir() and path not in libraries:
            libraries.append(path)
    # De-duplicate case-insensitively on Windows.
    seen: set[str] = set()
    result: list[Path] = []
    for path in libraries:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


@dataclass(frozen=True)
class Installed:
    profile: catalog.GameProfile
    app_id: str
    library: Path
    install_dir: Path
    manifest: Path

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.profile.id,
            "name": self.profile.name_zh,
            "app_id": self.app_id,
            "install_dir": str(self.install_dir),
        }


def installed_games(*, refresh: bool = False) -> dict[str, Installed]:
    """Map profile id -> installed manifest details.

    Results are cached for 60 seconds because the autopilot asks often and a
    full library scan touches several directories.
    """
    global _INSTALLED_CACHE, _INSTALLED_AT
    now = time.time()
    if not refresh and _INSTALLED_CACHE is not None and (now - _INSTALLED_AT) < 60.0:
        return dict(_INSTALLED_CACHE)

    found: dict[str, Installed] = {}
    libraries = steam_libraries()
    by_app: dict[str, catalog.GameProfile] = {
        game.app_id: game for game in catalog.GAMES if game.playable or game.vr_only
    }
    for library in libraries:
        steamapps = library / "steamapps"
        if not steamapps.is_dir():
            continue
        for manifest in steamapps.glob("appmanifest_*.acf"):
            match = re.search(r"appmanifest_(\d+)\.acf$", manifest.name, re.I)
            if not match:
                continue
            app_id = match.group(1)
            profile = by_app.get(app_id)
            if profile is None or profile.id in found:
                continue
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            install_name = ""
            name_match = re.search(r'"installdir"\s*"([^"]+)"', text, re.I)
            if name_match:
                install_name = name_match.group(1)
            if not install_name:
                install_name = profile.install_dirs[0] if profile.install_dirs else profile.exe
            install_dir = steamapps / "common" / install_name
            found[profile.id] = Installed(
                profile=profile,
                app_id=app_id,
                library=library,
                install_dir=install_dir,
                manifest=manifest,
            )
    _INSTALLED_CACHE = dict(found)
    _INSTALLED_AT = now
    return found


_INSTALLED_CACHE: dict[str, Installed] | None = None
_INSTALLED_AT = 0.0


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    visible: bool
    rect: tuple[int, int, int, int]
    minimized: bool = False

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    def is_usable(self, *, min_edge: int = 160) -> bool:
        return (
            not self.minimized
            and self.visible
            and self.width >= min_edge
            and self.height >= min_edge
        )

    def summary(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "title": self.title,
            "visible": self.visible,
            "minimized": self.minimized,
            "rect": list(self.rect),
            "size": [self.width, self.height],
            "usable": self.is_usable(),
        }


def is_minimized(hwnd: int) -> bool:
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32.IsIconic(wintypes.HWND(hwnd)))
    except Exception:
        return False


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
        return (0, 0, 0, 0)
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def client_rect_on_screen(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not _user32.GetClientRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
        return (0, 0, 0, 0)
    point = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(point)):
        return (0, 0, 0, 0)
    left, top = int(point.x), int(point.y)
    return (left, top, left + int(rect.right - rect.left), top + int(rect.bottom - rect.top))


# ctypes.WINFUNCTYPE only exists on Windows; keep the module importable on the
# Linux CI runner, where find_windows_for_pids returns early anyway.
WNDENUMPROC = (
    ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    if _IS_WINDOWS
    else None
)


def find_windows_for_pids(pids: Iterable[int]) -> list[WindowInfo]:
    if not _IS_WINDOWS:
        return []
    wanted = {int(pid) for pid in pids}
    if not wanted:
        return []
    results: list[WindowInfo] = []

    def callback(hwnd, _lparam):
        handle = as_hwnd(hwnd)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(pid))
        if int(pid.value) not in wanted:
            return True
        length = _user32.GetWindowTextLengthW(wintypes.HWND(handle))
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(wintypes.HWND(handle), buffer, length + 1)
        results.append(
            WindowInfo(
                hwnd=handle,
                pid=int(pid.value),
                title=str(buffer.value),
                visible=bool(_user32.IsWindowVisible(wintypes.HWND(handle))),
                rect=window_rect(handle),
                minimized=is_minimized(handle),
            )
        )
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return results


def foreground_window() -> int:
    if not _IS_WINDOWS:
        return 0
    return as_hwnd(_user32.GetForegroundWindow())


def is_foreground(hwnd: int) -> bool:
    """True when ``hwnd`` (or one of its descendants) owns the input focus."""
    if not _IS_WINDOWS or not hwnd:
        return False
    current = foreground_window()
    if not current:
        return False
    if current == int(hwnd):
        return True
    root = _user32.GetAncestor(wintypes.HWND(current), 2)  # GA_ROOT
    return as_hwnd(root) == int(hwnd)


def focus_window(hwnd: int) -> bool:
    """Best-effort activation; Windows may refuse a background caller."""
    if not _IS_WINDOWS or not hwnd:
        return False
    handle = wintypes.HWND(int(hwnd))
    try:
        if _user32.IsIconic(handle):
            _user32.ShowWindow(handle, 9)  # SW_RESTORE
        _user32.SetForegroundWindow(handle)
    except Exception:
        pass
    time.sleep(0.12)
    return is_foreground(int(hwnd))


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", wintypes.POINT),
    ]


def cursor_visible() -> bool:
    """Whether Windows currently draws a mouse cursor.

    Windowed Source games often leave the cursor "shown" even during mouse-look,
    so this alone is **not** a reliable menu signal.  It is only the fallback
    for true fullscreen, where the clip rectangle cannot distinguish the two.
    """
    if not _IS_WINDOWS:
        return False
    info = _CURSORINFO()
    info.cbSize = ctypes.sizeof(_CURSORINFO)
    try:
        if not _user32.GetCursorInfo(ctypes.byref(info)):
            return False
        return bool(int(info.flags) & 0x00000001)  # CURSOR_SHOWING
    except Exception:
        return False


def cursor_clip() -> tuple[int, int, int, int] | None:
    """The current ``ClipCursor`` rectangle, or ``None`` if unavailable.

    This is the reliable menu-vs-gameplay discriminator in windowed Source
    games: during mouse-look the game clips the cursor to its client area; a
    menu releases the clip to the whole virtual desktop.  Verified on the
    reference machine with Half-Life: gameplay gives (160,90,1760,990), the
    pause menu gives (0,0,1920,1080), and closing the menu restores the client
    rectangle.
    """
    if not _IS_WINDOWS:
        return None
    rect = wintypes.RECT()
    try:
        if not _user32.GetClipCursor(ctypes.byref(rect)):
            return None
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return None


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """Bounding rectangle of all monitors."""
    if not _IS_WINDOWS:
        return (0, 0, 0, 0)
    try:
        left = int(_user32.GetSystemMetrics(76))    # SM_XVIRTUALSCREEN
        top = int(_user32.GetSystemMetrics(77))     # SM_YVIRTUALSCREEN
        width = int(_user32.GetSystemMetrics(78))   # SM_CXVIRTUALSCREEN
        height = int(_user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
        if width > 0 and height > 0:
            return (left, top, left + width, top + height)
        return (0, 0, int(_user32.GetSystemMetrics(0)), int(_user32.GetSystemMetrics(1)))
    except Exception:
        return (0, 0, 0, 0)


def _rect_close(a: tuple[int, int, int, int] | None,
                b: tuple[int, int, int, int] | None, tolerance: int = 3) -> bool:
    if not a or not b:
        return False
    return all(abs(int(x) - int(y)) <= tolerance for x, y in zip(a, b))


def menu_active(hwnd: int) -> bool:
    """True when the game looks like it is in a menu/pause screen.

    Three signals, strongest first:

    1. clip rectangle == client rect (windowed gameplay);
    2. clip rectangle == whole desktop while the client is smaller (menu);
    3. true fullscreen: fall back to cursor visibility.

    A false "menu" only makes the catgirl wait; a false "gameplay" would make
    her press keys into a menu, so the ordering is deliberately conservative.
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    client = client_rect_on_screen(hwnd)
    clip = cursor_clip()
    full = virtual_screen_rect()
    client_is_full = _rect_close(client, full)
    if clip is not None and not client_is_full:
        if _rect_close(clip, client):
            return False
        if _rect_close(clip, full):
            # Portal 2 sometimes releases the clip to the whole desktop during
            # normal gameplay (observed after a level transition) while hiding
            # the cursor.  A hidden cursor is the stronger signal there: menus
            # always show it.
            return cursor_visible()
    return cursor_visible()


# ---------------------------------------------------------------------------
# Matching running processes to profiles
# ---------------------------------------------------------------------------


def _profile_from_process(exe: str, image_path: str, title: str) -> catalog.GameProfile | None:
    exe_lower = exe.lower()
    matching = [game for game in catalog.GAMES if game.exe.lower() == exe_lower]
    if not matching:
        return None

    # 1. Window title is the only reliable discriminator for hl2.exe (HL2 /
    #    Ep1 / Ep2 / Portal / Lost Coast / HL:Source all share it).
    for game in matching:
        for pattern in game.title_patterns:
            try:
                if re.search(pattern, title or "", re.I):
                    return game
            except re.error:
                continue

    # 2. Install directory disambiguates portal vs hl2 (both ship hl2.exe).
    path_lower = (image_path or "").replace("/", "\\").lower()
    for game in matching:
        for install in game.install_dirs:
            needle = f"\\common\\{install.lower()}\\"
            if needle in path_lower:
                # For shared install dirs, keep the first *mainline* candidate.
                if game.id in ("half_life_2", "half_life_source"):
                    continue
                return game
    for game in matching:
        if game.id in ("half_life_2", "portal", "half_life"):
            return game
    return matching[0]


@dataclass(frozen=True)
class RunningGame:
    profile: catalog.GameProfile
    pid: int
    exe_path: str
    window: WindowInfo | None

    @property
    def hwnd(self) -> int:
        return self.window.hwnd if self.window is not None else 0

    @property
    def title(self) -> str:
        return self.window.title if self.window is not None else ""

    def is_foreground(self) -> bool:
        return bool(self.hwnd and is_foreground(self.hwnd))

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.profile.id,
            "name": self.profile.name_zh,
            "pid": self.pid,
            "exe": self.exe_path,
            "window": self.window.summary() if self.window else None,
            "foreground": self.is_foreground(),
        }


def find_running_games() -> list[RunningGame]:
    """Every running process that belongs to a catalogued game."""
    if not _IS_WINDOWS:
        return []
    exe_map: dict[str, list[catalog.GameProfile]] = {}
    for game in catalog.GAMES:
        exe_map.setdefault(game.exe.lower(), []).append(game)

    candidates: list[tuple[int, str, str]] = []
    for pid, exe in list_processes():
        if exe.lower() in exe_map:
            candidates.append((pid, exe, process_image_path(pid)))

    by_pid: dict[int, list[WindowInfo]] = {}
    if candidates:
        windows = find_windows_for_pids([pid for pid, _exe, _path in candidates])
        for window in windows:
            by_pid.setdefault(window.pid, []).append(window)

    running: list[RunningGame] = []
    for pid, exe, image_path in candidates:
        windows = by_pid.get(pid, [])
        visible = [w for w in windows if w.is_usable()]
        window = next((w for w in visible if is_foreground(w.hwnd)), None)
        if window is None and visible:
            window = max(visible, key=lambda w: w.width * w.height)
        profile = _profile_from_process(exe, image_path, window.title if window else "")
        if profile is None:
            continue
        running.append(RunningGame(profile=profile, pid=pid, exe_path=image_path, window=window))

    # Foreground window first, then the largest usable window.
    def sort_key(game: RunningGame):
        foreground = 1 if game.is_foreground() else 0
        area = game.window.width * game.window.height if game.window else 0
        return (foreground, area)

    return sorted(running, key=sort_key, reverse=True)


def active_game() -> RunningGame | None:
    running = find_running_games()
    return running[0] if running else None


def find_game_window(game_id: str) -> RunningGame | None:
    for game in find_running_games():
        if game.profile.id == game_id:
            return game
    return None
