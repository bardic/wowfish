"""An offline simulator so the whole loop can be exercised without WoW.

It renders a small patch of "water" with a red float, makes the float bite on a
timer, and reacts to the bot's cast/loot input.  This is what ``--mode
simulate`` runs, and it is also what the self-test drives.
"""

from __future__ import annotations

import math
import random
import time
from typing import Callable, Optional

from .capture import CaptureSource, Frame
from .input import Controller

WATER_TOP = (24, 74, 132)
WATER_BOTTOM = (12, 44, 92)
BOBBER_RED = (206, 40, 34)
BOBBER_WHITE = (238, 238, 230)
SPLASH = (245, 250, 255)


class SimWorld:
    def __init__(
        self, width: int = 360, height: int = 260,
        seed: Optional[int] = None,
        bite_range: tuple[float, float] = (1.5, 3.0),
        escape_after: float = 5.0,
    ) -> None:
        self.width = width
        self.height = height
        self.rng = random.Random(seed)
        self.bite_range = bite_range
        self.escape_after = escape_after

        self.background = self._make_water()
        self.t = 0.0
        self.phase = "idle"  # idle | waiting | bite | looted
        self.bobber: Optional[tuple[float, float]] = None
        self.splash_until = -1.0
        self.bite_at = 0.0
        self.bite_started = -1.0
        self.pointer: tuple[int, int] = (0, 0)

        self.casts = 0
        self.bites = 0
        self.loots = 0
        self.escapes = 0

    # ------------------------------------------------------------- painting
    def _make_water(self) -> bytes:
        buf = bytearray(self.width * self.height * 3)
        rng = random.Random(1234)
        for y in range(self.height):
            f = y / max(1, self.height - 1)
            base = tuple(
                int(WATER_TOP[i] + (WATER_BOTTOM[i] - WATER_TOP[i]) * f)
                for i in range(3)
            )
            row = y * self.width * 3
            for x in range(self.width):
                n = rng.randint(-6, 6)
                i = row + x * 3
                buf[i] = max(0, min(255, base[0] + n))
                buf[i + 1] = max(0, min(255, base[1] + n))
                buf[i + 2] = max(0, min(255, base[2] + n))
        return bytes(buf)

    @staticmethod
    def _blit(buf: bytearray, width: int, height: int, cx: int, cy: int,
              radius: int, colour: tuple[int, int, int]) -> None:
        r2 = radius * radius
        for dy in range(-radius, radius + 1):
            y = cy + dy
            if y < 0 or y >= height:
                continue
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy > r2:
                    continue
                x = cx + dx
                if x < 0 or x >= width:
                    continue
                i = (y * width + x) * 3
                buf[i] = colour[0]
                buf[i + 1] = colour[1]
                buf[i + 2] = colour[2]

    # -------------------------------------------------------------- control
    def on_cast(self) -> None:
        if self.phase in ("waiting", "bite"):
            return  # already fishing
        self.casts += 1
        margin = 60
        bx = self.rng.randint(margin, self.width - margin)
        by = self.rng.randint(margin, self.height - margin)
        self.bobber = (float(bx), float(by))
        self.phase = "waiting"
        self.bite_at = self.t + self.rng.uniform(*self.bite_range)
        self.bite_started = -1.0

    def on_click(self, x: int, y: int, button: int = 3) -> bool:
        if self.bobber is None or self.phase not in ("waiting", "bite"):
            return False
        bx, by = self.bobber
        if math.hypot(x - bx, y - by) > 28:
            return False
        self.phase = "looted"
        self.bobber = None
        self.loots += 1
        return True

    def update(self, dt: float) -> None:
        self.t += dt
        if self.phase == "waiting" and self.t >= self.bite_at:
            self.phase = "bite"
            self.bite_started = self.t
            self.splash_until = self.t + 0.6
            self.bites += 1
        elif (
            self.phase == "bite"
            and self.bite_started >= 0
            and self.t - self.bite_started > self.escape_after
        ):
            self.phase = "idle"
            self.bobber = None
            self.escapes += 1

    # --------------------------------------------------------------- render
    def render(self) -> Frame:
        buf = bytearray(self.background)
        if self.bobber is not None:
            bx, by = self.bobber
            # gentle bob so the detector must tolerate idle motion
            wobble = math.sin(self.t * 2.4) * 1.2
            cx, cy = int(bx), int(by + wobble)
            if self.phase == "bite" and self.t < self.splash_until:
                self._blit(buf, self.width, self.height, cx, cy + 2, 22, SPLASH)
            self._blit(buf, self.width, self.height, cx, cy, 5, BOBBER_RED)
            self._blit(buf, self.width, self.height, cx, cy - 3, 2, BOBBER_WHITE)
        return Frame(self.width, self.height, bytes(buf))

    def stats(self) -> str:
        return (
            f"sim: {self.casts} casts, {self.bites} bites, "
            f"{self.loots} loots, {self.escapes} escapes"
        )


class SimulatedSource(CaptureSource):
    def __init__(self, world: SimWorld) -> None:
        self.world = world
        self._last = time.monotonic()

    def grab(self) -> Frame:
        now = time.monotonic()
        self.world.update(now - self._last)
        self._last = now
        return self.world.render()

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        return x, y

    def describe(self) -> str:
        return f"simulated {self.world.width}x{self.world.height} water"


class SimulatedController(Controller):
    def __init__(
        self, world: SimWorld, log: Callable[[str], None],
        cast_key: str = "1", loot_key: Optional[str] = None,
    ) -> None:
        self.world = world
        self.log = log
        self.cast_key = cast_key
        self.loot_key = loot_key

    def key_tap(self, name: str) -> None:
        if self.loot_key and name == self.loot_key:
            x, y = self.world.pointer
            if self.world.on_click(x, y):
                return
        self.world.on_cast()

    def move(self, x: int, y: int) -> None:
        self.world.pointer = (x, y)

    def click(self, x: int, y: int, button: int = 3) -> None:
        if not self.world.on_click(x, y, button):
            self.log(f"  [sim] click at ({x}, {y}) missed the float")

    def describe(self) -> str:
        return "simulated input"
