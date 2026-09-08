"""Interactive matplotlib editor: click loops, edit z/width, rebuild, export.

Testable headless: CookieGui(cfg) builds the figure without entering the
mainloop; run_gui(cfg) shows the window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from matplotlib.widgets import Button, RadioButtons, TextBox
from shapely.geometry import Point

from . import config as cfg_mod
from . import stl_io
from .config import Project, save_config
from .pipeline import BuildResult, build_project


def _poly_mpl(poly: Any) -> MplPath:
    """Shapely polygon (with holes) -> matplotlib Path."""
    vertices: list[tuple[float, float]] = []
    codes: list[Any] = []
    rings = [poly.exterior, *poly.interiors]
    for ring in rings:
        pts = list(ring.coords)[:-1]  # drop closing duplicate
        if len(pts) < 3:
            continue
        vertices.extend(pts)
        codes.append(MplPath.MOVETO)
        codes.extend([MplPath.LINETO] * (len(pts) - 1))
        codes.append(MplPath.CLOSEPOLY)
        vertices.append(pts[0])
    return MplPath(vertices, codes)


class CookieGui:
    def __init__(self, cfg: Project) -> None:
        import matplotlib.pyplot as plt

        self.cfg = cfg
        self.selected: int | None = None
        self.notes: list[str] = []
        self.result: BuildResult | None = None

        self.fig = plt.figure("cookiemaker", figsize=(15, 8.5))
        gs = self.fig.add_gridspec(
            1, 2, left=0.02, right=0.98, top=0.92, bottom=0.26, wspace=0.02
        )
        self.ax2d = self.fig.add_subplot(gs[0, 0])
        self.ax3d = self.fig.add_subplot(gs[0, 1], projection="3d")

        self._make_widgets()
        self.rebuild()
        self.fig.canvas.mpl_connect("button_press_event", self._on_click)

    # ---------- widgets ----------

    def _make_widgets(self) -> None:
        def wax(x: float, y: float, w: float, h: float) -> Any:
            return self.fig.add_axes((x, y, w, h))

        self.tb_z = TextBox(wax(0.03, 0.16, 0.07, 0.05), "z ", textalignment="left")
        self.tb_w = TextBox(wax(0.12, 0.16, 0.07, 0.05), "width ", textalignment="left")
        self.tb_bw = TextBox(
            wax(0.24, 0.16, 0.06, 0.05), "b.width ", textalignment="left"
        )
        self.tb_bw.set_val(f"{self.cfg.bridges.width:g}")
        self.tb_bz = TextBox(wax(0.33, 0.16, 0.06, 0.05), "b.z ", textalignment="left")
        self.tb_bz.set_val(f"{self.cfg.bridges.z:g}")
        self.rb_mode = RadioButtons(
            wax(0.44, 0.10, 0.08, 0.13), ("auto", "manual", "off")
        )
        self.rb_mode.set_active(("auto", "manual", "off").index(self.cfg.bridges.mode))
        self.rb_mode.on_clicked(lambda label: setattr(self.cfg.bridges, "mode", label))

        self.btn_apply = Button(wax(0.56, 0.16, 0.10, 0.05), "Apply + Rebuild")
        self.btn_apply.label.set_fontsize(9)
        self.btn_apply.on_clicked(lambda _: self.apply_and_rebuild())
        self.btn_save = Button(wax(0.68, 0.16, 0.09, 0.05), "Save TOML")
        self.btn_save.on_clicked(lambda _: self.save_toml())
        self.btn_export = Button(wax(0.79, 0.16, 0.09, 0.05), "Export STL")
        self.btn_export.on_clicked(lambda _: self.export_stl())

        self.fig.text(
            0.03, 0.235, "click a loop in the 2D view to edit its z/width", fontsize=9
        )
        self.hint = self.fig.text(0.56, 0.235, "", fontsize=9, color="0.35")

    # ---------- selection ----------

    def _on_click(self, event: Any) -> None:
        result = self.result
        if event.inaxes is not self.ax2d or event.xdata is None or result is None:
            return
        point = Point(event.xdata, event.ydata)
        best_id, best_d = None, float("inf")
        for lid, poly in result.footprints.items():
            d = poly.distance(point)
            if d < best_d:
                best_id, best_d = lid, d
        if best_id is not None:
            self.select(best_id)

    def select(self, loop_id: int) -> None:
        assert self.result is not None
        self.selected = loop_id
        loop = self.result.loops[loop_id]
        z, width = cfg_mod.resolve(self.cfg, loop)
        self.tb_z.set_val(f"{z:g}")
        self.tb_w.set_val(f"{width:g}")
        x0, y0, x1, y1 = loop.bbox
        self.hint.set_text(
            f"loop {loop_id}  [{loop.css_class or '-'} {loop.kind}]"
            f"  bbox [{x0:.0f},{y0:.0f}]-[{x1:.0f},{y1:.0f}]"
        )
        self.draw_2d()

    # ---------- rebuild ----------

    def apply_and_rebuild(self) -> None:
        if self.selected is not None and self.result is not None:
            o = cfg_mod.Overrides()
            try:
                z = float(self.tb_z.text)
                o.z = z
            except ValueError:
                pass
            try:
                width = float(self.tb_w.text)
                o.width = width
            except ValueError:
                pass
            if o.z is not None or o.width is not None:
                self.cfg.loops[str(self.selected)] = o
        try:
            self.cfg.bridges.width = max(float(self.tb_bw.text), 1e-6)
        except ValueError:
            pass
        try:
            self.cfg.bridges.z = max(float(self.tb_bz.text), 1e-6)
        except ValueError:
            pass
        self.rebuild()

    def rebuild(self) -> None:
        self.result = build_project(self.cfg)
        self.notes = self.result.notes
        self.draw_2d()
        self.draw_3d()
        self.draw_status()

    # ---------- drawing ----------

    def draw_2d(self) -> None:
        import matplotlib.pyplot as plt

        ax = self.ax2d
        ax.clear()
        if self.result is None:
            return
        ax.set_aspect("equal")
        ax.set_title("top view  (fill color = z)")

        zs = [cfg_mod.resolve(self.cfg, lp)[0] for lp in self.result.loops]
        zmax = max(zs) if zs else 1.0
        cmap = plt.get_cmap("viridis")

        for lid, poly in self.result.footprints.items():
            loop = self.result.loops[lid]
            z = cfg_mod.resolve(self.cfg, loop)[0]
            color = cmap(z / zmax if zmax > 0 else 0.0)
            is_sel = lid == self.selected
            ax.add_patch(
                PathPatch(
                    _poly_mpl(poly),
                    facecolor=color,
                    alpha=0.75,
                    edgecolor="red" if is_sel else "black",
                    lw=2.0 if is_sel else 0.4,
                    zorder=2,
                )
            )
        for link in self.result.bars:
            ax.add_patch(
                PathPatch(
                    _poly_mpl(link.poly),
                    facecolor="lightgrey",
                    edgecolor="0.4",
                    hatch="///" if link.kind == "bar" else "xx",
                    lw=0.5,
                    zorder=1,
                )
            )
        if self.result.footprints:
            ax.autoscale_view()
        ax.plot([], [])  # keep empty plots from erroring

    def draw_3d(self) -> None:
        ax = self.ax3d
        ax.clear()
        mesh = self.result.mesh if self.result else None
        ax.set_title("3D preview")
        if mesh is None:
            return
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection

        v, f = mesh.vertices, mesh.faces
        tris = v[f]
        heights = tris.mean(axis=1)[:, 2]
        zmax = heights.max() if heights.size and heights.max() > 0 else 1.0
        import matplotlib.pyplot as plt

        cmap = plt.get_cmap("viridis")
        colors = cmap(heights / zmax)
        coll = Poly3DCollection(tris, facecolors=colors, edgecolor="none")
        ax.add_collection3d(coll)
        lo, hi = mesh.bounds
        ax.set_xlim(lo[0], hi[0])
        ax.set_ylim(lo[1], hi[1])
        ax.set_zlim(0, hi[2])
        spans = hi - lo
        ax.set_box_aspect((spans[0], spans[1], max(spans[2], 1.0)))

    def draw_status(self) -> None:
        mesh = self.result.mesh if self.result else None
        status = " | ".join(self.notes) if self.notes else "ok"
        if mesh is not None:
            status = f"{stl_io.report(mesh)}  |  {status}"
        self.fig.suptitle(status, fontsize=9)

    # ---------- persistence ----------

    def save_toml(self) -> None:
        path = self.cfg.config_path or self.cfg.svg.with_suffix(".toml")
        path.write_text(save_config(self.cfg))
        self.notes = [f"saved {path}"]
        self.draw_status()

    def export_stl(self) -> None:
        if self.result is None or self.result.mesh is None:
            return
        out: Path = self.cfg.output
        out.parent.mkdir(parents=True, exist_ok=True)
        stl_io.export_stl(self.result.mesh, out)
        self.notes = [f"exported {out}"]
        self.draw_status()


def run_gui(cfg: Project) -> None:
    gui = CookieGui(cfg)
    gui.fig.show()
    import matplotlib.pyplot as plt

    plt.show()
