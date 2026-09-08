"""Parse SVG drawings into flattened 2D loops (polygons/polylines) in mm, y-up."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from svgelements import SVG, Shape
from svgelements import Path as SePath

# Flattening tolerance in SVG user units (before scaling to mm).
CHORD_TOL = 0.1
MAX_DEPTH = 18
# Two points closer than this (svg units) are considered identical.
MERGE_EPS = 1e-7


@dataclass
class Loop:
    """One drawable stroke or filled shape, flattened to coordinates.

    Coordinates are in mm, y-up, translated so the whole drawing sits
    in the positive quadrant.
    """

    id: int
    css_class: str
    closed: bool
    filled: bool
    coords: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)
    stroke_len: float = 0.0  # original path length in mm

    @property
    def kind(self) -> str:
        shape = "filled" if self.filled else ("ring" if self.closed else "open")
        return shape

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [x for x, _ in self.coords]
        ys = [y for _, y in self.coords]
        return min(xs), min(ys), max(xs), max(ys)


def _xy(pt: object) -> tuple[float, float]:
    if hasattr(pt, "x"):
        return float(pt.x), float(pt.y)  # type: ignore[attr-defined]
    c = complex(pt)  # type: ignore[arg-type]
    return c.real, c.imag


def _chord_error(
    p0: tuple[float, float], pm: tuple[float, float], p1: tuple[float, float]
) -> float:
    """Distance from pm to the chord p0-p1."""
    ax, ay = p0
    bx, by = p1
    mx, my = pm
    dx, dy = bx - ax, by - ay
    seg_len = (dx * dx + dy * dy) ** 0.5
    if seg_len < 1e-12:
        return ((mx - ax) ** 2 + (my - ay) ** 2) ** 0.5
    return abs((mx - ax) * dy - (my - ay) * dx) / seg_len


def _flatten_segment(
    seg: object,
    tol: float,
    p_start: tuple[float, float],
    p_end: tuple[float, float],
) -> list[tuple[float, float]]:
    """Adaptively sample a path segment into points (including both ends)."""
    pts: list[tuple[float, float]] = [p_start]

    def rec(
        t0: float, t1: float, a: tuple[float, float], b: tuple[float, float], depth: int
    ) -> None:
        tm = (t0 + t1) / 2.0
        pm = _xy(seg.point(tm))  # type: ignore[attr-defined]
        if depth >= MAX_DEPTH or _chord_error(a, pm, b) <= tol:
            return
        rec(t0, tm, a, pm, depth + 1)
        pts.append(pm)
        rec(tm, t1, pm, b, depth + 1)

    rec(0.0, 1.0, p_start, p_end, 0)
    pts.append(p_end)
    return pts


def _dedupe(
    points: list[tuple[float, float]], eps: float = MERGE_EPS
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    return out


def _is_filled(shape: Shape) -> bool:
    fill = getattr(shape, "fill", None)
    if fill is None:
        return False
    return getattr(fill, "value", None) is not None


def _subpaths(path: SePath) -> list[Any]:
    try:
        n = path.count_subpaths()
    except Exception:  # noqa: BLE001 -- svgelements raises assorted errors on odd paths
        n = 0
    if n:
        return [path.subpath(i) for i in range(n)]
    return [path]


def _flatten_path(path: SePath, tol: float) -> list[list[tuple[float, float]]]:
    """Flatten every subpath of a path to a deduplicated point list."""
    result: list[list[tuple[float, float]]] = []
    for sp in _subpaths(path):
        pts: list[tuple[float, float]] = []
        for seg in sp:
            try:
                p_start, p_end = _xy(seg.point(0)), _xy(seg.point(1))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001, S112 -- skip segments svgelements can't evaluate
                continue
            if (
                abs(p_start[0] - p_end[0]) <= MERGE_EPS
                and abs(p_start[1] - p_end[1]) <= MERGE_EPS
            ):
                continue  # Move / Close / degenerate
            pts.extend(_flatten_segment(seg, tol, p_start, p_end))
        pts = _dedupe(pts)
        if len(pts) >= 2:
            result.append(pts)
    return result


def _ring_area(ring: list[tuple[float, float]]) -> float:
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _is_closed(coords: list[tuple[float, float]], eps: float = MERGE_EPS) -> bool:
    x0, y0 = coords[0]
    x1, y1 = coords[-1]
    return abs(x1 - x0) <= eps and abs(y1 - y0) <= eps


def parse_svg(
    svg_path: Path | str, scale: float = 1.0, chord_tol: float = CHORD_TOL
) -> list[Loop]:
    """Parse an SVG file into Loop objects.

    Degenerate elements (moveto-only, zero-length) are skipped; loop ids are
    sequential over valid loops in document order.
    """
    svg = SVG.parse(str(svg_path))
    # Each entry: (css_class, filled, flattened subpaths in svg coords).
    raw: list[tuple[str, bool, list[list[tuple[float, float]]]]] = []

    for el in svg.elements():
        if not isinstance(el, Shape):
            continue
        path = el if isinstance(el, SePath) else SePath(el)
        css_class = (getattr(el, "values", {}).get("class") or "").strip()
        filled = _is_filled(el)
        flat = _flatten_path(path, chord_tol)
        if not flat:
            continue
        raw.append((css_class, filled, flat))

    # Transform: svg y-down -> y-up, scale to mm, shift into positive quadrant.
    all_pts = [p for _, _, subpaths in raw for sp in subpaths for p in sp]
    if not all_pts:
        return []
    min_x = min(p[0] for p in all_pts)
    max_y = max(p[1] for p in all_pts)

    def tx(p: tuple[float, float]) -> tuple[float, float]:
        return ((p[0] - min_x) * scale, (max_y - p[1]) * scale)

    def polyline_len(coords: list[tuple[float, float]]) -> float:
        return sum(
            ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
            for a, b in pairwise(coords)
        )

    loops: list[Loop] = []
    for css_class, filled, flat in raw:
        transformed = [[tx(p) for p in sp] for sp in flat]
        if filled:
            # Largest ring is the exterior, the rest are holes.
            ordered = sorted(transformed, key=_ring_area, reverse=True)
            exterior = ordered[0]
            loops.append(
                Loop(
                    id=len(loops),
                    css_class=css_class,
                    closed=True,
                    filled=True,
                    coords=exterior,
                    holes=[h for h in ordered[1:] if _ring_area(h) > 0],
                    stroke_len=polyline_len(exterior),
                )
            )
        else:
            for sp in transformed:
                closed = _is_closed(sp)
                if closed and len(sp) > 2:
                    sp = sp[:-1]  # drop duplicated endpoint
                loops.append(
                    Loop(
                        id=len(loops),
                        css_class=css_class,
                        closed=closed,
                        filled=False,
                        coords=sp,
                        stroke_len=polyline_len(sp),
                    )
                )
    return loops
