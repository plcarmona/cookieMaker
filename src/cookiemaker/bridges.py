"""Automatic bridges connecting disconnected islands into one printable body."""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from .solid import BUFFER_QUAD_SEGS

# Gaps below this (mm) count as tangent contact rather than a real gap.
CONTACT_EPS = 1e-6


@dataclass
class Link:
    """A bridge footprint plus the height it must be extruded to.

    kind == "bar": a bridge between two separated parts, extruded to the
    configured bridge z. kind == "patch": a weld for tangent contact; it is
    extruded to the tallest part it welds so the union stays manifold over
    the full height (a low patch would leave a pinched non-manifold edge
    where the parts touch above it).
    """

    poly: Polygon
    z: float
    kind: str  # "bar" | "patch"


def _components(geom: BaseGeometry) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, "geoms"):
        return [g for g in geom.geoms if isinstance(g, Polygon)]  # type: ignore[attr-defined]
    return []


def is_single_body(geom: BaseGeometry) -> bool:
    return isinstance(geom, Polygon) or len(_components(geom)) <= 1


def _bar(p1: object, p2: object, width: float) -> Polygon | None:
    """A bar polygon between two points, extended past both ends so it
    reliably overlaps the parts it connects."""
    x1, y1 = float(p1.x), float(p1.y)  # type: ignore[attr-defined]
    x2, y2 = float(p2.x), float(p2.y)  # type: ignore[attr-defined]
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0:
        return None
    ux, uy = dx / length, dy / length
    ext = width  # penetrate each part by a full width
    a = (x1 - ux * ext, y1 - uy * ext)
    b = (x2 + ux * ext, y2 + uy * ext)
    return LineString([a, b]).buffer(
        width / 2.0,
        cap_style="round",
        quad_segs=BUFFER_QUAD_SEGS,
    )


def _contact_patch(pt: object, width: float) -> Polygon:
    """Square patch (side 2*width) centered on a tangent contact point.

    Parts that touch at a single point need volumetric overlap, otherwise the
    3D union is pinched along a non-manifold edge (or fails to connect at all).
    """
    return box(
        float(pt.x) - width,  # type: ignore[attr-defined]
        float(pt.y) - width,  # type: ignore[attr-defined]
        float(pt.x) + width,  # type: ignore[attr-defined]
        float(pt.y) + width,  # type: ignore[attr-defined]
    )


def _dist(p1: object, p2: object) -> float:
    return ((float(p2.x) - float(p1.x)) ** 2 + (float(p2.y) - float(p1.y)) ** 2) ** 0.5  # type: ignore[attr-defined]


def _nearby_max_z(
    parts: list[tuple[Polygon, float]], pt: object, radius: float
) -> float | None:
    """Tallest part whose footprint reaches within radius of pt."""
    point = Point(float(pt.x), float(pt.y))  # type: ignore[attr-defined]
    heights = [z for poly, z in parts if poly.distance(point) <= radius]
    return max(heights) if heights else None


def _link(
    parts: list[tuple[Polygon, float]],
    p1: object,
    p2: object,
    width: float,
    bridge_z: float,
) -> Link | None:
    """Bar for a real gap, full-height weld patch for tangent contact."""
    if _dist(p1, p2) <= CONTACT_EPS:
        z1 = _nearby_max_z(parts, p1, width)
        z2 = _nearby_max_z(parts, p2, width)
        candidates = [z for z in (z1, z2, bridge_z) if z is not None]
        z = max(candidates) if candidates else bridge_z
        return Link(_contact_patch(p1, width), z, "patch")
    poly = _bar(p1, p2, width)
    if poly is None:
        return None
    return Link(poly, bridge_z, "bar")


def _greedy_connect(
    parts: list[tuple[Polygon, float]],
    merged: BaseGeometry,
    width: float,
    bridge_z: float,
    max_bridges: int,
    links: list[Link],
    warnings: list[str],
) -> BaseGeometry:
    """Greedily link remaining islands (smallest first) until one body remains."""
    if is_single_body(merged):
        return merged
    stuck = False
    for _ in range(max_bridges):
        if is_single_body(merged):
            break
        islands = _components(merged)
        # Smallest first (attach loose details to the big body); bounds as a
        # deterministic tie-break.
        islands.sort(key=lambda p: (p.area, p.bounds))
        before = len(islands)
        progressed = False
        for candidate in islands:
            rest = unary_union([i for i in islands if i is not candidate])
            p1, p2 = nearest_points(candidate, rest)
            link = _link(parts, p1, p2, width, bridge_z)
            if link is None or link.poly.is_empty:
                continue
            trial = unary_union([merged, link.poly])
            if len(_components(trial)) < before:
                merged = trial
                links.append(link)
                progressed = True
                break
        if not progressed:
            remaining = len(_components(merged))
            warnings.append(
                f"warning: bridges could not connect {remaining} island(s); "
                "check for degenerate/tangent geometry"
            )
            stuck = True
            break

    if not stuck and not is_single_body(merged):
        remaining = len(_components(merged))
        warnings.append(
            f"warning: bridge budget ({max_bridges}) exhausted, {remaining} island(s) remain"
        )
    return merged


def auto_bridges(
    parts: list[tuple[Polygon, float]],
    width: float,
    bridge_z: float,
    max_bridges: int = 500,
) -> tuple[list[Link], list[str]]:
    """Connect every disconnected island to its nearest neighbor.

    parts: (footprint, extrusion z) per loop. Returns (links, warnings);
    warnings describe islands that could not be connected (degenerate or
    tangent geometry that resisted linking, or bridge budget exhaustion)
    instead of failing silently.
    """
    if not parts:
        return [], []
    links: list[Link] = []
    warnings: list[str] = []
    merged = unary_union([poly for poly, _ in parts])
    _greedy_connect(parts, merged, width, bridge_z, max_bridges, links, warnings)
    return links, warnings


