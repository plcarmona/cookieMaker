"""2D footprints from loops and their extrusion to 3D solids."""

from __future__ import annotations

import warnings
from typing import cast

warnings.filterwarnings(
    "ignore", message=".*global interpreter lock.*", category=RuntimeWarning
)

import numpy as np
from manifold3d import CrossSection, FillRule, Manifold
from shapely import make_valid
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from .svg_io import Loop

# Segments per quadrant for round joins/caps.
BUFFER_QUAD_SEGS = 16


def _as_polygon(geom: BaseGeometry) -> Polygon | None:
    return geom if isinstance(geom, Polygon) else None


def _largest_polygon(geom: BaseGeometry | None) -> Polygon | None:
    """Normalize a geometry to its largest polygon (heals self-intersections)."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return (
            _as_polygon(geom)
            if geom.is_valid
            else _as_polygon(cast(Polygon, make_valid(geom)))
        )
    candidates: list[Polygon] = []
    if hasattr(geom, "geoms"):
        for g in geom.geoms:  # type: ignore[attr-defined]
            if isinstance(g, Polygon):
                candidates.append(_as_polygon(cast(Polygon, make_valid(g))) or g)
    candidates = [p for p in candidates if not p.is_empty]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.area)


def footprint(loop: Loop, width: float) -> Polygon | None:
    """Closed 2D polygon for a loop.

    - filled shape: the polygon itself (with holes)
    - open stroke: the stroke outline (line buffered by width/2)
    - closed stroke: a ring (donut) of the given width
    """
    if width <= 0:
        return None
    if loop.filled:
        poly: BaseGeometry = Polygon(loop.coords, loop.holes or None)
    else:
        coords = loop.coords if not loop.closed else [*loop.coords, loop.coords[0]]
        poly = LineString(coords).buffer(
            width / 2.0,
            join_style="round",
            cap_style="round",
            quad_segs=BUFFER_QUAD_SEGS,
        )
    return _largest_polygon(poly)


def _to_cross_section(poly: Polygon) -> CrossSection:
    contours = [np.asarray(poly.exterior.coords, dtype=np.float64)]
    for ring in poly.interiors:
        contours.append(np.asarray(ring.coords, dtype=np.float64))
    return CrossSection(contours, FillRule.EvenOdd)


def extrude(loop: Loop, z: float, width: float) -> Manifold | None:
    """Extrude a loop's footprint from z=0 to z=z as a manifold solid."""
    if z <= 0:
        return None
    poly = footprint(loop, width)
    if poly is None or poly.is_empty or poly.area <= 0:
        return None
    return Manifold.extrude(_to_cross_section(poly), z)


def extrude_polygon(poly: Polygon, z: float) -> Manifold | None:
    """Extrude an arbitrary polygon footprint (e.g. a bridge bar) to height z."""
    if z <= 0 or poly is None or poly.is_empty or poly.area <= 0:
        return None
    return Manifold.extrude(_to_cross_section(poly), z)
