"""Correctness checks. Run: .venv/bin/python -m pytest tests -q"""
import math
import re
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mh365.geometry import (Polyline, apply_overcut, bbox_of, nesting_depths,
                            order_for_cutting, travel_length)
from mh365.hpgl import HpglWriter, program_text
from mh365.machine import Profile, build_plan, check_limits, to_machine
from mh365.svgload import load_polylines

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "sample.svg")
# Point this at a directory of real cut files to exercise them too.
REAL_DIR = os.environ.get("MH365_TEST_SVG_DIR")


# ---------------------------------------------------------------- geometry
def test_overcut_extends_and_opens():
    sq = Polyline([(0, 0), (10, 0), (10, 10), (0, 10)], closed=True)
    o = apply_overcut(sq, 2.0)
    assert not o.closed
    assert math.isclose(o.pts[-1][0], 2.0, abs_tol=1e-9)
    assert math.isclose(o.length(), sq.length() + 2.0, abs_tol=1e-9)


def test_overcut_noop_on_open():
    ln = Polyline([(0, 0), (5, 0)], closed=False)
    assert apply_overcut(ln, 2.0) is ln


def test_nesting_and_order_holes_first():
    outer = Polyline([(0, 0), (10, 0), (10, 10), (0, 10)], closed=True)
    hole = Polyline([(2, 2), (4, 2), (4, 4), (2, 4)], closed=True)
    assert nesting_depths([outer, hole]) == [0, 1]
    assert order_for_cutting([outer, hole])[0].pts[0] == (2, 2)


def test_closed_loop_restarts_near_head():
    sq = Polyline([(0, 0), (10, 0), (10, 10), (0, 10)], closed=True)
    got = order_for_cutting([sq], start=(10, 10))
    assert got[0].pts[0] == (10, 10)


def test_ordering_reduces_travel():
    ps = [Polyline([(x, 0), (x + 1, 0)]) for x in (0, 50, 1, 51, 2)]
    assert travel_length(order_for_cutting(ps)) <= travel_length(ps)


# ---------------------------------------------------------------- transform
def test_rotate90_swaps_extent():
    p = Profile(rotate=90, margin_mm=0.0)
    src = [Polyline([(0, 0), (100, 0), (100, 20), (0, 20)], closed=True)]
    b = bbox_of(to_machine(src, p))
    assert math.isclose(b[2] - b[0], 20, abs_tol=1e-6)
    assert math.isclose(b[3] - b[1], 100, abs_tol=1e-6)


def test_margin_places_origin():
    p = Profile(rotate=0, margin_mm=7.0)
    b = bbox_of(to_machine([Polyline([(3, 3), (13, 9)])], p))
    assert math.isclose(b[0], 7.0, abs_tol=1e-6)
    assert math.isclose(b[1], 7.0, abs_tol=1e-6)


def test_limits_flag_overwide():
    p = Profile(width_mm=340.0, margin_mm=0.0)
    wide = [Polyline([(0, 0), (10, 0), (10, 900), (0, 900)], closed=True)]
    assert any("EXCEEDS CUT WIDTH" in w for w in check_limits(to_machine(wide, p), p))


def test_mirror_is_reversible():
    src = [Polyline([(0, 0), (10, 0), (10, 5)])]
    a = to_machine(src, Profile(margin_mm=0.0))
    b = to_machine(src, Profile(margin_mm=0.0, mirror_x=True))
    assert math.isclose(bbox_of(a)[2], bbox_of(b)[2], abs_tol=1e-6)
    assert [p for p in a][0].pts != [p for p in b][0].pts


# ---------------------------------------------------------------- hpgl
def _parse_plt(text, units_per_mm):
    """Read PU/PD back into polylines (mm) so the emitted program can be checked."""
    polys, cur, pen = [], [], False
    for m in re.finditer(r"(PU|PD)([-\d,]*);", text):
        op, arg = m.group(1), m.group(2)
        nums = [int(v) for v in arg.split(",") if v != ""]
        pts = [(nums[i] / units_per_mm, nums[i + 1] / units_per_mm)
               for i in range(0, len(nums) - 1, 2)]
        if op == "PU":
            if len(cur) > 1:
                polys.append(Polyline(cur))
            cur = pts[-1:] if pts else []
            pen = False
        else:
            cur += pts
            pen = True
    if len(cur) > 1:
        polys.append(Polyline(cur))
    return polys


def test_hpgl_structure():
    w = HpglWriter(1016 / 25.4)
    txt = program_text(w.program([Polyline([(0, 0), (10, 0)], closed=False)]))
    assert txt.startswith("\x1b.(IN;DF;PA;SP1;")
    assert txt.endswith("SP0;\x1b.)")
    assert "PU0,0;" in txt


def test_hpgl_units_exact():
    w = HpglWriter(1016 / 25.4)
    txt = program_text(w.program([Polyline([(0, 0), (25.4, 0)])]))
    assert ",1016," in txt or "1016,0" in txt


def test_pd_chunking_respects_limit():
    w = HpglWriter(1016 / 25.4, max_coords_per_cmd=4)
    p = Polyline([(i, 0) for i in range(20)])
    cmds = w.polyline(p)
    for c in cmds:
        if c.startswith("PD"):
            assert c.count(",") <= 2 * 4 - 1


