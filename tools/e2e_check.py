#!/usr/bin/env python3
"""End-to-end check: run the real bot against the fake game window.

This exercises the whole production path - XGetImage capture, bobber colour
detection, splash detection and injected input - without needing World of
Warcraft.  It needs a running X server and (on Wayland) a writable
``/dev/uinput``.

    ./tools/e2e_check.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wowfish import Config, FishingBot, UinputController, UinputDevice, X11
from wowfish.capture import WindowSource

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    display = os.environ.get("DISPLAY", ":0")
    env = dict(os.environ, DISPLAY=display)
    game = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "tools", "fake_game.py"), "700", "500"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        time.sleep(1.5)
        x11 = X11()
        matches = x11.find_windows("wowfish-fake-game")
        if not matches:
            print("FAIL: fake game window not found")
            return 1
        window, title = matches[0]
        source = WindowSource(x11, window, title)

        cfg = Config(
            mode="run", max_casts=3, cast_delay=2.0, loot_delay=1.0,
            recast_delay=0.5, poll_interval=0.1, bobber_timeout=6.0,
            bite_timeout=10.0, jitter=0.0,
        )
        backend = "uinput"
        if backend == "uinput":
            width, height = x11.window_size(x11.root)
            controller = UinputController(UinputDevice(screen=(width, height)))
        bot = FishingBot(cfg, source, controller)
        bot.run()
        controller.close()
        x11.close()

        print()
        print(f"casts={bot.casts} bites={bot.bites} loots={bot.loots}")
        if bot.loots < 1:
            print("FAIL: the bot never looted a bobber")
            return 1
        print("PASS: capture, detection and input all worked end to end")
        return 0
    finally:
        game.terminate()
        try:
            out, _ = game.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            game.kill()
            out = ""
        print("--- fake game ---")
        print(out.strip())


if __name__ == "__main__":
    raise SystemExit(main())
