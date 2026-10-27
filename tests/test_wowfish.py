from __future__ import annotations

import unittest

from wowfish.bot import FishingBot
from wowfish.capture import Frame
from wowfish.config import Config
from wowfish.detect import (
    SplashDetector,
    changed_ratio,
    find_bobber,
    mean_abs_diff,
)
from wowfish.simulate import SimWorld, SimulatedController, SimulatedSource
from wowfish.uinput import UinputDevice, UinputError, linux_keycode

WATER = (20, 60, 120)
RED = (205, 35, 30)


def solid(width: int, height: int, colour: tuple[int, int, int]) -> Frame:
    return Frame(width, height, bytes(colour) * (width * height))


def with_disc(
    width: int, height: int, background: tuple[int, int, int],
    colour: tuple[int, int, int], cx: int, cy: int, radius: int,
) -> Frame:
    buf = bytearray(bytes(background) * (width * height))
    for y in range(height):
        for x in range(width):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                i = (y * width + x) * 3
                buf[i:i + 3] = bytes(colour)
    return Frame(width, height, bytes(buf))


class TestBobberDetection(unittest.TestCase):
    def test_finds_red_float(self) -> None:
        frame = with_disc(200, 150, WATER, RED, 120, 80, 6)
        bobber = find_bobber(frame)
        self.assertIsNotNone(bobber)
        assert bobber is not None
        self.assertLess(abs(bobber.x - 120), 3)
        self.assertLess(abs(bobber.y - 80), 3)
        self.assertGreaterEqual(bobber.pixels, 5)

    def test_bounds_offset_the_result(self) -> None:
        frame = with_disc(200, 150, WATER, RED, 120, 80, 6)
        bobber = find_bobber(frame, (100, 60, 160, 110))
        self.assertIsNotNone(bobber)
        assert bobber is not None
        self.assertEqual((bobber.x, bobber.y), (120, 80))
        self.assertGreaterEqual(bobber.x0, 100)
        self.assertLess(bobber.y1, 110)

    def test_empty_water_has_no_float(self) -> None:
        self.assertIsNone(find_bobber(solid(100, 100, WATER)))

    def test_bounds_ignore_ui_chrome(self) -> None:
        # A red blob at the bottom (action bar) must not be mistaken for the
        # float when the search region excludes it.
        frame = with_disc(200, 200, WATER, RED, 100, 190, 8)
        self.assertIsNotNone(find_bobber(frame))
        self.assertIsNone(find_bobber(frame, (0, 0, 200, 150)))

    def test_white_pixels_are_not_a_float(self) -> None:
        frame = solid(80, 80, (240, 240, 240))
        self.assertIsNone(find_bobber(frame))


class TestFrame(unittest.TestCase):
    def test_crop_clamps_to_bounds(self) -> None:
        frame = with_disc(100, 100, WATER, RED, 10, 10, 5)
        crop = frame.crop(-20, -20, 50, 50)
        self.assertEqual((crop.width, crop.height), (30, 30))
        crop = frame.crop(90, 90, 50, 50)
        self.assertEqual((crop.width, crop.height), (10, 10))

    def test_patch_is_square_and_centred(self) -> None:
        frame = with_disc(100, 100, WATER, RED, 50, 50, 3)
        patch = frame.patch(50, 50, 20)
        self.assertEqual((patch.width, patch.height), (20, 20))

    def test_pixel_roundtrip(self) -> None:
        frame = with_disc(20, 20, WATER, RED, 5, 5, 2)
        self.assertEqual(frame.pixel(5, 5), RED)


class TestMetrics(unittest.TestCase):
    def test_mean_abs_diff_zero_for_identical(self) -> None:
        self.assertEqual(mean_abs_diff(b"\x01\x02\x03", b"\x01\x02\x03"), 0.0)

    def test_mean_abs_diff_value(self) -> None:
        self.assertAlmostEqual(mean_abs_diff(b"\x00\x00\x00", b"\x0a\x00\x00"), 10 / 3)

    def test_changed_ratio(self) -> None:
        self.assertEqual(changed_ratio(b"\x00\x00\x00", b"\xff\xff\xff", 10), 1.0)


class TestSplashDetector(unittest.TestCase):
    def test_ignores_small_idle_motion(self) -> None:
        detector = SplashDetector(threshold=26.0)
        base = with_disc(72, 72, WATER, RED, 36, 36, 5)
        detector.set_baseline(base)
        nudged = with_disc(72, 72, WATER, RED, 37, 36, 5)
        self.assertFalse(detector.update(nudged, 0.0))
        self.assertLess(detector.score, 26.0)

    def test_fires_on_splash(self) -> None:
        detector = SplashDetector(threshold=26.0)
        base = with_disc(72, 72, WATER, RED, 36, 36, 5)
        detector.set_baseline(base)
        splash = solid(72, 72, (245, 250, 255))
        self.assertTrue(detector.update(splash, 0.0))
        self.assertGreater(detector.score, 26.0)

    def test_consecutive_requirement(self) -> None:
        detector = SplashDetector(threshold=26.0, consecutive=2)
        base = with_disc(72, 72, WATER, RED, 36, 36, 5)
        detector.set_baseline(base)
        splash = solid(72, 72, (245, 250, 255))
        self.assertFalse(detector.update(splash, 0.0))
        self.assertTrue(detector.update(splash, 0.01))


