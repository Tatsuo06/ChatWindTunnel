import json
import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.api.deps import DB, AdminUser, CurrentUser
from backend.core.config import get_llm_model, get_llm_base_url, get_llm_api_key
from backend.db.models import Geometry, Project, Simulation, SimulationStatus, UserRole
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

            # Auto-detect divergence
            diverged = False
            diverged_reason = None
            case_params_path = Path(sim.case_dir) / "case_params.json"
            params_for_div = json.loads(case_params_path.read_text()) if case_params_path.exists() else (sim.parameters or {})
            end_time = params_for_div.get("end_time", 1000)
            last_time = float(df["Time"].iloc[-1])
            if last_time < end_time * 0.95:
                diverged = True
                diverged_reason = f"stopped at step {int(last_time)}/{end_time}"
            elif any(abs(float(avg.get(c, 0) or 0)) > 100 for c in ("Cx", "Cz", "Cy") if c in avg.index):
                diverged = True
                diverged_reason = "coefficient overflow (diverged values)"

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
                "diverged": diverged,
                "diverged_reason": diverged_reason,
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


class ProjectChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class ProjectChatResponse(BaseModel):
    reply: str


@router.post("/{project_id}/chat", response_model=ProjectChatResponse)
async def project_chat(project_id: int, body: ProjectChatRequest, current_user: CurrentUser, db: DB):
    """Stateless LLM chat with project-level result summary as context. History is managed client-side."""
    from litellm import acompletion
    from backend.core.config import settings

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _assert_owner_or_admin(project, current_user)

    # Build summary context from all done simulations
    geos_result = await db.execute(
        select(Geometry)
        .where(Geometry.project_id == project_id)
        .options(selectinload(Geometry.simulations))
    )
    geos = geos_result.scalars().all()

    summary_lines = [f"Project: {project.name}"]
    for geo in geos:
        geo_lines = []
        ref_header: str | None = None
        for sim in geo.simulations:
            if sim.status != SimulationStatus.done or not sim.case_dir:
                continue
            df = parse_force_coefficients(Path(sim.case_dir))
            if df.empty:
                continue
            tail = df.iloc[max(0, int(len(df) * 0.8)):]
            avg = tail.mean(numeric_only=True)

            cx = round(float(avg.get("Cx", 0)), 4)
            cy = round(float(avg.get("Cy", 0)), 4) if "Cy" in avg.index else None
            cz = round(float(avg.get("Cz", 0)), 4) if "Cz" in avg.index else None
            yaw = sim.yaw_deg or 0.0
            psi = math.radians(yaw)
            bx = round(-(cx * math.cos(psi) - (cy or 0) * math.sin(psi)), 4)

            if ref_header is None:
                case_params_path = Path(sim.case_dir) / "case_params.json"
                p = json.loads(case_params_path.read_text()) if case_params_path.exists() else (sim.parameters or {})
                ref_header = f"  (U={p.get('velocity_mps','?')}m/s, Aref={p.get('aref','?')}m², Lref={p.get('lref','?')}m)"

            # Compact: one line per case — only yaw + coefficients
            if cy is not None:
                line = f"  yaw={yaw}°: Cx={cx}, Cz={cz}, Cy={cy}, Cx(b)={bx}"
            else:
                line = f"  yaw={yaw}°: Cx={cx}, Cz={cz}, Cx(b)={bx}"
            geo_lines.append(line)

        if geo_lines:
            summary_lines.append(f"Geometry: {geo.name}{' ' + ref_header if ref_header else ''}")
            summary_lines.extend(geo_lines)

    context = "\n".join(summary_lines) if len(summary_lines) > 1 else "No completed simulations found."

    system_prompt = f"""\
You are a CFD wind tunnel results analyst for the ChatWindTunnel system.
You help users understand and interpret their simulation results.
The following is a summary of all completed simulation results for this project:

{context}

Coefficient definitions:
- Cx: drag coefficient in wind axis (flow direction +X). Positive = drag.
- Cz: lift coefficient in wind axis (vertical +Z). Positive = upforce (lift).
- Cy: side force coefficient in wind axis (+Y lateral).
- Cx(body): drag coefficient in body-fixed frame (forward direction of the vehicle).
- Yaw angle: angle between wind direction and vehicle heading. Positive = wind from left.

When asked about "drag", refer to Cx or Cx(body) as appropriate.
When asked about "lift", refer to Cz.
Be concise and specific. Reference actual values from the data above.
Respond in the same language as the user.
STRICT OUTPUT RULES: Start your reply directly with the answer. Do not start with "The user is asking", "I need to", or any internal analysis. Just answer.
"""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(body.history)
    messages.append({"role": "user", "content": body.message})

    try:
        response = await acompletion(
            model=f"openai/{get_llm_model()}",
            api_base=get_llm_base_url(),
            api_key=get_llm_api_key(),
            messages=messages,
            temperature=0.3,
            extra_body={"reasoning_effort": "none"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM error: {e}",
        )
    reply = response.choices[0].message.content or ""
    # Strip thinking-model preamble (lines starting with internal reasoning phrases)
    if reply:
        lines = reply.splitlines()
        skip_prefixes = (
            "the user is asking", "the user wants",
            "i need to", "i have to", "i will ", "i'll ",
            "let me ", "first, ", "okay, ", "alright, ", "sure, ",
            "to answer", "looking at", "from the data,",
        )
        while lines and lines[0].strip().lower().startswith(skip_prefixes):
            lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        reply = "\n".join(lines)
    return ProjectChatResponse(reply=reply)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, current_user: CurrentUser, db: DB):
    import shutil, threading
    from sqlalchemy.orm import selectinload as _sil
    from backend.cluster import get_runner
    from backend.cluster.cluster_runner import ClusterRunner, _ssh

    result = await db.execute(
        select(Project).where(Project.id == project_id)
        .options(_sil(Project.geometries).selectinload(Geometry.simulations))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    _assert_owner_or_admin(project, current_user)

    # Collect active job IDs, case dirs, and uploaded files before deleting DB records
    from backend.db.models import SimulationStatus
    active_job_ids = [
        s.job_id
        for geo in project.geometries
        for s in geo.simulations
        if s.job_id and s.status in (SimulationStatus.running, SimulationStatus.meshing)
    ]
    case_dirs = [
        Path(s.case_dir)
        for geo in project.geometries
        for s in geo.simulations
        if s.case_dir
    ]
    upload_paths = [
        Path(p)
        for geo in project.geometries
        for p in (geo.cad_file_path, geo.stl_file_path)
        if p
    ]

    await db.delete(project)
    await db.commit()

    # Delete local case directories
    for case_dir in case_dirs:
        if case_dir.exists():
            shutil.rmtree(case_dir, ignore_errors=True)

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

    # Delete uploaded CAD/STL files
    for p in upload_paths:
        p.unlink(missing_ok=True)
