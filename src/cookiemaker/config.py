"""TOML project configuration: per-loop / per-class Z and width overrides."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .svg_io import Loop

BRIDGE_MODES = ("auto", "manual", "off")


@dataclass
class Overrides:
    z: float | None = None
    width: float | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Overrides:
        return cls(z=d.get("z"), width=d.get("width"))


@dataclass
class BridgeConfig:
    mode: str = "auto"
    style: str = "bar"  # bar | tree | plate
    width: float = 1.5
    z: float = 2.0
    branches: int = 2  # tree style: links per island
    radius: float = 0.0  # tree style: max link length in mm (0 = unlimited)
    manual: list[list[str]] = field(default_factory=list)


@dataclass
class Project:
    svg: Path
    output: Path
    scale: float = 1.0
    default_z: float = 4.0
    default_width: float = 2.0
    classes: dict[str, Overrides] = field(default_factory=dict)
    loops: dict[str, Overrides] = field(default_factory=dict)
    bridges: BridgeConfig = field(default_factory=BridgeConfig)
    # Config file this project was loaded from (for GUI round-trip saves).
    config_path: Path | None = None


def load_config(path: Path | str) -> Project:
    path = Path(path)
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    base = path.parent
    svg = (base / data["svg"]).resolve() if "svg" in data else None
    if svg is None or not svg.exists():
        raise FileNotFoundError(f"config {path}: 'svg' entry missing or file not found")
    output = data.get("output")
    output = (base / output).resolve() if output else svg.with_suffix(".stl")

    bridges_raw = data.get("bridges", {})
    mode = bridges_raw.get("mode", "auto")
    if mode not in BRIDGE_MODES:
        raise ValueError(f"bridges.mode must be one of {BRIDGE_MODES}, got {mode!r}")
    b_width = float(bridges_raw.get("width", 1.5))
    b_z = float(bridges_raw.get("z", 2.0))
    if b_width <= 0:
        raise ValueError(f"bridges.width must be > 0, got {b_width:g}")
    if b_z <= 0:
        raise ValueError(f"bridges.z must be > 0, got {b_z:g}")
    style = str(bridges_raw.get("style", "bar"))
    if style not in ("bar", "tree", "plate"):
        raise ValueError(
            f'bridges.style must be "bar", "tree" or "plate", got {style!r}'
        )
    branches = int(bridges_raw.get("branches", 2))
    if branches < 1:
        raise ValueError(f"bridges.branches must be >= 1, got {branches}")
    radius = float(bridges_raw.get("radius", 0.0))
    if radius < 0:
        raise ValueError(f"bridges.radius must be >= 0, got {radius:g}")
    manual = [[str(a), str(b)] for a, b in bridges_raw.get("manual", [])]

    return Project(
        svg=svg,
        output=output,
        scale=float(data.get("scale", 1.0)),
        default_z=float(data.get("default_z", 4.0)),
        default_width=float(data.get("default_width", 2.0)),
        classes={
            k: Overrides.from_dict(v or {}) for k, v in data.get("classes", {}).items()
        },
        loops={
            k: Overrides.from_dict(v or {}) for k, v in data.get("loops", {}).items()
        },
        bridges=BridgeConfig(
            mode=mode,
            style=style,
            width=b_width,
            z=b_z,
            branches=branches,
            radius=radius,
            manual=manual,
        ),
        config_path=path.resolve(),
    )


def resolve(cfg: Project, loop: Loop) -> tuple[float, float]:
    """Effective (z, width) for a loop: loop override > class override > default."""
    o = cfg.loops.get(str(loop.id), Overrides())
    c = cfg.classes.get(loop.css_class, Overrides())
    z = o.z if o.z is not None else (c.z if c.z is not None else cfg.default_z)
    width = (
        o.width
        if o.width is not None
        else (c.width if c.width is not None else cfg.default_width)
    )
    return z, width


def _fmt(v: float) -> str:
    return f"{v:g}"


def dump_skeleton(
    svg_path: Path | str,
    output_path: Path | str,
    loops: list[Loop],
    default_z: float = 4.0,
    default_width: float = 2.0,
    base_dir: Path | str | None = None,
) -> str:
    """Render a TOML config skeleton listing every loop for per-loop editing.

    Paths are stored relative to base_dir (default: the config file location).
    """
    base = Path(base_dir) if base_dir else Path(output_path).parent

    def rel(p: Path | str) -> str:
        try:
            return str(Path(p).resolve().relative_to(base.resolve()))
        except ValueError:
            return str(Path(p).resolve())

    svg_path, output_path = Path(svg_path), Path(output_path)
    lines = [
        "# cookiemaker project config",
        "# Edit z / width per loop id (see table from `cookiemaker inspect`),",
        "# or per css class. Resolution: [loops] > [classes] > defaults.",
        f'svg = "{rel(svg_path)}"',
        f'output = "{rel(output_path)}"',
        "",
        "# SVG user units -> mm.",
        "scale = 1.0",
        "",
        "# Defaults for every loop without a more specific override.",
        f"default_z = {_fmt(default_z)}",
        f"default_width = {_fmt(default_width)}",
        "",
        "[classes]",
        "# st3 = { z = 6.0 }",
        "",
        "[loops]",
    ]
    for loop in loops:
        note = f"{loop.css_class or '-'} {loop.kind}"
        lines.append(
            f'# "{loop.id}" = {{ z = {_fmt(default_z)}, width = {_fmt(default_width)} }}  # {note}'
        )
    lines += [
        "",
        "[bridges]",
        'mode = "auto"  # auto | manual | off',
        'style = "bar"  # bar | tree | plate',
        "width = 1.5",
        "z = 2.0",
        "branches = 2  # tree style: connection points per loop",
        "radius = 0.0  # tree style: max link length mm (0 = unlimited)",
        '# manual = [["3", "7"]]  # pairs of loop ids (mode = "manual")',
        "",
    ]
    return "\n".join(lines)


def _override_line(key: str, o: Overrides) -> str:
    parts = []
    if o.z is not None:
        parts.append(f"z = {_fmt(o.z)}")
    if o.width is not None:
        parts.append(f"width = {_fmt(o.width)}")
    if not parts:
        return f'"{key}" = {{}}'
    return f'"{key}" = {{ {", ".join(parts)} }}'


def save_config(cfg: Project) -> str:
    """Serialize a Project (including GUI edits) back to valid TOML text.

    Paths are stored relative to the config file's directory when possible.
    """
    base = cfg.config_path.parent if cfg.config_path else Path.cwd()

    def rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(base.resolve()))
        except ValueError:
            return str(p.resolve())

    lines = [
        "# cookiemaker project config",
        "# Resolution: [loops] > [classes] > defaults.",
        f'svg = "{rel(cfg.svg)}"',
        f'output = "{rel(cfg.output)}"',
        "",
        "# SVG user units -> mm.",
        f"scale = {_fmt(cfg.scale)}",
        "",
        "# Defaults for every loop without a more specific override.",
        f"default_z = {_fmt(cfg.default_z)}",
        f"default_width = {_fmt(cfg.default_width)}",
        "",
    ]
    if cfg.classes:
        lines.append("[classes]")
        for name in sorted(cfg.classes):
            lines.append(_override_line(name, cfg.classes[name]))
        lines.append("")
    if cfg.loops:
        lines.append("[loops]")
        for lid in sorted(cfg.loops, key=int):
            lines.append(_override_line(lid, cfg.loops[lid]))
        lines.append("")
    b = cfg.bridges
    lines += [
        "[bridges]",
        f'mode = "{b.mode}"',
        f'style = "{b.style}"',
        f"width = {_fmt(b.width)}",
        f"z = {_fmt(b.z)}",
        f"branches = {b.branches}",
        f"radius = {_fmt(b.radius)}",
    ]
    if b.manual:
        pairs = ", ".join(f'["{a}", "{b_}"]' for a, b_ in b.manual)
        lines.append(f"manual = [{pairs}]")
    lines.append("")
    return "\n".join(lines)
