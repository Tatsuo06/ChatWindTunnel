from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.api.deps import DB, CurrentUser
from backend.core.config import settings
from backend.db.models import Geometry, Simulation, SimulationStatus, SimulatorType, UserRole
from backend.foam.case_builder import _turbulence_from_velocity

router = APIRouter(prefix="/simulations", tags=["simulations"])

DEFAULT_PARAMETERS = {
    # Domain auto-sizing (True = compute from STL bounding box at job submission)
    "auto_domain": True,
    # Flow conditions
    "velocity_mps": 10.0,
    "turbulent_ke": 0.24,
    "turbulent_omega": 1.78,
    # Solver
    "end_time": 1000,
    "delta_t": 1e-4,
    "n_processors": 16,
    "decompose_method": "scotch",
    # Unsteady (LES) Phase 2 solver settings — ignored for STEADY cases
    "les_end_time": 0.7,
    "les_delta_t": 1e-4,
    # Phase 2 animation frames (cutting plane / streamlines) every N time steps
    "les_anim_interval": 100,
    # Force coefficients
    "aref": 0.75,
    "lref": 1.42,
    "cofr": [0.72, 0.0, 0.0],
    # blockMesh domain  (scale: convertToMeters; x/y/z in normalized units)
    "domain_scale": 1,
    "domain_xmin": -5,
    "domain_xmax": 15,
    "domain_yhalf": 4,
    "domain_zmax": 8,
    "blockmesh_nx": 80,
    "blockmesh_ny": 67,
    "blockmesh_nz": 33,
    # snappyHexMesh refinement box and seed point (physical metres)
    "refbox_min": [-1.0, -0.7, 0.0],
    "refbox_max": [8.0, 0.7, 2.5],
    "location_in_mesh": [3.0001, 3.0001, 0.43],
    # Surface refinement levels
    "refinement_min": 5,
    "refinement_max": 6,
    # Turbulence model
    "turbulence_model": "kOmegaSST",
    # Analysis type: "aero" (wind loads) | "dispersion" (buoyant gas, Boussinesq)
    "case_type": "aero",
    # Gas dispersion settings (dispersion cases and gas-LES restarts)
    "gas_density_ratio": 0.07,  # rho_gas / rho_air — default hydrogen (H2 ~ 0.07)
    "source_position": None,    # [x, y, z]; None = auto (top centre of rotated geometry)
    "source_rate": 1.0,         # relative emission strength
}


class SimulationCreate(BaseModel):
    geometry_id: int
    name: str = ""
    solver_type: SimulatorType = SimulatorType.steady
    parameters: dict | None = None


class SimulationUpdate(BaseModel):
    parameters: dict | None = None
    yaw_deg: float | None = None
    pitch_deg: float | None = None
    roll_deg: float | None = None
    name: str | None = None


class SimulationResponse(BaseModel):
    id: int
    geometry_id: int
    name: str
    solver_type: SimulatorType
    status: SimulationStatus
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    parameters: dict
    job_id: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


async def _get_sim(sim_id: int, db) -> Simulation:
    result = await db.execute(
        select(Simulation)
        .where(Simulation.id == sim_id)
        .options(selectinload(Simulation.geometry).selectinload(Geometry.project))
    )
    sim = result.scalar_one_or_none()
    if not sim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    return sim


def _assert_access(sim: Simulation, user):
    if user.role != UserRole.admin and sim.geometry.project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")


_SYNC_STATUS_MAP = {
    "PENDING": SimulationStatus.meshing,
    "RUNNING": SimulationStatus.running,
    "DONE": SimulationStatus.done,
    "FAILED": SimulationStatus.failed,
}


@router.get("/active-jobs", tags=["jobs"])
async def list_active_jobs(current_user: CurrentUser, db: DB):
    """Return all RUNNING/MESHING simulations with enough info for the sync UI."""
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")

    result = await db.execute(
        select(Simulation)
        .options(selectinload(Simulation.geometry).selectinload(Geometry.project))
        .where(Simulation.status.in_([SimulationStatus.running, SimulationStatus.meshing]))
    )
    sims = result.scalars().all()
    return [
        {
            "sim_id": s.id,
            "name": s.name,
            "project": s.geometry.project.name,
            "geometry": s.geometry.name,
            "status": s.status.value,
            "job_id": s.job_id,
        }
        for s in sims
    ]