def test_roundtrip_dimensions_match_design():
    """The bytes we would send must describe the same size as the artwork."""
    polys, _ = load_polylines(FIXTURE, tolerance_mm=0.02)
    p = Profile(rotate=90, margin_mm=5.0, overcut_mm=0.0)
    plan = build_plan(polys, p)
    w = HpglWriter(p.units_per_mm)
    txt = program_text(w.program(plan.polys))
    back = _parse_plt(txt, p.units_per_mm)
    b0, b1 = bbox_of(plan.polys), bbox_of(back)
    assert math.isclose(b1[2] - b1[0], b0[2] - b0[0], abs_tol=0.05)
    assert math.isclose(b1[3] - b1[1], b0[3] - b0[1], abs_tol=0.05)
    L0 = sum(q.length() for q in plan.polys)
    L1 = sum(q.length() for q in back)
    assert abs(L1 - L0) / L0 < 0.01


def test_layer_filter_partitions_the_drawing():
    """--layer must select exactly one layer, and the layers must add up."""
    allp, _ = load_polylines(FIXTURE)
    l1, _ = load_polylines(FIXTURE, only_label="Layer 1")
    l2, _ = load_polylines(FIXTURE, only_label="Layer 2")
    assert len(l1) == 1
    assert len(l2) >= 10
    assert len(l1) + len(l2) == len(allp)
    assert math.isclose(
        sum(p.length() for p in l1) + sum(p.length() for p in l2),
        sum(p.length() for p in allp), rel_tol=1e-9,
    )


def test_guide_layer_is_skipped_by_default():
    """Regression: the label lives under an expanded XML namespace key, so a
    naive lookup of "inkscape:label" found nothing and the guide got cut."""
    polys, info = load_polylines(FIXTURE)
    assert any("guide" in s.lower() for s in info["skipped_labels"])
    b = bbox_of(polys)
    # the guide rectangle is exactly 9x33in; the artwork is slightly smaller
    assert (b[3] - b[1]) < 33 * 25.4 - 0.2


def test_ordering_never_worse_than_file_order():
    """Guard: greedy nearest-neighbour plus the nesting constraint was once
    WORSE than the file's own order. It must never regress again."""
    polys, _ = load_polylines(FIXTURE)
    p = Profile(rotate=90)
    naive = build_plan(polys, p, order=False)
    smart = build_plan(polys, p, order=True)
    assert smart.travel_mm <= naive.travel_mm * 1.001


def test_nesting_constraint_still_holds_after_optimisation():
    outer = Polyline([(0, 0), (60, 0), (60, 60), (0, 60)], closed=True)
    holes = [Polyline([(x, x), (x + 4, x), (x + 4, x + 4), (x, x + 4)], closed=True)
             for x in (5, 20, 40)]
    got = order_for_cutting([outer] + holes)
    assert got[-1].pts[0] in [tuple(v) for v in outer.pts]


def test_real_files_if_available():
    """Opt-in: MH365_TEST_SVG_DIR=/path/to/cut/files pytest tests"""
    if not REAL_DIR or not os.path.isdir(REAL_DIR):
        return
    import glob
    for f in sorted(glob.glob(os.path.join(REAL_DIR, "*.svg"))):
        polys, _ = load_polylines(f)
        assert polys, f
        plan = build_plan(polys, Profile(rotate=90))
        assert not [w for w in plan.warnings if "EXCEEDS" in w], f


# ---------------------------------------------------------------- completion
class _FakeArgs:
    def __init__(self, **kw):
        self.no_wait = False
        self.wait_timeout = 900.0
        self.probe_timeout = 3.0
        self.drain_seconds = 0.2
        self.__dict__.update(kw)


class _FakeTransport:
    """Stands in for a cutter that never answers output queries."""

    def __init__(self, replies=""):
        self.replies = replies
        self.waited = False

    def query(self, cmd, timeout=3.0):
        return self.replies

    def wait_idle(self, timeout=900.0):
        self.waited = True
        return bool(self.replies)


def test_silent_cutter_does_not_block_on_oa():
    """Regression: a unit that answers nothing used to hang until --wait-timeout
    (30 min by default) at the 'waiting for the cutter to finish' step."""
    import time as _t
    from mh365.cli import finish
    tr = _FakeTransport(replies="")          # silent machine
    t0 = _t.time()
    finish(tr, None, _FakeArgs(wait_timeout=600.0), answers=False)
    assert _t.time() - t0 < 5.0
    assert not tr.waited                     # must not use the barrier


def test_answering_cutter_uses_the_barrier():
    from mh365.cli import finish
    tr = _FakeTransport(replies="0,0")
    finish(tr, None, _FakeArgs(), answers=True)
    assert tr.waited


def test_no_wait_returns_immediately():
    import time as _t
    from mh365.cli import finish
    tr = _FakeTransport(replies="")
    t0 = _t.time()
    finish(tr, None, _FakeArgs(no_wait=True, drain_seconds=30.0), answers=False)
    assert _t.time() - t0 < 0.5
    assert not tr.waited
