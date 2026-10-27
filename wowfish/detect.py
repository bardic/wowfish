"""Bobber tracking and bite ("splash") detection.

Everything here works on plain :class:`~wowfish.capture.Frame` buffers so the
logic can be unit-tested with synthetic images and no game running.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import NamedTuple, Optional

from .capture import Frame


class Bobber(NamedTuple):
    """Centroid of the pixels that look like a fishing bobber."""

    x: int
    y: int
    pixels: int
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.x1, self.y1


@dataclass
class BobberStyle:
    """Colour rule for "this pixel is part of the bobber".

    Defaults match the classic red-and-white float: strongly red, weakly green
    and blue.  Tune with ``fishbot.py --calibrate``.
    """

    r_min: int = 140
    g_max: int = 115
    b_max: int = 115
    dominance: int = 45  # r - max(g, b)
    min_pixels: int = 5


# Per-channel lookup tables mapping a byte to 1/0 for "passes this test".
# ``bytes.translate`` then does the filtering at C speed, so the Python loop
# only visits pixels that already look red instead of all ~8M of a 4K frame.
_RED_TABLES: dict[int, bytes] = {}


def _red_table(r_min: int) -> bytes:
    table = _RED_TABLES.get(r_min)
    if table is None:
        table = bytes(1 if i >= r_min else 0 for i in range(256))
        _RED_TABLES[r_min] = table
    return table


def find_bobber(
    frame: Frame,
    bounds: Optional[tuple[int, int, int, int]] = None,
    style: Optional[BobberStyle] = None,
) -> Optional[Bobber]:
    """Return the centroid of bobber-coloured pixels, or ``None``.

    ``bounds`` is an inclusive-exclusive ``(x0, y0, x1, y1)`` window used to
    ignore UI chrome such as the action bar or a red minimap blip.
    """
    style = style or BobberStyle()
    x0, y0, x1, y1 = bounds or (0, 0, frame.width, frame.height)
    x0 = max(0, min(x0, frame.width))
    y0 = max(0, min(y0, frame.height))
    x1 = max(x0, min(x1, frame.width))
    y1 = max(y0, min(y1, frame.height))
    if x1 <= x0 or y1 <= y0:
        return None

    if (x0, y0, x1, y1) == (0, 0, frame.width, frame.height):
        sub = frame
    else:
        sub = frame.crop(x0, y0, x1 - x0, y1 - y0)

    rgb = sub.rgb
    width = sub.width
    mask = rgb[0::3].translate(_red_table(style.r_min))

    g_max, b_max = style.g_max, style.b_max
    dominance = style.dominance
    count = 0
    sum_x = 0
    sum_y = 0
    min_x = min_y = 1 << 30
    max_x = max_y = -1

    pos = mask.find(1)
    while pos != -1:
        i = pos * 3
        g = rgb[i + 1]
        if g <= g_max:
            b = rgb[i + 2]
            r = rgb[i]
            if b <= b_max and r - (g if g > b else b) >= dominance:
                x = pos % width
                y = pos // width
                count += 1
                sum_x += x
                sum_y += y
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
        pos = mask.find(1, pos + 1)

    if count < style.min_pixels:
        return None
    return Bobber(
        sum_x // count + x0, sum_y // count + y0, count,
        min_x + x0, min_y + y0, max_x + 1 + x0, max_y + 1 + y0,
    )


def mean_abs_diff(a: bytes, b: bytes) -> float:
    """Mean absolute per-channel difference between two equally sized buffers."""
    if len(a) != len(b):
        raise ValueError("buffers must be the same size")
    if not a:
        return 0.0
    total = sum(map(abs, map(operator.sub, a, b)))
    return total / len(a)


def changed_ratio(a: bytes, b: bytes, delta: int = 45) -> float:
    """Fraction of channels that differ by more than ``delta``."""
    if len(a) != len(b):
        raise ValueError("buffers must be the same size")
    if not a:
        return 0.0
    hits = 0
    for i in range(0, len(a), 3):
        if abs(a[i] - b[i]) > delta:
            hits += 1
    return hits / (len(a) / 3)


class SplashDetector:
    """Detect the bite by comparing live frames to a settled baseline.

    A bobber riding the water bobs gently, producing a small mean difference.
    When a fish bites, the float is yanked under and foam erupts: a large,
    short-lived jump in the same metric.  Comparing against a baseline recorded
    right after the cast cleanly separates the two.
    """

    def __init__(
        self, threshold: float = 26.0, consecutive: int = 1,
        cooldown: float = 1.0,
    ) -> None:
        self.threshold = threshold
        self.consecutive = max(1, consecutive)
        self.cooldown = cooldown
        self.baseline: Optional[bytes] = None
        self.score = 0.0
        self._strikes = 0
        self._last_fire = float("-inf")

    def set_baseline(self, patch: Frame) -> None:
        self.baseline = patch.rgb
        self._strikes = 0
        self.score = 0.0

    def clear(self) -> None:
        """Forget the baseline; called at the start of every cast."""
        self.baseline = None
        self._strikes = 0
        self._last_fire = float("-inf")

    def update(self, patch: Frame, now: float) -> bool:
        """Feed a fresh patch; return ``True`` on the frame the bite is seen."""
        if self.baseline is None or len(patch.rgb) != len(self.baseline):
            self.set_baseline(patch)
            return False

        self.score = mean_abs_diff(self.baseline, patch.rgb)
        if self.score < self.threshold:
            self._strikes = 0
            return False

        self._strikes += 1
        if self._strikes < self.consecutive:
            return False
        if now - self._last_fire < self.cooldown:
            return False

        self._last_fire = now
        self._strikes = 0
        return True
