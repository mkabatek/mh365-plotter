"""Machine profile, design->machine coordinate transform, and limits."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field

from .geometry import Polyline, bbox_of, map_points, travel_length


@dataclass
class Profile:
    """MH365 defaults.

    Anything here that the machine does not actually report (unit scale, which
    axis is the carriage, where the origin sits, whether an axis is mirrored)
    is a setting, not a fact. `mh365 calibrate` exists to pin them down.
    """

    name: str = "MH365"
    units_per_inch: float = 1016.0   # HPGL plotter unit = 1/1016 in
    width_mm: float = 340.0          # usable cut width across the carriage
    max_length_mm: float = 5000.0    # along the media feed
    baud: int = 9600
    flow: str = "rtscts"             # rtscts | xonxoff | none
    # orientation
    rotate: int = 0                  # 0/90/180/270, applied to the design
    mirror_x: bool = False
    mirror_y: bool = False
    flip_y: bool = True              # SVG y-down -> machine y-up
    swap_axes: bool = False          # if the carriage turns out to be X
    scale: float = 1.0               # uniform artwork scale, 0.95 = 95%
    margin_mm: float = 5.0
    # cutting
    overcut_mm: float = 0.5
    passes: int = 1
    speed: int | None = None         # HPGL VS; None = leave to front panel
    force: int | None = None         # HPGL FS; None = leave to front panel
    # estimation only
    est_cut_mm_s: float = 150.0
    est_travel_mm_s: float = 300.0

    @property
    def units_per_mm(self) -> float:
        return self.units_per_inch / 25.4

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Profile":
        with open(path) as f:
            return cls(**json.load(f))


def to_machine(polys: list[Polyline], p: Profile) -> list[Polyline]:
    """Design mm (origin top-left, y down) -> machine mm (origin at margin, y up)."""
    r = p.rotate % 360
    if r not in (0, 90, 180, 270):
        raise ValueError("rotate must be 0, 90, 180 or 270")
    th = math.radians(r)
    c, s = round(math.cos(th)), round(math.sin(th))

    k = float(p.scale)
    if k <= 0:
        raise ValueError("scale must be > 0")

    def fn(x, y):
        x, y = x * k, y * k
        X, Y = x * c - y * s, x * s + y * c
        if p.flip_y:
            Y = -Y
        if p.mirror_x:
            X = -X
        if p.mirror_y:
            Y = -Y
        if p.swap_axes:
            X, Y = Y, X
        return X, Y

    out = map_points(polys, fn)
    x0, y0, _, _ = bbox_of(out)
    return map_points(out, lambda x, y: (x - x0 + p.margin_mm, y - y0 + p.margin_mm))


@dataclass
class Plan:
    polys: list[Polyline]
    cut_mm: float
    travel_mm: float
    bbox: tuple[float, float, float, float]
    seconds: float
    warnings: list[str] = field(default_factory=list)

    @property
    def width_mm(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height_mm(self) -> float:
        return self.bbox[3] - self.bbox[1]


def check_limits(polys: list[Polyline], p: Profile) -> list[str]:
    """The carriage axis is hard-limited; the feed axis is limited by the roll."""
    x0, y0, x1, y1 = bbox_of(polys)
    carriage = (x1 - x0) if p.swap_axes else (y1 - y0)
    feed = (y1 - y0) if p.swap_axes else (x1 - x0)
    w = []
    if carriage > p.width_mm:
        w.append(
            f"EXCEEDS CUT WIDTH: needs {carriage:.1f} mm across the carriage, "
            f"machine allows {p.width_mm:.1f} mm. Try --rotate 90."
        )
    if feed > p.max_length_mm:
        w.append(f"EXCEEDS FEED LENGTH: needs {feed:.1f} mm, limit {p.max_length_mm:.1f} mm.")
    if min(x0, y0) < -1e-6:
        w.append(f"Negative coordinates present (min {min(x0, y0):.2f} mm).")
    return w


def build_plan(polys: list[Polyline], p: Profile, order: bool = True,
               optimize_start: bool = True) -> Plan:
    from .geometry import apply_overcut, order_for_cutting

    m = to_machine(polys, p)
    if order:
        m = order_for_cutting(m, start=(0.0, 0.0), optimize_start=optimize_start)
    if p.overcut_mm > 0:
        m = [apply_overcut(q, p.overcut_mm) for q in m]
    cut = sum(q.length() for q in m) * max(1, p.passes)
    trav = travel_length(m, (0.0, 0.0)) * max(1, p.passes)
    secs = cut / max(p.est_cut_mm_s, 1e-6) + trav / max(p.est_travel_mm_s, 1e-6)
    return Plan(m, cut, trav, bbox_of(m), secs, check_limits(m, p))