def _pair_gap(a: Polygon, b: Polygon) -> float:
    """Gap between two loops: 0.0 for tangent/overlapping, real distance otherwise."""
    return a.distance(b)


def _is_fused(a: Polygon, b: Polygon) -> bool:
    """True when two loops overlap with real area (already strongly connected)."""
    return a.distance(b) <= CONTACT_EPS and a.intersection(b).area > CONTACT_EPS


def tree_bridges(
    parts: list[tuple[Polygon, float]],
    width: float,
    bridge_z: float,
    branches: int = 2,
    radius: float = 0.0,
    max_bridges: int = 500,
) -> tuple[list[Link], list[str]]:
    """Dendritic web over individual loops (not merged islands).

    Each loop links to its `branches` nearest other loops, so dense interior
    details (whiskers, pupils...) get their own multi-directional attachment
    instead of relying on the island they happen to touch. Pairs overlapping
    with real area are already fused and are skipped; tangent pairs get
    full-height welds. `radius` > 0 caps link length (mm). A greedy
    island-level fallback guarantees a single body.
    """
    if not parts:
        return [], []

    links: list[Link] = []
    seen_pairs: set[tuple[int, int]] = set()
    polys = [poly for poly, _ in parts]
    n = len(polys)

    for i in range(n):
        neighbors: list[tuple[float, int]] = []
        for j in range(n):
            if i == j:
                continue
            pair = (min(i, j), max(i, j))
            if pair in seen_pairs:
                continue
            dist = _pair_gap(polys[i], polys[j])
            if dist <= CONTACT_EPS:
                if _is_fused(polys[i], polys[j]):
                    continue  # already one solid chunk
                neighbors.append((0.0, j))  # tangent: weld candidate
            elif radius <= 0 or dist <= radius:
                neighbors.append((dist, j))
        neighbors.sort()
        for _dist, j in neighbors[: max(1, branches)]:
            pair = (min(i, j), max(i, j))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            p1, p2 = nearest_points(polys[i], polys[j])
            link = _link(parts, p1, p2, width, bridge_z)
            if link is not None and not link.poly.is_empty:
                links.append(link)

    warnings: list[str] = []
    merged: BaseGeometry = unary_union(polys)
    if links:
        merged = unary_union([merged, *(lk.poly for lk in links)])
    _greedy_connect(parts, merged, width, bridge_z, max_bridges, links, warnings)
    return links, warnings


def plate_bridge(
    parts: list[tuple[Polygon, float]], width: float, bridge_z: float
) -> Link | None:
    """Full-area base: convex hull slab (plus a width/2 rounded margin)
    extruded as one plate under everything."""
    if not parts:
        return None
    hull = unary_union([poly for poly, _ in parts]).convex_hull
    slab = hull.buffer(
        width / 2.0,
        join_style="round",
        quad_segs=BUFFER_QUAD_SEGS,
    )
    if slab.is_empty or slab.area <= 0:
        return None
    return Link(slab, bridge_z, "plate")


BRIDGE_STYLES = ("bar", "tree", "plate")


def _tangent_welds(
    parts: list[tuple[Polygon, float]], width: float, bridge_z: float
) -> list[Link]:
    """Full-height patches for parts touching at a point.

    Point-tangent parts keep a pinched non-manifold edge above a low plate,
    so the plate style welds them explicitly. Pairs that overlap with real
    area are already manifold and are skipped.
    """
    welds: list[Link] = []
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, za = parts[i]
            b, zb = parts[j]
            if a.distance(b) > CONTACT_EPS:
                continue
            if a.intersection(b).area > CONTACT_EPS:
                continue  # volumetric overlap: no pinch
            p1, _ = nearest_points(a, b)
            patch = _contact_patch(p1, width)
            if not patch.is_empty:
                welds.append(Link(patch, max(za, zb, bridge_z), "patch"))
    return welds


def build_bridges(
    parts: list[tuple[Polygon, float]],
    width: float,
    bridge_z: float,
    style: str = "bar",
    branches: int = 2,
    radius: float = 0.0,
    max_bridges: int = 500,
) -> tuple[list[Link], list[str]]:
    """Dispatch on bridges.style: "bar" (single shortest links), "tree"
    (k-nearest web over loops), or "plate" (full-area slab)."""
    if style == "plate":
        link = plate_bridge(parts, width, bridge_z)
        if link is None:
            return [], ["warning: plate style produced an empty slab"]
        return [link, *_tangent_welds(parts, width, bridge_z)], []
    if style == "tree":
        return tree_bridges(
            parts,
            width,
            bridge_z,
            branches=branches,
            radius=radius,
            max_bridges=max_bridges,
        )
    if style == "bar":
        return auto_bridges(parts, width, bridge_z, max_bridges=max_bridges)
    raise ValueError(f"bridges.style must be one of {BRIDGE_STYLES}, got {style!r}")


def manual_bridge(
    a: Polygon,
    b: Polygon,
    width: float,
    bridge_z: float,
    za: float = 0.0,
    zb: float = 0.0,
) -> Link | None:
    """Bridge link between two specific footprints."""
    p1, p2 = nearest_points(a, b)
    return _link([(a, za), (b, zb)], p1, p2, width, bridge_z)
