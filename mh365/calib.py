"""Calibration shapes.

Unit scale, which axis is the carriage, and mirroring cannot be read back from
an MH365 - it reports nothing about its own geometry. These shapes make the
answers measurable with a ruler.
"""
from __future__ import annotations

from .geometry import Polyline


def square(size_mm: float = 100.0) -> list[Polyline]:
    s = size_mm
    return [Polyline([(0, 0), (s, 0), (s, s), (0, s)], closed=True)]


def orientation_L(long_mm: float = 100.0, short_mm: float = 40.0) -> list[Polyline]:
    """An asymmetric L. Cut it, look at the vinyl, and you know the axis
    mapping and the handedness in one go: the long arm runs along machine X
    (the feed) and the short arm along machine Y (the carriage)."""
    a, b = long_mm, short_mm
    return [
        Polyline([(0, 0), (a, 0)], closed=False),
        Polyline([(0, 0), (0, b)], closed=False),
        # tick marks every 10 mm on the long arm
        *[Polyline([(x, 0), (x, 4)], closed=False) for x in range(10, int(a) + 1, 10)],
    ]
