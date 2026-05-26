"""Results visualization endpoints — returns PNG images."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select

from sqlalchemy.orm import selectinload

from backend.api.deps import DB, CurrentUser
from backend.db.models import Geometry, Simulation, SimulationStatus
from backend.visualization.pyvista_backend import backend

router = APIRouter(prefix="/simulations/{sim_id}/results", tags=["results"])


async def _get_done_sim(sim_id: int, db) -> Simulation:
    result = await db.execute(
        select(Simulation).where(Simulation.id == sim_id)
        .options(selectinload(Simulation.geometry))
    )
    sim = result.scalar_one_or_none()
    if not sim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    return sim


def _png(data: bytes) -> Response:
    return Response(content=data, media_type="image/png")


@router.get("/geometry")
async def geometry_preview(sim_id: int, current_user: CurrentUser, db: DB):
    import json as _json
    import tempfile
    from backend.cad.converter import rotate_stl
    from backend.foam.case_builder import _auto_domain_params, _refbox_from_rotated_stl

    sim = await _get_done_sim(sim_id, db)
    if not sim.geometry.stl_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No STL available")

    # 1) ジョブ投入済み: case_dir 内の実際の回転済みSTLを使用
    rotated = Path(sim.case_dir) / "constant" / "triSurface" / "motorBike.stl" if sim.case_dir else None
    tmp_path = None
    domain: dict | None = None

    # Use case_dir STL only if the stored angles match the current settings
    params_file = Path(sim.case_dir) / "case_params.json" if sim.case_dir else None
    angles_match = False
    if rotated and rotated.exists() and params_file and params_file.exists():
        p = _json.loads(params_file.read_text())
        angles_match = (
            abs(p.get("yaw_deg", 0) - sim.yaw_deg) < 0.01 and
            abs(p.get("pitch_deg", 0) - sim.pitch_deg) < 0.01 and
            abs(p.get("roll_deg", 0) - sim.roll_deg) < 0.01
        )

    if rotated and rotated.exists() and angles_match:
        stl_path = rotated
        p = _json.loads(params_file.read_text())
        label = f"ヨー: {p.get('yaw_deg', 0)}°  ピッチ: {p.get('pitch_deg', 0)}°  ロール: {p.get('roll_deg', 0)}°（計算済み）"
        domain = p

    elif sim.yaw_deg or sim.pitch_deg or sim.roll_deg:
        # 2) 未投入だがyaw/pitch/roll設定あり: オンザフライで回転してプレビュー
        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".stl")
        import os; os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        rotate_stl(Path(sim.geometry.stl_file_path), tmp_path, sim.yaw_deg, sim.pitch_deg, sim.roll_deg)
        stl_path = tmp_path
        label = f"ヨー: {sim.yaw_deg}°  ピッチ: {sim.pitch_deg}°  ロール: {sim.roll_deg}°（プレビュー）"

    else:
        # 3) 回転なし
        stl_path = Path(sim.geometry.stl_file_path)
        label = "ヨー: 0°  ピッチ: 0°  ロール: 0°（回転なし）"

    # Domain params: from case_params.json if available, else from pre-rotation STL
    original_stl = Path(sim.geometry.stl_file_path)
    if domain is None:
        try:
            domain = _auto_domain_params(original_stl)
        except Exception:
            domain = None

    # RefinementBox: always from rotated STL bounding box + 20% margin
    refbox = domain  # fallback to domain params if refbox computation fails
    try:
        refbox = {**(domain or {}), **_refbox_from_rotated_stl(stl_path)}
    except Exception:
        pass

    try:
        return _png(backend.preview_geometry(stl_path, label=label, domain=domain, refbox=refbox))
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


@router.get("/residuals")
async def residuals(sim_id: int, current_user: CurrentUser, db: DB):
    sim = await _get_done_sim(sim_id, db)
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No case directory")
    case_dir = Path(sim.case_dir)
    # Prefer main solver logs over auxiliary ones (potentialFoam, etc.)
    for solver in ("simpleFoam", "pisoFoam", "rhoSimpleFoam"):
        log = case_dir / f"log.{solver}"
        if log.exists():
            break
    else:
        log = next(case_dir.glob("log.*Foam"), None)
    if not log:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No solver log found")
    return _png(backend.plot_residuals(log))


@router.get("/force-coefficients")
async def force_coefficients(sim_id: int, current_user: CurrentUser, db: DB):
    sim = await _get_done_sim(sim_id, db)
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No case directory")
    return _png(backend.plot_force_coefficients(Path(sim.case_dir)))


@router.get("/cutting-plane")
async def cutting_plane(sim_id: int, field: str = "p", current_user: CurrentUser = None, db: DB = None):
    sim = await _get_done_sim(sim_id, db)
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No case directory")

    case_dir = Path(sim.case_dir)
    vtk_files = sorted(
        (case_dir / "postProcessing" / "cuttingPlane").glob("**/yNormal.vtp"),
        key=lambda p: float(p.parent.name) if p.parent.name.replace(".", "").isdigit() else 0,
    )
    if not vtk_files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No cutting plane data")

    geo_bounds = None
    params_file = case_dir / "case_params.json"
    if params_file.exists():
        import json as _json
        params = _json.loads(params_file.read_text())
        rb_min = params.get("refbox_min")
        rb_max = params.get("refbox_max")
        if rb_min and rb_max:
            geo_bounds = {"xmin": rb_min[0], "xmax": rb_max[0],
                          "zmin": rb_min[2], "zmax": rb_max[2]}

    stl_path = case_dir / "constant" / "triSurface" / "motorBike.stl"
    return _png(backend.plot_cutting_plane(vtk_files[-1], field=field, geo_bounds=geo_bounds,
                                           stl_path=stl_path if stl_path.exists() else None))


@router.get("/cutting-plane-data")
async def cutting_plane_data(sim_id: int, field: str = "p", current_user: CurrentUser = None, db: DB = None):
    sim = await _get_done_sim(sim_id, db)
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No case directory")

    case_dir = Path(sim.case_dir)
    vtk_files = sorted(
        (case_dir / "postProcessing" / "cuttingPlane").glob("**/yNormal.vtp"),
        key=lambda p: float(p.parent.name) if p.parent.name.replace(".", "").isdigit() else 0,
    )
    if not vtk_files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No cutting plane data")

    data = backend.cutting_plane_data(vtk_files[-1], field=field)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")
    return data


@router.get("/mesh")
async def mesh_surface(sim_id: int, view: str = "iso", current_user: CurrentUser = None, db: DB = None):
    sim = await _get_done_sim(sim_id, db)
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No case directory")
    case_dir = Path(sim.case_dir)
    if not (case_dir / "case.foam").exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case.foam not found")
    return _png(backend.plot_mesh_surface(case_dir, view=view))


@router.get("/mesh-stats")
async def mesh_stats(sim_id: int, current_user: CurrentUser, db: DB):
    import pyvista as pv
    from backend.visualization.parsers import parse_mesh_info, parse_peak_memory

    sim = await _get_done_sim(sim_id, db)
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No case directory")
    case_dir = Path(sim.case_dir)
    info = parse_mesh_info(case_dir)
    peak_mem = parse_peak_memory(case_dir)
    if peak_mem.get("simpleFoam") is not None:
        info["peak_memory_simple_kb"] = peak_mem["simpleFoam"]
    if peak_mem.get("snappyHexMesh") is not None:
        info["peak_memory_snappy_kb"] = peak_mem["snappyHexMesh"]

    foam_file = case_dir / "case.foam"
    if foam_file.exists():
        try:
            reader = pv.OpenFOAMReader(str(foam_file))
            if reader.time_values:
                reader.set_active_time_value(reader.time_values[-1])
                dataset = reader.read()
                for patch_name in ("motorBike", "object"):
                    try:
                        surface_mesh = dataset["boundary"][patch_name]
                        info["surface_cells"] = surface_mesh.n_cells
                        break
                    except (KeyError, TypeError):
                        pass
        except Exception:
            pass

    return info


@router.get("/streamlines")
async def streamlines(sim_id: int, current_user: CurrentUser, db: DB):
    sim = await _get_done_sim(sim_id, db)
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No case directory")

    case_dir = Path(sim.case_dir)

    def _latest_vtk(pattern: str) -> Path | None:
        files = sorted(
            case_dir.glob(pattern),
            key=lambda p: float(p.parent.name) if p.parent.name.replace(".", "").isdigit() else 0,
        )
        return files[-1] if files else None

    # Collect forward and backward streamline VTKs (newest time step each)
    vtk_paths: list[Path] = []
    for pattern in (
        "postProcessing/sets/streamLines/**/*.vtp",
        "postProcessing/streamLines/**/*.vtp",
        "postProcessing/streamLines/**/*.vtk",
    ):
        f = _latest_vtk(pattern)
        if f:
            vtk_paths.append(f)
            break
    for pattern in (
        "postProcessing/sets/streamLinesBack/**/*.vtp",
        "postProcessing/streamLinesBack/**/*.vtp",
        "postProcessing/streamLinesBack/**/*.vtk",
    ):
        f = _latest_vtk(pattern)
        if f:
            vtk_paths.append(f)
            break

    if not vtk_paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No streamline data")

    geo_bounds = None
    params_file = case_dir / "case_params.json"
    if params_file.exists():
        import json as _json
        params = _json.loads(params_file.read_text())
        rb_min = params.get("refbox_min")
        rb_max = params.get("refbox_max")
        if rb_min and rb_max:
            geo_bounds = {"xmin": rb_min[0], "xmax": rb_max[0],
                          "zmin": rb_min[2], "zmax": rb_max[2]}

    stl_path = case_dir / "constant" / "triSurface" / "motorBike.stl"
    return _png(backend.plot_streamlines(vtk_paths, geo_bounds=geo_bounds,
                                         stl_path=stl_path if stl_path.exists() else None))


@router.get("/streamlines-paths")
async def streamlines_paths(sim_id: int, current_user: CurrentUser, db: DB):
    """Return local file paths of streamline VTKs and STL for stpyvista interactive rendering."""
    sim = await _get_done_sim(sim_id, db)
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No case directory")

    case_dir = Path(sim.case_dir)

    def _latest_vtk(pattern: str) -> Path | None:
        files = sorted(
            case_dir.glob(pattern),
            key=lambda p: float(p.parent.name) if p.parent.name.replace(".", "").isdigit() else 0,
        )
        return files[-1] if files else None

    vtk_paths: list[str] = []
    for pattern in (
        "postProcessing/sets/streamLines/**/*.vtp",
        "postProcessing/streamLines/**/*.vtp",
        "postProcessing/streamLines/**/*.vtk",
    ):
        f = _latest_vtk(pattern)
        if f:
            vtk_paths.append(str(f))
            break
    for pattern in (
        "postProcessing/sets/streamLinesBack/**/*.vtp",
        "postProcessing/streamLinesBack/**/*.vtp",
        "postProcessing/streamLinesBack/**/*.vtk",
    ):
        f = _latest_vtk(pattern)
        if f:
            vtk_paths.append(str(f))
            break

    if not vtk_paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No streamline data")

    stl_path = case_dir / "constant" / "triSurface" / "motorBike.stl"
    return {
        "vtk_paths": vtk_paths,
        "stl_path": str(stl_path) if stl_path.exists() else None,
    }
