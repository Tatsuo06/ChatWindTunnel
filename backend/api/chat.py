from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.api.deps import DB, CurrentUser
from backend.chat.agent import chat
from backend.cluster import get_runner
from backend.cluster.cluster_runner import ClusterRunner
from backend.core.config import settings
from backend.db.models import ChatMessage, Geometry, Simulation, SimulationStatus, SimulatorType
from backend.db.models import UserRole
from backend.foam.case_builder import build_case

router = APIRouter(prefix="/simulations/{sim_id}/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    updated_parameters: dict



@router.post("", response_model=ChatResponse)
async def send_message(sim_id: int, body: ChatRequest, current_user: CurrentUser, db: DB):
    result = await db.execute(select(Simulation).where(Simulation.id == sim_id))
    sim = result.scalar_one_or_none()
    if not sim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")

    # Load history
    hist_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.simulation_id == sim_id)
        .order_by(ChatMessage.id)
    )
    history = [
        {"role": m.role, "content": m.content}
        for m in hist_result.scalars().all()
        if m.role in ("user", "assistant")
    ]
    history.append({"role": "user", "content": body.message})

    # Save user message
    db.add(ChatMessage(simulation_id=sim_id, role="user", content=body.message))

    # Call LLM
    case_dir = settings.CASES_DIR / str(sim_id)
    reply, updated_params, angle_updates, sim_creations, job_submission = await chat(
        history, sim.parameters, case_dir=case_dir
    )

    # Persist updates to current simulation
    sim.parameters = updated_params
    if "yaw_deg" in angle_updates:
        sim.yaw_deg = angle_updates["yaw_deg"]
    if "pitch_deg" in angle_updates:
        sim.pitch_deg = angle_updates["pitch_deg"]
    if "roll_deg" in angle_updates:
        sim.roll_deg = angle_updates["roll_deg"]

    # Create new simulations requested by the agent
    for sc in sim_creations:
        solver = SimulatorType.unsteady if sc.get("solver_type") == "UNSTEADY" else SimulatorType.steady
        new_params = {**sim.parameters, "velocity_mps": sc["velocity_mps"]}
        new_sim = Simulation(
            geometry_id=sim.geometry_id,
            name=sc["name"],
            solver_type=solver,
            yaw_deg=sc["yaw_deg"],
            pitch_deg=sc["pitch_deg"],
            roll_deg=sc.get("roll_deg", 0.0),
            parameters=new_params,
        )
        db.add(new_sim)

    db.add(ChatMessage(simulation_id=sim_id, role="assistant", content=reply))
    await db.commit()

    # Submit jobs if requested
    if job_submission:
        await _submit_jobs_from_chat(sim, job_submission, db)

    return ChatResponse(reply=reply, updated_parameters=updated_params)


async def _submit_jobs_from_chat(current_sim: Simulation, job_submission: dict, db):
    """Submit pending simulations for the same geometry based on agent's job_submission spec."""
    # Reload geometry relationship for STL path
    result = await db.execute(
        select(Simulation)
        .where(Simulation.geometry_id == current_sim.geometry_id)
        .where(Simulation.status == SimulationStatus.pending)
        .options(selectinload(Simulation.geometry))
    )
    candidates = result.scalars().all()

    # Filter by names if specified
    names = job_submission.get("names")
    if names:
        candidates = [s for s in candidates if s.name in names]

    runner = get_runner()
    for s in candidates:
        if not s.geometry.stl_file_path:
            continue
        try:
            n_processors = (
                settings.CLUSTER_N_PROCESSORS
                if isinstance(runner, ClusterRunner)
                else int(s.parameters.get("n_processors", 6))
            )
            cd = build_case(
                sim_id=s.id,
                stl_path=Path(s.geometry.stl_file_path),
                solver_type=s.solver_type,
                params={**s.parameters, "n_processors": n_processors},
                yaw_deg=s.yaw_deg,
                pitch_deg=s.pitch_deg,
                roll_deg=s.roll_deg,
            )
            job_id = runner.submit(case_dir=cd, n_processors=n_processors, job_name=f"cwt_{s.id}")
            s.job_id = job_id
            s.case_dir = str(cd)
            s.status = SimulationStatus.meshing
            s.started_at = datetime.now(timezone.utc)
        except Exception as e:
            s.status = SimulationStatus.failed
            s.finished_at = datetime.now(timezone.utc)

    await db.commit()


@router.get("", response_model=list[dict])
async def get_history(sim_id: int, current_user: CurrentUser, db: DB):
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.simulation_id == sim_id)
        .order_by(ChatMessage.id)
    )
    return [{"role": m.role, "content": m.content, "id": m.id} for m in result.scalars().all()]
