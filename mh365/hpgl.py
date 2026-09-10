"""HPGL generation for MH-series cutters."""
from __future__ import annotations

from .geometry import Polyline

ESC = "\x1b"


class HpglWriter:
    """Emit HPGL.

    MH-series cutters accept a small HPGL subset: IN/DF (init), SP (pen),
    PU/PD (move/cut), PA (absolute), and optionally VS/FS for speed and force.
    Coordinates are integers in plotter units (default 1016 per inch).
    """

    def __init__(
        self,
        units_per_mm: float,
        speed: int | None = None,
        force: int | None = None,
        max_coords_per_cmd: int = 16,
    ) -> None:
        self.u = units_per_mm
        self.speed = speed
        self.force = force
        self.max_coords = max(1, max_coords_per_cmd)

    def _q(self, p) -> tuple[int, int]:
        return int(round(p[0] * self.u)), int(round(p[1] * self.u))

    def preamble(self) -> list[str]:
        out = [f"{ESC}.(", "IN;", "DF;", "PA;", "SP1;"]
        if self.speed is not None:
            out.append(f"VS{int(self.speed)};")
        if self.force is not None:
            out.append(f"FS{int(self.force)};")
        return out

    def postamble(self, park: tuple[float, float] | None = (0.0, 0.0)) -> list[str]:
        out = ["PU;"]
        if park is not None:
            x, y = self._q(park)
            out.append(f"PU{x},{y};")
        out += ["SP0;", f"{ESC}.)"]
        return out

    def polyline(self, p: Polyline) -> list[str]:
        pts = list(p.pts) + ([p.pts[0]] if p.closed else [])
        x, y = self._q(pts[0])
        cmds = [f"PU{x},{y};"]
        rest = [self._q(q) for q in pts[1:]]
        for i in range(0, len(rest), self.max_coords):
            chunk = rest[i : i + self.max_coords]
            cmds.append("PD" + ",".join(f"{a},{b}" for a, b in chunk) + ";")
        return cmds

    def program(
        self,
        polys: list[Polyline],
        passes: int = 1,
        park: tuple[float, float] | None = (0.0, 0.0),
    ) -> list[str]:
        cmds = self.preamble()
        for _ in range(max(1, passes)):
            for p in polys:
                cmds += self.polyline(p)
        cmds += self.postamble(park)
        return cmds


def program_text(cmds: list[str]) -> str:
    return "".join(cmds)
