"""Geometry (CAD) management endpoints."""
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
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
    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    _assert_project_access(geo.project, current_user)
    await db.delete(geo)
    await db.commit()


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

    original_path = upload_dir / file.filename
    with open(original_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    stl_path = convert_to_stl(original_path, upload_dir, scale=scale)

    geo.cad_file_path = str(original_path)
    geo.stl_file_path = str(stl_path)
    if not geo.name or geo.name == "geometry":
        geo.name = original_path.stem
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

    upload_dir = Path(geo.cad_file_path).parent
    stl_path = convert_to_stl(Path(geo.cad_file_path), upload_dir, scale=factor)
    geo.stl_file_path = str(stl_path)
    await db.commit()
    await db.refresh(geo)
    return geo
