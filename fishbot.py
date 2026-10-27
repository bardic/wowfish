#!/usr/bin/env python3
"""Entry point for the fishing bot.

    ./fishbot.py --mode list-windows
    ./fishbot.py --mode calibrate --window-title "World of Warcraft"
    ./fishbot.py --mode simulate --max-casts 3
    ./fishbot.py --mode run --window-title "World of Warcraft" --debug
"""

from __future__ import annotations

import os
import signal
import sys
import time
from typing import Optional

from wowfish import (
    Config,
    FishingBot,
    Frame,
    UinputController,
    UinputDevice,
    UinputError,
    X11,
    X11Controller,
    X11Error,
    dump_default_config,
    find_source,
    find_bobber,
)
from wowfish.capture import CaptureError
from wowfish.detect import mean_abs_diff
from wowfish.input import Controller, LoggingController
from wowfish.simulate import SimWorld, SimulatedController, SimulatedSource


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------- modes
def mode_list_windows(cfg: Config) -> int:
    with X11(cfg.display) as x11:
        windows = x11.find_windows("")
        if not windows:
            log("no titled windows found")
            return 1
        log(f"{'window':>12}  {'geometry':>20}  title")
        for win, title in windows:
            try:
                gx, gy, gw, gh = x11.window_geometry(win)
            except X11Error:
                continue
            log(f"0x{win:010x}  {gw:4d}x{gh:<4d}+{gx:<5d}+{gy:<5d}  {title[:70]}")
    return 0


def mode_calibrate(cfg: Config) -> int:
    with X11(cfg.display) as x11:
        source = find_source(x11, cfg.window_title, cfg.roi)
        log(f"capturing {source.describe()}")
        first = source.grab()
        time.sleep(0.3)
        second = source.grab()

    bobber = find_bobber(first, None, cfg.style())
    idle_noise = mean_abs_diff(first.rgb, second.rgb)
    log(f"frame {first.width}x{first.height}")

    if bobber:
        log(
            f"bobber found at ({bobber.x}, {bobber.y}) "
            f"box={bobber.box} pixels={bobber.pixels}"
        )
        annotated = draw_rect(first, bobber.box, (0, 255, 0), 2)
        out = "calibration.png"
        annotated.save(out)
        log(f"annotated snapshot written to {out}")
    else:
        log("no bobber found - adjust --r-min/--g-max/--b-max/--dominance")
        first.save("calibration.png")
        log("raw snapshot written to calibration.png")

    log(f"idle frame-to-frame difference: {idle_noise:.2f}")
    suggested = max(10.0, idle_noise * 4.0)
    log(f"suggested --threshold is at least {suggested:.1f} (default 26.0)")
    return 0 if bobber else 2


def mode_simulate(cfg: Config) -> int:
    world = SimWorld(seed=7, bite_range=(0.8, 1.6))
    source = SimulatedSource(world)
    # The simulation needs input to progress, so --dry-run is not meaningful
    # here; the simulated controller is already harmless.
    controller = SimulatedController(world, log, cfg.cast_key, cfg.loot_key)

    if not cfg.max_casts and not cfg.max_runtime:
        # Nothing was requested, so this is the "just show me" path: run three
        # casts at a snappy pace instead of the realistic one.
        defaults = Config()
        fast = {"cast_delay": 0.9, "loot_delay": 0.4, "recast_delay": 0.3,
                "poll_interval": 0.08}
        for name, value in fast.items():
            if getattr(cfg, name) == getattr(defaults, name):
                setattr(cfg, name, value)
        cfg.max_casts = 3
        log("(simulation) 3 casts at a fast pace; "
            "use --max-casts/--max-runtime/delays to change")

    bot = FishingBot(cfg, source, controller, log)
    install_signal_handlers(bot)
    bot.run()
    log(world.stats())
    return 0


def resolve_backend(cfg: Config) -> str:
    """Pick an input backend, preferring uinput on Wayland.

    Under a rootless Wayland compositor XTEST delivers keystrokes but drops
    synthetic mouse input, so uinput (which sits below the compositor) is the
    only backend that can actually click the bobber.
    """
    if cfg.input_backend != "auto":
        return cfg.input_backend
    on_wayland = (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )
    if on_wayland and os.access("/dev/uinput", os.W_OK):
        return "uinput"
    return "x11"


