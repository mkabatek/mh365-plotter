# mh365

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Driver for MH-series HPGL vinyl cutters — MH365 / MH721 / MH871, the machines
usually sold as "generic plotter" — over USB serial, on macOS and Linux.

Feed it an SVG, get vinyl. It flattens curves, orients the job to the machine,
orders the cut so interior holes are released before the contours holding them,
adds overcut so closed loops actually sever, and streams HPGL over serial with
flow control.

```
mh365 ports                                  # find the cutter
mh365 identify                               # ask what it is (no motion)
mh365 calibrate                              # cut an L, learn the axis mapping
mh365 preview --rotate 90 -o tp.svg design.svg
mh365 cut     --rotate 90            design.svg
```

## Install

```bash
git clone https://github.com/mkabatek/mh365-plotter.git && cd mh365-plotter && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

`./bin/mh365` runs the CLI inside that venv, from any working directory.
Optionally alias it so you can type `mh365` anywhere:

```bash
echo "alias mh365=\"$PWD/bin/mh365\"" >> ~/.zshrc && source ~/.zshrc
```

The rest of this README assumes that alias.

## Step 1 — Checks that never touch the hardware

`info`, `preview` and `hpgl` never open the serial port, so run them freely.

```bash
mh365 info --rotate 90 design.svg
```

Reports path count, finished size, cut and travel length, a time estimate, and
whether the job fits the machine.

Render the toolpath and look at it before committing vinyl — cuts in green,
travel moves in grey, the carriage limit in pink, start points as dots:

```bash
mh365 preview --rotate 90 -o toolpath.svg design.svg
```

Or write the HPGL to a file without a cutter attached:

```bash
mh365 hpgl --rotate 90 -o design.plt design.svg
```

**About `--rotate 90`:** the carriage is the hard limit (~340 mm usable). A job
longer than that in one axis must run that axis along the media feed instead.
A design that doesn't fit is refused rather than cut off the media; use
`--force-fit` to override.

## Step 2 — Find and greet the cutter

```bash
mh365 ports
```

Autodetect is normally enough, so `--port` is rarely needed. On macOS a single
cutter often appears as *two* device nodes (see [Known unknowns](#known-unknowns));
`ports` groups them into one entry and says so.

This sends only HPGL *query* commands — nothing moves:

```bash
mh365 identify
```

A model string back from `OI;` confirms HPGL. Silence is common on MH-series
units and does not mean anything is wrong.

## Step 3 — Calibrate, on scrap vinyl

Do this once per machine. An MH365 cannot report its unit scale or which axis
is the carriage, so this measures them. Load a scrap offcut, set blade depth
and force on the front panel, and check the panel baud rate matches (9600 by
default).

```bash
mh365 calibrate
```

You get an asymmetric L with 10 mm ticks. Read the result off the vinyl:

| What you see | What to add |
|---|---|
| long arm runs across the carriage | `--swap-axes` |
| mirrored left/right | `--mirror-x` |
| mirrored front/back | `--mirror-y` |
| ticks aren't 10 mm apart | rescale `--units-per-inch` |

Then check dimensional accuracy:

```bash
mh365 test-square --size 100
```

If it measures `S` mm instead of 100, set `--units-per-inch` to `1016 * S/100`
— e.g. 98 mm means `--units-per-inch 995.7`. Some units are 1000/inch, not 1016.

## Step 4 — Save what you settled on

Add whatever flags calibration told you, then persist them:

```bash
mh365 --rotate 90 profile --save
```

That writes `~/.config/mh365/profile.json`, which every later run picks up, so
you can drop the flags. `mh365 profile` prints the current settings.

## Step 5 — Cut

```bash
mh365 cut design.svg
```

`cut`, `test-square`, `calibrate` and `home` print the plan and wait for a `y`.
`--yes` skips the prompt; on a non-interactive shell they refuse rather than
assume. Ctrl-C lifts the knife and aborts graphics.

## Multi-layer jobs

For a two-colour paint mask, cut one layer per colour and paint between passes.
Layers exported from the same page share an origin, so cutting them with
**identical** options makes them register:

```bash
mh365 cut --rotate 90 layer1.svg
```

Weed, paint, peel, let it cure, then:

```bash
mh365 cut --rotate 90 layer2.svg
```

Don't change `--rotate`, `--margin-mm` or `--swap-axes` between layers or they
won't line up.

A single multi-layer SVG works too. Layers labelled *guide*, *do not cut* or
*registration* are skipped automatically, and one layer can be selected by label:

```bash
mh365 cut --rotate 90 --layer "Layer 2" combined.svg
```

## Options

Flags work before or after the subcommand.

| Flag | Why |
|---|---|
| `--rotate {0,90,180,270}` | run the long axis along the media feed |
| `--overcut-mm N` | raise if closed loops aren't quite severing (default 0.5) |
| `--passes N` | thick vinyl — better than more blade force |
| `--margin-mm N` | shift the job off the media edge (default 5) |
| `--layer NAME` | cut only layers whose label contains NAME |
| `--tolerance N` | curve flattening in mm (default 0.05) |
| `--port DEV` | override autodetect |
| `--baud N`, `--flow {rtscts,xonxoff,none}` | serial settings |
| `--units-per-inch N` | unit scale from calibration (default 1016) |
| `--width-mm N` | usable cut width (default 340) |
| `--mirror-x`, `--mirror-y`, `--swap-axes` | orientation from calibration |
| `--speed N`, `--force N` | HPGL `VS`/`FS`; usually ignored, see below |
| `--min-length N` | drop paths shorter than N mm |
| `--no-order` | keep the file's own path order |
| `--yes` | skip the confirmation prompt |
| `--force-fit` | cut even if the design exceeds the machine |

`MH365_PORT` in the environment acts as a default for `--port`.

## Commands

| | |
|---|---|
| `ports` | list serial ports, group one device exposed twice |
| `identify` | query `OI`/`OA`/`OH`/`OS` — motion-free |
| `info FILE` | size, cut/travel length, time estimate, limit warnings |
| `preview FILE -o OUT.svg` | toolpath drawing |
| `hpgl FILE -o OUT.plt` | write the HPGL program, no device |
| `cut FILE` | send to the cutter |
| `test-square`, `calibrate` | calibration shapes |
| `home` | send the head to the origin |
| `profile [--save]` | show or persist settings |

## How it cuts

1. **SVG → polylines.** `svgelements` resolves transforms and viewBox; curves
   are flattened by adaptive subdivision to `--tolerance`, so straight runs
   stay cheap and tight curves get the points they need.
2. **Orientation.** Rotate / mirror / swap axes, flip SVG's y-down to the
   plotter's y-up, then translate so the design starts at `--margin-mm`.
3. **Order.** Interior holes are cut before the contours that contain them (by
   nesting depth), so a freed island can't shift while the knife is still
   working around it. Greedy nearest-neighbour within a depth, then or-opt/2-opt
   refinement of the sequence — worth ~21% less travel on a real job. Greedy
   ordering *alone* came out worse than the file's own order, so there's a
   regression test pinning this.
4. **Overcut.** Closed loops continue `--overcut-mm` past their start, because
   a drag knife pivots behind the spindle and never quite severs the last
   fraction of a loop.
5. **HPGL.** `IN;DF;PA;SP1;` then `PU`/`PD` in plotter units, with `PD` chunked
   to 16 coordinate pairs per command to stay inside the machine's buffer.
6. **Send.** Hardware flow control by default, chunked writes, then an `OA;`
   barrier — the cutter answers it only after executing everything queued
   before it, which is what tells us the job is genuinely finished. Closing the
   port early truncates the cut.

## Known unknowns

An MH365 speaks HPGL but reports almost nothing about its own geometry, so
these defaults are **settings, not measurements**. All are overridable, and
`calibrate` / `test-square` exist to pin them down against a ruler:

1016 plotter units per inch, 9600 8N1, RTS/CTS flow control, 340 mm usable
width, machine Y = the carriage, origin at the near corner, no mirroring.
`VS`/`FS` are omitted by default, because MH-series units generally take speed
and force from the front panel and ignore them.

What *is* verified: the emitted HPGL round-trips to the same dimensions as the
source artwork, within 0.05 mm, cut length within 1%. There's a test for it.

Time estimates are optimistic — they ignore acceleration, so a real cut takes
longer than reported.

### One chip, two device nodes (macOS)

MH-series cutters usually bridge to USB through an FTDI FT232R (`0403:6001`).
On macOS, if both Apple's `AppleUSBFTDI` driver and FTDI's own VCP driver are
installed, **both** claim the chip and you get two `/dev/cu.usbserial-*` nodes
for a single physical cutter. `ports` detects this and groups them under one
entry, so you don't waste time wondering which of your two cutters is real.
Use either node; if a cut stalls, try the other with `--port`.

## Troubleshooting

- **Cut stops partway / garbled** — flow control. Try `--flow xonxoff`, then
  `--flow none --chunk 32 --pace 0.02`. Check the panel baud matches `--baud`.
- **Nothing happens** — wrong device node (see above), or the panel isn't in
  HPGL/plotter mode.
- **Wrong size** — `test-square`, then `--units-per-inch`.
- **Mirrored or rotated** — `calibrate`, then `--mirror-x/-y` / `--swap-axes`.
- **Corners rounded, loops not closing** — raise `--overcut-mm`; check blade
  depth and that the blade spins freely.
- **Thick vinyl** — `--passes 2` rather than more blade force.
- **"Refusing to cut"** — the design exceeds the machine. Try `--rotate 90`.

## Development

```bash
./.venv/bin/python -m pytest tests -q
```

18 tests, run against a synthetic fixture in `tests/fixtures/` so they need no
hardware and no local files. To also exercise your own cut files:

```bash
MH365_TEST_SVG_DIR=/path/to/svgs ./.venv/bin/python -m pytest tests -q
```

## License

MIT — see [LICENSE](LICENSE).
