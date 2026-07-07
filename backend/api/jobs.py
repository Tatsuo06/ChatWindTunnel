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
from backend.cluster.local_runner import LocalRunner, local_foam_available
from backend.core.config import settings
from backend.db.models import Geometry, Simulation, SimulationStatus, SimulatorType, UserRole
from backend.foam.case_builder import build_case, build_restart_case

router = APIRouter(prefix="/simulations/{sim_id}/job", tags=["jobs"])


class SubmitRequest(BaseModel):
    runner_type: str = "auto"  # "auto" | "local" | "cluster"


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
async def submit_job(sim_id: int, body: SubmitRequest, current_user: CurrentUser, db: DB):
    sim = await _get_sim_with_geo(sim_id, db)
    if current_user.role != UserRole.admin and sim.geometry.project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if sim.status == SimulationStatus.running:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already running")
    if not sim.geometry.stl_file_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No STL uploaded")

    if body.runner_type == "local":
        if not settings.ALLOW_LOCAL_RUNNER:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Local runner is disabled (ALLOW_LOCAL_RUNNER=false)")
        runner = LocalRunner()
    elif body.runner_type == "cluster":
        runner = ClusterRunner()
    else:
        runner = get_runner()

    import os
    if isinstance(runner, ClusterRunner):
        n_processors = settings.CLUSTER_N_PROCESSORS
    else:
        n_processors = min(int(sim.parameters.get("n_processors", 6)), os.cpu_count() or 6)
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


def _phase_log_names(params: dict) -> tuple[str, str]:
    """(phase1, phase2) solver log names — Boussinesq solvers for dispersion cases.

    Parameter-based (not file-existence-based) because progress polling tails
    logs on the cluster where local existence checks don't apply.
    """
    if params.get("case_type") == "dispersion":
        return "log.buoyantBoussinesqSimpleFoam", "log.buoyantBoussinesqPimpleFoam"
    return "log.simpleFoam", "log.pisoFoam"


def _tail_log_text(runner, case_dir: Path, log_name: str) -> str:
    if isinstance(runner, ClusterRunner):
        from backend.cluster.cluster_runner import _ssh
        remote_dir = runner._remote_dir(case_dir)
        text, _ = _ssh(f"tail -300 {remote_dir}/{log_name} 2>/dev/null")
        return text
    log_path = case_dir / log_name
    if log_path.exists():
        lines = log_path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-300:])
    return ""


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
    runner = get_runner()
    phase1_log, phase2_log = _phase_log_names(sim.parameters)

    if sim.solver_type == SimulatorType.unsteady:
        # Phase 2 (LES) takes over from Phase 1 (RAS) partway through the
        # Allrun script; report whichever phase has actually produced output so far.
        text = _tail_log_text(runner, case_dir, phase2_log)
        times = _re.findall(r"^Time = ([\d.e+\-]+)", text, _re.MULTILINE)
        if times:
            log_name = phase2_log
            end_time = float(sim.parameters.get("les_end_time", 0.7))
            phase = 2
        else:
            log_name = phase1_log
            end_time = float(sim.parameters.get("end_time", 500))
            phase = 1
            text = _tail_log_text(runner, case_dir, log_name)
            times = _re.findall(r"^Time = ([\d.e+\-]+)", text, _re.MULTILINE)

        if not times:
            return {"current_time": None, "end_time": end_time, "pct": 0.0,
                    "solver": log_name.replace("log.", ""), "phase": phase}

        current_time = float(times[-1])
        pct = min(100.0, round(current_time / end_time * 100, 1)) if end_time > 0 else 0.0
        return {
            "current_time": current_time,
            "end_time": end_time,
            "pct": pct,
            "solver": log_name.replace("log.", ""),
            "phase": phase,
        }

    log_name = phase1_log
    end_time = float(sim.parameters.get("end_time", 500))
    text = _tail_log_text(runner, case_dir, log_name)

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


class RestartRequest(BaseModel):
    mode: str = "steady"           # "steady" = extend iterations | "unsteady" = transition to LES
    new_end_time: int | None = None
    les_end_time: float | None = None
    les_delta_t: float | None = None
    les_anim_interval: int | None = None
    les_model: str = "kOmegaSSTDDES"


@router.post("/restart", response_model=JobStatusResponse)
async def restart_job(sim_id: int, body: RestartRequest, current_user: CurrentUser, db: DB):
    """Restart a finished steady case: extend it, or transition it to LES."""
    from backend.foam.case_builder import build_les_restart_case, LES_RESTART_MODELS

    sim = await _get_sim_with_geo(sim_id, db)
    if current_user.role != UserRole.admin and sim.geometry.project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if sim.status not in (SimulationStatus.done, SimulationStatus.failed):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can only restart DONE or FAILED jobs")
    if not sim.case_dir:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No case directory found")
    if sim.solver_type != SimulatorType.steady:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Restart only supported for steady-state simulations")

    if body.mode == "steady":
        if body.new_end_time is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="new_end_time is required for steady restart")
        build_restart_case(Path(sim.case_dir), body.new_end_time)
        new_params = {**sim.parameters, "end_time": body.new_end_time}
        new_solver_type = sim.solver_type

    elif body.mode == "unsteady":
        if sim.status != SimulationStatus.done:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="LES transition requires a completed (DONE) steady run")
        turb = sim.parameters.get("turbulence_model", "kOmegaSST")
        if turb != "kOmegaSST":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"LES transition requires a kOmegaSST steady solution (this case used {turb})")
        if body.les_model not in LES_RESTART_MODELS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"les_model must be one of {LES_RESTART_MODELS}")
        new_params = {**sim.parameters, "les_model": body.les_model}
        for key, val in (("les_end_time", body.les_end_time),
                         ("les_delta_t", body.les_delta_t),
                         ("les_anim_interval", body.les_anim_interval)):
            if val is not None:
                new_params[key] = val
        build_les_restart_case(Path(sim.case_dir), new_params)
        new_solver_type = SimulatorType.unsteady

    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode must be 'steady' or 'unsteady'")

    runner = get_runner()
    if isinstance(runner, ClusterRunner):
        n_processors = settings.CLUSTER_N_PROCESSORS
    else:
        import os
        n_processors = min(int(sim.parameters.get("n_processors", 6)), os.cpu_count() or 6)
    job_id = runner.submit(
        case_dir=Path(sim.case_dir),
        n_processors=n_processors,
        job_name=f"cwt_{sim.id}_r",
    )

    sim.parameters = new_params
    sim.solver_type = new_solver_type
    sim.job_id = job_id
    sim.status = SimulationStatus.running
    sim.started_at = datetime.now(timezone.utc)
    sim.finished_at = None
    await db.commit()
    await db.refresh(sim)

    return JobStatusResponse(sim_id=sim.id, status=sim.status, job_id=job_id)


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