@router.post("/{sim_id}/sync-one", tags=["jobs"])
async def sync_one_job(sim_id: int, current_user: CurrentUser, db: DB):
    """Sync a single simulation with the cluster. Returns a result dict."""
    import asyncio
    from backend.cluster import get_runner
    from backend.cluster.cluster_runner import ClusterRunner

    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")

    runner = get_runner()
    if not isinstance(runner, ClusterRunner):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cluster runner not configured")

    result = await db.execute(
        select(Simulation)
        .options(selectinload(Simulation.geometry).selectinload(Geometry.project))
        .where(Simulation.id == sim_id)
    )
    sim = result.scalar_one_or_none()
    if not sim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")

    if not sim.job_id:
        return {"sim_id": sim_id, "result": "skipped", "reason": "no job_id"}

    try:
        raw = runner.status(sim.job_id)
        new_status = _SYNC_STATUS_MAP.get(raw, SimulationStatus.running)

        if new_status == SimulationStatus.done and sim.case_dir:
            from backend.cluster.cluster_runner import _ssh as _cluster_ssh
            remote_dir = runner._remote_dir(Path(sim.case_dir))
            from backend.api.jobs import _phase_log_names
            _p1, _p2 = _phase_log_names(sim.parameters or {})
            solver_log = _p1 if sim.solver_type == SimulatorType.steady else _p2
            check_out, _ = _cluster_ssh(f"test -f {remote_dir}/{solver_log} && echo yes || echo no")
            if check_out.strip() != "yes":
                return {"sim_id": sim_id, "result": "error",
                        "reason": f"qstat=DONE but {solver_log} missing on cluster"}

        if new_status != sim.status:
            old_status = sim.status.value
            sim.status = new_status
            if new_status in (SimulationStatus.done, SimulationStatus.failed):
                sim.finished_at = datetime.now(timezone.utc)
            await db.commit()
            if new_status == SimulationStatus.done and sim.case_dir:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, runner.fetch_results, sim.job_id, Path(sim.case_dir)
                )
            return {
                "sim_id": sim_id, "result": "updated",
                "name": sim.name,
                "project": sim.geometry.project.name,
                "geometry": sim.geometry.name,
                "old_status": old_status,
                "new_status": new_status.value,
            }
        return {"sim_id": sim_id, "result": "no_change", "status": new_status.value}

    except Exception as exc:
        return {"sim_id": sim_id, "result": "error", "reason": str(exc)}


@router.post("/sync-cluster", tags=["jobs"])
async def sync_cluster_jobs(current_user: CurrentUser, db: DB):
    """Admin: poll cluster for all running/meshing jobs and update DB + fetch results."""
    import asyncio
    from backend.cluster import get_runner
    from backend.cluster.cluster_runner import ClusterRunner

    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")

    runner = get_runner()
    if not isinstance(runner, ClusterRunner):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cluster runner not configured")

    result = await db.execute(
        select(Simulation)
        .options(selectinload(Simulation.geometry).selectinload(Geometry.project))
        .where(Simulation.status.in_([SimulationStatus.running, SimulationStatus.meshing]))
    )
    sims = result.scalars().all()

    updated = []
    errors = []
    for sim in sims:
        if not sim.job_id:
            continue
        try:
            raw = runner.status(sim.job_id)
            new_status = _SYNC_STATUS_MAP.get(raw, SimulationStatus.running)

            if new_status == SimulationStatus.done and sim.case_dir:
                from backend.cluster.cluster_runner import _ssh as _cluster_ssh
                remote_dir = runner._remote_dir(Path(sim.case_dir))
                from backend.api.jobs import _phase_log_names
                _p1, _p2 = _phase_log_names(sim.parameters or {})
                solver_log = _p1 if sim.solver_type == SimulatorType.steady else _p2
                check_out, _ = _cluster_ssh(f"test -f {remote_dir}/{solver_log} && echo yes || echo no")
                if check_out.strip() != "yes":
                    errors.append({"sim_id": sim.id, "error": f"qstat=DONE but {solver_log} missing on cluster — skipped"})
                    continue

            if new_status != sim.status:
                old_status = sim.status
                sim.status = new_status
                if new_status in (SimulationStatus.done, SimulationStatus.failed):
                    sim.finished_at = datetime.now(timezone.utc)
                await db.commit()
                updated.append({
                    "sim_id": sim.id,
                    "name": sim.name,
                    "project": sim.geometry.project.name,
                    "geometry": sim.geometry.name,
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                })
                if new_status == SimulationStatus.done and sim.case_dir:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, runner.fetch_results, sim.job_id, Path(sim.case_dir)
                    )
        except Exception as exc:
            errors.append({"sim_id": sim.id, "error": str(exc)})

    return {"checked": len(sims), "updated": updated, "errors": errors}


@router.get("/runner-info")
async def runner_info(current_user: CurrentUser):
    """Return which runners are available."""
    from backend.cluster.local_runner import local_foam_available
    return {
        "cluster_available": bool(settings.CLUSTER_HOST),
        "local_available": settings.ALLOW_LOCAL_RUNNER and local_foam_available(),
    }