class TestUinputMapping(unittest.TestCase):
    def test_common_keys(self) -> None:
        self.assertEqual(linux_keycode("1"), 2)
        self.assertEqual(linux_keycode("0"), 11)
        self.assertEqual(linux_keycode("A"), 30)
        self.assertEqual(linux_keycode("space"), 57)
        self.assertEqual(linux_keycode("F1"), 59)
        self.assertEqual(linux_keycode("f12"), 88)

    def test_unknown_key_raises(self) -> None:
        with self.assertRaises(UinputError):
            linux_keycode("nosuchkey")

    def test_missing_uinput_device_is_reported(self) -> None:
        with self.assertRaises(UinputError):
            UinputDevice(path="/dev/definitely-not-uinput")


class TestConfig(unittest.TestCase):
    def test_style_is_built_from_thresholds(self) -> None:
        cfg = Config(r_min=200, g_max=10, b_max=20, dominance=150, min_pixels=9)
        style = cfg.style()
        self.assertEqual(
            (style.r_min, style.g_max, style.b_max, style.dominance,
             style.min_pixels),
            (200, 10, 20, 150, 9),
        )

    def test_parser_reads_json_and_cli(self) -> None:
        import json
        import tempfile
        from wowfish.config import parse_args

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"cast_key": "5", "threshold": 40}, fh)
            path = fh.name
        try:
            cfg = parse_args(["--config", path, "--threshold", "55"])
            self.assertEqual(cfg.cast_key, "5")      # from the file
            self.assertEqual(cfg.threshold, 55.0)     # CLI wins
        finally:
            import os
            os.unlink(path)


class TestSimulatedCycle(unittest.TestCase):
    def _run(self, casts: int = 3) -> tuple[FishingBot, SimWorld]:
        cfg = Config(
            mode="simulate",
            cast_delay=0.05,
            loot_delay=0.02,
            recast_delay=0.02,
            poll_interval=0.01,
            jitter=0.0,
            bobber_timeout=2.0,
            bite_timeout=3.0,
            max_casts=casts,
            threshold=26.0,
            patch_size=72,
        )
        world = SimWorld(seed=1, bite_range=(0.05, 0.15))
        source = SimulatedSource(world)
        controller = SimulatedController(world, lambda _m: None)
        bot = FishingBot(cfg, source, controller, log=lambda _m: None)
        bot.run()
        return bot, world

    def test_completes_casts_and_loots(self) -> None:
        bot, world = self._run()
        self.assertGreaterEqual(bot.casts, 3)
        self.assertGreaterEqual(bot.bites, 1)
        self.assertGreaterEqual(bot.loots, 1)
        self.assertEqual(bot.loots, world.loots)

    def test_loot_key_instead_of_click(self) -> None:
        cfg = Config(
            mode="simulate", cast_delay=0.05, loot_delay=0.02,
            recast_delay=0.02, poll_interval=0.01, jitter=0.0,
            bobber_timeout=2.0, bite_timeout=3.0, max_casts=2,
            loot_key="f",
        )
        world = SimWorld(seed=3, bite_range=(0.05, 0.15))
        controller = SimulatedController(world, lambda _m: None, "1", "f")
        bot = FishingBot(cfg, SimulatedSource(world), controller, log=lambda _m: None)
        bot.run()
        self.assertGreaterEqual(bot.loots, 1)
        self.assertEqual(world.loots, bot.loots)

    def test_dry_run_sends_no_input(self) -> None:
        from wowfish.input import LoggingController

        # The dry-run controller never drives the simulated world, so the bot
        # walks the timeout paths instead of catching fish. Keep them short.
        cfg = Config(
            mode="simulate", max_casts=1, cast_delay=0.01,
            bobber_timeout=0.2, bite_timeout=0.2, poll_interval=0.01,
        )
        world = SimWorld(seed=2)
        source = SimulatedSource(world)
        controller = LoggingController(lambda _m: None)
        bot = FishingBot(cfg, source, controller, log=lambda _m: None)
        bot.run()
        self.assertEqual(world.casts, 0)
        self.assertTrue(controller.actions)
        self.assertEqual(controller.actions[0], ("key", "1"))


if __name__ == "__main__":
    unittest.main()
