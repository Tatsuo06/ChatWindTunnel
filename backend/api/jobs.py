"""Job submission and status endpoints."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.api.deps import DB, CurrentUser
from backend.cluster import get_runner
from backend.cluster.cluster_runner import ClusterRunner
from backend.core.config import settings
from backend.db.models import Geometry, Simulation, SimulationStatus, UserRole
from backend.foam.case_builder import build_case

router = APIRouter(prefix="/simulations/{sim_id}/job", tags=["jobs"])


class JobStatusResponse(BaseModel):
    sim_id: int
    status: SimulationStatus
    job_id: str


async def _get_sim_with_geo(sim_id: int, db) -> Simulation:
    result = await db.execute(
        select(Simulation)
        .where(Simulation.id == sim_id)
        .options(selectinload(Simulation.geometry).selectinload(Geometry.project))
    )
    sim = result.scalar_one_or_none()
    if not sim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    return sim


@router.post("/submit", response_model=JobStatusResponse)
async def submit_job(sim_id: int, current_user: CurrentUser, db: DB):
    sim = await _get_sim_with_geo(sim_id, db)
    if current_user.role != UserRole.admin and sim.geometry.project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if sim.status == SimulationStatus.running:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already running")
    if not sim.geometry.stl_file_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No STL uploaded")

    runner = get_runner()
    n_processors = (
        settings.CLUSTER_N_PROCESSORS
        if isinstance(runner, ClusterRunner)
        else int(sim.parameters.get("n_processors", 6))
    )
    case_dir = build_case(
        sim_id=sim.id,
        stl_path=Path(sim.geometry.stl_file_path),
        solver_type=sim.solver_type,
        params={**sim.parameters, "n_processors": n_processors},
        yaw_deg=sim.yaw_deg,
        pitch_deg=sim.pitch_deg,
        roll_deg=sim.roll_deg,
    )
    job_id = runner.submit(
        case_dir=case_dir,
        n_processors=n_processors,
        job_name=f"cwt_{sim.id}",
    )

    sim.job_id = job_id
    sim.case_dir = str(case_dir)
    sim.status = SimulationStatus.meshing
    sim.started_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sim)

    return JobStatusResponse(sim_id=sim.id, status=sim.status, job_id=job_id)


@router.get("/status", response_model=JobStatusResponse)
async def poll_status(sim_id: int, current_user: CurrentUser, db: DB):
    result = await db.execute(select(Simulation).where(Simulation.id == sim_id))
    sim = result.scalar_one_or_none()
    if not sim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")

    if sim.status in (SimulationStatus.done, SimulationStatus.failed, SimulationStatus.pending):
        return JobStatusResponse(sim_id=sim.id, status=sim.status, job_id=sim.job_id)

    runner = get_runner()
    raw_status = runner.status(sim.job_id)

    status_map = {
        "PENDING": SimulationStatus.meshing,
        "RUNNING": SimulationStatus.running,
        "DONE": SimulationStatus.done,
        "FAILED": SimulationStatus.failed,
    }
    new_status = status_map.get(raw_status, SimulationStatus.running)

    if new_status != sim.status:
        sim.status = new_status
        if new_status in (SimulationStatus.done, SimulationStatus.failed):
            sim.finished_at = datetime.now(timezone.utc)
        await db.commit()

    # Download results from cluster when job finishes
    if new_status == SimulationStatus.done and sim.case_dir:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, runner.fetch_results, sim.job_id, Path(sim.case_dir)
        )

    return JobStatusResponse(sim_id=sim.id, status=sim.status, job_id=sim.job_id)


@router.get("/progress")
async def job_progress(sim_id: int, current_user: CurrentUser, db: DB):
    """Return solver progress: current Time, end_time, and percent complete."""
    import re as _re
    from backend.db.models import SimulatorType

    result = await db.execute(select(Simulation).where(Simulation.id == sim_id))
    sim = result.scalar_one_or_none()
    if not sim or not sim.case_dir:
        return {"current_time": None, "end_time": None, "pct": None, "solver": None}

    case_dir = Path(sim.case_dir)

    if sim.solver_type == SimulatorType.unsteady:
        log_name = "log.pisoFoam"
        end_time = 0.7
    else:
        log_name = "log.simpleFoam"
        end_time = float(sim.parameters.get("end_time", 500))

    text = ""
    runner = get_runner()
    if isinstance(runner, ClusterRunner):
        from backend.cluster.cluster_runner import _ssh
        remote_dir = runner._remote_dir(case_dir)
        text, _ = _ssh(f"tail -300 {remote_dir}/{log_name} 2>/dev/null")
    else:
        log_path = case_dir / log_name
        if log_path.exists():
            lines = log_path.read_text(errors="replace").splitlines()
            text = "\n".join(lines[-300:])

    times = _re.findall(r"^Time = ([\d.e+\-]+)", text, _re.MULTILINE)
    if not times:
        return {"current_time": None, "end_time": end_time, "pct": 0.0,
                "solver": log_name.replace("log.", "")}

    current_time = float(times[-1])
    pct = min(100.0, round(current_time / end_time * 100, 1)) if end_time > 0 else 0.0
    return {
        "current_time": current_time,
        "end_time": end_time,
        "pct": pct,
        "solver": log_name.replace("log.", ""),
    }


@router.post("/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(sim_id: int, current_user: CurrentUser, db: DB):
    result = await db.execute(select(Simulation).where(Simulation.id == sim_id))
    sim = result.scalar_one_or_none()
    if not sim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    runner = get_runner()
    runner.cancel(sim.job_id)
    sim.status = SimulationStatus.failed
    sim.finished_at = datetime.now(timezone.utc)
    await db.commit()
