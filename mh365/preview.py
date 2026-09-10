"""Render the toolpath so you can see what will happen before the knife moves."""
from __future__ import annotations

from .geometry import Polyline
from .machine import Plan, Profile

MM = 96.0 / 25.4  # display px per mm


def render_svg(plan: Plan, p: Profile, path: str, show_travel: bool = True) -> str:
    x0, y0, x1, y1 = plan.bbox
    carriage = p.width_mm
    pad = 10.0
    W = (max(x1, 0) + pad * 2)
    H = (max(y1, carriage) + pad * 2)

    def X(v):
        return (v + pad) * MM

    def Y(v):
        return (H - (v + pad)) * MM  # machine Y is up; SVG Y is down

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W*MM:.0f}" height="{H*MM:.0f}" '
        f'viewBox="0 0 {W*MM:.0f} {H*MM:.0f}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="8" y="20" font-family="system-ui" font-size="13" fill="#333">'
        f'{p.name}: cut {plan.cut_mm/1000:.2f} m, travel {plan.travel_mm/1000:.2f} m, '
        f'{plan.width_mm:.1f} x {plan.height_mm:.1f} mm, est {plan.seconds/60:.1f} min</text>',
    ]
    # carriage limit band
    if p.swap_axes:
        out.append(
            f'<rect x="{X(0):.1f}" y="{Y(y1):.1f}" width="{(carriage)*MM:.1f}" '
            f'height="{(y1-0)*MM:.1f}" fill="none" stroke="#e0006c" '
            f'stroke-width="1" stroke-dasharray="6 4"/>'
        )
    else:
        out.append(
            f'<rect x="{X(0):.1f}" y="{Y(carriage):.1f}" width="{(max(x1,0))*MM:.1f}" '
            f'height="{carriage*MM:.1f}" fill="none" stroke="#e0006c" '
            f'stroke-width="1" stroke-dasharray="6 4"/>'
        )
    if show_travel:
        here = (0.0, 0.0)
        d = []
        for q in plan.polys:
            d.append(f"M{X(here[0]):.1f},{Y(here[1]):.1f}L{X(q.pts[0][0]):.1f},{Y(q.pts[0][1]):.1f}")
            here = q.end()
        out.append(
            f'<path d="{"".join(d)}" fill="none" stroke="#9bb7d4" stroke-width="0.7" '
            f'stroke-dasharray="3 3"/>'
        )
    for q in plan.polys:
        pts = list(q.pts) + ([q.pts[0]] if q.closed else [])
        d = "M" + "L".join(f"{X(a):.2f},{Y(b):.2f}" for a, b in pts)
        out.append(f'<path d="{d}" fill="none" stroke="#1d6b2f" stroke-width="1.1"/>')
    for i, q in enumerate(plan.polys[:60]):
        out.append(
            f'<circle cx="{X(q.pts[0][0]):.1f}" cy="{Y(q.pts[0][1]):.1f}" r="2.4" '
            f'fill="#e0006c"/>'
        )
    out.append(f'<circle cx="{X(0):.1f}" cy="{Y(0):.1f}" r="4" fill="none" stroke="#000"/>')
    out.append("</svg>")
    svg = "\n".join(out)
    with open(path, "w") as f:
        f.write(svg)
    return path
