"""Shared build pipeline used by both the CLI and the GUI."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any

warnings.filterwarnings(
    "ignore", message=".*global interpreter lock.*", category=RuntimeWarning
)

from . import config as cfg_mod
from . import solid, stl_io
from .bridges import Link, build_bridges, manual_bridge
from .svg_io import Loop, parse_svg


@dataclass
class BuildResult:
    loops: list[Loop] = field(default_factory=list)
    footprints: dict[int, Any] = field(
        default_factory=dict
    )  # loop id -> shapely Polygon
    heights: dict[int, float] = field(default_factory=dict)  # loop id -> extrusion z
    bars: list[Link] = field(default_factory=list)  # bridge links
    solids: list[Any] = field(default_factory=list)  # manifold3d solids
    mesh: Any | None = None  # trimesh.Trimesh
    notes: list[str] = field(default_factory=list)


def build_project(cfg: cfg_mod.Project) -> BuildResult:
    """Full pipeline: parse SVG -> per-loop footprints -> extrusion -> bridges -> union."""
    loops = parse_svg(cfg.svg, scale=cfg.scale)
    if not loops:
        raise ValueError(f"no drawable elements found in {cfg.svg}")

    result = BuildResult(loops=loops)
    for loop in loops:
        z, width = cfg_mod.resolve(cfg, loop)
        poly = solid.footprint(loop, width)
        if poly is None:
            result.notes.append(
                f"loop {loop.id}: empty footprint (width={width:g}), skipped"
            )
            continue
        m = solid.extrude(loop, z, width)
        if m is None:
            result.notes.append(f"loop {loop.id}: z={z:g} <= 0, skipped")
            continue
        result.footprints[loop.id] = poly
        result.heights[loop.id] = z
        result.solids.append(m)

    bcfg = cfg.bridges
    if bcfg.mode != "off" and result.footprints:
        parts = [(poly, result.heights[lid]) for lid, poly in result.footprints.items()]
        links: list[Link] = []
        if bcfg.mode == "auto":
            links, bridge_warnings = build_bridges(
                parts,
                bcfg.width,
                bcfg.z,
                style=bcfg.style,
                branches=bcfg.branches,
                radius=bcfg.radius,
            )
            result.notes.extend(bridge_warnings)
        elif bcfg.manual:
            for a, b in bcfg.manual:
                try:
                    ia, ib = int(a), int(b)
                    pa, pb = result.footprints[ia], result.footprints[ib]
                except KeyError, ValueError:
                    result.notes.append(
                        f"warning: bridge {a}-{b}: unknown loop id, skipped"
                    )
                    continue
                link = manual_bridge(
                    pa,
                    pb,
                    bcfg.width,
                    bcfg.z,
                    result.heights.get(ia, 0.0),
                    result.heights.get(ib, 0.0),
                )
                if link is not None and not link.poly.is_empty:
                    links.append(link)
        for link in links:
            m = solid.extrude_polygon(link.poly, link.z)
            if m is not None:
                result.solids.append(m)
        result.bars = links
        if links:
            patches = sum(1 for lk in links if lk.kind == "patch")
            if bcfg.style == "plate":
                note = f"bridges: base plate (width={bcfg.width:g}mm margin, z={bcfg.z:g}mm)"
            else:
                note = (
                    f"bridges[{bcfg.style}]: {len(links)} added "
                    f"(width={bcfg.width:g}mm, z={bcfg.z:g}mm"
                    + (f", branches={bcfg.branches}" if bcfg.style == "tree" else "")
                    + (
                        f", radius={bcfg.radius:g}mm"
                        if bcfg.style == "tree" and bcfg.radius > 0
                        else ""
                    )
                    + ")"
                )
            if patches:
                note += f", incl. {patches} tangent weld(s) at full height"
            result.notes.append(note)
        elif bcfg.mode == "auto" and not any(
            n.startswith("warning") for n in result.notes
        ):
            result.notes.append("bridges: none needed (already a single body)")

    result.mesh = stl_io.build_mesh(result.solids)
    return result
