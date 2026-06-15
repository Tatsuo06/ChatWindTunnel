"""Geometry (CAD) management endpoints."""
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.api.deps import DB, CurrentUser
from backend.cad.converter import convert_to_stl, get_bounding_box
from backend.core.config import settings
from backend.db.models import Geometry, Project, UserRole

router = APIRouter(tags=["geometries"])


class GeometryCreate(BaseModel):
    name: str = ""


class GeometryUpdate(BaseModel):
    name: str


class GeometryResponse(BaseModel):
    id: int
    project_id: int
    name: str
    stl_file_path: str | None
    cad_file_path: str | None
    transform_info: dict | None = None

    model_config = {"from_attributes": True}


def _assert_project_access(project: Project, user):
    if user.role != UserRole.admin and project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")


@router.get("/projects/{project_id}/geometries", response_model=list[GeometryResponse])
async def list_geometries(project_id: int, current_user: CurrentUser, db: DB):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _assert_project_access(project, current_user)
    result = await db.execute(select(Geometry).where(Geometry.project_id == project_id))
    return result.scalars().all()


@router.post("/projects/{project_id}/geometries", response_model=GeometryResponse,
             status_code=status.HTTP_201_CREATED)
async def create_geometry(project_id: int, body: GeometryCreate, current_user: CurrentUser, db: DB):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _assert_project_access(project, current_user)

    geo = Geometry(project_id=project_id, name=body.name or "geometry")
    db.add(geo)
    await db.commit()
    await db.refresh(geo)
    return geo


@router.get("/geometries/{geo_id}", response_model=GeometryResponse)
async def get_geometry(geo_id: int, current_user: CurrentUser, db: DB):
    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)
    return geo


@router.patch("/geometries/{geo_id}", response_model=GeometryResponse)
async def update_geometry(geo_id: int, body: GeometryUpdate, current_user: CurrentUser, db: DB):
    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)
    geo.name = body.name
    await db.commit()
    await db.refresh(geo)
    return geo


@router.delete("/geometries/{geo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_geometry(geo_id: int, current_user: CurrentUser, db: DB):
    import threading
    from backend.cluster import get_runner
    from backend.cluster.cluster_runner import ClusterRunner, _ssh
    from backend.db.models import Simulation, SimulationStatus
    from sqlalchemy.orm import selectinload as _sil

    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id)
        .options(selectinload(Geometry.project), _sil(Geometry.simulations))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)

    # Collect active job IDs and case dirs before deleting DB records
    active_job_ids = [
        s.job_id for s in geo.simulations
        if s.job_id and s.status in (SimulationStatus.running, SimulationStatus.meshing)
    ]
    case_dirs = [Path(s.case_dir) for s in geo.simulations if s.case_dir]

    await db.delete(geo)
    await db.commit()

    # Cancel running cluster jobs, then delete remote dirs (fire-and-forget)
    runner = get_runner()
    if isinstance(runner, ClusterRunner):
        def _cleanup():
            for job_id in active_job_ids:
                try:
                    _ssh(f"qdel {job_id} 2>/dev/null || true")
                except Exception:
                    pass
            if case_dirs:
                rm_cmd = "rm -rf " + " ".join(runner._remote_dir(d) for d in case_dirs)
                _ssh(rm_cmd)
        threading.Thread(target=_cleanup, daemon=True).start()

    # Delete local case directories
    for case_dir in case_dirs:
        if case_dir.exists():
            shutil.rmtree(case_dir, ignore_errors=True)

    # Delete uploaded CAD/STL files
    for path_attr in (geo.cad_file_path, geo.stl_file_path):
        if path_attr:
            p = Path(path_attr)
            if p.exists():
                p.unlink(missing_ok=True)


@router.get("/geometries/{geo_id}/download-cad")
async def download_cad(geo_id: int, current_user: CurrentUser, db: DB):
    """Download the original uploaded CAD file."""
    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)
    if not geo.cad_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No CAD file uploaded")
    path = Path(geo.cad_file_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CAD file not found on disk")
    filename = f"{geo.name}{path.suffix}"
    return FileResponse(str(path), filename=filename, media_type="application/octet-stream")


@router.get("/geometries/{geo_id}/bbox")
async def geometry_bbox(geo_id: int, current_user: CurrentUser, db: DB):
    """Return bounding box and centroid of the uploaded STL."""
    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)
    if not geo.stl_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No STL uploaded")
    return get_bounding_box(Path(geo.stl_file_path))


