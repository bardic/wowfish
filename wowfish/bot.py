"""The fishing state machine.

The loop mirrors what a human does:

    cast -> wait for the float to land -> watch it -> click on the splash
         -> wait for the loot -> cast again

Each stage has a timeout so a missed cast, a snagged bobber or a fish that got
away simply restarts the cycle instead of hanging forever.
"""

from __future__ import annotations

import math
import random
import time
from typing import Callable, Optional

from .capture import CaptureError, CaptureSource, Frame
from .config import Config
from .detect import Bobber, SplashDetector, find_bobber
from .input import Controller

Rect = tuple[int, int, int, int]


class FishingBot:
    #: how many consecutive failed screen grabs before the bot gives up
    GRAB_RETRIES = 20

    def __init__(
        self,
        config: Config,
        source: CaptureSource,
        controller: Controller,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cfg = config
        self.source = source
        self.controller = controller
        self._log = log or print

        self.style = config.style()
        self.detector = SplashDetector(
            threshold=config.threshold,
            consecutive=config.consecutive,
        )

        self.casts = 0
        self.loots = 0
        self.bites = 0
        self.started = time.monotonic()
        self.stopped = False

        self.bobber: Optional[Bobber] = None
        self.anchor: Optional[tuple[int, int]] = None
        self._rect: Optional[Rect] = None
        self._search: Optional[Rect] = None

    # ------------------------------------------------------------------ api
    def stop(self) -> None:
        self.stopped = True

    def run(self) -> None:
        cfg = self.cfg
        self.log(
            f"capturing {self.source.describe()} | {self.controller.describe()}"
        )
        self.log(
            f"cast key={cfg.cast_key!r} loot button={cfg.loot_button} "
            f"threshold={cfg.threshold} patch={cfg.patch_size}px"
        )
        try:
            while not self.stopped:
                if cfg.max_runtime and self.elapsed() >= cfg.max_runtime:
                    self.log("reached --max-runtime, stopping")
                    break
                if cfg.max_casts and self.casts >= cfg.max_casts:
                    self.log("reached --max-casts, stopping")
                    break
                self.cycle()
        except KeyboardInterrupt:
            self.log("interrupted")
        finally:
            self.summary()

    def cycle(self) -> None:
        self.cast()
        bobber = self.await_bobber()
        if bobber is None:
            self.log("  no bobber found - recasting")
            self.pause(self.cfg.recast_delay)
            return
        if self.await_bite(bobber):
            self.bites += 1
            self.loot()
        else:
            self.log("  no bite before timeout - recasting")
            self.pause(self.cfg.recast_delay)

    # --------------------------------------------------------------- stages
    def cast(self) -> None:
        self.casts += 1
        self.log(f"[cast {self.casts}] casting with key {self.cfg.cast_key!r}")
        self.controller.key_tap(self.cfg.cast_key)
        self.bobber = None
        self.anchor = None
        self._rect = None
        self._search = None
        self.detector.clear()
        self.pause(self.cfg.cast_delay)

    def await_bobber(self) -> Optional[Bobber]:
        deadline = time.monotonic() + self.cfg.bobber_timeout
        while not self.stopped and time.monotonic() < deadline:
            frame = self.grab()
            bobber = find_bobber(frame, None, self.style)
            if bobber:
                self.anchor_to(frame, bobber)
                self.log(
                    f"  bobber at ({bobber.x}, {bobber.y}) "
                    f"[{bobber.pixels}px] - watching"
                )
                return bobber
            self.pause(self.cfg.poll_interval)
        return None

    def await_bite(self, bobber: Bobber) -> bool:
        assert self.anchor is not None and self._rect is not None
        deadline = time.monotonic() + self.cfg.bite_timeout
        while not self.stopped and time.monotonic() < deadline:
            frame = self.grab()
            found = find_bobber(frame, self._search, self.style)
            if found:
                self.bobber = found
                if self._drifted(found):
                    self.anchor_to(frame, found)
                    if self.cfg.debug:
                        self.log(
                            f"  bobber drifted to ({found.x}, {found.y}) "
                            "- re-baselined"
                        )
                    self.pause(self.cfg.poll_interval)
                    continue

            patch = frame.crop(*self._rect)
            fired = self.detector.update(patch, time.monotonic())
            if self.cfg.debug:
                self.log(
                    f"  score={self.detector.score:6.2f} "
                    f"(threshold {self.detector.threshold:.1f})"
                )
            if fired:
                self.log(f"  bite! splash score {self.detector.score:.1f}")
                return True
            self.pause(self.cfg.poll_interval)
        return False

    def loot(self) -> None:
        assert self.bobber is not None
        sx, sy = self.source.to_screen(self.bobber.x, self.bobber.y)
        if self.cfg.loot_key:
            self.log(
                f"  looting with key {self.cfg.loot_key!r} at screen ({sx}, {sy})"
            )
            self.controller.move(sx, sy)
            self.pause(0.05)
            self.controller.key_tap(self.cfg.loot_key)
        else:
            self.log(f"  looting at screen ({sx}, {sy})")
            self.controller.click(sx, sy, self.cfg.loot_button)
        self.loots += 1
        self.pause(self.cfg.loot_delay)
        self.pause(self.cfg.recast_delay)

    # -------------------------------------------------------------- helpers
    def anchor_to(self, frame: Frame, bobber: Bobber) -> None:
        """Fix the watch rectangle and record a fresh baseline."""
        half = self.cfg.patch_size // 2
        x = min(max(bobber.x - half, 0), max(0, frame.width - self.cfg.patch_size))
        y = min(max(bobber.y - half, 0), max(0, frame.height - self.cfg.patch_size))
        self._rect = (x, y, self.cfg.patch_size, self.cfg.patch_size)
        self.anchor = (bobber.x, bobber.y)
        self.bobber = bobber
        self.detector.set_baseline(frame.crop(*self._rect))
        m = self.cfg.search_margin
        self._search = (bobber.x - m, bobber.y - m, bobber.x + m, bobber.y + m)

    def _drifted(self, bobber: Bobber) -> bool:
        if self.anchor is None:
            return True
        dx = bobber.x - self.anchor[0]
        dy = bobber.y - self.anchor[1]
        return math.hypot(dx, dy) > self.cfg.drift_reset

    def grab(self) -> Frame:
        """Grab a frame, tolerating a few transient failures.

        A compositor hiccup or a briefly hidden window is worth retrying, but
        if the window is really gone we would otherwise spin forever, so give
        up after ~10 seconds and let the error propagate.
        """
        for _ in range(self.GRAB_RETRIES):
            try:
                return self.source.grab()
            except CaptureError as exc:
                if self.stopped:
                    raise
                self.log(f"  capture failed ({exc}); retrying")
                time.sleep(0.5)
        raise CaptureError(
            f"capture failed {self.GRAB_RETRIES} times in a row; giving up"
        )

    def pause(self, seconds: float) -> None:
        """Sleep, adding a little randomness so timing is not perfectly rigid."""
        if self.cfg.jitter:
            seconds *= 1.0 + random.uniform(-self.cfg.jitter, self.cfg.jitter)
        # Sleep in slices so Ctrl-C and stop() stay responsive.
        end = time.monotonic() + max(0.0, seconds)
        while not self.stopped:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.1))

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def summary(self) -> None:
        mins, secs = divmod(int(self.elapsed()), 60)
        self.log(
            f"stopped after {mins}m{secs:02d}s - "
            f"{self.casts} casts, {self.bites} bites, {self.loots} loots"
        )

    def log(self, message: str) -> None:
        self._log(f"[{self.elapsed():7.2f}] {message}")
