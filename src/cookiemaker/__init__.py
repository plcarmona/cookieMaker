"""cookiemaker: SVG line art -> extruded STL with per-loop Z/width and auto bridges."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as cfg_mod
from . import stl_io
from .pipeline import build_project
from .svg_io import Loop, parse_svg


def _print_table(loops: list[Loop], cfg: cfg_mod.Project) -> None:
    header = f"{'id':>4}  {'class':<6} {'kind':<7} {'pts':>5}  {'z':>6} {'width':>6}  bbox (mm)"
    print(header)
    print("-" * len(header))
    for loop in loops:
        z, width = cfg_mod.resolve(cfg, loop)
        x0, y0, x1, y1 = loop.bbox
        print(
            f"{loop.id:>4}  {loop.css_class or '-':<6} {loop.kind:<7} {len(loop.coords):>5}"
            f"  {z:>6g} {width:>6g}  [{x0:.1f}, {y0:.1f}] .. [{x1:.1f}, {y1:.1f}]"
        )


def cmd_inspect(args: argparse.Namespace) -> int:
    svg_path = Path(args.svg)
    if not svg_path.exists():
        print(f"error: {svg_path} not found", file=sys.stderr)
        return 1
    loops = parse_svg(svg_path, scale=args.scale)
    if not loops:
        print("error: no drawable elements found", file=sys.stderr)
        return 1

    out = Path(args.config) if args.config else svg_path.with_suffix(".toml")
    project = cfg_mod.Project(svg=svg_path, output=svg_path.with_suffix(".stl"))
    _print_table(loops, project)
    skeleton = cfg_mod.dump_skeleton(
        svg_path, svg_path.with_suffix(".stl"), loops, base_dir=out.parent
    )
    out.write_text(skeleton)
    print(f"\n{len(loops)} loops -> config skeleton written to {out}")
    print("Edit z/width values there, then: cookiemaker build", out)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    try:
        cfg = cfg_mod.load_config(args.config)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output:
        cfg.output = Path(args.output)

    try:
        result = build_project(cfg)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    mesh = result.mesh
    if mesh is None:
        print("error: nothing to export (all loops skipped?)", file=sys.stderr)
        return 1

    cfg.output.parent.mkdir(parents=True, exist_ok=True)
    stl_io.export_stl(mesh, cfg.output)
    for note in result.notes:
        print(note)
    print(f"-> {cfg.output}  [{stl_io.report(mesh)}]")
    if not mesh.is_watertight:
        print("warning: mesh is not watertight", file=sys.stderr)
        return 2
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        import matplotlib
    except ImportError:
        print(
            "error: matplotlib is required for the gui."
            " Install with: uv sync --extra gui",
            file=sys.stderr,
        )
        return 1
    try:
        cfg = cfg_mod.load_config(args.config)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output:
        cfg.output = Path(args.output)

    if matplotlib.get_backend().lower() == "agg" and not matplotlib.is_interactive():
        print(
            "error: matplotlib has no interactive backend here;"
            " install tk or set MPLBACKEND to a gui backend",
            file=sys.stderr,
        )
        return 1

    from .gui import run_gui

    try:
        run_gui(cfg)
    except Exception as exc:  # noqa: BLE001 -- toolkit/display failures (TclError etc.)
        print(f"error: gui failed to start: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    try:
        cfg = cfg_mod.load_config(args.config)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output:
        cfg.output = Path(args.output)

    from .web import run_web

    run_web(cfg, port=args.port, open_browser=not args.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cookiemaker", description="SVG line art -> extruded STL"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inspect = sub.add_parser(
        "inspect", help="parse SVG, print loop table, write config skeleton"
    )
    p_inspect.add_argument("svg", help="input SVG file")
    p_inspect.add_argument(
        "-o", "--config", help="config skeleton path (default: <svg>.toml)"
    )
    p_inspect.add_argument(
        "--scale", type=float, default=1.0, help="svg units -> mm (default 1.0)"
    )
    p_inspect.set_defaults(func=cmd_inspect)

    p_build = sub.add_parser(
        "build", help="extrude SVG per config, add bridges, export STL"
    )
    p_build.add_argument("config", help="project TOML config (from inspect)")
    p_build.add_argument("-o", "--output", help="output STL path (overrides config)")
    p_build.set_defaults(func=cmd_build)

    p_gui = sub.add_parser("gui", help="interactive editor (needs --extra gui)")
    p_gui.add_argument("config", help="project TOML config (from inspect)")
    p_gui.add_argument("-o", "--output", help="output STL path (overrides config)")
    p_gui.set_defaults(func=cmd_gui)

    p_web = sub.add_parser("web", help="browser editor with three.js viewer")
    p_web.add_argument("config", help="project TOML config (from inspect)")
    p_web.add_argument("-o", "--output", help="output STL path (overrides config)")
    p_web.add_argument("--port", type=int, default=8765, help="port to serve on")
    p_web.add_argument(
        "--no-browser", action="store_true", help="do not auto-open the browser"
    )
    p_web.set_defaults(func=cmd_web)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
