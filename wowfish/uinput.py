"""A virtual keyboard + mouse built on the kernel's ``/dev/uinput`` interface.

XTEST is the natural choice on X11, but under a rootless Wayland compositor it
only half works: keyboard events are delivered, while ``XTestFakeMotionEvent``
is dropped and ``XWarpPointer`` moves the pointer that ``XQueryPointer``
reports without moving the real cursor or generating motion events.  A uinput
device sits below the display server, so the compositor treats it exactly like
a physical mouse and keyboard - which makes it the reliable backend on Wayland.

Access to ``/dev/uinput`` usually needs root or a udev rule, e.g.::

    # /etc/udev/rules.d/99-uinput.rules
    KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
"""

from __future__ import annotations

import fcntl
import os
import struct
import time
from typing import Optional

# --- linux/input-event-codes.h ---------------------------------------------
EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03

SYN_REPORT = 0

REL_X = 0x00
REL_Y = 0x01

ABS_X = 0x00
ABS_Y = 0x01

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112

BUS_USB = 0x03
UINPUT_MAX_NAME_SIZE = 80
ABS_CNT = 0x40

# --- linux/uinput.h ioctls --------------------------------------------------
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

_UINPUT_USER_DEV = f"80sHHHHI{ABS_CNT}i{ABS_CNT}i{ABS_CNT}i{ABS_CNT}i"
_INPUT_EVENT = "llHHi"

# X11-style key names -> Linux key codes. Covers what a fishing bot needs:
# the cast key, the loot key, modifier-ish keys and the function keys.
KEY_CODES: dict[str, int] = {
    "escape": 1, "esc": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "0": 11,
    "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23,
    "o": 24, "p": 25, "bracketleft": 26, "bracketright": 27, "return": 28,
    "enter": 28, "control": 29, "ctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37,
    "l": 38, "semicolon": 39, "apostrophe": 40, "grave": 41, "shift": 42,
    "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "comma": 51, "period": 52, "slash": 53, "shift_r": 54,
    "alt": 56, "space": 57, "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65,
    "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
}

# X11 mouse button numbers -> Linux BTN_* codes.
BUTTON_CODES = {1: BTN_LEFT, 2: BTN_MIDDLE, 3: BTN_RIGHT}


class UinputError(RuntimeError):
    pass


def linux_keycode(name: str) -> int:
    """Translate an X11-style key name to a Linux input key code."""
    key = name.strip().lower()
    code = KEY_CODES.get(key)
    if code is None:
        raise UinputError(f"no uinput key code known for {name!r}")
    return code


class UinputDevice:
    """A single virtual device exposing keyboard keys and an absolute pointer."""

    def __init__(
        self, name: str = "wowfish virtual input",
        screen: tuple[int, int] = (1920, 1080),
        path: str = "/dev/uinput",
    ) -> None:
        self.width, self.height = screen
        try:
            self.fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise UinputError(
                f"cannot open {path}: {exc}. Add a udev rule or run as root."
            ) from exc

        try:
            self._configure(name)
        except Exception:
            self.close()
            raise

    def _configure(self, name: str) -> None:
        def set_bit(request: int, value: int) -> None:
            fcntl.ioctl(self.fd, request, value)

        set_bit(UI_SET_EVBIT, EV_KEY)
        set_bit(UI_SET_EVBIT, EV_ABS)
        for code in set(KEY_CODES.values()) | set(BUTTON_CODES.values()):
            set_bit(UI_SET_KEYBIT, code)
        set_bit(UI_SET_ABSBIT, ABS_X)
        set_bit(UI_SET_ABSBIT, ABS_Y)

        absmax = [0] * ABS_CNT
        absmax[ABS_X] = max(0, self.width - 1)
        absmax[ABS_Y] = max(0, self.height - 1)
        zeros = [0] * ABS_CNT
        blob = struct.pack(
            _UINPUT_USER_DEV,
            name.encode()[: UINPUT_MAX_NAME_SIZE - 1],
            BUS_USB, 0x1234, 0x5678, 1,
            0,  # ff_effects_max
            *absmax, *zeros, *zeros, *zeros,
        )
        os.write(self.fd, blob)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        # Give the compositor a moment to pick the device up.
        time.sleep(0.4)

    # --------------------------------------------------------------- events
    def emit(self, event_type: int, code: int, value: int) -> None:
        os.write(self.fd, struct.pack(_INPUT_EVENT, 0, 0, event_type, code, value))

    def sync(self) -> None:
        self.emit(EV_SYN, SYN_REPORT, 0)

    def key(self, name: str, pressed: bool) -> None:
        self.emit(EV_KEY, linux_keycode(name), 1 if pressed else 0)
        self.sync()

    def move_abs(self, x: int, y: int) -> None:
        self.emit(EV_ABS, ABS_X, max(0, min(int(x), self.width - 1)))
        self.emit(EV_ABS, ABS_Y, max(0, min(int(y), self.height - 1)))
        self.sync()

    def button(self, button: int, pressed: bool) -> None:
        code = BUTTON_CODES.get(button)
        if code is None:
            raise UinputError(f"unsupported mouse button {button}")
        self.emit(EV_KEY, code, 1 if pressed else 0)
        self.sync()

    def close(self) -> None:
        fd: Optional[int] = getattr(self, "fd", None)
        if fd is None:
            return
        try:
            fcntl.ioctl(fd, UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        self.fd = None

    def __enter__(self) -> "UinputDevice":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
