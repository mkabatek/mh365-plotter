# mh365

Driver for MH-series HPGL vinyl cutters (MH365 / MH721 / MH871 — sold as
"generic plotter") over USB serial, on macOS or Linux.

Built to cut the 9" x 33" skateboard paint masks in
`~/Desktop/pickle-deck-mask/`, but it takes any SVG.

```
./bin/mh365 ports                  # find the cutter
./bin/mh365 identify               # ask it what it is (no motion)
./bin/mh365 calibrate              # cut an L, learn the axis mapping
./bin/mh365 preview --rotate 90 -o tp.svg mask.svg
./bin/mh365 cut     --rotate 90    mask.svg
```

## Install

```
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

`./bin/mh365` runs the CLI inside that venv. Tests: `./.venv/bin/python -m pytest tests -q`.

## Cutting the deck masks

The masks are 9" across and 33" long. The MH365's carriage is 365 mm
(~340 mm usable), so **the design must be rotated 90°** to run the long axis
along the media feed. Without `--rotate 90` the tool refuses and tells you so.

Both layers share one page origin, so cutting them with identical options makes
them register on the deck:

```
R=~/Desktop/pickle-deck-mask/rev4
./bin/mh365 cut --rotate 90 $R/01_layer1_body_lightgreen.svg    # paint light green
./bin/mh365 cut --rotate 90 $R/02_layer2_details_darkgreen.svg  # then dark green
```

`04_combined_layers.svg` also works: the deck-outline guide layer is skipped
automatically (any layer labelled *guide*, *do not cut* or *registration*), and
`--layer "Layer 2"` selects one layer.

## Calibrate before you trust it

An MH365 reports nothing about its own geometry. Unit scale, which axis is the
carriage, and handedness are **settings, not facts**. Two commands make them
measurable:

- `test-square --size 100` — cut a square, measure it. If it comes out `S` mm
  instead of 100, set `--units-per-inch (1016 * S/100)`.
- `calibrate` — cuts an asymmetric L with 10 mm ticks. From the vinyl:
  - long arm across the carriage → add `--swap-axes`
  - mirrored left/right → add `--mirror-x`
  - mirrored front/back → add `--mirror-y`
  - ticks not 10 mm apart → rescale `--units-per-inch`

Then persist it: `./bin/mh365 --rotate 90 --units-per-inch 1016 profile --save`
writes `~/.config/mh365/profile.json`, used by every later run.

## Safety

- Nothing moves without confirmation. `cut`, `test-square`, `calibrate` and
  `home` prompt first; `--yes` skips it, and a non-TTY run refuses rather than
  assuming.
- `identify` sends only HPGL *output* queries — no motion.
- `info`, `preview` and `hpgl` never open the serial port at all.
- A design that exceeds the machine is refused (override: `--force-fit`).
- Ctrl-C lifts the knife (`PU;`) and aborts graphics (`ESC .K`).

## Commands

| | |
|---|---|
| `ports` | list serial ports, flag one physical device exposed twice |
| `identify` | query `OI/OA/OH/OS` — motion-free |
| `info FILE` | path count, size, cut/travel length, time estimate, limit warnings |
| `preview FILE -o OUT.svg` | toolpath drawing: cuts, travel moves, start dots, carriage limit |
| `hpgl FILE -o OUT.plt` | write the HPGL program without a device |
| `cut FILE` | send to the cutter |
| `test-square`, `calibrate` | calibration shapes |
| `home` | send the head to the origin |
| `profile [--save]` | show or persist settings |

Useful options: `--rotate`, `--mirror-x/-y`, `--swap-axes`, `--margin-mm`,
`--overcut-mm`, `--passes`, `--speed`, `--force`, `--tolerance`, `--layer`,
`--units-per-inch`, `--width-mm`, `--port`, `--baud`, `--flow`.

## How it cuts

1. **SVG → polylines.** `svgelements` resolves transforms and viewBox; curves
   are flattened by adaptive subdivision to `--tolerance` (default 0.05 mm), so
   straight runs stay cheap and tight curves get the points.
2. **Orientation.** Rotate / mirror / swap axes, flip SVG's y-down to the
   plotter's y-up, then translate so the design starts at `--margin-mm`.
3. **Order.** Interior holes are cut before the contours that contain them
   (by nesting depth), so nothing shifts mid-cut; nearest-neighbour within a
   depth. The greedy pass is then refined with or-opt/2-opt, and closed loops
   are re-started at whichever vertex is closest to the head. On layer 2 that
   cuts travel from 2.54 m to 2.00 m (21%). Greedy ordering *alone* came out
   worse than the file's own order, so there is a regression test pinning
   this.
4. **Overcut.** Closed loops continue `--overcut-mm` (default 0.5) past their
   start, because a drag knife pivots behind the spindle and never quite severs
   the last fraction of a loop.
5. **HPGL.** `IN;DF;PA;SP1;` then `PU`/`PD` in plotter units, `PD` chunked to 16
   coordinate pairs per command to stay inside the machine's buffer.
6. **Send.** Hardware flow control by default, chunked writes, then an `OA;`
   barrier — the cutter answers it only after executing everything queued
   before it, which is what tells us the job is actually finished. Closing the
   port early truncates the cut.

## Known unknowns

An MH365 speaks HPGL but reports almost nothing about its own geometry, so the
defaults below are **settings, not measurements**. All are overridable, and
`calibrate` / `test-square` exist to pin them down against a ruler:

1016 plotter units per inch, 9600 8N1, RTS/CTS flow control, 340 mm usable
width, machine Y = the carriage, origin at the near corner, no mirroring.
`VS`/`FS` are omitted by default, because MH-series units generally take speed
and force from the front panel and ignore them.

What *is* verified: the emitted HPGL round-trips to the same dimensions as the
source artwork (within 0.05 mm, cut length within 1%). There is a test for it.

### One chip, two device nodes (macOS)

MH-series cutters usually bridge to USB through an FTDI FT232R (`0403:6001`).
On macOS, if both Apple's `AppleUSBFTDI` driver and FTDI's own VCP driver are
installed, **both** claim the chip and you get two `/dev/cu.usbserial-*` nodes
for a single physical cutter. `ports` detects this and groups them under one
entry, so you don't waste time wondering which of your two cutters is real.
Use either node; if a cut stalls, try the other.

## Troubleshooting

- **Cut stops partway / garbled** — flow control. Try `--flow xonxoff`, then
  `--flow none --chunk 32 --pace 0.02`. Check the panel baud matches `--baud`.
- **Nothing happens** — wrong node (see above), or the panel is not in
  HPGL/plotter mode.
- **Wrong size** — `test-square`, then `--units-per-inch`. Some units are
  1000/inch, not 1016.
- **Mirrored or rotated** — `calibrate`, then `--mirror-x/-y` / `--swap-axes`.
- **Corners rounded, loops not closing** — raise `--overcut-mm`; check blade
  depth and that the blade spins freely.
- **Thick vinyl** — `--passes 2` rather than more blade force.
