"""Command line / JSON configuration for the fishing bot."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, fields
from typing import Any, Optional

from .detect import BobberStyle

VERSION = "0.1.0"


@dataclass
class Config:
    # --- what to do -------------------------------------------------------
    mode: str = "run"  # run | calibrate | simulate | self-test | list-windows
    dry_run: bool = False
    debug: bool = False
    max_runtime: float = 0.0  # seconds; 0 = run until Ctrl-C
    max_casts: int = 0  # 0 = unlimited

    # --- where to look ----------------------------------------------------
    display: Optional[str] = None
    window_title: Optional[str] = None
    roi: Optional[str] = None  # "x,y,w,h"

    # --- how to act -------------------------------------------------------
    input_backend: str = "auto"  # auto | x11 | uinput
    cast_key: str = "1"
    loot_button: int = 3  # right mouse button
    loot_key: Optional[str] = None  # e.g. a key bound to "Interact with Mouseover"
    cast_delay: float = 2.6  # settle time after casting
    loot_delay: float = 1.3  # wait after the loot click
    recast_delay: float = 0.7
    bobber_timeout: float = 12.0  # give up looking for the float
    bite_timeout: float = 32.0  # give up waiting for a bite
    poll_interval: float = 0.12  # seconds between frames
    jitter: float = 0.15  # +/- fraction added to every delay

    # --- detection tuning -------------------------------------------------
    patch_size: int = 72
    threshold: float = 26.0
    consecutive: int = 1
    min_pixels: int = 5
    drift_reset: int = 14  # px of drift that triggers a new baseline
    search_margin: int = 90  # search box around the last known float
    r_min: int = 140
    g_max: int = 115
    b_max: int = 115
    dominance: int = 45

    # --- internal ---------------------------------------------------------
    config_path: Optional[str] = field(default=None, repr=False)

    def style(self) -> BobberStyle:
        return BobberStyle(
            r_min=self.r_min, g_max=self.g_max, b_max=self.b_max,
            dominance=self.dominance, min_pixels=self.min_pixels,
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fishbot.py",
        description="A simple, dependency-free World of Warcraft fishing bot.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    p.add_argument(
        "--config", metavar="FILE",
        help="JSON file whose keys are the long option names (without --)",
    )
    p.add_argument(
        "--mode",
        choices=["run", "calibrate", "simulate", "self-test", "list-windows",
                 "check-input"],
        default="run",
        help="run the bot, snapshot the screen, run the offline simulation, "
             "run the built-in self test, list capturable windows, or verify "
             "that keyboard/mouse injection actually reaches the game",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="detect and log but never send input")
    p.add_argument("--debug", action="store_true",
                   help="log per-frame detection scores")

    p.add_argument("--display", help="X display name, e.g. :0")
    p.add_argument("--window-title", help="capture the window whose title contains this")
    p.add_argument("--roi", metavar="X,Y,W,H",
                   help="capture only this rectangle of the root window")

    p.add_argument("--input-backend", choices=["auto", "x11", "uinput"],
                   help="auto prefers uinput on Wayland, XTEST elsewhere")
    p.add_argument("--cast-key", help="key that casts the fishing ability")
    p.add_argument("--loot-button", type=int, help="mouse button used to loot")
    p.add_argument("--loot-key",
                   help="loot by moving the cursor and tapping this key "
                        "(e.g. one bound to 'Interact with Mouseover') "
                        "instead of clicking")
    p.add_argument("--cast-delay", type=float)
    p.add_argument("--loot-delay", type=float)
    p.add_argument("--recast-delay", type=float)
    p.add_argument("--bobber-timeout", type=float)
    p.add_argument("--bite-timeout", type=float)
    p.add_argument("--poll-interval", type=float)
    p.add_argument("--jitter", type=float)

    p.add_argument("--patch-size", type=int)
    p.add_argument("--threshold", type=float,
                   help="mean absolute difference that counts as a splash")
    p.add_argument("--consecutive", type=int,
                   help="frames above threshold required to fire")
    p.add_argument("--min-pixels", type=int)
    p.add_argument("--drift-reset", type=int)
    p.add_argument("--search-margin", type=int)

    p.add_argument("--r-min", type=int)
    p.add_argument("--g-max", type=int)
    p.add_argument("--b-max", type=int)
    p.add_argument("--dominance", type=int)

    p.add_argument("--max-runtime", type=float, help="seconds; 0 = forever")
    p.add_argument("--max-casts", type=int, help="stop after N casts; 0 = forever")
    return p


def parse_args(argv: Optional[list[str]] = None) -> Config:
    parser = build_parser()
    args = parser.parse_args(argv)

    defaults: dict[str, Any] = {}
    if args.config:
        with open(args.config, "r", encoding="utf-8") as fh:
            defaults = json.load(fh)
        if not isinstance(defaults, dict):
            parser.error("config file must contain a JSON object")

    known = {f.name for f in fields(Config)}
    unknown = set(defaults) - known
    if unknown:
        parser.error(f"unknown config keys: {', '.join(sorted(unknown))}")

    values = {k: v for k, v in defaults.items()}
    for key, value in vars(args).items():
        if key == "config" or value is None:
            continue
        values[key] = value
    if args.config:
        values["config_path"] = args.config

    return Config(**values)


def dump_default_config(path: str) -> None:
    cfg = Config()
    data = {f.name: getattr(cfg, f.name) for f in fields(Config)
            if f.name != "config_path"}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
