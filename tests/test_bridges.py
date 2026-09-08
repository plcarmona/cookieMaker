"""Robustness tests for automatic bridges."""

from __future__ import annotations

import pytest
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from cookiemaker.bridges import (
    auto_bridges,
    build_bridges,
    is_single_body,
    manual_bridge,
    plate_bridge,
    tree_bridges,
)

Z = 4.0


def _tangent_disks():
    # Two disks tangent at (5, 0): classic silent-failure case.
    return [
        (Point(0, 0).buffer(5, quad_segs=32), Z),
        (Point(10, 0).buffer(5, quad_segs=32), Z),
    ]


def test_tangent_parts_get_full_height_patch():
    parts = _tangent_disks()
    links, warnings = auto_bridges(parts, width=1.5, bridge_z=2.0)
    assert warnings == []
    assert len(links) == 1
    assert links[0].kind == "patch"
    assert links[0].z == Z  # welds at the taller part's height, not bridge_z
    merged = unary_union([p for p, _ in parts] + [lk.poly for lk in links])
    assert is_single_body(merged)


def test_tangent_different_heights_weld_to_max():
    parts = [
        (Point(0, 0).buffer(5, quad_segs=32), 3.0),
        (Point(10, 0).buffer(5, quad_segs=32), 7.0),
    ]
    links, _ = auto_bridges(parts, width=1.5, bridge_z=2.0)
    assert len(links) == 1
    assert links[0].z == 7.0


def test_manual_bridge_tangent_returns_patch():
    (a, _), (b, _) = _tangent_disks()
    link = manual_bridge(a, b, width=1.5, bridge_z=2.0, za=3.0, zb=5.0)
    assert link is not None and not link.poly.is_empty
    assert link.kind == "patch"
    assert link.z == 5.0


def test_tiny_gap_gets_bar():
    parts = [
        (Point(0, 0).buffer(5, quad_segs=32), Z),
        (Point(10.001, 0).buffer(5, quad_segs=32), Z),
    ]
    links, warnings = auto_bridges(parts, width=1.5, bridge_z=2.0)
    assert warnings == []
    assert len(links) == 1
    assert links[0].kind == "bar"
    assert links[0].z == 2.0
    assert is_single_body(
        unary_union([p for p, _ in parts] + [lk.poly for lk in links])
    )


def test_island_chain_connects():
    parts = [(box(i * 20, 0, i * 20 + 8, 8), Z) for i in range(5)]
    links, warnings = auto_bridges(parts, width=2.0, bridge_z=2.0)
    assert warnings == []
    assert len(links) == 4
    assert all(lk.kind == "bar" for lk in links)
    assert is_single_body(
        unary_union([p for p, _ in parts] + [lk.poly for lk in links])
    )


def test_single_body_no_links():
    parts = [(box(0, 0, 10, 10), Z), (box(10, 0, 20, 10), Z)]  # edge-touching
    links, warnings = auto_bridges(parts, width=2.0, bridge_z=2.0)
    assert links == []
    assert warnings == []


def test_budget_exhaustion_warns():
    parts = [(box(i * 20, 0, i * 20 + 8, 8), Z) for i in range(5)]
    links, warnings = auto_bridges(parts, width=2.0, bridge_z=2.0, max_bridges=2)
    assert len(links) == 2
    assert len(warnings) == 1
    assert "exhausted" in warnings[0]
    assert "3 island(s) remain" in warnings[0]


def test_empty_input():
    assert auto_bridges([], width=2.0, bridge_z=2.0) == ([], [])


def _chain(n: int = 4) -> list[tuple[Polygon, float]]:
    return [(box(i * 20, 0, i * 20 + 8, 8), Z) for i in range(n)]


def _all_links_single_body(parts, links) -> bool:
    merged = unary_union([p for p, _ in parts] + [lk.poly for lk in links])
    return is_single_body(merged)


def test_tree_style_more_connections_than_bar():
    parts = _chain(4)
    bar_links, w1 = build_bridges(parts, 2.0, 2.0, style="bar")
    tree_links, w2 = build_bridges(parts, 2.0, 2.0, style="tree", branches=2)
    assert w1 == w2 == []
    assert len(bar_links) == 3  # chain of 4 -> 3 bars
    assert len(tree_links) > len(bar_links)  # web adds extra links
    assert _all_links_single_body(parts, tree_links)


def test_tree_style_branches_grow_with_k():
    parts = _chain(5)
    k1, w1 = build_bridges(parts, 2.0, 2.0, style="tree", branches=1)
    k3, w2 = build_bridges(parts, 2.0, 2.0, style="tree", branches=3)
    assert w1 == w2 == []
    assert len(k3) > len(k1)
    assert _all_links_single_body(parts, k1)
    assert _all_links_single_body(parts, k3)


