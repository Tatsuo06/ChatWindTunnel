"""CAD file conversion and STL rotation utilities.

Supports: .stl (passthrough), .step/.stp, .iges/.igs via cadquery.
STL rotation uses numpy-stl + scipy for yaw/pitch transforms.
"""
from pathlib import Path

import numpy as np


def convert_to_stl(input_path: Path, output_dir: Path, scale: float = 1.0) -> tuple[Path, bool]:
    """Convert CAD file to STL. Returns (stl_path, mm_to_m_applied).

    scale: multiply all vertex coordinates by this factor (e.g. 0.001 for mm→m).
    If the resulting STL has xmax-xmin > 1000, it is assumed to be in millimetres
    and automatically scaled by 0.001 before the user scale is applied.
    """
    suffix = input_path.suffix.lower()
    stl_path = output_dir / (input_path.stem + ".stl")

    if suffix == ".stl":
        if input_path != stl_path:
            import shutil
            shutil.copy2(input_path, stl_path)
    elif suffix == ".obj":
        _convert_obj_to_stl(input_path, stl_path)
    elif suffix in (".step", ".stp", ".iges", ".igs"):
        _convert_via_cadquery(input_path, stl_path)
    else:
        raise ValueError(f"Unsupported CAD format: {suffix}")

    mm_to_m = _needs_mm_to_m_scale(stl_path)
    if mm_to_m:
        _scale_stl(stl_path, 0.001)
    if scale != 1.0:
        _scale_stl(stl_path, scale)
    return stl_path, mm_to_m


def _needs_mm_to_m_scale(stl_path: Path) -> bool:
    """Return True if the STL bounding box suggests millimetre units (xmax-xmin > 1000)."""
    from stl import mesh as stl_mesh
    geometry = stl_mesh.Mesh.from_file(str(stl_path))
    verts = geometry.vectors.reshape(-1, 3)
    return float(verts[:, 0].max() - verts[:, 0].min()) > 1000


def _scale_stl(stl_path: Path, scale: float) -> None:
    """Scale all vertices of an STL file in-place."""
    from stl import mesh as stl_mesh
    geometry = stl_mesh.Mesh.from_file(str(stl_path))
    geometry.vectors *= scale
    geometry.update_normals()
    geometry.save(str(stl_path))


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
        from OCP.IGESControl import IGESControl_Reader
        reader = IGESControl_Reader()
        if reader.ReadFile(str(input_path)).name != "IFSelect_RetDone":
            raise ValueError("IGES file could not be loaded")
        reader.TransferRoots()
        shape = cq.Shape(reader.OneShape())
    else:
        raise ValueError(f"Unsupported format: {suffix}")

    cq.exporters.export(shape, str(stl_path))


def rotate_stl(input_path: Path, output_path: Path,
               yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0,
               center: tuple | None = None, invert: bool = True) -> None:
    """Rotate STL geometry by yaw (Z-axis), pitch (Y-axis), and roll (X-axis).

    Wind direction is always +X in OpenFOAM. Rotating the geometry by (-yaw, -pitch, -roll)
    is equivalent to the wind arriving at (yaw, pitch, roll) relative to the object.
    Rotation order: yaw → pitch → roll (applied as Z→Y→X Euler angles).

    center: rotation centre. None = bounding-box centroid of the input STL
            (wind-direction convention). Pass (0, 0, 0) to rotate about the
            global origin, e.g. for coordinate-system correction at upload.
    invert: negate the angles (wind-direction convention). Pass False to
            rotate the geometry itself by +angles (right-hand rule).
    """
    import numpy as np
    from stl import mesh as stl_mesh
    from scipy.spatial.transform import Rotation

    geometry = stl_mesh.Mesh.from_file(str(input_path))

    vectors = geometry.vectors.reshape(-1, 3)

    if center is None:
        center = (vectors.min(axis=0) + vectors.max(axis=0)) / 2.0
    else:
        center = np.asarray(center, dtype=vectors.dtype)
    vectors -= center

    sign = -1.0 if invert else 1.0
    rot = Rotation.from_euler(
        "zyx", [sign * yaw_deg, sign * pitch_deg, sign * roll_deg], degrees=True
    )
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
