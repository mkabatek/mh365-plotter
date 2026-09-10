"""Polyline geometry: nesting, cut ordering, overcut, measurement."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

Point = tuple[float, float]


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


@dataclass
class Polyline:
    """A single continuous run of the knife. Coordinates are millimetres."""

    pts: list[Point]
    closed: bool = False
    label: str = ""

    def length(self) -> float:
        p = self.pts + ([self.pts[0]] if self.closed and len(self.pts) > 1 else [])
        return sum(dist(p[i], p[i + 1]) for i in range(len(p) - 1))

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return min(xs), min(ys), max(xs), max(ys)

    def start(self) -> Point:
        return self.pts[0]

    def end(self) -> Point:
        return self.pts[0] if self.closed else self.pts[-1]

    def reverse(self) -> "Polyline":
        return replace(self, pts=list(reversed(self.pts)))

    def rotate_start(self, i: int) -> "Polyline":
        """Re-start a closed loop at vertex i (a closed loop may begin anywhere)."""
        if not self.closed or i == 0:
            return self
        return replace(self, pts=self.pts[i:] + self.pts[:i])

    def nearest_vertex(self, to: Point) -> int:
        return min(range(len(self.pts)), key=lambda i: dist(self.pts[i], to))


def bbox_of(polys: list[Polyline]) -> tuple[float, float, float, float]:
    if not polys:
        return 0.0, 0.0, 0.0, 0.0
    bs = [p.bbox() for p in polys]
    return (
        min(b[0] for b in bs), min(b[1] for b in bs),
        max(b[2] for b in bs), max(b[3] for b in bs),
    )


def map_points(polys: list[Polyline], fn) -> list[Polyline]:
    return [replace(p, pts=[fn(x, y) for (x, y) in p.pts]) for p in polys]


def point_in_ring(pt: Point, ring: list[Point]) -> bool:
    """Even-odd ray cast. `ring` is treated as closed."""
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            t = (y - y0) / (y1 - y0)
            if x < x0 + t * (x1 - x0):
                inside = not inside
    return inside


def nesting_depths(polys: list[Polyline]) -> list[int]:
    """How many other closed loops contain each polyline.

    Cutting deepest-first means interior holes are released before the outline
    that holds them, so nothing shifts mid-cut.
    """
    boxes = [p.bbox() for p in polys]
    depths = []
    for i, p in enumerate(polys):
        probe = p.pts[0]
        d = 0
        for j, q in enumerate(polys):
            if i == j or not q.closed:
                continue
            bx = boxes[j]
            if not (bx[0] <= probe[0] <= bx[2] and bx[1] <= probe[1] <= bx[3]):
                continue
            if point_in_ring(probe, q.pts):
                d += 1
        depths.append(d)
    return depths


def _seq_cost(seq: list[Polyline], here: Point) -> float:
    total, cur = 0.0, here
    for p in seq:
        total += dist(cur, p.start())
        cur = p.end()
    return total


def _improve(seq: list[Polyline], here: Point, max_n: int = 300,
             rounds: int = 6) -> list[Polyline]:
    """or-opt + 2-opt on the visiting sequence.

    Greedy nearest-neighbour alone can be worse than the file's own order, so
    the sequence gets refined afterwards. Cheap: these files hold tens of
    paths, not thousands.
    """
    n = len(seq)
    if n < 3 or n > max_n:
        return seq
    best, bestc = seq[:], _seq_cost(seq, here)
    for _ in range(rounds):
        improved = False
        for i in range(n):                      # or-opt: relocate one path
            for j in range(n):
                if i == j:
                    continue
                cand = best[:]
                cand.insert(j, cand.pop(i))
                c = _seq_cost(cand, here)
                if c < bestc - 1e-9:
                    best, bestc, improved = cand, c, True
        for i in range(n - 1):                  # 2-opt: reverse a run
            for j in range(i + 2, n + 1):
                cand = best[:i] + best[i:j][::-1] + best[j:]
                c = _seq_cost(cand, here)
                if c < bestc - 1e-9:
                    best, bestc, improved = cand, c, True
        if not improved:
            break
    return best


def _reseat(seq: list[Polyline], here: Point, optimize_start: bool) -> list[Polyline]:
    """Pick each path's entry point given who now precedes it."""
    out, cur = [], here
    for p in seq:
        if p.closed and optimize_start:
            p = p.rotate_start(p.nearest_vertex(cur))
        elif not p.closed and dist(cur, p.pts[-1]) < dist(cur, p.pts[0]):
            p = p.reverse()
        out.append(p)
        cur = p.end()
    return out


def order_for_cutting(
    polys: list[Polyline],
    start: Point = (0.0, 0.0),
    optimize_start: bool = True,
    respect_nesting: bool = True,
) -> list[Polyline]:
    """Order the cut.

    Interior holes are released before the contour containing them, so a freed
    island cannot shift while the knife is still working around it. That
    constraint comes first; travel is minimised within it.
    """
    if not polys:
        return []
    depths = nesting_depths(polys) if respect_nesting else [0] * len(polys)
    out: list[Polyline] = []
    here = start
    for d in sorted(set(depths), reverse=True):
        pool = [p for p, dd in zip(polys, depths) if dd == d]
        greedy: list[Polyline] = []
        cur = here
        while pool:
            i = min(range(len(pool)),
                    key=lambda k: min(dist(cur, pool[k].pts[0]), dist(cur, pool[k].pts[-1])))
            p = pool.pop(i)
            greedy.append(p)
            cur = p.end()
        seq = _improve(greedy, here)
        seq = _reseat(seq, here, optimize_start)
        out += seq
        here = seq[-1].end() if seq else here
    return out


def apply_overcut(p: Polyline, mm: float) -> Polyline:
    """Continue a closed loop past its start point.

    A drag knife pivots behind the spindle, so the last fraction of a
    millimetre of a loop is never actually severed. Re-cutting a short lead-in
    closes it.
    """
    if not p.closed or mm <= 0 or len(p.pts) < 2:
        return p
    pts = list(p.pts) + [p.pts[0]]
    run, i = 0.0, 0
    extra: list[Point] = []
    while run < mm and i < len(pts) - 1:
        a, b = pts[i], pts[i + 1]
        seg = dist(a, b)
        if seg <= 0:
            i += 1
            continue
        if run + seg >= mm:
            t = (mm - run) / seg
            extra.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            break
        extra.append(b)
        run += seg
        i += 1
    return replace(p, pts=pts + extra, closed=False)


def travel_length(polys: list[Polyline], start: Point = (0.0, 0.0)) -> float:
    here, total = start, 0.0
    for p in polys:
        total += dist(here, p.pts[0])
        here = p.end()
    return total + dist(here, start)