def test_plate_style_single_slab_connects_all():
    parts = _chain(5)  # far-apart islands no bar chain would enjoy
    links, warnings = build_bridges(parts, 2.0, 2.0, style="plate")
    assert warnings == []
    assert len(links) == 1
    assert links[0].kind == "plate"
    assert links[0].z == 2.0
    assert _all_links_single_body(parts, links)
    # slab covers every part
    for poly, _ in parts:
        assert links[0].poly.contains(poly.representative_point())


def test_plate_ignores_existing_connectivity():
    parts = [(box(0, 0, 10, 10), Z)]  # single island
    links, warnings = build_bridges(parts, 2.0, 2.0, style="plate")
    assert warnings == []
    assert len(links) == 1 and links[0].kind == "plate"


def test_plate_welds_tangent_parts():
    # corner-touching squares pinch above the plate -> needs a full-height weld
    a = box(0, 0, 5, 5)
    b = box(5, 5, 10, 10)  # tangent to a at exactly (5, 5)
    parts = [(a, Z), (b, Z)]
    links, warnings = build_bridges(parts, 1.5, 2.0, style="plate")
    assert warnings == []
    kinds = sorted(lk.kind for lk in links)
    assert kinds == ["patch", "plate"]
    weld = next(lk for lk in links if lk.kind == "patch")
    assert weld.z == Z  # full height, not plate z


def test_plate_skips_overlapping_parts():
    # real overlap: plate alone is already manifold, no weld needed
    a = box(0, 0, 5, 5)
    b = box(4, 4, 10, 10)
    links, warnings = build_bridges([(a, Z), (b, Z)], 1.5, 2.0, style="plate")
    assert warnings == []
    assert [lk.kind for lk in links] == ["plate"]


def test_plate_empty_input():
    assert plate_bridge([], 2.0, 2.0) is None


def test_tree_empty_and_single():
    assert tree_bridges([], 2.0, 2.0) == ([], [])
    assert tree_bridges([(box(0, 0, 5, 5), Z)], 2.0, 2.0) == ([], [])


def test_tree_skips_fused_pairs():
    # overlapping loops are already one chunk -> no links between them
    parts = [(box(0, 0, 10, 10), Z), (box(5, 5, 15, 15), Z)]
    links, warnings = build_bridges(parts, 2.0, 2.0, style="tree", branches=3)
    assert warnings == []
    assert links == []


def test_tree_welds_tangent_loops_at_full_height():
    a = box(0, 0, 5, 5)
    b = box(5, 5, 10, 10)  # tangent at (5, 5)
    links, warnings = build_bridges(
        [(a, 7.0), (b, 3.0)], 1.5, 2.0, style="tree", branches=1
    )
    assert warnings == []
    assert len(links) == 1
    assert links[0].kind == "patch"
    assert links[0].z == 7.0


def test_tree_radius_caps_link_length():
    # chain spacing: adjacent gap = 12mm, next-adjacent = 32mm
    parts = _chain(4)
    capped, w1 = build_bridges(parts, 2.0, 2.0, style="tree", branches=3, radius=15.0)
    full, w2 = build_bridges(parts, 2.0, 2.0, style="tree", branches=3, radius=0.0)
    assert w1 == w2 == []
    assert len(capped) == 3  # only adjacent pairs
    assert len(full) > len(capped)
    # capped web still connects everything (greedy fallback within radius is
    # not needed here: adjacent chain already forms a single body)
    assert _all_links_single_body(parts, capped)


def test_tree_no_saturation_at_island_count():
    # 6 boxes all merged into ONE island via overlaps of a central blob, plus
    # one distant box: island count alone must not cap loop-level branching.
    blob = box(40, 40, 60, 60)
    parts = [(box(35 + 10 * i, 45, 45 + 10 * i, 55), Z) for i in range(3)] + [
        (blob, Z),
        (box(100, 45, 108, 55), Z),
    ]
    # everything overlaps the blob -> single island + 1 far island = 2 islands
    k1, _ = build_bridges(parts, 1.5, 2.0, style="tree", branches=1)
    k4, _ = build_bridges(parts, 1.5, 2.0, style="tree", branches=4)
    assert len(k4) > len(k1)  # more branches -> more links despite 2 islands
    assert _all_links_single_body(parts, k4)


def test_dispatcher_rejects_unknown_style():
    with pytest.raises(ValueError, match="bridges.style"):
        build_bridges(_chain(2), 2.0, 2.0, style="voronoi")
