"""Command line interface."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__
from .calib import orientation_L, square
from .geometry import Polyline, bbox_of
from .hpgl import HpglWriter, program_text
from .machine import Profile, build_plan
from .preview import render_svg
from .svgload import load_polylines
from .transport import SerialTransport, candidate_ports

DEFAULT_PROFILE = os.path.expanduser("~/.config/mh365/profile.json")


# ---------------------------------------------------------------- helpers
def load_profile(args) -> Profile:
    p = Profile.load(args.profile) if (args.profile and os.path.exists(args.profile)) else Profile()
    for k in ("rotate", "scale", "margin_mm", "overcut_mm", "passes", "speed", "force",
              "units_per_inch", "width_mm", "max_length_mm", "baud", "flow",
              "est_cut_mm_s", "est_travel_mm_s"):
        v = getattr(args, k, None)
        if v is not None:
            setattr(p, k, v)
    for k in ("mirror_x", "mirror_y", "swap_axes"):
        if getattr(args, k, False):
            setattr(p, k, True)
    if getattr(args, "no_flip_y", False):
        p.flip_y = False
    return p


def group_ports() -> list[dict]:
    """One entry per physical chip; macOS can expose a single FTDI device twice
    when both Apple's driver and FTDI's VCP driver are installed."""
    ports = candidate_ports()
    by_key: dict = {}
    for p in ports:
        key = (p["vid"], p["pid"], p["serial_number"]) if p["vid"] else (p["device"],)
        by_key.setdefault(key, []).append(p)
    out = []
    for key, group in by_key.items():
        first = dict(group[0])
        first["nodes"] = [g["device"] for g in group]
        out.append(first)
    return out


def resolve_port(args) -> str:
    if args.port:
        return args.port
    env = os.environ.get("MH365_PORT")
    if env:
        return env
    groups = [g for g in group_ports() if g.get("vid")]
    if not groups:
        sys.exit("No USB serial device found. Is the cutter powered on and plugged in?")
    if len(groups) > 1:
        print("Multiple USB serial devices found; choose one with --port:", file=sys.stderr)
        for g in groups:
            print(f"  {g['nodes'][0]}  {g['description']} ({g['manufacturer']})", file=sys.stderr)
        sys.exit(2)
    return groups[0]["nodes"][0]


def confirm(msg: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"{msg}\nRefusing to move the knife without confirmation "
              f"(stdin is not a terminal). Re-run with --yes.", file=sys.stderr)
        return False
    return input(f"{msg} [y/N] ").strip().lower() in ("y", "yes")


def describe(plan, p: Profile) -> None:
    print(f"  paths          : {len(plan.polys)}")
    print(f"  size           : {plan.width_mm:.1f} x {plan.height_mm:.1f} mm "
          f"({plan.width_mm/25.4:.2f} x {plan.height_mm/25.4:.2f} in)")
    print(f"  machine bbox   : x {plan.bbox[0]:.1f}..{plan.bbox[2]:.1f}, "
          f"y {plan.bbox[1]:.1f}..{plan.bbox[3]:.1f} mm")
    carriage = plan.width_mm if p.swap_axes else plan.height_mm
    print(f"  across carriage: {carriage:.1f} mm of {p.width_mm:.1f} mm available")
    print(f"  cut length     : {plan.cut_mm/1000:.2f} m   travel {plan.travel_mm/1000:.2f} m")
    if abs(p.scale - 1.0) > 1e-9:
        print(f"  scale          : {p.scale:.4g}  ({p.scale*100:.4g}% of the artwork)")
    print(f"  passes         : {p.passes}   overcut {p.overcut_mm} mm")
    print(f"  estimated time : {plan.seconds/60:.1f} min (at {p.est_cut_mm_s:.0f} mm/s, estimate only)")
    for w in plan.warnings:
        print(f"  !! {w}")


def plan_from_file(path: str, args, p: Profile):
    polys, info = load_polylines(
        path, tolerance_mm=args.tolerance, only_label=args.layer,
        min_length_mm=args.min_length,
    )
    if not polys:
        sys.exit(f"No cuttable geometry found in {path}")
    if info["skipped_labels"]:
        print(f"  skipped layers : {', '.join(info['skipped_labels'])}")
    plan = build_plan(polys, p, order=not args.no_order,
                      optimize_start=not args.no_optimize_start)
    return plan, info


def finish(tr, plan, args, answers: bool) -> None:
    """Wait for the machine to finish, by whatever means it actually supports.

    `OA;` is answered only after everything queued before it has executed, which
    makes it an exact completion barrier - but only on units that implement HPGL
    output commands. Plenty of MH-series machines answer nothing at all, and
    blocking on a reply that will never arrive just hangs until the timeout.
    So: probe first, and fall back to a timed drain.

    The fallback is short by design. Flow control means write() only returns
    once the cutter has accepted every byte, so what is left to execute is at
    most one buffer - seconds, not minutes.
    """
    if args.no_wait:
        print("  sent. Not waiting (--no-wait); let the head stop before unloading.")
        return
    if answers:
        print("  waiting for the cutter to finish (OA; barrier)...")
        if tr.wait_idle(timeout=args.wait_timeout):
            print("  cutter reports idle.")
        else:
            print("  no reply within --wait-timeout; the cut may still be running. "
                  "Watch the head.", file=sys.stderr)
        return
    n = max(0.0, args.drain_seconds)
    print(f"  this unit does not answer HPGL output queries, so completion cannot")
    print(f"  be confirmed directly. All bytes are accepted; draining {n:.0f}s...")
    step = 0.5
    waited = 0.0
    while waited < n:
        time.sleep(min(step, n - waited))
        waited += step
        print(f"\r  draining        : {n - min(waited, n):4.1f}s ", end="", flush=True)
    print("\r  drained. If the head is still moving it is working from its own "
          "buffer;\n  that is normal - wait for it to stop.        ")


def send(plan, p: Profile, args, park=(0.0, 0.0)) -> None:
    w = HpglWriter(p.units_per_mm, speed=p.speed, force=p.force)
    text = program_text(w.program(plan.polys, passes=p.passes, park=park))
    port = resolve_port(args)
    print(f"  port           : {port} @ {p.baud} baud, flow={p.flow}")
    print(f"  program        : {len(text)} bytes")
    t0 = time.time()
    tr = SerialTransport(port, baud=p.baud, flow=p.flow, chunk=args.chunk, pace=args.pace)
    try:
        with tr:
            answers = False
            if not args.no_wait:
                answers = bool(tr.query("OA;", timeout=args.probe_timeout))

            def prog(sent, total):
                pct = 100.0 * sent / max(total, 1)
                print(f"\r  sending        : {pct:5.1f}%  ({sent}/{total} bytes)",
                      end="", flush=True)

            tr.write(text, progress=prog)
            print()
            finish(tr, plan, args, answers)
            print(f"  elapsed {time.time()-t0:.0f} s")
    except KeyboardInterrupt:
        tr.abort()
        tr.close()
        sys.exit("\nAborted: knife lifted, graphics aborted.")


# ---------------------------------------------------------------- commands
def cmd_ports(args) -> None:
    groups = group_ports()
    if not groups:
        print("No serial ports found.")
        return
    for g in groups:
        print(f"{g['nodes'][0]}")
        print(f"  description  : {g['description']}  ({g['manufacturer']})")
        if g["vid"]:
            print(f"  usb id       : {g['vid']:04x}:{g['pid']:04x}  serial={g['serial_number'] or '-'}")
        if len(g["nodes"]) > 1:
            print(f"  ** one physical device, {len(g['nodes'])} device nodes: {', '.join(g['nodes'])}")
            print(f"     Both Apple's FTDI driver and FTDI's own VCP driver are claiming it.")
            print(f"     Use ONE of them; if a cut stalls, try the other.")


def cmd_identify(args) -> None:
    """Motion-free: only HPGL output queries are sent."""
    port = resolve_port(args)
    p = load_profile(args)
    print(f"Querying {port} @ {p.baud} baud (no motion; output commands only)")
    with SerialTransport(port, baud=p.baud, flow=p.flow) as tr:
        for cmd, what in (("\x1b.(", "enter HPGL"), ("OI;", "identify"),
                          ("OA;", "actual position"), ("OH;", "hard-clip limits"),
                          ("OS;", "status")):
            if cmd.startswith("\x1b"):
                tr.write(cmd)
                continue
            r = tr.query(cmd, timeout=args.probe_timeout)
            print(f"  {cmd:<5} {what:<18} -> {r!r}" if r else
                  f"  {cmd:<5} {what:<18} -> (no reply)")
    print("\nA model string from OI; confirms HPGL. Many MH-series units answer "
          "nothing at all\nyet still cut correctly - so silence here is not proof "
          "of a problem.")


def cmd_info(args) -> None:
    p = load_profile(args)
    plan, info = plan_from_file(args.file, args, p)
    print(f"{args.file}")
    print(f"  svg viewport   : {info['svg_width_px']:.0f} x {info['svg_height_px']:.0f} px")
    describe(plan, p)


def cmd_preview(args) -> None:
    p = load_profile(args)
    plan, _ = plan_from_file(args.file, args, p)
    print(f"{args.file}")
    describe(plan, p)
    out = args.output or os.path.splitext(args.file)[0] + "_toolpath.svg"
    render_svg(plan, p, out, show_travel=not args.no_travel)
    print(f"  wrote          : {out}")


def cmd_hpgl(args) -> None:
    p = load_profile(args)
    plan, _ = plan_from_file(args.file, args, p)
    w = HpglWriter(p.units_per_mm, speed=p.speed, force=p.force)
    text = program_text(w.program(plan.polys, passes=p.passes))
    out = args.output or os.path.splitext(args.file)[0] + ".plt"
    with open(out, "w") as f:
        f.write(text)
    print(f"{args.file}")
    describe(plan, p)
    print(f"  wrote          : {out}  ({len(text)} bytes)")


def cmd_cut(args) -> None:
    p = load_profile(args)
    plan, _ = plan_from_file(args.file, args, p)
    print(f"{args.file}")
    describe(plan, p)
    blocking = [w for w in plan.warnings if "EXCEEDS" in w]
    if blocking and not args.force_fit:
        sys.exit("Refusing to cut: the design does not fit the machine. "
                 "Use --rotate 90, or --force-fit to override.")
    if not confirm(f"Cut {plan.cut_mm/1000:.2f} m of path now?", args.yes):
        sys.exit("Cancelled.")
    send(plan, p, args)


def cmd_test_square(args) -> None:
    p = load_profile(args)
    plan = build_plan(square(args.size), p, order=False)
    print(f"Calibration square {args.size} mm")
    describe(plan, p)
    if not confirm("Cut it now?", args.yes):
        sys.exit("Cancelled.")
    send(plan, p, args)
    print(f"\nMeasure both sides. If they read S instead of {args.size} mm, set\n"
          f"  --units-per-inch {{:.1f}} = {p.units_per_inch} * (S / {args.size})\n"
          f"and save it with `mh365 profile --save`.")


def cmd_calibrate(args) -> None:
    p = load_profile(args)
    plan = build_plan(orientation_L(args.long, args.short), p, order=False)
    print(f"Orientation L: {args.long} mm along machine X, {args.short} mm along machine Y")
    describe(plan, p)
    if not confirm("Cut it now?", args.yes):
        sys.exit("Cancelled.")
    send(plan, p, args)
    print("\nRead the result off the vinyl:\n"
          "  long arm across the carriage  -> add --swap-axes\n"
          "  arms mirrored left/right      -> add --mirror-x\n"
          "  arms mirrored front/back      -> add --mirror-y\n"
          "  tick spacing not 10 mm        -> rescale --units-per-inch")


def cmd_home(args) -> None:
    p = load_profile(args)
    if not confirm("Move the head to the origin?", args.yes):
        sys.exit("Cancelled.")
    port = resolve_port(args)
    with SerialTransport(port, baud=p.baud, flow=p.flow) as tr:
        answers = bool(tr.query("OA;", timeout=args.probe_timeout))
        tr.write("\x1b.(IN;PA;SP1;PU0,0;SP0;")
        if answers:
            tr.wait_idle(timeout=120.0)
        else:
            time.sleep(min(5.0, max(0.0, args.drain_seconds)))
    print("Sent.")


def cmd_profile(args) -> None:
    p = load_profile(args)
    if args.save:
        os.makedirs(os.path.dirname(args.profile or DEFAULT_PROFILE), exist_ok=True)
        p.save(args.profile or DEFAULT_PROFILE)
        print(f"Saved profile to {args.profile or DEFAULT_PROFILE}")
    for k, v in vars(p).items():
        print(f"  {k:<16} {v}")


# ---------------------------------------------------------------- parser
# Shared options are attached to the top level AND to every subcommand, so both
# `mh365 --rotate 90 cut f.svg` and `mh365 cut --rotate 90 f.svg` work. They use
# SUPPRESS defaults so a subparser cannot clobber a value given at the top level.
DEFAULTS = dict(
    profile=DEFAULT_PROFILE, port=None, baud=None, flow=None, units_per_inch=None,
    width_mm=None, max_length_mm=None, rotate=None, mirror_x=False, mirror_y=False,
    swap_axes=False, no_flip_y=False, scale=None, margin_mm=None, overcut_mm=None, passes=None,
    speed=None, force=None, tolerance=0.05, layer=None, min_length=0.0,
    no_order=False, no_optimize_start=False, est_cut_mm_s=None, est_travel_mm_s=None,
    chunk=64, pace=0.0, wait_timeout=900.0, probe_timeout=3.0,
    drain_seconds=15.0, no_wait=False, yes=False, force_fit=False,
)

S = argparse.SUPPRESS


def add_common(ap: argparse.ArgumentParser) -> None:
    g = ap.add_argument_group("machine and job options")
    g.add_argument("--profile", default=S, help="profile JSON path")
    g.add_argument("--port", default=S, help="serial device (default: autodetect, or $MH365_PORT)")
    g.add_argument("--baud", type=int, default=S)
    g.add_argument("--flow", choices=["rtscts", "xonxoff", "none"], default=S)
    g.add_argument("--units-per-inch", type=float, dest="units_per_inch", default=S)
    g.add_argument("--width-mm", type=float, dest="width_mm", default=S, help="usable cut width")
    g.add_argument("--max-length-mm", type=float, dest="max_length_mm", default=S)
    g.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=S)
    g.add_argument("--scale", type=float, default=S,
                   help="uniform artwork scale; 0.95 cuts at 95%% of size")
    g.add_argument("--mirror-x", action="store_true", default=S)
    g.add_argument("--mirror-y", action="store_true", default=S)
    g.add_argument("--swap-axes", action="store_true", default=S)
    g.add_argument("--no-flip-y", action="store_true", default=S)
    g.add_argument("--margin-mm", type=float, dest="margin_mm", default=S)
    g.add_argument("--overcut-mm", type=float, dest="overcut_mm", default=S)
    g.add_argument("--passes", type=int, default=S)
    g.add_argument("--speed", type=int, default=S, help="HPGL VS; omit to use the front panel")
    g.add_argument("--force", type=int, default=S, help="HPGL FS; omit to use the front panel")
    g.add_argument("--tolerance", type=float, default=S, help="curve flattening, mm")
    g.add_argument("--layer", default=S, help="only geometry whose label contains this")
    g.add_argument("--min-length", type=float, default=S, help="drop paths shorter than, mm")
    g.add_argument("--no-order", action="store_true", default=S, help="keep file order")
    g.add_argument("--no-optimize-start", action="store_true", default=S)
    g.add_argument("--est-cut-mm-s", type=float, dest="est_cut_mm_s", default=S)
    g.add_argument("--est-travel-mm-s", type=float, dest="est_travel_mm_s", default=S)
    g.add_argument("--chunk", type=int, default=S, help="serial write chunk bytes")
    g.add_argument("--pace", type=float, default=S, help="seconds between chunks")
    g.add_argument("--wait-timeout", type=float, default=S,
                   help="max wait for an OA; completion reply (units that answer)")
    g.add_argument("--probe-timeout", type=float, default=S,
                   help="how long to test whether the unit answers queries at all")
    g.add_argument("--drain-seconds", type=float, default=S,
                   help="fallback wait after sending, for units that stay silent")
    g.add_argument("--no-wait", action="store_true", default=S,
                   help="send and exit without waiting")
    g.add_argument("--yes", action="store_true", default=S, help="skip confirmation")
    g.add_argument("--force-fit", action="store_true", default=S,
                   help="cut even if the design exceeds the machine limits")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mh365",
        description="Drive an MH365 (or other MH-series HPGL) vinyl cutter over USB.",
    )
    ap.add_argument("--version", action="version", version=f"mh365 {__version__}")
    add_common(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_, **kw):
        s = sub.add_parser(name, help=help_, **kw)
        add_common(s)
        return s

    add("ports", "list serial ports").set_defaults(fn=cmd_ports)
    add("identify", "query the cutter (no motion)").set_defaults(fn=cmd_identify)
    s = add("info", "report what would be cut"); s.add_argument("file"); s.set_defaults(fn=cmd_info)
    s = add("preview", "render the toolpath to SVG")
    s.add_argument("file"); s.add_argument("-o", "--output", default=None)
    s.add_argument("--no-travel", action="store_true", default=False)
    s.set_defaults(fn=cmd_preview)
    s = add("hpgl", "write a .plt file, no device")
    s.add_argument("file"); s.add_argument("-o", "--output", default=None); s.set_defaults(fn=cmd_hpgl)
    s = add("cut", "send to the cutter"); s.add_argument("file"); s.set_defaults(fn=cmd_cut)
    s = add("test-square", "cut a calibration square")
    s.add_argument("--size", type=float, default=100.0); s.set_defaults(fn=cmd_test_square)
    s = add("calibrate", "cut an orientation L")
    s.add_argument("--long", type=float, default=100.0)
    s.add_argument("--short", type=float, default=40.0); s.set_defaults(fn=cmd_calibrate)
    add("home", "send the head to the origin").set_defaults(fn=cmd_home)
    s = add("profile", "show or save settings")
    s.add_argument("--save", action="store_true", default=False); s.set_defaults(fn=cmd_profile)
    return ap


def main(argv=None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    for k, v in DEFAULTS.items():
        if not hasattr(args, k):
            setattr(args, k, v)
    args.fn(args)


if __name__ == "__main__":
    main()
