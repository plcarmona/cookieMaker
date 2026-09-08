# cookiemaker

SVG line art to 3D-printable extruded STL: give every stroke its own height
and width, bridge the loose parts into one solid body, and export a watertight
mesh ready for the slicer.

![web editor](docs/web.png)

## Features

- **Per-loop Z and width** — every path/circle/line in the SVG becomes an
  editable loop with its own extrusion height and stroke thickness
  (overridable per loop, per CSS class, or globally)
- **Three bridge styles** — `bar` (shortest links), `tree` (dendritic
  k-nearest web with per-loop attachment points), `plate` (full-area base
  slab)
- **Robust connectivity** — tangent parts are welded at full height, bridge
  failures surface as warnings instead of silent disconnects
- **Watertight output** — manifold boolean union with float32 snapping, so
  the STL survives the export/import round-trip
- **Interactive web editor** — three.js viewer, click-to-select loops, live
  rebuilds, Save TOML / Export STL

## Install

Requires Python >= 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # CLI only (svgelements, shapely, trimesh, manifold3d)
uv sync --extra gui     # + matplotlib fallback GUI
```

## Quickstart

```bash
# 1. Inspect: print the loop table and write a config skeleton
cookiemaker inspect input_svg/tony.svg        # -> tony.toml

# 2. Edit z / width per loop id (or per class) in tony.toml

# 3. Build: extrude, bridge, export
cookiemaker build tony.toml                   # -> output_stl/tony.stl
```

Or skip the text editor entirely:

```bash
cookiemaker web tony.toml    # browser editor at http://127.0.0.1:8765
```

## Usage

```
cookiemaker inspect <svg> [-o CONFIG] [--scale SCALE]
    Parse the SVG, print the loop table (id, class, kind, bbox) and write a
    TOML config skeleton. --scale converts svg units to mm (default 1.0).

cookiemaker build <config> [-o OUTPUT]
    Extrude every loop per config, add bridges, export binary STL.
    Exits 2 with a warning if the mesh is not watertight.

cookiemaker gui <config> [-o OUTPUT]
    Matplotlib editor (needs: uv sync --extra gui).

cookiemaker web <config> [-o OUTPUT] [--port PORT] [--no-browser]
    Browser editor with a three.js viewer (default port 8765).
```

## Configuration

```toml
svg = "input_svg/tony.svg"
output = "output_stl/tony.stl"
scale = 1.0            # svg units -> mm

# defaults for loops without a more specific override
default_z = 4.0        # mm
default_width = 2.0    # mm stroke thickness in XY

[classes]              # CSS class from the SVG
st3 = { z = 6.0 }      # e.g. the filled pupils

[loops]                # per-loop overrides; ids from `cookiemaker inspect`
"14" = { z = 8.0, width = 3.0 }

[bridges]
mode = "auto"          # auto | manual | off
style = "bar"          # bar | tree | plate
width = 1.5            # mm
z = 2.0                # mm
branches = 2           # tree: connection points per loop
radius = 0.0           # tree: max link length mm (0 = unlimited)
# manual = [["3", "7"]]  # pairs of loop ids (mode = "manual")
```

Resolution order: `[loops]` > `[classes]` > defaults.

## Bridge styles

![bridge styles](docs/bridges.png)

- **`bar`** — one shortest link per disconnected island. Minimal material;
  on the sample face: 9 links.
- **`tree`** — dendritic web over the *individual loops* (not merged
  islands): every loop links to its `branches` nearest neighboring loops, so
  dense interior details (whiskers, pupils) get real multi-directional
  attachment. Links keep growing up to the all-pairs limit (sample: 54 links
  at `branches = 2`, 332 at `branches = 26`). `radius` caps link length so a
  high branch count stays local.
- **`plate`** — one convex-hull slab under everything, extruded to `z`.
  Everything sits on a common base (enclosed holes get filled below `z`).

Loops that already overlap are skipped (fused); loops that touch at a single
point get an automatic **full-height weld** so the union stays manifold.

## Web editor

- Orbit/zoom/pan (WebGL), height-colored mesh with colorbar
- Click a part to select its loop; edit z/width, bridge style/branches/radius,
  defaults and scale — live rebuild (~0.5 s for a 27-loop model)
- Save TOML / Export STL buttons use the exact same pipeline as the CLI
- Served locally on 127.0.0.1; three.js is vendored, no internet needed

## How it works

1. **Parse & flatten** — svgelements; bezier arcs sampled adaptively, Y axis
   flipped, scaled to mm (degenerate elements skipped)
2. **Footprints** — open strokes buffered to their width (shapely); closed
   strokes become rings; filled shapes become polygons with holes
3. **Extrusion** — each footprint extruded from Z=0 to its configured height
   (manifold3d)
4. **Bridges** — 2D connectivity analysis; bars/welds/plate extruded and
   merged
5. **Union & export** — single boolean union, vertices snapped to float32,
   degenerate faces dropped, binary STL written

## Development

```bash
uv run pytest            # 32 tests
uvx ruff check src tests
uvx ruff format src tests
```
