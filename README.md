# wowfish

A small, dependency-free World of Warcraft fishing bot.

It watches a patch of the screen for the red float, waits for the splash that
means a fish is biting, clicks the float, and casts again. Everything is plain
Python + `ctypes`: no OpenCV, no numpy, no `pynput`, no `mss`, no `pip install`.

> **Heads up** — automating the game violates Blizzard's Terms of Service and
> can get your account banned. This project exists to show how screen-scraping
> bots work. Use it on a private server, a throwaway account, or not at all.

---

## How it works

```
        cast (key)                watch the float             bite!
   you ─────────────▶  bobber ───────────────────▶ splash ─────────▶ click
                         ▲                                            │
                         └──────────────── recast ◀───────────────────┘
```

1. **Cast** — taps your fishing keybind.
2. **Find the bobber** — scans the frame for strongly-red, weakly-green/blue
   pixels (the float) and takes their centroid.
3. **Watch** — records a 72×72 patch around the float as a baseline and each
   frame measures the mean absolute pixel difference against it. The float
   bobbing gently on the water is a difference of ~1; a bite erupting in foam
   is 100+.
4. **Loot** — right-clicks the float (or moves the cursor there and taps an
   `Interact with Mouseover` key).
5. Repeat until you press Ctrl-C.

Detection is deliberately simple and fast: a full 4K frame is scanned in
~15 ms, and a restricted region (`--roi`) in well under a millisecond.

---

## Requirements

* Python 3.9+
* A running X server (`DISPLAY`). WoW under Proton runs through XWayland, so
  this works on both X11 and Wayland desktops.
* For reliable **mouse** input on Wayland: access to `/dev/uinput`.
  On X11 nothing extra is needed.

### Wayland note (important)

Rootless Wayland compositors do not pass synthetic mouse input through to
XWayland. Concretely, on KWin/Wayland:

| action | XTEST (`--input-backend x11`) | uinput |
|---|---|---|
| key press (cast) | ✅ works | ✅ works |
| pointer move | ⚠️ changes X's idea of the pointer, not the real cursor | ✅ works |
| mouse click (loot) | ❌ never arrives | ✅ works |

So on Wayland the bot automatically prefers the **uinput** backend. Give it
access with a udev rule:

```sh
# /etc/udev/rules.d/99-uinput.rules
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
```

```sh
sudo usermod -aG input "$USER"   # log out / back in
```

On X11 (or if `/dev/uinput` is unavailable) it falls back to XTEST, which is
fine because X11 delivers mouse input normally.

Run `./fishbot.py --mode check-input` to see which backend is chosen and whether
your cursor actually moves.

---

## Quick start

No WoW required — try it against the built-in simulator:

```sh
./fishbot.py --mode simulate --max-casts 3
```

Then try the full pipeline against a fake game window (real screen capture +
real input injection):

```sh
./tools/fake_game.py &                    # a window that bites like WoW
./tools/e2e_check.py                      # asserts the bot loots
```

Finally, against the real game:

```sh
# 1. verify input reaches the game
./fishbot.py --mode check-input

# 2. find the right capture region and colour thresholds
./fishbot.py --mode calibrate --window-title "World of Warcraft"

# 3. fish
./fishbot.py --mode run --window-title "World of Warcraft"
```

---

## Setting up WoW

1. **Bind Fishing** to a key (Keybindings → miscellaneous). The default here is
   `1`; change it with `--cast-key`.
2. Stand facing open water. Aim the camera down a little so the float lands on
   screen and stays visible.
3. The float must not sit behind red UI (a health bar, a minimap blip). If the
   bot locks onto the wrong thing, restrict the search area with
   `--roi X,Y,W,H` (see `--mode calibrate`).
4. Looting is a right-click by default. Alternatively bind a key to
   **Interact with Mouseover** and pass `--loot-key` — that can be more robust
   on unusual input setups.

---

## Configuration

Every option has a sensible default. Run `./fishbot.py --help` for the full
list, or write a config file:

```sh
./fishbot.py --dump-config fishbot.config.json
./fishbot.py --config fishbot.config.json
```

The options you are most likely to touch:

| option | default | meaning |
|---|---|---|
| `--window-title` | *(unset)* | capture this window instead of the whole screen |
| `--roi X,Y,W,H` | whole screen | restrict capture to a rectangle |
| `--input-backend` | `auto` | `auto`, `x11`, or `uinput` |
| `--cast-key` | `1` | key that casts Fishing |
| `--loot-button` | `3` | mouse button used to loot (3 = right) |
| `--loot-key` | *(unset)* | loot with a keybind instead of a click |
| `--threshold` | `26.0` | mean pixel difference that counts as a splash |
| `--patch-size` | `72` | size of the watched square, in pixels |
| `--r-min` / `--g-max` / `--b-max` | 140 / 115 / 115 | float colour rule |
| `--search-margin` | `90` | how far the float may drift before re-baselining |
| `--dry-run` | off | detect and log, but never send input |
| `--debug` | off | print the splash score every frame |

### Tuning the detector

`--mode calibrate` captures a frame, marks what it thinks is the float, saves
`calibration.png`, and reports the idle frame-to-frame noise so you can pick a
threshold safely above it.

```
bobber found at (731, 402) box=(725, 396, 738, 409) pixels=71
annotated snapshot written to calibration.png
idle frame-to-frame difference: 3.14
suggested --threshold is at least 12.6 (default 26.0)
```

If the float is missed, loosen the colour rule (`--r-min 110`, `--g-max 140`,
`--b-max 140`). If the bot locks onto red UI, tighten it or use `--roi`.

---

## Testing

```sh
python3 -m unittest discover -s tests -t .   # 21 unit tests, ~2 s
./fishbot.py --mode self-test                # the same, via the CLI
./tools/e2e_check.py                         # real capture + input, needs an X display
```

The unit tests cover colour detection, the diff metric, the splash state
machine, the bot's full cycle against the simulator, config parsing, and the
uinput key map.

---

## Project layout

```
fishbot.py            CLI entry point and the run/calibrate/simulate modes
wowfish/
  x11.py              ctypes bindings for libX11 + XTEST (capture, input, windows)
  capture.py          Frame buffers and screen/window capture backends
  detect.py           find_bobber() and SplashDetector
  bot.py              the cast → watch → loot state machine
  input.py            Controller backends: XTEST, uinput, dry-run
  uinput.py           a virtual mouse/keyboard built on /dev/uinput
  simulate.py         an offline water world for tests and demos
  config.py           options, JSON config files, argparse
tools/
  fake_game.py        an X11 window that renders a float and bites
  e2e_check.py        runs the real bot against the fake game
tests/
  test_wowfish.py
```

## License

Public domain / CC0. Do what you like with it.
