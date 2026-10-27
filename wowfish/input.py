"""Input backends. The bot only ever needs "tap a key" and "click a point"."""

from __future__ import annotations

import time
from typing import Callable

from .uinput import UinputDevice
from .x11 import BUTTON_RIGHT, X11


class Controller:
    """Interface the bot programs against."""

    def key_tap(self, name: str) -> None:
        raise NotImplementedError

    def move(self, x: int, y: int) -> None:
        raise NotImplementedError

    def click(self, x: int, y: int, button: int = BUTTON_RIGHT) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


class X11Controller(Controller):
    """Real input via the XTEST extension.

    WoW (through Wine/Proton) samples the pointer position when the button
    event arrives, so we move first and leave a beat before pressing.
    """

    def __init__(self, x11: X11, settle: float = 0.035) -> None:
        self.x11 = x11
        self.settle = settle

    def key_tap(self, name: str) -> None:
        self.x11.tap_key(name)

    def move(self, x: int, y: int) -> None:
        self.x11.move_pointer(x, y)

    def click(self, x: int, y: int, button: int = BUTTON_RIGHT) -> None:
        self.x11.move_pointer(x, y)
        time.sleep(self.settle)
        self.x11.button_event(button, True)
        time.sleep(self.settle)
        self.x11.button_event(button, False)

    def describe(self) -> str:
        return "X11/XTEST input"


class LoggingController(Controller):
    """Dry-run controller: records what *would* have happened."""

    def __init__(self, log: Callable[[str], None]) -> None:
        self.log = log
        self.actions: list[tuple[str, object]] = []

    def key_tap(self, name: str) -> None:
        self.actions.append(("key", name))
        self.log(f"  [dry-run] tap key {name!r}")

    def move(self, x: int, y: int) -> None:
        self.actions.append(("move", (x, y)))
        self.log(f"  [dry-run] move to ({x}, {y})")

    def click(self, x: int, y: int, button: int = BUTTON_RIGHT) -> None:
        self.actions.append(("click", (x, y, button)))
        self.log(f"  [dry-run] click button {button} at ({x}, {y})")

    def describe(self) -> str:
        return "dry-run (no input sent)"


class UinputController(Controller):
    """Real input through a kernel uinput device.

    This is the reliable choice under a rootless Wayland compositor, where
    XTEST keyboard events work but synthetic mouse events do not.
    """

    def __init__(self, device: "UinputDevice", settle: float = 0.03) -> None:
        self.device = device
        self.settle = settle

    def key_tap(self, name: str) -> None:
        self.device.key(name, True)
        self.device.key(name, False)

    def move(self, x: int, y: int) -> None:
        self.device.move_abs(x, y)

    def click(self, x: int, y: int, button: int = BUTTON_RIGHT) -> None:
        self.device.move_abs(x, y)
        time.sleep(self.settle)
        self.device.button(button, True)
        time.sleep(self.settle)
        self.device.button(button, False)

    def describe(self) -> str:
        return "uinput virtual mouse/keyboard"

    def close(self) -> None:
        self.device.close()