@router.get("/status-summary")
async def status_summary(current_user: CurrentUser, db: DB):
    """Return DB status counts plus live qstat Q/R counts from the cluster."""
    import asyncio
    from sqlalchemy import func
    from backend.db.models import Project
    from backend.cluster import get_runner
    from backend.cluster.cluster_runner import ClusterRunner

    if current_user.role == UserRole.admin:
        result = await db.execute(
            select(Simulation.status, func.count(Simulation.id))
            .group_by(Simulation.status)
        )
    else:
        result = await db.execute(
            select(Simulation.status, func.count(Simulation.id))
            .join(Geometry, Simulation.geometry_id == Geometry.id)
            .join(Project, Geometry.project_id == Project.id)
            .where(Project.owner_id == current_user.id)
            .group_by(Simulation.status)
        )
    db_counts = {row[0].value: row[1] for row in result.all()}

    # Augment with live qstat counts
    runner = get_runner()
    if isinstance(runner, ClusterRunner):
        loop = asyncio.get_event_loop()
        try:
            qcounts = await loop.run_in_executor(None, runner.queue_counts)
            db_counts["QUEUED"] = qcounts.get("queued", 0)
            db_counts["CLUSTER_RUNNING"] = qcounts.get("running", 0)
        except Exception:
            pass

    return db_counts


@router.get("/geometry/{geo_id}", response_model=list[SimulationResponse])
async def list_simulations(geo_id: int, current_user: CurrentUser, db: DB):
    result = await db.execute(
        select(Geometry).where(Geometry.id == geo_id).options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    if current_user.role != UserRole.admin and geo.project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    result = await db.execute(select(Simulation).where(Simulation.geometry_id == geo_id))
    return result.scalars().all()


@router.post("", response_model=SimulationResponse, status_code=status.HTTP_201_CREATED)
async def create_simulation(body: SimulationCreate, current_user: CurrentUser, db: DB):
    result = await db.execute(
        select(Geometry).where(Geometry.id == body.geometry_id)
        .options(selectinload(Geometry.project))
    )
    geo = result.scalar_one_or_none()
    if not geo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    if current_user.role != UserRole.admin and geo.project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    params = DEFAULT_PARAMETERS.copy()
    if body.parameters:
        params.update(body.parameters)
    k, omega = _turbulence_from_velocity(
        params["velocity_mps"], params.get("turbulence_intensity", 0.05), params.get("lref", 1.0)
    )
    params["turbulent_ke"] = k
    params["turbulent_omega"] = omega
    sim = Simulation(
        geometry_id=body.geometry_id,
        name=body.name or f"sim_{body.solver_type.value.lower()}",
        solver_type=body.solver_type,
        parameters=params,
    )
    db.add(sim)
    await db.commit()
    await db.refresh(sim)
    return sim


@router.get("/{sim_id}", response_model=SimulationResponse)
async def get_simulation(sim_id: int, current_user: CurrentUser, db: DB):
    sim = await _get_sim(sim_id, db)
    _assert_access(sim, current_user)
    return sim


@router.patch("/{sim_id}", response_model=SimulationResponse)
async def update_simulation(sim_id: int, body: SimulationUpdate, current_user: CurrentUser, db: DB):
    sim = await _get_sim(sim_id, db)
    _assert_access(sim, current_user)

    if sim.status in (SimulationStatus.meshing, SimulationStatus.running):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cannot update a simulation that is currently running")

    if body.name is not None:
        sim.name = body.name
    if body.parameters is not None:
        merged = {**sim.parameters, **body.parameters}
        # Recompute k and omega whenever velocity or lref changes
        if "velocity_mps" in body.parameters or "lref" in body.parameters or "turbulence_intensity" in body.parameters:
            k, omega = _turbulence_from_velocity(
                merged["velocity_mps"], merged.get("turbulence_intensity", 0.05), merged.get("lref", 1.0)
            )
            merged["turbulent_ke"] = k
            merged["turbulent_omega"] = omega
        sim.parameters = merged
    if body.yaw_deg is not None:
        sim.yaw_deg = body.yaw_deg
    if body.pitch_deg is not None:
        sim.pitch_deg = body.pitch_deg
    if body.roll_deg is not None:
        sim.roll_deg = body.roll_deg

    await db.commit()
    await db.refresh(sim)
    return sim


@router.delete("/{sim_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_simulation(sim_id: int, current_user: CurrentUser, db: DB):
    import asyncio, shutil
    from backend.cluster import get_runner
    from backend.cluster.cluster_runner import ClusterRunner

    sim = await _get_sim(sim_id, db)
    _assert_access(sim, current_user)

    case_dir = Path(sim.case_dir) if sim.case_dir else None

    await db.delete(sim)
    await db.commit()

    # Delete local case directory
    if case_dir and case_dir.exists():
        shutil.rmtree(case_dir, ignore_errors=True)

    # Delete cluster case directory (fire-and-forget background thread)
    runner = get_runner()
    if isinstance(runner, ClusterRunner) and case_dir:
        from backend.cluster.cluster_runner import _ssh
        import threading
        remote_dir = runner._remote_dir(case_dir)
        threading.Thread(target=_ssh, args=(f"rm -rf {remote_dir}",), daemon=True).start()
