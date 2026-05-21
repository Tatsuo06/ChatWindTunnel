"""CAD file conversion and STL rotation utilities.

Supports: .stl (passthrough), .step/.stp, .iges/.igs via cadquery.
STL rotation uses numpy-stl + scipy for yaw/pitch transforms.
"""
from pathlib import Path

import numpy as np


def convert_to_stl(input_path: Path, output_dir: Path) -> Path:
    """Convert CAD file to STL. Returns path to the STL file."""
    suffix = input_path.suffix.lower()
    stl_path = output_dir / (input_path.stem + ".stl")

    if suffix == ".stl":
        if input_path != stl_path:
            import shutil
            shutil.copy2(input_path, stl_path)
        return stl_path

    if suffix == ".obj":
        _convert_obj_to_stl(input_path, stl_path)
        return stl_path

    if suffix in (".step", ".stp", ".iges", ".igs"):
        _convert_via_cadquery(input_path, stl_path)
        return stl_path

    raise ValueError(f"Unsupported CAD format: {suffix}")


def _convert_obj_to_stl(input_path: Path, stl_path: Path) -> None:
    import pyvista as pv
    mesh = pv.read(str(input_path))
    mesh.save(str(stl_path))


def _convert_via_cadquery(input_path: Path, stl_path: Path) -> None:
    import cadquery as cq

    suffix = input_path.suffix.lower()
    if suffix in (".step", ".stp"):
        shape = cq.importers.importStep(str(input_path))
    elif suffix in (".iges", ".igs"):
        shape = cq.importers.importStep(str(input_path))  # cq uses OCC for both
    elif suffix == ".obj":
        # cadquery doesn't support OBJ natively; fall back to numpy-stl approximation
        raise ValueError("OBJ format not supported. Please convert to STL or STEP first.")
    else:
        raise ValueError(f"Unsupported format: {suffix}")

    cq.exporters.export(shape, str(stl_path))


def rotate_stl(input_path: Path, output_path: Path,
               yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> None:
    """Rotate STL geometry by yaw (Z-axis), pitch (Y-axis), and roll (X-axis).

    Wind direction is always +X in OpenFOAM. Rotating the geometry by (-yaw, -pitch, -roll)
    is equivalent to the wind arriving at (yaw, pitch, roll) relative to the object.
    Rotation order: yaw → pitch → roll (applied as Z→Y→X Euler angles).
    Rotation center is the bounding-box centroid of the original STL.
    """
    from stl import mesh as stl_mesh
    from scipy.spatial.transform import Rotation

    geometry = stl_mesh.Mesh.from_file(str(input_path))

    vectors = geometry.vectors.reshape(-1, 3)

    # Rotate around the bounding-box centroid
    center = (vectors.min(axis=0) + vectors.max(axis=0)) / 2.0
    vectors -= center

    rot = Rotation.from_euler("zyx", [-yaw_deg, -pitch_deg, -roll_deg], degrees=True)
    rotated = (rot.as_matrix() @ vectors.T).T

    rotated += center
    geometry.vectors = rotated.reshape(-1, 3, 3)

    geometry.update_normals()
    geometry.save(str(output_path))


def get_bounding_box(stl_path: Path) -> dict:
    """Return bounding box info for geometry preview."""
    from stl import mesh as stl_mesh

    geometry = stl_mesh.Mesh.from_file(str(stl_path))
    min_coords = geometry.vectors.reshape(-1, 3).min(axis=0)
    max_coords = geometry.vectors.reshape(-1, 3).max(axis=0)
    size = max_coords - min_coords
    return {
        "min": min_coords.tolist(),
        "max": max_coords.tolist(),
        "size": size.tolist(),
        "center": ((min_coords + max_coords) / 2).tolist(),
    }
