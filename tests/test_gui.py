"""Headless smoke test for the GUI (Agg backend, no window)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

import matplotlib

matplotlib.use("Agg", force=True)

from cookiemaker import main
from cookiemaker.config import load_config
from cookiemaker.gui import CookieGui

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


def test_gui_headless_edit_rebuild_save_export(tmp_path: Path):
    import matplotlib.pyplot as plt

    cfg_path = _make_config(tmp_path)
    cfg = load_config(cfg_path)
    gui = CookieGui(cfg)
    try:
        assert gui.result is not None and gui.result.mesh is not None

        # select loop 0, edit z, apply
        gui.select(0)
        assert gui.selected == 0
        gui.tb_z.set_val("9")
        gui.tb_w.set_val("2.5")
        gui.apply_and_rebuild()
        assert cfg.loops["0"].z == 9.0
        assert cfg.loops["0"].width == 2.5
        assert gui.result.mesh.bounds[1][2] >= 9.0  # tallest z now 9

        # bridge settings round-trip
        gui.tb_bw.set_val("3")
        gui.tb_bz.set_val("2")
        gui.apply_and_rebuild()
        assert cfg.bridges.width == 3.0
        assert cfg.bridges.z == 2.0

        # save + reload
        gui.save_toml()
        cfg2 = load_config(cfg_path)
        assert cfg2.loops["0"].z == 9.0
        assert cfg2.bridges.width == 3.0

        # export
        gui.export_stl()
        out = cfg.output
        assert out.exists() and out.stat().st_size > 84

    finally:
        plt.close("all")


def test_gui_save_export_auto_apply_pending(tmp_path: Path):
    """Save TOML / Export STL must reflect on-screen edits without Apply."""
    import matplotlib.pyplot as plt

    cfg_path = _make_config(tmp_path)
    cfg = load_config(cfg_path)
    gui = CookieGui(cfg)
    try:
        assert gui.result is not None and gui.result.mesh is not None
        old_top = gui.result.mesh.bounds[1][2]

        # edit z of loop 1, then save+export WITHOUT pressing Apply + Rebuild
        gui.select(1)
        gui.tb_z.set_val("12")
        gui.tb_bw.set_val("not-a-number")  # invalid bridge box must not crash
        gui.save_toml()
        assert load_config(cfg_path).loops["1"].z == 12.0

        gui.tb_bw.set_val("2")
        gui.export_stl()
        assert gui.result.mesh.bounds[1][2] >= 12.0 > old_top
        assert cfg.output.exists() and cfg.output.stat().st_size > 84

        # status line confirms the action instead of staying silent
        assert str(cfg.output) in gui.notes[0]
    finally:
        plt.close("all")
