import math
from pathlib import Path

import pytest

from cookiemaker import config as cfg_mod
from cookiemaker import main
from cookiemaker.bridges import auto_bridges, is_single_body
from cookiemaker.config import load_config, resolve, save_config
from cookiemaker.solid import extrude, footprint
from cookiemaker.svg_io import parse_svg

SYNTHETIC = """<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">
  <style>
    .st0{fill:none;stroke:#000000;stroke-miterlimit:10;}
    .st1{stroke:#000000;stroke-miterlimit:10;}
  </style>
  <path class="st0" d="M10,10 L40,10"/>
  <path class="st0" d="M50,20 L70,20 L70,40 Z"/>
  <circle class="st1" cx="85" cy="45" r="5"/>
</svg>
"""


@pytest.fixture
def synthetic_svg(tmp_path: Path) -> Path:
    p = tmp_path / "syn.svg"
    p.write_text(SYNTHETIC)
    return p


def test_parse_loops(synthetic_svg: Path):
    loops = parse_svg(synthetic_svg, scale=1.0)
    # open line, closed stroked triangle, filled circle
    assert len(loops) == 3
    open_l, ring, filled = loops
    assert not open_l.closed and not open_l.filled and open_l.css_class == "st0"
    assert ring.closed and not ring.filled
    assert filled.filled and filled.closed
    # y-up + shift into positive quadrant: content bbox is x[10,90] y[10,50] in svg
    # open line svg(10,10)-(40,10) -> x[0,30], y[40,40]
    x0, y0, x1, y1 = open_l.bbox
    assert (x0, x1) == pytest.approx((0.0, 30.0))
    assert y0 == pytest.approx(40.0) and y1 == pytest.approx(40.0)
    # circle cx=85 cy=45 r=5 -> x[70,80]; bottom-most content is the circle
    # itself (svg y 40..50, max_y=50) -> y[0,10]
    x0, y0, x1, y1 = filled.bbox
    assert (x0, x1) == pytest.approx((70.0, 80.0), abs=0.05)
    assert (y0, y1) == pytest.approx((0.0, 10.0), abs=0.05)


def test_footprint_kinds(synthetic_svg: Path):
    open_l, ring, filled = parse_svg(synthetic_svg, scale=1.0)
    w = 2.0
    po = footprint(open_l, w)
    assert po is not None and po.area > 0
    # open stroke of length 30, width 2 -> band 60 + round caps (~3.1)
    assert 60 <= po.area < 68
    pr = footprint(ring, w)
    assert pr is not None and len(pr.interiors) == 1  # ring has a hole
    pf = footprint(filled, w)
    assert pf is not None and len(pf.interiors) == 0
    # flattened polygon slightly under-approximates the circle (r=5 -> ~2%)
    assert pf.area == pytest.approx(math.pi * 25, rel=0.05)


def test_extrude_volume(synthetic_svg: Path):
    open_l, _, _ = parse_svg(synthetic_svg, scale=1.0)
    m = extrude(open_l, z=5.0, width=2.0)
    assert m is not None
    assert 300 <= m.volume() < 340  # 30 * 2 * 5 = 300 + caps


def test_auto_bridges_single_body():
    from shapely.geometry import box

    a = box(0, 0, 10, 10)
    b = box(30, 0, 40, 10)
    c = box(60, 5, 70, 15)
    links, warnings = auto_bridges(
        [(a, 4.0), (b, 4.0), (c, 4.0)], width=2.0, bridge_z=2.0
    )
    assert len(links) == 2  # 3 islands -> 2 bridges
    assert warnings == []
    from shapely.ops import unary_union

    merged = unary_union([a, b, c, *(lk.poly for lk in links)])
    assert is_single_body(merged)


def test_config_resolution(synthetic_svg: Path):
    loops = parse_svg(synthetic_svg)
    cfg = cfg_mod.Project(
        svg=synthetic_svg,
        output=synthetic_svg.with_suffix(".stl"),
        default_z=4.0,
        default_width=2.0,
        classes={"st1": cfg_mod.Overrides(z=6.0)},
        loops={"0": cfg_mod.Overrides(z=8.0, width=3.0)},
    )
    assert resolve(cfg, loops[0]) == (8.0, 3.0)  # loop override wins
    assert resolve(cfg, loops[2]) == (6.0, 2.0)  # class override
    assert resolve(cfg, loops[1]) == (4.0, 2.0)  # defaults


def test_config_bridge_style_roundtrip(synthetic_svg: Path, tmp_path: Path):
    cfg_path = tmp_path / "styled.toml"
    cfg_path.write_text(
        f'svg = "{synthetic_svg}"\noutput = "{tmp_path / "o.stl"}"\n'
        '[bridges]\nmode = "auto"\nstyle = "tree"\nbranches = 3\n'
        "width = 2.0\nz = 1.5\nradius = 30.0\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.bridges.style == "tree"
    assert cfg.bridges.branches == 3
    assert cfg.bridges.radius == 30.0
    saved = tmp_path / "saved.toml"
    cfg.config_path = saved
    saved.write_text(save_config(cfg))
    cfg2 = load_config(saved)
    assert cfg2.bridges.style == "tree"
    assert cfg2.bridges.branches == 3
    assert cfg2.bridges.radius == 30.0
    with pytest.raises(ValueError):
        cfg_path.write_text(f'svg = "{synthetic_svg}"\n[bridges]\nstyle = "nonsense"\n')
        load_config(cfg_path)
    with pytest.raises(ValueError):
        cfg_path.write_text(f'svg = "{synthetic_svg}"\n[bridges]\nradius = -5.0\n')
        load_config(cfg_path)


def test_end_to_end_watertight(synthetic_svg: Path, tmp_path: Path, capsys):
    cfg_path = tmp_path / "syn.toml"
    rc = main(["inspect", str(synthetic_svg), "-o", str(cfg_path)])
    assert rc == 0
    cfg = load_config(cfg_path)
    assert (tmp_path / cfg.svg.name).exists()  # svg path resolved relative to config

    out = tmp_path / "out.stl"
    rc = main(["build", str(cfg_path), "-o", str(out)])
    assert rc == 0
    assert out.exists() and out.stat().st_size > 84
    import trimesh

    mesh = trimesh.load(out, force="mesh")
    assert isinstance(mesh, trimesh.Trimesh)
    assert mesh.is_watertight
    assert mesh.volume > 0


def test_degenerate_elements_skipped(tmp_path: Path):
    p = tmp_path / "deg.svg"
    p.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<path d="M5,5" fill="none" stroke="black"/>'
        '<path d="M0,0 L5,5" fill="none" stroke="black"/></svg>'
    )
    loops = parse_svg(p)
    assert len(loops) == 1
