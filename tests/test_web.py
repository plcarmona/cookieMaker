"""Headless tests for the web GUI backend (no browser needed)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cookiemaker import main
from cookiemaker.config import load_config
from cookiemaker.web import WebState, run_web

SYNTHETIC = """<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">
  <path d="M10,10 L40,10" fill="none" stroke="black"/>
  <path d="M55,20 L75,20 L75,40 Z" fill="none" stroke="black"/>
  <circle cx="85" cy="45" r="5" stroke="black"/>
</svg>
"""


def _make_config(tmp_path: Path) -> Path:
    svg = tmp_path / "syn.svg"
    svg.write_text(SYNTHETIC)
    cfg_path = tmp_path / "syn.toml"
    assert main(["inspect", str(svg), "-o", str(cfg_path)]) == 0
    return cfg_path


def test_state_rebuild_updates(tmp_path: Path):
    cfg_path = _make_config(tmp_path)
    state = WebState(load_config(cfg_path))

    payload = state.payload()
    assert payload["mesh"]["faces"]
    assert payload["mesh"]["watertight"]
    assert len(payload["loops"]) == 3
    assert payload["loops"][0]["footprint"]["exterior"]

    state.rebuild(
        {
            "loops": {"0": {"z": 9.0, "width": 3.0}},
            "bridges": {"mode": "auto", "width": 2.5, "z": 2.0},
            "default_z": 5.0,
        }
    )
    payload = state.payload()
    assert payload["loops"][0]["z"] == 9.0
    assert payload["config"]["bridges"]["width"] == 2.5
    zmax = max(payload["mesh"]["positions"][2::3])
    assert zmax >= 9.0

    with pytest.raises(ValueError):
        state.rebuild({"bridges": {"width": -1}})
    with pytest.raises(ValueError, match="style"):
        state.rebuild({"bridges": {"style": "voronoi"}})

    # style round-trips through the payload and rebuilds
    for style in ("tree", "plate"):
        state.rebuild(
            {"bridges": {"mode": "auto", "style": style, "branches": 3, "radius": 25.0}}
        )
        payload = state.payload()
        assert payload["config"]["bridges"]["style"] == style
        assert payload["config"]["bridges"]["radius"] == 25.0
        assert payload["bridges"], style
        assert payload["mesh"]["watertight"]
    assert state.payload()["bridges"][0]["kind"] == "plate"
    with pytest.raises(ValueError, match="radius"):
        state.rebuild({"bridges": {"radius": -1}})


@pytest.fixture
def server(tmp_path: Path):
    cfg_path = _make_config(tmp_path)
    cfg = load_config(cfg_path)
    server = run_web.__globals__["ThreadingHTTPServer"](
        ("127.0.0.1", 0),
        type("H", (run_web.__globals__["_Handler"],), {"state": WebState(cfg)}),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", tmp_path
    server.shutdown()


def _get(url: str, path: str):
    with urllib.request.urlopen(url + path) as res:
        return res.status, res.read(), res.headers


def _post(url: str, path: str, body: dict):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as res:
        return res.status, json.loads(res.read())


def test_http_api_roundtrip(server):
    url, tmp_path = server

    status, body, headers = _get(url, "/")
    assert status == 200 and b"importmap" in body
    assert "no-store" in headers["Cache-Control"]

    status, body, _ = _get(url, "/vendor/three.module.js")
    assert status == 200 and len(body) > 1_000_000

    status, body, _ = _get(url, "/api/project")
    assert status == 200
    project = json.loads(body)
    assert len(project["loops"]) == 3

    status, data = _post(url, "/api/build", {"loops": {"0": {"z": 8.0, "width": 2.0}}})
    assert status == 200
    assert data["loops"][0]["z"] == 8.0

    status, data = _post(url, "/api/save", {})
    assert status == 200 and data["ok"]
    reloaded = load_config(tmp_path / "syn.toml")
    assert reloaded.loops["0"].z == 8.0

    status, data = _post(url, "/api/export", {})
    assert status == 200 and data["ok"]
    out = Path(data["path"])
    assert out.exists() and out.stat().st_size > 84

    # invalid updates -> 400, server stays alive
    req = urllib.request.Request(
        url + "/api/build",
        data=json.dumps({"bridges": {"width": -2}}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    status, body, _ = _get(url, "/api/project")
    assert status == 200

    with pytest.raises(urllib.error.HTTPError):
        _get(url, "/../etc/passwd")
