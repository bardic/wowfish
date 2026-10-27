#!/usr/bin/env python3
"""A fake WoW fishing window for testing the bot without the game.

It opens a real X11 window, paints water, spawns a red bobber when you press a
cast key, makes it "bite" after a couple of seconds, and removes it when you
right-click (or press the loot key) on top of it.  Point the bot at it with::

    ./tools/fake_game.py &
    ./fishbot.py --mode run --window-title wowfish-fake-game --max-casts 3

This exercises the exact production path: XGetImage capture, colour detection,
splash detection and injected input.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import random
import select
import sys
import time
from ctypes import (
    POINTER, c_char_p, c_int, c_long, c_uint, c_ulong, c_void_p, cast,
)

WATER = 0x1A3C78
RED = 0xCE2822
WHITE = 0xF5FAFF

ExposeMask = 1 << 15
StructureNotifyMask = 1 << 17
ButtonPressMask = 1 << 2
KeyPressMask = 1 << 0


class FakeGame:
    def __init__(self, width: int = 700, height: int = 500) -> None:
        self.lib = ctypes.CDLL(ctypes.util.find_library("X11"))
        self._bind()
        self.width, self.height = width, height
        self.rng = random.Random()
        self.bobber: tuple[int, int] | None = None
        self.phase = "idle"
        self.bite_at = 0.0
        self.splash_until = 0.0
        self.loots = 0
        self.casts = 0

        self._error_handler = ctypes.CFUNCTYPE(c_int, c_void_p, c_void_p)(
            lambda _d, _e: 0
        )
        self.lib.XSetErrorHandler.argtypes = [
            ctypes.CFUNCTYPE(c_int, c_void_p, c_void_p)
        ]
        self.lib.XSetErrorHandler.restype = c_void_p
        self.lib.XSetErrorHandler(self._error_handler)

        self.display = self.lib.XOpenDisplay(None)
        if not self.display:
            raise SystemExit("cannot open display")
        self.root = self.lib.XDefaultRootWindow(self.display)
        self.win = self.lib.XCreateSimpleWindow(
            self.display, self.root, 150, 150, width, height, 0, 0, WATER
        )
        # Managed (not override-redirect) so the window manager gives it real
        # keyboard focus; KWin does not route keys to override-redirect windows.
        self.lib.XStoreName(self.display, self.win, b"wowfish-fake-game")
        self.lib.XSelectInput(
            self.display, self.win,
            ExposeMask | StructureNotifyMask | ButtonPressMask | KeyPressMask,
        )
        self.gc = self.lib.XCreateGC(self.display, self.win, 0, None)
        self.lib.XMapWindow(self.display, self.win)
        self.lib.XSetInputFocus(self.display, self.win, 2, 0)
        self.lib.XSync(self.display, 0)
        self.fd = self.lib.XConnectionNumber(self.display)

    def _bind(self) -> None:
        lib = self.lib
        lib.XOpenDisplay.restype = c_void_p
        lib.XOpenDisplay.argtypes = [c_char_p]
        lib.XDefaultRootWindow.restype = c_ulong
        lib.XDefaultRootWindow.argtypes = [c_void_p]
        lib.XCreateSimpleWindow.restype = c_ulong
        lib.XCreateSimpleWindow.argtypes = [
            c_void_p, c_ulong, c_int, c_int, c_uint, c_uint, c_uint, c_ulong,
            c_ulong,
        ]
        lib.XStoreName.argtypes = [c_void_p, c_ulong, c_char_p]
        lib.XSelectInput.argtypes = [c_void_p, c_ulong, c_long]
        lib.XCreateGC.restype = c_void_p
        lib.XCreateGC.argtypes = [c_void_p, c_ulong, c_ulong, c_void_p]
        lib.XSetForeground.argtypes = [c_void_p, c_void_p, c_ulong]
        lib.XFillRectangle.argtypes = [
            c_void_p, c_ulong, c_void_p, c_int, c_int, c_uint, c_uint,
        ]
        lib.XMapWindow.argtypes = [c_void_p, c_ulong]
        lib.XSetInputFocus.argtypes = [c_void_p, c_ulong, c_int, c_ulong]
        lib.XSync.argtypes = [c_void_p, c_int]
        lib.XFlush.argtypes = [c_void_p]
        lib.XPending.argtypes = [c_void_p]
        lib.XPending.restype = c_int
        lib.XNextEvent.argtypes = [c_void_p, c_void_p]
        lib.XConnectionNumber.argtypes = [c_void_p]
        lib.XConnectionNumber.restype = c_int

    # ------------------------------------------------------------- drawing
    def fill(self, x: int, y: int, w: int, h: int, colour: int) -> None:
        self.lib.XSetForeground(self.display, self.gc, colour)
        self.lib.XFillRectangle(self.display, self.win, self.gc, x, y, w, h)

    def draw(self) -> None:
        self.fill(0, 0, self.width, self.height, WATER)
        if self.bobber:
            bx, by = self.bobber
            if self.phase == "bite" and time.monotonic() < self.splash_until:
                self.fill(bx - 34, by - 34, 68, 68, WHITE)
            self.fill(bx - 6, by - 6, 12, 12, RED)
        self.lib.XFlush(self.display)

    # -------------------------------------------------------------- logic
    def cast(self) -> None:
        if self.phase in ("waiting", "bite"):
            return
        self.casts += 1
        self.bobber = (
            self.rng.randint(60, self.width - 60),
            self.rng.randint(60, self.height - 60),
        )
        self.phase = "waiting"
        self.bite_at = time.monotonic() + self.rng.uniform(2.0, 3.5)
        print(f"fake-game: cast #{self.casts} -> bobber at {self.bobber}", flush=True)

    def click(self, x: int, y: int) -> None:
        if self.bobber and self.phase in ("waiting", "bite"):
            bx, by = self.bobber
            if abs(x - bx) <= 30 and abs(y - by) <= 30:
                self.phase = "looted"
                self.bobber = None
                self.loots += 1
                print(f"fake-game: looted #{self.loots} at ({x}, {y})", flush=True)
                return
        print(f"fake-game: click at ({x}, {y}) missed", flush=True)

    def update(self) -> None:
        if self.phase == "waiting" and time.monotonic() >= self.bite_at:
            self.phase = "bite"
            self.splash_until = time.monotonic() + 0.8
            print("fake-game: bite!", flush=True)
        elif self.phase == "bite" and time.monotonic() > self.splash_until + 4:
            self.phase = "idle"
            self.bobber = None

    def handle_events(self) -> None:
        buf = ctypes.create_string_buffer(256)
        while self.lib.XPending(self.display):
            self.lib.XNextEvent(self.display, buf)
            etype = cast(buf, POINTER(c_int))[0]
            if etype == 4:  # ButtonPress
                x = c_int.from_buffer(buf, 64).value
                y = c_int.from_buffer(buf, 68).value
                self.click(x, y)
            elif etype == 2:  # KeyPress
                self.cast()
            elif etype == 22:  # ConfigureNotify
                self.width = c_int.from_buffer(buf, 56).value
                self.height = c_int.from_buffer(buf, 60).value

    def run(self) -> None:
        print(
            f"fake-game running: window 0x{self.win:x} {self.width}x{self.height}",
            flush=True,
        )
        last_focus = 0.0
        while True:
            self.handle_events()
            self.update()
            self.draw()
            now = time.monotonic()
            if now - last_focus > 1.0:
                # Keep keyboard focus pinned on the test window so injected
                # keys always land here.
                self.lib.XSetInputFocus(self.display, self.win, 2, 0)
                self.lib.XFlush(self.display)
                last_focus = now
            select.select([self.fd], [], [], 0.03)


if __name__ == "__main__":
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 700
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    FakeGame(w, h).run()
