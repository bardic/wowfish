"""Screen/window capture and the :class:`Frame` pixel buffer."""

from __future__ import annotations

import ctypes
from typing import Optional

from .x11 import X11, X11Error, XImage


class CaptureError(RuntimeError):
    """Raised when a frame could not be grabbed."""


def _trailing_zeros(mask: int) -> int:
    if mask == 0:
        return 0
    return (mask & -mask).bit_length() - 1


def _mask_to_rgb(
    raw: bytes, width: int, height: int, bytes_per_line: int,
    bits_per_pixel: int, red_mask: int, green_mask: int, blue_mask: int,
    byte_order: int,
) -> bytes:
    """Generic (slow) converter for exotic visual formats."""
    step = bits_per_pixel // 8
    r_shift, g_shift, b_shift = (
        _trailing_zeros(red_mask),
        _trailing_zeros(green_mask),
        _trailing_zeros(blue_mask),
    )
    order = "little" if byte_order == 0 else "big"
    out = bytearray(width * height * 3)
    for y in range(height):
        base = y * bytes_per_line
        o = y * width * 3
        for x in range(width):
            off = base + x * step
            value = int.from_bytes(raw[off:off + step], order)
            out[o] = (value & red_mask) >> r_shift
            out[o + 1] = (value & green_mask) >> g_shift
            out[o + 2] = (value & blue_mask) >> b_shift
            o += 3
    return bytes(out)


def _image_to_rgb(image: XImage) -> bytes:
    width, height = image.width, image.height
    bpl = image.bytes_per_line
    raw = ctypes.string_at(image.data, bpl * height) if image.data else b""
    if image.bits_per_pixel == 32 and image.byte_order == 0:
        # The overwhelmingly common case: BGRA/BGRX little-endian.
        out = bytearray(width * height * 3)
        for y in range(height):
            row = raw[y * bpl:y * bpl + width * 4]
            o = y * width * 3
            out[o:o + width * 3:3] = row[2::4]
            out[o + 1:o + width * 3:3] = row[1::4]
            out[o + 2:o + width * 3:3] = row[0::4]
        return bytes(out)
    return _mask_to_rgb(
        raw, width, height, bpl, image.bits_per_pixel,
        image.red_mask, image.green_mask, image.blue_mask, image.byte_order,
    )


class Frame:
    """An RGB24 image held as tightly packed ``bytes`` (row-major)."""

    __slots__ = ("width", "height", "rgb")

    def __init__(self, width: int, height: int, rgb: bytes) -> None:
        if len(rgb) != width * height * 3:
            raise ValueError(
                f"expected {width * height * 3} bytes, got {len(rgb)}"
            )
        self.width = width
        self.height = height
        self.rgb = rgb

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]

    def crop(self, x: int, y: int, width: int, height: int) -> "Frame":
        x0 = max(0, min(int(x), self.width))
        y0 = max(0, min(int(y), self.height))
        x1 = max(x0, min(int(x + width), self.width))
        y1 = max(y0, min(int(y + height), self.height))
        w, h = x1 - x0, y1 - y0
        rows = [
            self.rgb[(yy * self.width + x0) * 3:(yy * self.width + x1) * 3]
            for yy in range(y0, y1)
        ]
        return Frame(w, h, b"".join(rows))

    def patch(self, cx: int, cy: int, size: int) -> "Frame":
        half = size // 2
        return self.crop(cx - half, cy - half, size, size)

    def save(self, path: str) -> None:
        """Write a PNG when Pillow is present, otherwise a plain PPM."""
        try:
            from PIL import Image  # type: ignore

            Image.frombytes("RGB", (self.width, self.height), self.rgb).save(path)
            return
        except ImportError:
            pass
        if not path.lower().endswith(".ppm"):
            path += ".ppm"
        with open(path, "wb") as fh:
            fh.write(b"P6\n%d %d\n255\n" % (self.width, self.height))
            fh.write(self.rgb)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Frame({self.width}x{self.height})"


class CaptureSource:
    """Something the bot can pull frames from and map to screen coordinates."""

    def grab(self) -> Frame:
        raise NotImplementedError

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Map a frame-local coordinate to an absolute screen coordinate."""
        return x, y

    def describe(self) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


class RegionSource(CaptureSource):
    """Capture a fixed rectangle of the root window."""

    def __init__(self, x11: X11, x: int, y: int, width: int, height: int) -> None:
        self.x11 = x11
        self.x, self.y = int(x), int(y)
        self.width, self.height = int(width), int(height)

    def grab(self) -> Frame:
        try:
            image = self.x11.capture(self.x, self.y, self.width, self.height)
        except X11Error as exc:
            raise CaptureError(str(exc)) from exc
        try:
            return Frame(image.contents.width, image.contents.height,
                         _image_to_rgb(image.contents))
        finally:
            self.x11.destroy_image(image)

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        return self.x + x, self.y + y

    def describe(self) -> str:
        return f"root region {self.width}x{self.height}+{self.x}+{self.y}"


class WindowSource(CaptureSource):
    """Capture the client area of an X11/XWayland window."""

    def __init__(self, x11: X11, window: int, title: str = "") -> None:
        self.x11 = x11
        self.window = int(window)
        self.title = title

    def grab(self) -> Frame:
        try:
            image = self.x11.capture_window(self.window)
        except X11Error as exc:
            raise CaptureError(str(exc)) from exc
        try:
            return Frame(image.contents.width, image.contents.height,
                         _image_to_rgb(image.contents))
        finally:
            self.x11.destroy_image(image)

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        return self.x11.to_root(self.window, x, y)

    def describe(self) -> str:
        return f"window 0x{self.window:x} {self.title!r}"


def full_screen_source(x11: X11) -> RegionSource:
    width, height = x11.window_size(x11.root)
    return RegionSource(x11, 0, 0, width, height)


def parse_roi(text: str) -> tuple[int, int, int, int]:
    """Parse ``x,y,w,h`` (or ``x,y,x2,y2`` is *not* accepted)."""
    parts = [p.strip() for p in text.replace("x", ",").split(",") if p.strip()]
    if len(parts) != 4:
        raise ValueError("ROI must look like x,y,w,h")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def find_source(
    x11: X11, window_title: Optional[str], roi: Optional[str]
) -> CaptureSource:
    if window_title:
        matches = x11.find_windows(window_title)
        if not matches:
            raise CaptureError(f"no window matching {window_title!r}")
        window, title = matches[0]
        source: CaptureSource = WindowSource(x11, window, title)
    elif roi:
        source = RegionSource(x11, *parse_roi(roi))
    else:
        source = full_screen_source(x11)
    return source
