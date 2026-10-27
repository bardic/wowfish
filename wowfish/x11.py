"""Minimal ctypes bindings for X11 + XTEST.

The bot talks to ``libX11``/``libXtst`` directly instead of pulling in
``python-xlib``/``pynput``/``opencv``.  That keeps the project installable with
nothing but a Python interpreter, and it happens to be the right tool for the
job: WoW under Proton runs as an XWayland client, so XTEST input and
``XGetImage`` capture both work even on a Wayland desktop session.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_char_p,
    c_int,
    c_ubyte,
    c_uint,
    c_ulong,
    c_void_p,
)
from typing import Iterable, Optional

ZPIXMAP = 2
ALL_PLANES = c_ulong(-1).value

# int (*XErrorHandler)(Display *, XErrorEvent *)
ErrorHandler = ctypes.CFUNCTYPE(c_int, c_void_p, c_void_p)
XA_STRING = 31
ANY_PROPERTY_TYPE = 0

# Mouse buttons as understood by X11 / WoW.
BUTTON_LEFT = 1
BUTTON_MIDDLE = 2
BUTTON_RIGHT = 3


class X11Error(RuntimeError):
    """Raised when the X server refuses a request."""


class XImage(Structure):
    """The leading fields of Xlib's ``XImage`` struct.

    We only ever read the header; the function-pointer table at the tail is
    never touched, so it is modelled as opaque storage.
    """

    _fields_ = [
        ("width", c_int),
        ("height", c_int),
        ("xoffset", c_int),
        ("format", c_int),
        ("data", c_void_p),
        ("byte_order", c_int),
        ("bitmap_unit", c_int),
        ("bitmap_bit_order", c_int),
        ("bitmap_pad", c_int),
        ("depth", c_int),
        ("bytes_per_line", c_int),
        ("bits_per_pixel", c_int),
        ("red_mask", c_ulong),
        ("green_mask", c_ulong),
        ("blue_mask", c_ulong),
        ("obdata", c_void_p),
        ("f", c_void_p * 6),
    ]


class X11:
    """A thin, stateful wrapper around a single X display connection."""

    def __init__(self, display_name: Optional[str] = None) -> None:
        self._lib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self._xtst: Optional[ctypes.CDLL] = None
        self._bind()

        raw_name = display_name.encode() if display_name else None
        self.display = self._lib.XOpenDisplay(raw_name)
        if not self.display:
            raise X11Error(
                "could not open an X display"
                + (f" ({display_name!r})" if display_name else "")
                + " - is DISPLAY set?"
            )
        self.root = self._lib.XDefaultRootWindow(self.display)
        self._keycodes: dict[str, int] = {}
        self._atoms: dict[str, int] = {}

        # Xlib's default error handler calls exit(). We never want a failed
        # XGetImage (e.g. a region outside a rootless XWayland root) to kill
        # the bot, so swallow errors and surface them as exceptions instead.
        self._last_error: Optional[int] = None

        def _on_error(_display: int, event: int) -> int:
            self._last_error = int(event)
            return 0

        self._error_handler = ErrorHandler(_on_error)
        self._lib.XSetErrorHandler(self._error_handler)

    # ------------------------------------------------------------------ setup
    def _bind(self) -> None:
        lib = self._lib
        lib.XOpenDisplay.argtypes = [c_char_p]
        lib.XOpenDisplay.restype = c_void_p

        lib.XCloseDisplay.argtypes = [c_void_p]
        lib.XCloseDisplay.restype = c_int

        lib.XDefaultRootWindow.argtypes = [c_void_p]
        lib.XDefaultRootWindow.restype = c_ulong

        lib.XGetImage.argtypes = [
            c_void_p, c_ulong, c_int, c_int, c_uint, c_uint, c_ulong, c_int,
        ]
        lib.XGetImage.restype = POINTER(XImage)

        lib.XDestroyImage.argtypes = [POINTER(XImage)]
        lib.XDestroyImage.restype = c_int

        lib.XStringToKeysym.argtypes = [c_char_p]
        lib.XStringToKeysym.restype = c_ulong

        lib.XKeysymToKeycode.argtypes = [c_void_p, c_ulong]
        lib.XKeysymToKeycode.restype = c_ubyte

        lib.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        lib.XInternAtom.restype = c_ulong

        lib.XGetWindowProperty.argtypes = [
            c_void_p, c_ulong, c_ulong, ctypes.c_long, ctypes.c_long, c_int,
            c_ulong, POINTER(c_ulong), POINTER(c_int), POINTER(c_ulong),
            POINTER(c_ulong), POINTER(c_void_p),
        ]
        lib.XGetWindowProperty.restype = c_int

        lib.XQueryTree.argtypes = [
            c_void_p, c_ulong, POINTER(c_ulong), POINTER(c_ulong),
            POINTER(POINTER(c_ulong)), POINTER(c_uint),
        ]
        lib.XQueryTree.restype = c_int

        lib.XGetGeometry.argtypes = [
            c_void_p, c_ulong, POINTER(c_ulong), POINTER(c_int), POINTER(c_int),
            POINTER(c_uint), POINTER(c_uint), POINTER(c_uint), POINTER(c_uint),
        ]
        lib.XGetGeometry.restype = c_int

        lib.XTranslateCoordinates.argtypes = [
            c_void_p, c_ulong, c_ulong, c_int, c_int,
            POINTER(c_int), POINTER(c_int), POINTER(c_ulong),
        ]
        lib.XTranslateCoordinates.restype = c_int

        lib.XQueryPointer.argtypes = [
            c_void_p, c_ulong, POINTER(c_ulong), POINTER(c_ulong),
            POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int),
            POINTER(c_uint),
        ]
        lib.XQueryPointer.restype = c_int

        lib.XSync.argtypes = [c_void_p, c_int]
        lib.XSync.restype = c_int

        lib.XFlush.argtypes = [c_void_p]
        lib.XFlush.restype = c_int

        lib.XFree.argtypes = [c_void_p]
        lib.XFree.restype = c_int

        lib.XSetInputFocus.argtypes = [c_void_p, c_ulong, c_int, c_ulong]
        lib.XSetInputFocus.restype = c_int

        lib.XWarpPointer.argtypes = [
            c_void_p, c_ulong, c_ulong, c_int, c_int, c_uint, c_uint, c_int,
            c_int,
        ]
        lib.XWarpPointer.restype = c_int

        lib.XSetErrorHandler.argtypes = [ErrorHandler]
        lib.XSetErrorHandler.restype = c_void_p

    def _load_xtst(self) -> None:
        if self._xtst is not None:
            return
        lib = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        lib.XTestQueryExtension.argtypes = [
            c_void_p, POINTER(c_int), POINTER(c_int), POINTER(c_int),
            POINTER(c_int),
        ]
        lib.XTestQueryExtension.restype = c_int
        lib.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
        lib.XTestFakeKeyEvent.restype = c_int
        lib.XTestFakeButtonEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
        lib.XTestFakeButtonEvent.restype = c_int
        lib.XTestFakeMotionEvent.argtypes = [
            c_void_p, c_int, c_int, c_int, c_ulong,
        ]
        lib.XTestFakeMotionEvent.restype = c_int
        self._xtst = lib

    def close(self) -> None:
        if getattr(self, "display", None):
            self._lib.XCloseDisplay(self.display)
            self.display = None

    def __enter__(self) -> "X11":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --------------------------------------------------------------- capture
    def capture(self, x: int, y: int, width: int, height: int) -> XImage:
        """Grab a rectangle from the root window (``x``/``y`` are screen px)."""
        self._last_error = None
        img = self._lib.XGetImage(
            self.display, self.root, int(x), int(y),
            c_uint(int(width)), c_uint(int(height)), ALL_PLANES, ZPIXMAP,
        )
        if self._last_error is not None:
            code = self._last_error
            self._last_error = None
            raise X11Error(
                f"XGetImage failed for region {x},{y} {width}x{height} "
                f"(X error {code}) - is this a Wayland session? Try "
                f"--window-title instead of --roi"
            )
        if not img:
            raise X11Error(
                f"XGetImage returned nothing for region {x},{y} {width}x{height}"
            )
        return img

    def capture_window(self, window: int) -> XImage:
        """Grab the full contents of ``window``."""
        width, height = self.window_size(window)
        self._last_error = None
        img = self._lib.XGetImage(
            self.display, c_ulong(window), 0, 0,
            c_uint(width), c_uint(height), ALL_PLANES, ZPIXMAP,
        )
        if self._last_error is not None:
            code = self._last_error
            self._last_error = None
            raise X11Error(
                f"XGetImage failed for window 0x{window:x} (X error {code}) - "
                "it may be obscured, minimised or not an X11 window"
            )
        if not img:
            raise X11Error(
                f"XGetImage returned nothing for window 0x{window:x} - "
                "it may be unmapped, minimised or not an X11 window"
            )
        return img

    def destroy_image(self, image: XImage) -> None:
        self._lib.XDestroyImage(image)

    # ----------------------------------------------------------------- input
    def keycode(self, name: str) -> int:
        """Translate a keysym name such as ``"1"`` or ``"F5"`` to a keycode."""
        code = self._keycodes.get(name)
        if code is None:
            keysym = self._lib.XStringToKeysym(name.encode())
            if keysym == 0:
                raise X11Error(f"unknown key name: {name!r}")
            code = int(self._lib.XKeysymToKeycode(self.display, c_ulong(keysym)))
            if code == 0:
                raise X11Error(f"key {name!r} is not mapped on this keyboard")
            self._keycodes[name] = code
        return code

    def key_event(self, name: str, pressed: bool) -> None:
        self._load_xtst()
        code = self.keycode(name)
        assert self._xtst is not None
        self._xtst.XTestFakeKeyEvent(self.display, code, 1 if pressed else 0, 0)
        self._lib.XFlush(self.display)

    def tap_key(self, name: str) -> None:
        self.key_event(name, True)
        self.key_event(name, False)

    def move_pointer(self, x: int, y: int) -> None:
        """Move the cursor to absolute root coordinates.

        ``XWarpPointer`` is a core request that XWayland forwards to the
        compositor, whereas ``XTestFakeMotionEvent`` is silently dropped by
        KWin's XWayland (it returns success but the cursor never moves).  We
        warp first and only fall back to XTEST if the warp is rejected.
        """
        ok = self._lib.XWarpPointer(
            self.display, 0, self.root, 0, 0, 0, 0, int(x), int(y)
        )
        if not ok:
            self._load_xtst()
            assert self._xtst is not None
            self._xtst.XTestFakeMotionEvent(self.display, -1, int(x), int(y), 0)
        self._lib.XFlush(self.display)

    def button_event(self, button: int, pressed: bool) -> None:
        self._load_xtst()
        assert self._xtst is not None
        self._xtst.XTestFakeButtonEvent(
            self.display, c_uint(button), 1 if pressed else 0, 0
        )
        self._lib.XFlush(self.display)

    def click(self, button: int = BUTTON_RIGHT) -> None:
        self.button_event(button, True)
        self.button_event(button, False)

    def click_at(self, x: int, y: int, button: int = BUTTON_RIGHT) -> None:
        self.move_pointer(x, y)
        self.click(button)

    def set_input_focus(self, window: int, revert_to: int = 2) -> None:
        """Give keyboard focus to ``window`` (revert_to 2 = Parent)."""
        self._lib.XSetInputFocus(self.display, c_ulong(window), revert_to, 0)
        self._lib.XFlush(self.display)

    def pointer_position(self) -> tuple[int, int]:
        root_x, root_y = c_int(), c_int()
        mask = c_uint()
        r = c_ulong()
        c = c_ulong()
        self._lib.XQueryPointer(
            self.display, self.root, byref(r), byref(c),
            byref(root_x), byref(root_y),
            byref(c_int()), byref(c_int()), byref(mask),
        )
        return root_x.value, root_y.value

    def has_xtest(self) -> bool:
        try:
            self._load_xtst()
        except OSError:
            return False
        assert self._xtst is not None
        return bool(
            self._xtst.XTestQueryExtension(
                self.display, byref(c_int()), byref(c_int()),
                byref(c_int()), byref(c_int()),
            )
        )

    # --------------------------------------------------------------- windows
    def intern_atom(self, name: str) -> int:
        atom = self._atoms.get(name)
        if atom is None:
            atom = int(self._lib.XInternAtom(self.display, name.encode(), 0))
            self._atoms[name] = atom
        return atom

    def _property(self, window: int, prop: str) -> Optional[bytes]:
        atom = self.intern_atom(prop)
        if not atom:
            return None
        actual_type = c_ulong()
        actual_format = c_int()
        nitems = c_ulong()
        bytes_after = c_ulong()
        data = c_void_p()
        status = self._lib.XGetWindowProperty(
            self.display, c_ulong(window), c_ulong(atom), 0, 1024, 0,
            ANY_PROPERTY_TYPE, byref(actual_type), byref(actual_format),
            byref(nitems), byref(bytes_after), byref(data),
        )
        if status != 0 or not data.value or actual_format.value == 0:
            return None
        try:
            size = nitems.value * (actual_format.value // 8)
            return ctypes.string_at(data, size)
        finally:
            self._lib.XFree(data)

    def window_title(self, window: int) -> str:
        raw = self._property(window, "_NET_WM_NAME")
        if raw:
            return raw.decode("utf-8", "replace").strip("\x00")
        raw = self._property(window, "WM_NAME")
        if raw:
            return raw.decode("utf-8", "replace").strip("\x00")
        return ""

    def window_geometry(self, window: int) -> tuple[int, int, int, int]:
        """Return ``(x, y, width, height)`` in root coordinates."""
        root = c_ulong()
        x, y = c_int(), c_int()
        w, h = c_uint(), c_uint()
        border, depth = c_uint(), c_uint()
        if not self._lib.XGetGeometry(
            self.display, c_ulong(window), byref(root), byref(x), byref(y),
            byref(w), byref(h), byref(border), byref(depth),
        ):
            raise X11Error(f"XGetGeometry failed for window 0x{window:x}")
        dest_x, dest_y = c_int(), c_int()
        self._lib.XTranslateCoordinates(
            self.display, c_ulong(window), self.root, 0, 0,
            byref(dest_x), byref(dest_y), byref(c_ulong()),
        )
        return dest_x.value, dest_y.value, w.value, h.value

    def window_size(self, window: int) -> tuple[int, int]:
        root = c_ulong()
        x, y = c_int(), c_int()
        w, h = c_uint(), c_uint()
        border, depth = c_uint(), c_uint()
        if not self._lib.XGetGeometry(
            self.display, c_ulong(window), byref(root), byref(x), byref(y),
            byref(w), byref(h), byref(border), byref(depth),
        ):
            raise X11Error(f"XGetGeometry failed for window 0x{window:x}")
        return w.value, h.value

    def to_root(self, window: int, x: int, y: int) -> tuple[int, int]:
        dest_x, dest_y = c_int(), c_int()
        self._lib.XTranslateCoordinates(
            self.display, c_ulong(window), self.root, int(x), int(y),
            byref(dest_x), byref(dest_y), byref(c_ulong()),
        )
        return dest_x.value, dest_y.value

    def _children(self, window: int) -> list[int]:
        root, parent = c_ulong(), c_ulong()
        children = POINTER(c_ulong)()
        count = c_uint()
        ok = self._lib.XQueryTree(
            self.display, c_ulong(window), byref(root), byref(parent),
            byref(children), byref(count),
        )
        if not ok:
            return []
        try:
            return [int(children[i]) for i in range(count.value)]
        finally:
            if children:
                self._lib.XFree(children)

    def find_windows(self, needle: str) -> list[tuple[int, str]]:
        """Breadth-first search for windows whose title contains ``needle``."""
        needle = needle.lower()
        found: list[tuple[int, str]] = []
        queue: Iterable[int] = [self.root]
        seen = 0
        while queue and seen < 20000:
            nxt: list[int] = []
            for win in queue:
                seen += 1
                title = self.window_title(win)
                if title and needle in title.lower():
                    found.append((win, title))
                nxt.extend(self._children(win))
            queue = nxt
        return found
