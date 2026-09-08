"""Mesh assembly: boolean union of solids and binary STL export."""

from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore", message=".*global interpreter lock.*", category=RuntimeWarning
)

import numpy as np
import trimesh
from manifold3d import Manifold, OpType


def build_mesh(solids: list[Manifold]) -> trimesh.Trimesh | None:
    """Boolean-union solids into a single watertight trimesh mesh."""
    solids = [s for s in solids if s is not None and not s.is_empty()]
    if not solids:
        return None
    merged = (
        solids[0] if len(solids) == 1 else Manifold.batch_boolean(solids, OpType.Add)
    )
    if merged.is_empty():
        return None
    mesh = merged.to_mesh()
    vertices = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    faces = np.asarray(mesh.tri_verts, dtype=np.int64)
    # STL stores float32: snap vertices to float32 now so the export/import
    # round-trip cannot merge distinct vertices and create degenerate faces.
    vertices = vertices.astype(np.float32).astype(np.float64)
    tm = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    f = tm.faces
    keep = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])
    if not keep.all():
        tm.update_faces(keep)
    tm.remove_unreferenced_vertices()
    return tm


def export_stl(mesh: trimesh.Trimesh, path: str | object) -> None:
    mesh.export(str(path), file_type="stl")  # binary STL


def report(mesh: trimesh.Trimesh) -> str:
    bbox = mesh.bounds
    size = bbox[1] - bbox[0]
    return (
        f"triangles={len(mesh.faces)} "
        f"watertight={mesh.is_watertight} "
        f"volume={mesh.volume:.2f}mm3 "
        f"bbox={size[0]:.1f}x{size[1]:.1f}x{size[2]:.1f}mm"
    )