def build_controller(cfg: Config, x11: X11) -> Controller:
    if cfg.dry_run:
        return LoggingController(log)
    backend = resolve_backend(cfg)
    if backend == "uinput":
        width, height = x11.window_size(x11.root)
        try:
            return UinputController(UinputDevice(screen=(width, height)))
        except UinputError as exc:
            log(f"uinput unavailable ({exc}); falling back to X11/XTEST")
    return X11Controller(x11)


def mode_check_input(cfg: Config) -> int:
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    log(f"session type     : {session}")
    log(f"DISPLAY          : {os.environ.get('DISPLAY', '(unset)')}")
    log(f"WAYLAND_DISPLAY  : {os.environ.get('WAYLAND_DISPLAY', '(unset)')}")
    log(f"/dev/uinput      : {'writable' if os.access('/dev/uinput', os.W_OK) else 'not writable'}")

    x11 = X11(cfg.display)
    try:
        width, height = x11.window_size(x11.root)
        log(f"root window      : {width}x{height}")
        log(f"XTEST extension  : {'yes' if x11.has_xtest() else 'no'}")
        backend = resolve_backend(cfg)
        log(f"chosen backend   : {backend}")
        controller = build_controller(cfg, x11)
        log(f"controller       : {controller.describe()}")
        log("moving the cursor to (12, 12) ...")
        controller.move(12, 12)
        time.sleep(0.3)
        log(
            "if your cursor is now in the top-left corner, input works. "
            "X11 thinks it is at " + str(x11.pointer_position())
        )
        log("note: on Wayland, X11's pointer position can be stale; trust your eyes.")
        controller.close()
    finally:
        x11.close()
    return 0


def mode_self_test(cfg: Config) -> int:
    import pathlib
    import unittest

    root = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests"), top_level_dir=str(root)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def mode_run(cfg: Config) -> int:
    x11 = X11(cfg.display)
    controller: Controller = LoggingController(log)
    try:
        source = find_source(x11, cfg.window_title, cfg.roi)
        controller = build_controller(cfg, x11)
        bot = FishingBot(cfg, source, controller, log)
        install_signal_handlers(bot)
        bot.run()
    finally:
        controller.close()
        x11.close()
    return 0


# ------------------------------------------------------------------- helpers
def draw_rect(frame: Frame, rect: tuple[int, int, int, int],
              colour: tuple[int, int, int], thickness: int = 2) -> Frame:
    buf = bytearray(frame.rgb)
    x, y, w, h = rect
    for t in range(thickness):
        for px in range(max(0, x - t), min(frame.width, x + w + t)):
            for py in (y - t, y + h - 1 + t):
                if 0 <= py < frame.height:
                    i = (py * frame.width + px) * 3
                    buf[i:i + 3] = bytes(colour)
        for py in range(max(0, y - t), min(frame.height, y + h + t)):
            for px in (x - t, x + w - 1 + t):
                if 0 <= px < frame.width:
                    i = (py * frame.width + px) * 3
                    buf[i:i + 3] = bytes(colour)
    return Frame(frame.width, frame.height, bytes(buf))


def install_signal_handlers(bot: FishingBot) -> None:
    state = {"count": 0}

    def handler(signum: int, frame: object) -> None:
        state["count"] += 1
        if state["count"] == 1:
            log("\nstopping after the current step (Ctrl-C again to force quit)")
            bot.stop()
        else:
            os._exit(130)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


MODES = {
    "run": mode_run,
    "calibrate": mode_calibrate,
    "simulate": mode_simulate,
    "self-test": mode_self_test,
    "list-windows": mode_list_windows,
    "check-input": mode_check_input,
}


def main(argv: Optional[list[str]] = None) -> int:
    from wowfish import parse_args

    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--dump-config"]:
        target = argv[1] if len(argv) > 1 else "fishbot.config.json"
        dump_default_config(target)
        log(f"wrote {target}")
        return 0

    cfg = parse_args(argv)
    return MODES[cfg.mode](cfg)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (X11Error, CaptureError, UinputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
