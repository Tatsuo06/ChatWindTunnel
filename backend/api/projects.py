from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.api.deps import DB, AdminUser, CurrentUser
from backend.db.models import Geometry, Project, Simulation, SimulationStatus, UserRole
import json

from backend.visualization.parsers import parse_force_coefficients

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str
    owner_id: int
    owner_username: str = ""

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _extract_owner_username(cls, v):
        if hasattr(v, "owner") and v.owner is not None:
            return {
                "id": v.id,
                "name": v.name,
                "description": v.description,
                "owner_id": v.owner_id,
                "owner_username": v.owner.username,
            }
        return v


def _assert_owner_or_admin(project: Project, user):
    if user.role != UserRole.admin and project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")


_WITH_OWNER = selectinload(Project.owner)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, current_user: CurrentUser, db: DB):
    project = Project(name=body.name, description=body.description, owner_id=current_user.id)
    db.add(project)
    await db.commit()
    result = await db.execute(
        select(Project).where(Project.id == project.id).options(_WITH_OWNER)
    )
    return result.scalar_one()


@router.get("", response_model=list[ProjectResponse])
async def list_projects(current_user: CurrentUser, db: DB):
    q = select(Project).options(_WITH_OWNER)
    if current_user.role != UserRole.admin:
        q = q.where(Project.owner_id == current_user.id)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int, current_user: CurrentUser, db: DB):
    result = await db.execute(
        select(Project).where(Project.id == project_id).options(_WITH_OWNER)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _assert_owner_or_admin(project, current_user)
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: int, body: ProjectUpdate, current_user: CurrentUser, db: DB):
    result = await db.execute(
        select(Project).where(Project.id == project_id).options(_WITH_OWNER)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _assert_owner_or_admin(project, current_user)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    await db.commit()
    await db.refresh(project)
    result = await db.execute(
        select(Project).where(Project.id == project.id).options(_WITH_OWNER)
    )
    return result.scalar_one()


@router.get("/{project_id}/results/cd-cl")
async def cd_cl_summary(project_id: int, current_user: CurrentUser, db: DB):
    """Return final Cd/Cl values for all DONE simulations in the project, grouped by geometry."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _assert_owner_or_admin(project, current_user)

    geos_result = await db.execute(
        select(Geometry)
        .where(Geometry.project_id == project_id)
        .options(selectinload(Geometry.simulations))
    )
    geos = geos_result.scalars().all()

    out = []
    for geo in geos:
        points = []
        all_ref: list[dict] = []
        for sim in geo.simulations:
            if sim.status != SimulationStatus.done or not sim.case_dir:
                continue
            df = parse_force_coefficients(Path(sim.case_dir))
            if df.empty:
                continue
            tail = df.iloc[max(0, int(len(df) * 0.8)):]
            avg = tail.mean(numeric_only=True)
            points.append({
                "sim_id": sim.id,
                "sim_name": sim.name,
                "yaw_deg": sim.yaw_deg,
                "pitch_deg": sim.pitch_deg,
                "roll_deg": sim.roll_deg,
                "Cx": round(float(avg["Cx"]), 4),
                "Cz": round(float(avg["Cz"]), 4),
                "Cy": round(float(avg["Cy"]), 4) if "Cy" in avg.index else None,
                "CmYaw": round(float(avg["CmYaw"]), 4) if "CmYaw" in avg.index else None,
            })
            case_params_file = Path(sim.case_dir) / "case_params.json"
            if case_params_file.exists():
                p = json.loads(case_params_file.read_text())
            else:
                p = sim.parameters or {}
            all_ref.append({
                "sim_name": sim.name,
                "velocity_mps": p.get("velocity_mps"),
                "aref": p.get("aref"),
                "lref": p.get("lref"),
                "cofr": p.get("cofr"),
                "nu": p.get("nu", 1.5e-5),
            })
        if points:
            points.sort(key=lambda x: x["yaw_deg"])
            ref_params = {"rho_ref": 1.0, **{k: all_ref[0][k] for k in ("velocity_mps", "aref", "lref", "cofr", "nu")}} if all_ref else None
            # Detect inconsistencies across cases
            mismatch = []
            for key in ("velocity_mps", "aref", "lref", "cofr"):
                vals = [r[key] for r in all_ref if r[key] is not None]
                if len(set(str(v) for v in vals)) > 1:
                    mismatch.append(key)
            out.append({
                "geo_id": geo.id,
                "geo_name": geo.name,
                "points": points,
                "ref_params": ref_params,
                "ref_params_mismatch": mismatch or None,
                "all_ref": all_ref if mismatch else None,
            })

    return out


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, current_user: CurrentUser, db: DB):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _assert_owner_or_admin(project, current_user)
    await db.delete(project)
    await db.commit()
