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
    """(phase1, final-phase) solver log names, parameter-based (not
    file-existence-based) because progress polling tails logs on the cluster
    where local existence checks don't apply.

    The second name is the log of the *last* phase the case will run: the gas
    LES for gas-restarted cases, otherwise the aero/dispersion LES solver.
    """
    if params.get("gas_les"):
        return "log.simpleFoam", "log.rhoReactingBuoyantFoam"
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
            if sim.parameters.get("gas_les"):
                end_time = float(sim.parameters.get("gas_end_time", 0.7))
                phase = 3
            else:
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
    mode: str = "steady"           # "steady" = extend | "unsteady" = to LES | "gas" = to gas-dispersion LES
    name: str = ""                 # child case name (required)
    new_end_time: int | None = None
    les_end_time: float | None = None
    les_delta_t: float | None = None
    les_anim_interval: int | None = None
    les_model: str = "kOmegaSSTDDES"
    gas_end_time: float | None = None
    gas_delta_t: float | None = None
    gas_model: str | None = None
    gas_density_ratio: float | None = None
    source_position: list | None = None
    source_rate: float | None = None
    gas_source_start_time: float | None = None
    gas_source_stop_time: float | None = None
    les_warmup_time: float | None = None   # steady->gas: aero-LES warm-up before gas (0 = direct)


