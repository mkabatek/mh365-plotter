"""Load an SVG into flattened polylines in millimetres."""
from __future__ import annotations

import math
import re

from svgelements import SVG, Close, Path, Shape

from .geometry import Point, Polyline

MM_PER_PX = 25.4 / 96.0  # svgelements is parsed at 96 ppi
_DEFAULT_SKIP = re.compile(r"guide|do not cut|registration", re.I)


def _seg_deviation(pm: Point, a: Point, b: Point) -> float:
    """Distance from pm to segment a-b."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(pm[0] - ax, pm[1] - ay)
    t = max(0.0, min(1.0, ((pm[0] - ax) * dx + (pm[1] - ay) * dy) / L2))
    return math.hypot(pm[0] - (ax + t * dx), pm[1] - (ay + t * dy))


def _flatten_segment(seg, tol_px: float, out: list[Point], max_depth: int = 18) -> None:
    """Adaptive subdivision: subdivide only where the curve actually bends."""
    p0, p1 = seg.point(0.0), seg.point(1.0)

    def rec(t0, a, t1, b, depth):
        tm = 0.5 * (t0 + t1)
        m = seg.point(tm)
        mp = (m.x, m.y)
        if depth >= max_depth or _seg_deviation(mp, a, b) <= tol_px:
            out.append(b)
        else:
            rec(t0, a, tm, mp, depth + 1)
            rec(tm, mp, t1, b, depth + 1)

    rec(0.0, (p0.x, p0.y), 1.0, (p1.x, p1.y), 0)


def _label_of(el) -> str:
    """Find a layer label.

    svgelements inherits group attributes down to children, but stores
    namespaced ones under their expanded name, e.g.
    "{http://www.inkscape.org/namespaces/inkscape}label" - so a plain lookup of
    "inkscape:label" silently finds nothing.
    """
    v = getattr(el, "values", {}) or {}
    for k, val in v.items():
        if not val:
            continue
        local = k.rsplit("}", 1)[-1].rsplit(":", 1)[-1]
        if local == "label":
            return str(val)
    for k in ("inkscape:label", "label", "id"):
        if v.get(k):
            return str(v[k])
    return ""


def load_polylines(
    path: str,
    tolerance_mm: float = 0.05,
    only_label: str | None = None,
    skip_labels: bool = True,
    min_length_mm: float = 0.0,
) -> tuple[list[Polyline], dict]:
    """Return (polylines in mm, info).

    Every subpath becomes one polyline; holes and outer contours alike, which is
    what a cut file needs. Guide layers are skipped by default.
    """
    svg = SVG.parse(path, ppi=96.0, reify=True)
    tol_px = max(tolerance_mm / MM_PER_PX, 1e-4)

    polys: list[Polyline] = []
    skipped: list[str] = []
    for el in svg.elements():
        if not isinstance(el, Shape):
            continue
        label = _label_of(el)
        if only_label and only_label.lower() not in label.lower():
            continue
        if skip_labels and not only_label and _DEFAULT_SKIP.search(label):
            if label not in skipped:
                skipped.append(label)
            continue
        try:
            p = Path(el)
        except Exception:
            continue
        for sub in p.as_subpaths():
            segs = [s for s in sub if s.__class__.__name__ != "Move"]
            if not segs:
                continue
            closed = isinstance(segs[-1], Close)
            pts: list[Point] = []
            first = segs[0].point(0.0)
            pts.append((first.x, first.y))
            for s in segs:
                if isinstance(s, Close):
                    continue
                _flatten_segment(s, tol_px, pts)
            # de-duplicate consecutive identical points
            ded = [pts[0]]
            for q in pts[1:]:
                if abs(q[0] - ded[-1][0]) > 1e-9 or abs(q[1] - ded[-1][1]) > 1e-9:
                    ded.append(q)
            if len(ded) > 2 and math.hypot(ded[0][0] - ded[-1][0], ded[0][1] - ded[-1][1]) < 1e-6:
                ded.pop()
                closed = True
            if len(ded) < 2:
                continue
            mm = [(x * MM_PER_PX, y * MM_PER_PX) for (x, y) in ded]
            poly = Polyline(mm, closed=closed, label=label)
            if min_length_mm and poly.length() < min_length_mm:
                continue
            polys.append(poly)

    info = {
        "source": path,
        "svg_width_px": float(svg.width) if svg.width else None,
        "svg_height_px": float(svg.height) if svg.height else None,
        "paths": len(polys),
        "skipped_labels": skipped,
        "tolerance_mm": tolerance_mm,
    }
    return polys, info
