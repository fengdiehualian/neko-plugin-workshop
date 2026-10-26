"""Low-level keyboard/mouse injection for Source-engine games.

Source and GoldSrc read input through DirectInput/RawInput, so posting
``WM_KEYDOWN`` to the game window does nothing useful.  The only reliable route
from a plain Python process is ``SendInput`` with **hardware scan codes**
(``KEYEVENTF_SCANCODE``), which is what a physical keyboard emits and what the
games observe.  Relative ``MOUSEEVENTF_MOVE`` drives mouse-look; FPS games do
not use the desktop cursor, so absolute cursor movement is only used by menus.

Everything here is stdlib ``ctypes``: the host interpreter ships ``win32api``
but not ``win32gui`` / ``pyautogui`` / ``pydirectinput``, so we cannot depend
on a convenience library.

Safety contract
---------------
``KeyInjector`` refuses to emit any event unless the target window is the
foreground window (``require_foreground=True``, the default).  A desktop pet
that types into whatever the user happens to have focused would be a bug, not
a feature.  ``release_all()`` clears every key the injector believes it is
holding, and is called from the autopilot's stop/shutdown paths.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from typing import Iterable

__all__ = [
    "INPUT_SIZE",
    "SCAN_CODES",
    "InputUnavailable",
    "KeyInjector",
    "InputGuardError",
    "normalize_key",
]

# --------------------------------------------------------------------------
# Win32 plumbing
# --------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

INPUT_KEYBOARD = 1
INPUT_MOUSE = 0

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


#: ``sizeof(INPUT)`` on x64 Windows is 40 bytes; on x86 it is 28.  The
#: autopilot asserts this before the first injection so a struct-layout
#: mistake fails loudly instead of silently mangling input.
INPUT_SIZE = ctypes.sizeof(INPUT)
_EXPECTED_INPUT_SIZE = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28


class InputUnavailable(RuntimeError):
    """Raised when the platform cannot inject input at all."""


class InputGuardError(RuntimeError):
    """Raised when injection was refused by the foreground-window guard."""


# --------------------------------------------------------------------------
# Scan code table (PC/AT Set 1 make codes)
# --------------------------------------------------------------------------

#: ``name -> (scan_code, extended)``.  Extended keys are prefixed with ``E0``
#: on the wire and require ``KEYEVENTF_EXTENDEDKEY``.
SCAN_CODES: dict[str, tuple[int, bool]] = {
    "ESC": (0x01, False),
    "ESCAPE": (0x01, False),
    "1": (0x02, False), "2": (0x03, False), "3": (0x04, False), "4": (0x05, False),
    "5": (0x06, False), "6": (0x07, False), "7": (0x08, False), "8": (0x09, False),
    "9": (0x0A, False), "0": (0x0B, False),
    "MINUS": (0x0C, False), "EQUALS": (0x0D, False),
    "BACKSPACE": (0x0E, False), "TAB": (0x0F, False),
    "Q": (0x10, False), "W": (0x11, False), "E": (0x12, False), "R": (0x13, False),
    "T": (0x14, False), "Y": (0x15, False), "U": (0x16, False), "I": (0x17, False),
    "O": (0x18, False), "P": (0x19, False),
    "LBRACKET": (0x1A, False), "RBRACKET": (0x1B, False),
    "ENTER": (0x1C, False), "RETURN": (0x1C, False),
    "LCTRL": (0x1D, False), "CTRL": (0x1D, False),
    "A": (0x1E, False), "S": (0x1F, False), "D": (0x20, False), "F": (0x21, False),
    "G": (0x22, False), "H": (0x23, False), "J": (0x24, False), "K": (0x25, False),
    "L": (0x26, False),
    "SEMICOLON": (0x27, False), "APOSTROPHE": (0x28, False), "GRAVE": (0x29, False),
    "LSHIFT": (0x2A, False), "SHIFT": (0x2A, False),
    "BACKSLASH": (0x2B, False),
    "Z": (0x2C, False), "X": (0x2D, False), "C": (0x2E, False), "V": (0x2F, False),
    "B": (0x30, False), "N": (0x31, False), "M": (0x32, False),
    "COMMA": (0x33, False), "PERIOD": (0x34, False), "SLASH": (0x35, False),
    "RSHIFT": (0x36, False),
    "MULTIPLY": (0x37, False),
    "LALT": (0x38, False), "ALT": (0x38, False),
    "SPACE": (0x39, False),
    "CAPSLOCK": (0x3A, False),
    "F1": (0x3B, False), "F2": (0x3C, False), "F3": (0x3D, False), "F4": (0x3E, False),
    "F5": (0x3F, False), "F6": (0x40, False), "F7": (0x41, False), "F8": (0x42, False),
    "F9": (0x43, False), "F10": (0x44, False),
    "NUMLOCK": (0x45, False), "SCROLLLOCK": (0x46, False),
    "F11": (0x57, False), "F12": (0x58, False),
    # Extended (E0-prefixed)
    "UP": (0x48, True), "DOWN": (0x50, True),
    "LEFT": (0x4B, True), "RIGHT": (0x4D, True),
    "HOME": (0x47, True), "END": (0x4F, True),
    "PAGEUP": (0x49, True), "PAGEDOWN": (0x51, True),
    "INSERT": (0x52, True), "DELETE": (0x53, True),
    "RCTRL": (0x1D, True), "RALT": (0x38, True),
    "NUMPADENTER": (0x1C, True),
}

_ALIASES = {
    " " : "SPACE",
    "空格": "SPACE",
    "上": "UP", "下": "DOWN", "左": "LEFT", "右": "RIGHT",
    "CTRL": "LCTRL", "SHIFT": "LSHIFT", "ALT": "LALT",
    "ESCAPE": "ESC",
    "回车": "ENTER", "确认": "ENTER",
    "跳跃": "SPACE", "跳": "SPACE", "蹲": "LCTRL", "蹲下": "LCTRL",
    "PLUS": "EQUALS", "DASH": "MINUS",
    "MOUSE1": "MOUSE1", "MOUSE2": "MOUSE2", "MOUSE3": "MOUSE3",
    "鼠标左键": "MOUSE1", "左键": "MOUSE1", "鼠标右键": "MOUSE2", "右键": "MOUSE2",
    "鼠标中键": "MOUSE3", "中键": "MOUSE3",
}

#: Names understood by ``key_down``/``key_up`` that are mouse buttons rather
#: than keyboard scan codes.
MOUSE_BUTTONS = frozenset({"MOUSE1", "MOUSE2", "MOUSE3"})

#: Virtual-key codes used only for read-only "is a human touching the controls"
#: polling.  Injected keys are excluded by the injector itself.
_HUMAN_VKS: dict[str, int] = {
    "W": 0x57, "A": 0x41, "S": 0x53, "D": 0x44,
    "SPACE": 0x20, "LCTRL": 0x11, "LSHIFT": 0x10,
    "E": 0x45, "R": 0x52, "Q": 0x51, "F": 0x46,
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35, "6": 0x36,
    "ESC": 0x1B, "TAB": 0x09,
    "MOUSE1": 0x01, "MOUSE2": 0x02, "MOUSE3": 0x04,
}

#: F8 pauses/resumes.  Chosen because neither Half-Life nor Portal binds it to
#: a destructive action (HL1 uses F10 for "quit prompt"; F5-F7 are saves).
PAUSE_VK = 0x77  # VK_F8


def normalize_key(name: str) -> str:
    """Map a human/config key name onto a canonical table key.

    Accepts single letters, ``vk:``-free names from :data:`SCAN_CODES`, a few
    Chinese aliases, and is case-insensitive.  Raises :class:`ValueError` for
    unknown names so a typo in ``plugin.toml`` surfaces at startup instead of
    silently doing nothing mid-fight.
    """
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("empty key name")
    # Chinese aliases first: they are not case-foldable.
    if raw in _ALIASES:
        return _ALIASES[raw]
    upper = raw.upper()
    if upper in _ALIASES:
        return _ALIASES[upper]
    if upper in SCAN_CODES:
        return upper
    raise ValueError(f"unknown key name: {name!r}")


class KeyInjector:
    """Scan-code keyboard/mouse injector with a foreground-window guard."""

    def __init__(
        self,
        *,
        logger=None,
        require_foreground: bool = True,
        foreground_check=None,
        dry_run: bool = False,
    ) -> None:
        self._logger = logger
        self.require_foreground = bool(require_foreground)
        self.dry_run = bool(dry_run)
        # ``foreground_check`` is a zero-arg callable returning bool.  The
        # service supplies one bound to the ICEY window; ``None`` disables the
        # window-specific part of the guard (used by unit tests).
        self._foreground_check = foreground_check
        self._held: set[str] = set()
        self._mouse_held: set[str] = set()
        self._available = sys.platform == "win32" and _user32 is not None
        self._events_sent = 0
        self._events_refused = 0

    # -- diagnostics ------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    @property
    def held_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._held))

    @property
    def held_buttons(self) -> tuple[str, ...]:
        return tuple(sorted(self._mouse_held))

    def stats(self) -> dict[str, object]:
        return {
            "available": self._available,
            "require_foreground": self.require_foreground,
            "dry_run": self.dry_run,
            "held_keys": sorted(self._held),
            "held_buttons": sorted(self._mouse_held),
            "events_sent": self._events_sent,
            "events_refused": self._events_refused,
            "input_struct_size": INPUT_SIZE,
            "input_struct_ok": INPUT_SIZE == _EXPECTED_INPUT_SIZE,
        }

    def layout_is_valid(self) -> bool:
        """Verify the ``INPUT`` struct matches the ABI the OS expects."""
        return INPUT_SIZE == _EXPECTED_INPUT_SIZE

    # -- guard ------------------------------------------------------------

    def _guard(self) -> None:
        if self.dry_run:
            self._events_refused += 1
            raise InputGuardError("dry_run is enabled: input injection is disabled")
        if not self._available:
            raise InputUnavailable("SendInput is only available on Windows")
        if not self.require_foreground:
            return
        check = self._foreground_check
        if check is None:
            return
        try:
            ok = bool(check())
        except Exception:  # pragma: no cover - defensive
            ok = False
        if not ok:
            self._events_refused += 1
            raise InputGuardError(
                "refusing to inject input: the game window is not in the foreground"
            )

    # -- primitive injection ---------------------------------------------

    def _send(self, inputs: list[INPUT]) -> None:
        if not inputs:
            return
        array = (INPUT * len(inputs))(*inputs)
        sent = _user32.SendInput(len(inputs), ctypes.byref(array), INPUT_SIZE)
        if sent != len(inputs):
            err = ctypes.get_last_error()
            raise InputUnavailable(
                f"SendInput injected {sent}/{len(inputs)} events (winerror={err})"
            )
        self._events_sent += len(inputs)

    @staticmethod
    def _key_input(key: str, *, key_up: bool) -> INPUT:
        scan, extended = SCAN_CODES[key]
        flags = KEYEVENTF_SCANCODE
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if key_up:
            flags |= KEYEVENTF_KEYUP
        return INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUTUNION(ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)),
        )

    def key_down(self, name: str) -> None:
        key = normalize_key(name)
        if key in MOUSE_BUTTONS:
            self.mouse_down(key)
            return
        self._guard()
        self._send([self._key_input(key, key_up=False)])
        self._held.add(key)

    def key_up(self, name: str) -> None:
        key = normalize_key(name)
        if key in MOUSE_BUTTONS:
            self.mouse_up(key)
            return
        # A key-up is always allowed through the guard *if* we are tracking the
        # key as held: refusing it would leave the game with a stuck key.
        tracked = key in self._held
        if not tracked:
            self._guard()
        self._send([self._key_input(key, key_up=True)])
        self._held.discard(key)

    def tap(self, name: str, hold_seconds: float = 0.05) -> None:
        """Press and release one key or mouse button."""
        key = normalize_key(name)
        if key in MOUSE_BUTTONS:
            button = {"MOUSE1": "left", "MOUSE2": "right", "MOUSE3": "middle"}[key]
            self.mouse_down(button)
            try:
                _sleep(hold_seconds)
            finally:
                self.mouse_up(button)
            return
        self._guard()
        self._send([self._key_input(key, key_up=False)])
        try:
            _sleep(hold_seconds)
        finally:
            self._send([self._key_input(key, key_up=True)])

    def press_many(self, names: Iterable[str], hold_seconds: float = 0.05) -> None:
        """Press several keys together (e.g. a modifier chord), then release."""
        keys = [normalize_key(n) for n in names]
        if not keys:
            return
        self._guard()
        self._send([self._key_input(k, key_up=False) for k in keys])
        try:
            _sleep(hold_seconds)
        finally:
            self._send([self._key_input(k, key_up=True) for k in reversed(keys)])

    def release_all(self) -> None:
        """Release every key/button we are tracking. Never raises on the guard."""
        pending = sorted(self._held)
        pending_buttons = sorted(self._mouse_held)
        self._held.clear()
        self._mouse_held.clear()
        if not self._available:
            return
        try:
            events = [self._key_input(k, key_up=True) for k in pending]
            for button in pending_buttons:
                flag = {
                    "MOUSE1": MOUSEEVENTF_LEFTUP,
                    "MOUSE2": MOUSEEVENTF_RIGHTUP,
                    "MOUSE3": MOUSEEVENTF_MIDDLEUP,
                }[button]
                events.append(INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(
                    dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0))))
            if events:
                self._send(events)
        except Exception as exc:  # pragma: no cover - defensive
            if self._logger is not None:
                try:
                    self._logger.warning("[input] release_all failed: {}", exc)
                except Exception:
                    pass

    # -- convenience macros ----------------------------------------------

    def hold_for(self, name: str, seconds: float) -> None:
        """Hold ``name`` for ``seconds`` (blocking; runs in the executor)."""
        key = normalize_key(name)
        if key in MOUSE_BUTTONS:
            self.mouse_down({"MOUSE1": "left", "MOUSE2": "right", "MOUSE3": "middle"}[key])
            try:
                _sleep(seconds)
            finally:
                self.mouse_up({"MOUSE1": "left", "MOUSE2": "right", "MOUSE3": "middle"}[key])
            return
        self.key_down(key)
        try:
            _sleep(seconds)
        finally:
            self.key_up(key)

    # -- mouse -------------------------------------------------------------

    def mouse_down(self, button: str = "left") -> None:
        key = normalize_key(button) if str(button).lower().startswith("mouse") else \
            {"left": "MOUSE1", "right": "MOUSE2", "middle": "MOUSE3"}.get(
                str(button).lower(), "MOUSE1")
        if key not in MOUSE_BUTTONS:
            raise ValueError(f"unknown mouse button: {button!r}")
        self._guard()
        flag = {
            "MOUSE1": MOUSEEVENTF_LEFTDOWN,
            "MOUSE2": MOUSEEVENTF_RIGHTDOWN,
            "MOUSE3": MOUSEEVENTF_MIDDLEDOWN,
        }[key]
        self._send([
            INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0))),
        ])
        self._mouse_held.add(key)

    def mouse_up(self, button: str = "left") -> None:
        key = normalize_key(button) if str(button).lower().startswith("mouse") else \
            {"left": "MOUSE1", "right": "MOUSE2", "middle": "MOUSE3"}.get(
                str(button).lower(), "MOUSE1")
        if key not in MOUSE_BUTTONS:
            raise ValueError(f"unknown mouse button: {button!r}")
        if key not in self._mouse_held:
            self._guard()
        flag = {
            "MOUSE1": MOUSEEVENTF_LEFTUP,
            "MOUSE2": MOUSEEVENTF_RIGHTUP,
            "MOUSE3": MOUSEEVENTF_MIDDLEUP,
        }[key]
        self._send([
            INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0))),
        ])
        self._mouse_held.discard(key)

    def mouse_move(self, dx: float, dy: float, *, steps: int = 1) -> None:
        """Inject a relative mouse move (mouse-look), in device counts.

        Large deltas are split into several events because some games clamp a
        single event; the total is preserved.  ``dx``/``dy`` are *counts*, not
        degrees -- :mod:`policy` converts using the game's sensitivity.
        """
        self._guard()
        dx_i = int(round(dx))
        dy_i = int(round(dy))
        if dx_i == 0 and dy_i == 0:
            return
        pieces = max(1, min(int(steps), max(abs(dx_i), abs(dy_i), 1)))
        sent_x = 0
        sent_y = 0
        for index in range(pieces):
            target_x = int(round(dx_i * (index + 1) / pieces))
            target_y = int(round(dy_i * (index + 1) / pieces))
            step_x, step_y = target_x - sent_x, target_y - sent_y
            sent_x, sent_y = target_x, target_y
            if not step_x and not step_y:
                continue
            self._send([
                INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(
                    dx=step_x, dy=step_y, mouseData=0, dwFlags=MOUSEEVENTF_MOVE,
                    time=0, dwExtraInfo=0))),
            ])
            if pieces > 1:
                _sleep(0.004)

    def look(self, dx: float, dy: float) -> None:
        """Relative mouse-look with a small per-event chunk size."""
        count = max(1, int(max(abs(dx), abs(dy)) / 120.0) + 1)
        self.mouse_move(dx, dy, steps=min(count, 24))

    # -- read-only human-input detection ----------------------------------

    def physical_keys_down(self, *, include_mouse: bool = True) -> list[str]:
        """Keys currently held by *anyone*, excluding keys we injected.

        Used to yield control the moment the player touches WASD/fire -- the
        "jump in and play together" requirement.  This is polling, not a hook,
        so it cannot see a key that is already down before we looked, and it
        cannot distinguish a human pressing the same key we are holding.
        """
        if not self._available:
            return []
        active: list[str] = []
        for key, vk in _HUMAN_VKS.items():
            if key.startswith("MOUSE") and not include_mouse:
                continue
            try:
                down = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
            except Exception:
                continue
            if not down:
                continue
            if key in self._held or key in self._mouse_held:
                continue
            active.append(key)
        return active

    @staticmethod
    def pause_hotkey_down() -> bool:
        """True while F8 is physically held (edge detection is the caller's)."""
        if _user32 is None:
            return False
        try:
            return bool(_user32.GetAsyncKeyState(PAUSE_VK) & 0x8000)
        except Exception:
            return False

    def move_cursor(self, x: int, y: int, *, screen_w: int, screen_h: int) -> None:
        """Move the mouse using absolute normalised coordinates."""
        self._guard()
        nx = int(max(0, min(65535, round(x * 65535 / max(1, screen_w - 1)))))
        ny = int(max(0, min(65535, round(y * 65535 / max(1, screen_h - 1)))))
        self._send([
            INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(
                dx=nx, dy=ny, mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                time=0, dwExtraInfo=0))),
        ])

    def click(self, *, button: str = "left", hold_seconds: float = 0.04) -> None:
        self.mouse_down(button)
        try:
            _sleep(hold_seconds)
        finally:
            self.mouse_up(button)


def _sleep(seconds: float) -> None:
    delay = float(seconds)
    if delay > 0:
        time.sleep(delay)
