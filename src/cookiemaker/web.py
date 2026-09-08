"""Local web GUI: serves a three.js viewer/editor and a small JSON API.

Runs a threaded stdlib HTTP server bound to 127.0.0.1. No external deps.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import config as cfg_mod
from . import stl_io
from .config import Project, save_config
from .pipeline import BuildResult, build_project

WEB_DIR = Path(__file__).parent / "web"
CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


class WebState:
    """Shared server state: the project config and the latest build result."""

    def __init__(self, cfg: Project) -> None:
        self.cfg = cfg
        self.lock = threading.Lock()
        self.result: BuildResult | None = None
        self.rebuild()

    def rebuild(self, updates: dict[str, Any] | None = None) -> None:
        if updates:
            _apply_updates(self.cfg, updates)
        result = build_project(self.cfg)
        with self.lock:
            self.result = result

    def payload(self) -> dict[str, Any]:
        with self.lock:
            result = self.result
            cfg = self.cfg
        if result is None:
            raise RuntimeError("no build result")
        return {
            "config": _config_dict(cfg),
            "loops": _loops_payload(cfg, result),
            "bridges": [
                {
                    "poly": _poly_dict(lk.poly),
                    "z": lk.z,
                    "kind": lk.kind,
                }
                for lk in result.bars
            ],
            "mesh": _mesh_payload(result),
            "notes": list(result.notes),
            "output": str(cfg.output),
        }


def _apply_updates(cfg: Project, updates: dict[str, Any]) -> None:
    """Replace config sections from a /api/build request body."""
    for key in ("scale", "default_z", "default_width"):
        if key in updates and updates[key] is not None:
            val = float(updates[key])  # type: ignore[arg-type]
            if val <= 0:
                raise ValueError(f"{key} must be > 0, got {val:g}")
            setattr(cfg, key, val)
    if "classes" in updates:
        cfg.classes = {
            str(k): cfg_mod.Overrides(**_clean(v))
            for k, v in updates["classes"].items()
        }
    if "loops" in updates:
        cfg.loops = {
            str(k): cfg_mod.Overrides(**_clean(v)) for k, v in updates["loops"].items()
        }
    if "bridges" in updates:
        b = updates["bridges"]
        mode = b.get("mode", cfg.bridges.mode)
        if mode not in cfg_mod.BRIDGE_MODES:
            raise ValueError(f"bridges.mode must be one of {cfg_mod.BRIDGE_MODES}")
        width = float(b.get("width", cfg.bridges.width))
        z = float(b.get("z", cfg.bridges.z))
        if width <= 0 or z <= 0:
            raise ValueError("bridges.width and bridges.z must be > 0")
        style = str(b.get("style", cfg.bridges.style))
        if style not in ("bar", "tree", "plate"):
            raise ValueError(
                f'bridges.style must be "bar", "tree" or "plate", got {style!r}'
            )
        branches = int(b.get("branches", cfg.bridges.branches))
        if branches < 1:
            raise ValueError(f"bridges.branches must be >= 1, got {branches}")
        radius = float(b.get("radius", cfg.bridges.radius))
        if radius < 0:
            raise ValueError(f"bridges.radius must be >= 0, got {radius:g}")
        cfg.bridges.mode = mode
        cfg.bridges.style = style
        cfg.bridges.width = width
        cfg.bridges.z = z
        cfg.bridges.branches = branches
        cfg.bridges.radius = radius


def _clean(v: dict[str, Any]) -> dict[str, float | None]:
    z = v.get("z")
    width = v.get("width")
    return {
        "z": float(z) if z is not None else None,
        "width": float(width) if width is not None else None,
    }


def _config_dict(cfg: Project) -> dict[str, Any]:
    return {
        "svg": str(cfg.svg),
        "output": str(cfg.output),
        "scale": cfg.scale,
        "default_z": cfg.default_z,
        "default_width": cfg.default_width,
        "classes": {
            name: {"z": o.z, "width": o.width} for name, o in cfg.classes.items()
        },
        "loops": {lid: {"z": o.z, "width": o.width} for lid, o in cfg.loops.items()},
        "bridges": {
            "mode": cfg.bridges.mode,
            "style": cfg.bridges.style,
            "width": cfg.bridges.width,
            "z": cfg.bridges.z,
            "branches": cfg.bridges.branches,
            "radius": cfg.bridges.radius,
            "manual": [list(p) for p in cfg.bridges.manual],
        },
    }


def _ring(coords: Any) -> list[list[float]]:
    return [[round(x, 3), round(y, 3)] for x, y in coords]


def _poly_dict(poly: Any) -> dict[str, Any]:
    return {
        "exterior": _ring(poly.exterior.coords),
        "holes": [_ring(r.coords) for r in poly.interiors],
    }


def _loops_payload(cfg: Project, result: BuildResult) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for loop in result.loops:
        z, width = cfg_mod.resolve(cfg, loop)
        poly = result.footprints.get(loop.id)
        out.append(
            {
                "id": loop.id,
                "cls": loop.css_class,
                "kind": loop.kind,
                "z": z,
                "width": width,
                "points": len(loop.coords),
                "bbox": [round(v, 2) for v in loop.bbox],
                "footprint": _poly_dict(poly) if poly is not None else None,
            }
        )
    return out


def _mesh_payload(result: BuildResult) -> dict[str, Any]:
    mesh = result.mesh
    if mesh is None:
        return {"positions": [], "faces": [], "watertight": False, "volume": 0.0}
    return {
        "positions": [round(float(v), 4) for v in mesh.vertices.ravel()],
        "faces": mesh.faces.ravel().tolist(),
        "watertight": bool(mesh.is_watertight),
        "volume": round(float(mesh.volume), 2),
    }


class _Handler(BaseHTTPRequestHandler):
    state: WebState  # assigned by run_web

    # ---- helpers ----

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def _static(self, rel: str) -> None:
        base = WEB_DIR.resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    # ---- HTTP methods ----

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path == "/api/project":
            try:
                self._send_json(self.state.payload())
            except Exception as exc:  # noqa: BLE001 -- report, don't kill server
                self._send_json({"error": str(exc)}, 500)
        elif path.startswith("/vendor/") or path in ("/app.js", "/style.css"):
            self._static(path.lstrip("/"))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/build":
                body = self._read_json()
                self.state.rebuild(body)
                self._send_json(self.state.payload())
            elif path == "/api/save":
                path_out = self.state.cfg.config_path or self.state.cfg.svg.with_suffix(
                    ".toml"
                )
                path_out.write_text(save_config(self.state.cfg))
                self._send_json({"ok": True, "path": str(path_out)})
            elif path == "/api/export":
                out = self.state.cfg.output
                result = self.state.result
                if result is None or result.mesh is None:
                    raise ValueError("nothing to export")
                out.parent.mkdir(parents=True, exist_ok=True)
                stl_io.export_stl(result.mesh, out)
                self._send_json({"ok": True, "path": str(out)})
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001 -- report, don't kill server
            self._send_json({"error": str(exc)}, 400)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # keep the console quiet; errors are returned over HTTP


def run_web(cfg: Project, port: int = 8765, open_browser: bool = True) -> None:
    handler = type("BoundHandler", (_Handler,), {"state": WebState(cfg)})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"cookiemaker web: {url}  (ctrl+c to stop)")
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