@router.post("/geometries/{geo_id}/upload-cad", response_model=GeometryResponse)
async def upload_cad(
    geo_id: int, file: UploadFile, current_user: CurrentUser, db: DB,
    scale: float = Query(1.0, description="Scale factor applied to all vertices (e.g. 0.001 for mm→m)"),
    yaw: float = Query(0.0, description="Yaw rotation angle in degrees (Z-axis)"),
    pitch: float = Query(0.0, description="Pitch rotation angle in degrees (Y-axis)"),
    roll: float = Query(0.0, description="Roll rotation angle in degrees (X-axis)"),
):
    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)

    upload_dir = settings.UPLOAD_DIR / "geo" / str(geo_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Save original uploaded file (never overwritten)
    file_stem = Path(file.filename).stem
    original_path = upload_dir / file.filename
    with open(original_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Convert/scale into a separate directory so original is never modified
    processed_dir = upload_dir / "processed"
    processed_dir.mkdir(exist_ok=True)
    stl_path, mm_to_m = convert_to_stl(original_path, processed_dir, scale=scale)

    if yaw != 0.0 or pitch != 0.0 or roll != 0.0:
        from backend.cad.converter import rotate_stl
        # Coordinate-system correction: rotate about the global origin,
        # geometry rotates by +angles (right-hand rule).
        rotate_stl(stl_path, stl_path, yaw, pitch, roll,
                   center=(0.0, 0.0, 0.0), invert=False)

    geo.cad_file_path = str(original_path)
    geo.stl_file_path = str(stl_path)
    geo.transform_info = {
        "source": file.filename,
        "mm_to_m": mm_to_m,
        "scale": scale,
        "yaw": yaw,
        "pitch": pitch,
        "roll": roll,
    }
    if not geo.name or geo.name == "geometry":
        geo.name = file_stem
    await db.commit()
    await db.refresh(geo)
    return geo


@router.post("/geometries/{geo_id}/scale", response_model=GeometryResponse)
async def scale_geometry(
    geo_id: int, current_user: CurrentUser, db: DB,
    factor: float = Query(..., description="Scale factor to apply (e.g. 2.0 to double size)"),
):
    """Re-convert original CAD file with a new scale factor, replacing the current STL."""
    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)
    if not geo.cad_file_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No original CAD file available")

    # Re-convert from the untouched original into processed/, then re-apply
    # the rotation recorded at upload time.
    processed_dir = Path(geo.cad_file_path).parent / "processed"
    processed_dir.mkdir(exist_ok=True)
    stl_path, mm_to_m = convert_to_stl(Path(geo.cad_file_path), processed_dir, scale=factor)

    info = dict(geo.transform_info or {})
    yaw, pitch, roll = info.get("yaw", 0.0), info.get("pitch", 0.0), info.get("roll", 0.0)
    if yaw or pitch or roll:
        from backend.cad.converter import rotate_stl
        rotate_stl(stl_path, stl_path, yaw, pitch, roll,
                   center=(0.0, 0.0, 0.0), invert=False)

    info.update({"mm_to_m": mm_to_m, "scale": factor})
    geo.transform_info = info
    geo.stl_file_path = str(stl_path)
    await db.commit()
    await db.refresh(geo)
    return geo


@router.get("/geometries/{geo_id}/domain-params")
async def geometry_domain_params(geo_id: int, current_user: CurrentUser, db: DB):
    """Return auto-computed domain and refinement box params for a geometry."""
    from backend.foam.case_builder import _auto_domain_params, _refbox_from_rotated_stl

    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)
    if not geo.stl_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No STL available")

    stl_path = Path(geo.stl_file_path)
    domain = _auto_domain_params(stl_path)
    refbox = {**domain, **_refbox_from_rotated_stl(stl_path)}
    s = domain["domain_scale"]
    return {
        "domain": {
            "xmin": domain["domain_xmin"] * s,
            "xmax": domain["domain_xmax"] * s,
            "ymin": -domain["domain_yhalf"] * s,
            "ymax":  domain["domain_yhalf"] * s,
            "zmin": domain["domain_zmin"] * s,
            "zmax": domain["domain_zmax"] * s,
        },
        "refbox": {
            "xmin": refbox["refbox_min"][0],
            "xmax": refbox["refbox_max"][0],
            "ymin": refbox["refbox_min"][1],
            "ymax": refbox["refbox_max"][1],
            "zmin": refbox["refbox_min"][2],
            "zmax": refbox["refbox_max"][2],
        },
    }


@router.get("/geometries/{geo_id}/preview")
async def geometry_preview(geo_id: int, current_user: CurrentUser, db: DB):
    """Render a PNG preview of the geometry (no simulation required)."""
    from fastapi.responses import Response
    from backend.visualization import get_backend
    from backend.foam.case_builder import _auto_domain_params, _refbox_from_rotated_stl

    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)
    if not geo.stl_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No STL available")

    stl_path = Path(geo.stl_file_path)
    try:
        domain = _auto_domain_params(stl_path)
        refbox = {**domain, **_refbox_from_rotated_stl(stl_path)}
    except Exception:
        domain, refbox = None, None

    viz = get_backend()
    png = viz.preview_geometry(stl_path, label=geo.name, domain=domain, refbox=refbox)
    return Response(content=png, media_type="image/png")