@router.post("/restart", response_model=JobStatusResponse)
async def restart_job(sim_id: int, body: RestartRequest, current_user: CurrentUser, db: DB):
    """Restart a finished case as a NEW child case: extend steady, transition
    to LES, or to gas-dispersion LES.

    The parent case is never modified — a new Simulation row + case directory is
    created, its mesh + converged solution seeded from the parent's decomposed
    processor* dirs. This lets a single parent spawn several children (e.g. LES
    DDES vs IDDES) and each child be deleted independently.
    """
    import shutil
    from backend.foam.case_builder import (
        build_les_restart_case, build_gas_les_restart_case, build_warmup_gas_case,
        copy_case_for_restart, LES_RESTART_MODELS,
    )

    parent = await _get_sim_with_geo(sim_id, db)
    if current_user.role != UserRole.admin and parent.geometry.project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if parent.status not in (SimulationStatus.done, SimulationStatus.failed):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can only restart DONE or FAILED jobs")
    if not parent.case_dir:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No case directory found")
    child_name = (body.name or "").strip()
    if not child_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Child case name is required")
    if body.mode in ("steady", "unsteady") and parent.solver_type != SimulatorType.steady:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Restart only supported for steady-state simulations")

    # Validate per mode and prepare new_params / new_solver_type / a builder that
    # runs against the child dir (created after these checks pass).
    if body.mode == "steady":
        if body.new_end_time is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="new_end_time is required for steady restart")
        _new_end = body.new_end_time
        new_params = {**parent.parameters, "end_time": _new_end}
        new_solver_type = parent.solver_type

        def _build(child_dir: Path):
            build_restart_case(child_dir, _new_end)

    elif body.mode == "unsteady":
        if parent.status != SimulationStatus.done:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="LES transition requires a completed (DONE) steady run")
        turb = parent.parameters.get("turbulence_model", "kOmegaSST")
        if turb != "kOmegaSST":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"LES transition requires a kOmegaSST steady solution (this case used {turb})")
        if body.les_model not in LES_RESTART_MODELS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"les_model must be one of {LES_RESTART_MODELS}")
        new_params = {**parent.parameters, "les_model": body.les_model}
        for key, val in (("les_end_time", body.les_end_time),
                         ("les_delta_t", body.les_delta_t),
                         ("les_anim_interval", body.les_anim_interval)):
            if val is not None:
                new_params[key] = val
        new_solver_type = SimulatorType.unsteady

        def _build(child_dir: Path):
            build_les_restart_case(child_dir, new_params)

    elif body.mode == "gas":
        if parent.status != SimulationStatus.done:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Gas-dispersion restart requires a completed (DONE) run")
        if parent.parameters.get("gas_les"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="This case already ran the gas-dispersion stage")
        # Two entry points to gas dispersion, both producing an UNSTEADY gas child:
        #  - from a finished aero LES (DES): emission usually immediate
        #  - from a finished steady kOmegaSST: one rhoReactingBuoyantFoam run that
        #    develops turbulence gas-free then emits at gas_source_start_time
        if parent.solver_type == SimulatorType.unsteady:
            les_model = parent.parameters.get("les_model")
            if les_model not in LES_RESTART_MODELS:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="Gas-dispersion restart requires a kOmegaSST-DES aero LES "
                                           f"(this case used {les_model})")
            gas_model = body.gas_model or les_model
        else:  # steady parent
            turb = parent.parameters.get("turbulence_model", "kOmegaSST")
            if turb != "kOmegaSST":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="Direct gas-dispersion restart requires a kOmegaSST steady "
                                           f"solution (this case used {turb})")
            gas_model = body.gas_model or "kOmegaSSTDDES"
        if gas_model not in LES_RESTART_MODELS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"gas_model must be one of {LES_RESTART_MODELS}")
        gas_end = body.gas_end_time if body.gas_end_time is not None else 0.7
        gas_dt = body.gas_delta_t if body.gas_delta_t is not None else 1e-4
        gas_start = body.gas_source_start_time if body.gas_source_start_time is not None else 0.0
        gas_stop = body.gas_source_stop_time if body.gas_source_stop_time is not None else 0.0
        new_params = {
            **parent.parameters,
            "gas_les": True,
            "gas_end_time": gas_end,
            "gas_delta_t": gas_dt,
            "gas_model": gas_model,
            "gas_source_start_time": gas_start,
            "gas_source_stop_time": gas_stop,
            "les_model": gas_model,
            "les_end_time": gas_end,
            "les_delta_t": gas_dt,
        }
        if body.les_anim_interval is not None:
            new_params["les_anim_interval"] = body.les_anim_interval
        if body.gas_density_ratio is not None:
            new_params["gas_density_ratio"] = body.gas_density_ratio
        if body.source_position is not None:
            new_params["source_position"] = body.source_position
        if body.source_rate is not None:
            new_params["source_rate"] = body.source_rate
        new_solver_type = SimulatorType.unsteady

        # From a steady parent, an optional aero-LES warm-up develops turbulence
        # before the compressible gas stage (the direct-from-RANS seed stalls).
        warmup = body.les_warmup_time or 0.0
        _use_warmup = (parent.solver_type == SimulatorType.steady and warmup > 0)
        if _use_warmup:
            new_params["les_warmup_time"] = warmup

        def _build(child_dir: Path):
            if _use_warmup:
                build_warmup_gas_case(child_dir, new_params)
            else:
                build_gas_les_restart_case(child_dir, new_params)

    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode must be 'steady', 'unsteady' or 'gas'")

    # Create the child Simulation row (independent case, references the parent).
    child = Simulation(
        geometry_id=parent.geometry_id,
        parent_id=parent.id,
        name=child_name,
        solver_type=new_solver_type,
        status=SimulationStatus.pending,
        parameters=new_params,
        yaw_deg=parent.yaw_deg,
        pitch_deg=parent.pitch_deg,
        roll_deg=parent.roll_deg,
    )
    db.add(child)
    await db.flush()          # assign child.id
    child.case_dir = str(settings.CASES_DIR / str(child.id))

    parent_dir = Path(parent.case_dir)
    child_dir = Path(child.case_dir)

    runner = get_runner()
    if isinstance(runner, ClusterRunner):
        n_processors = settings.CLUSTER_N_PROCESSORS
    else:
        import os
        n_processors = min(int(parent.parameters.get("n_processors", 6)), os.cpu_count() or 6)

    try:
        copy_case_for_restart(parent_dir, child_dir)
        _build(child_dir)
        job_id = runner.submit(
            case_dir=child_dir,
            n_processors=n_processors,
            job_name=f"cwt_{child.id}",
            seed_processors_from=parent_dir,
        )
    except Exception:
        # Roll back the half-created child so the list doesn't show a stray case
        await db.rollback()
        if child_dir.exists():
            shutil.rmtree(child_dir, ignore_errors=True)
        raise

    child.job_id = job_id
    child.status = SimulationStatus.running
    child.started_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(child)

    return JobStatusResponse(sim_id=child.id, status=child.status, job_id=job_id)


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
