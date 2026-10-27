"""A simple, dependency-free World of Warcraft fishing bot.

Public API::

    from wowfish import Config, FishingBot, X11, find_source, X11Controller

    x11 = X11()
    source = find_source(x11, window_title="World of Warcraft", roi=None)
    bot = FishingBot(Config(), source, X11Controller(x11))
    bot.run()
"""

from .bot import FishingBot
from .capture import CaptureError, CaptureSource, Frame, find_source, parse_roi
from .config import Config, VERSION, dump_default_config, parse_args
from .detect import Bobber, BobberStyle, SplashDetector, find_bobber
from .input import Controller, LoggingController, UinputController, X11Controller
from .uinput import UinputDevice, UinputError, linux_keycode
from .x11 import X11, X11Error

__all__ = [
    "FishingBot", "Config", "VERSION", "parse_args", "dump_default_config",
    "CaptureError", "CaptureSource", "Frame", "find_source", "parse_roi",
    "Bobber", "BobberStyle", "SplashDetector", "find_bobber",
    "Controller", "LoggingController", "UinputController", "X11Controller",
    "UinputDevice", "UinputError", "linux_keycode",
    "X11", "X11Error",
]
