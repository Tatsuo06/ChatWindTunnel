"""LLM chat agent with tool use via LiteLLM.

LM Studio (local) is used by default. Switch to Claude/OpenAI by changing
LLM_BASE_URL and LLM_MODEL in config or .env.
"""
import json
from pathlib import Path
from typing import AsyncIterator

from litellm import acompletion

from backend.core.config import settings
from backend.visualization.parsers import parse_force_coefficients, parse_mesh_info, parse_residuals

SYSTEM_PROMPT = """\
You are a CFD simulation assistant for the ChatWindTunnel system.
Your role is to help users configure wind tunnel simulations using OpenFOAM v2206.
When the user specifies simulation conditions in natural language, call the appropriate tools to set parameters.
When the user asks to create multiple cases (e.g., sweep yaw angles), call create_simulation once per case.
When results are available, explain Cd (drag coefficient), Cl (lift coefficient), and convergence behavior clearly.
Always confirm parameter changes with the user before finalizing.
Respond in the same language as the user.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_flow_conditions",
            "description": "Set wind speed and direction for the simulation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "velocity_mps": {"type": "number", "description": "Wind speed in m/s"},
                    "yaw_deg": {"type": "number", "description": "Yaw angle in degrees (horizontal rotation)"},
                    "pitch_deg": {"type": "number", "description": "Pitch angle in degrees (vertical tilt)"},
                    "roll_deg": {"type": "number", "description": "Roll angle in degrees (rotation around flow axis)"},
                    "turbulence_intensity": {"type": "number", "description": "Turbulence intensity (0-1), used to compute k and omega"},
                },
                "required": ["velocity_mps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_solver_settings",
            "description": "Set solver type and time control parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "solver_type": {"type": "string", "enum": ["STEADY", "UNSTEADY"]},
                    "end_time": {"type": "number", "description": "Number of iterations (steady) or physical end time in seconds (unsteady)"},
                    "delta_t": {"type": "number", "description": "Time step size in seconds (unsteady only)"},
                    "n_processors": {"type": "integer", "description": "Number of parallel processors"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_mesh_settings",
            "description": "Set snappyHexMesh refinement levels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "refinement_min": {"type": "integer", "description": "Minimum refinement level (default 5)"},
                    "refinement_max": {"type": "integer", "description": "Maximum refinement level (default 6)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_force_reference",
            "description": "Set reference values for force/moment coefficient calculation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "aref": {"type": "number", "description": "Reference area in m²"},
                    "lref": {"type": "number", "description": "Reference length in m"},
                    "cofr": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Centre of rotation [x, y, z] in m",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_result_summary",
            "description": "Retrieve simulation result summary (Cd, Cl, convergence status).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_mesh_info",
            "description": "Retrieve mesh statistics: total cell count, face count, point count, max/average non-orthogonality, and max skewness from the checkMesh log.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_jobs",
            "description": "Submit simulations for computation. Use submit_all_pending=true to submit all pending cases for this geometry, or specify names to submit specific cases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "submit_all_pending": {
                        "type": "boolean",
                        "description": "If true, submit all PENDING simulations for the same geometry",
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of specific case names to submit",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_simulation",
            "description": "Create a new simulation case for the same geometry. Use this when the user wants to create additional cases with different conditions (e.g., yaw angle sweep). Call once per case.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Case name (e.g., 'p010')"},
                    "yaw_deg": {"type": "number", "description": "Yaw angle in degrees"},
                    "pitch_deg": {"type": "number", "description": "Pitch angle in degrees (default 0)"},
                    "roll_deg": {"type": "number", "description": "Roll angle in degrees (default 0)"},
                    "velocity_mps": {"type": "number", "description": "Wind speed in m/s"},
                    "solver_type": {"type": "string", "enum": ["STEADY", "UNSTEADY"], "description": "Solver type (default STEADY)"},
                },
                "required": ["name", "yaw_deg", "velocity_mps"],
            },
        },
    },
]


def _turbulence_from_intensity(velocity: float, intensity: float, lref: float = 1.0) -> dict:
    """Compute k and omega from turbulence intensity, velocity, and reference length."""
    from backend.foam.case_builder import _turbulence_from_velocity
    k, omega = _turbulence_from_velocity(velocity, intensity, lref)
    return {"turbulent_ke": k, "turbulent_omega": omega}


def _execute_tool(
    tool_name: str, tool_args: dict, current_params: dict, case_dir: Path
) -> tuple[dict, str, dict, list[dict], dict | None]:
    """Apply tool call to current_params dict.
    Returns (updated_params, result_message, angle_updates, sim_creations, job_submission).
    sim_creations: list of dicts for new simulations to create in the DB.
    job_submission: dict describing which simulations to submit, or None.
    """
    params = current_params.copy()

    if tool_name == "set_flow_conditions":
        if "velocity_mps" in tool_args:
            params["velocity_mps"] = tool_args["velocity_mps"]
        if "turbulence_intensity" in tool_args:
            turb = _turbulence_from_intensity(
                params["velocity_mps"], tool_args["turbulence_intensity"]
            )
            params.update(turb)
        result = (
            f"Flow conditions set: velocity={params['velocity_mps']} m/s, "
            f"k={params.get('turbulent_ke')}, ω={params.get('turbulent_omega')}"
        )
        yaw = tool_args.get("yaw_deg")
        pitch = tool_args.get("pitch_deg")
        roll = tool_args.get("roll_deg")
        return params, result, {"yaw_deg": yaw, "pitch_deg": pitch, "roll_deg": roll}, [], None

    if tool_name == "set_solver_settings":
        params.update({k: v for k, v in tool_args.items() if k in ("end_time", "delta_t", "n_processors")})
        if "solver_type" in tool_args:
            params["solver_type"] = tool_args["solver_type"]
        return params, f"Solver settings updated: {tool_args}", {}, [], None

    if tool_name == "set_mesh_settings":
        params.update(tool_args)
        return params, f"Mesh settings updated: {tool_args}", {}, [], None

    if tool_name == "set_force_reference":
        params.update(tool_args)
        return params, f"Force reference updated: {tool_args}", {}, [], None

    if tool_name == "get_result_summary":
        summary_lines = []
        log = next(case_dir.glob("log.*Foam"), None) if case_dir.exists() else None
        if log:
            df = parse_residuals(log)
            if not df.empty:
                last = df.iloc[-1]
                summary_lines.append(f"Final residuals: {last.to_dict()}")
        fc_df = parse_force_coefficients(case_dir)
        if not fc_df.empty:
            last = fc_df.iloc[-1]
            summary_lines.append(f"Final Cd={last.get('Cd', 'N/A'):.4f}, Cl={last.get('Cl', 'N/A'):.4f}")
        result = "\n".join(summary_lines) if summary_lines else "No results available yet."
        return params, result, {}, [], None

    if tool_name == "get_mesh_info":
        info = parse_mesh_info(case_dir) if case_dir.exists() else {}
        if not info:
            return params, "Mesh info not available (log.checkMesh not found).", {}, [], None
        lines = []
        if "cells" in info:
            lines.append(f"Total cells: {info['cells']:,}")
        if "faces" in info:
            lines.append(f"Total faces: {info['faces']:,}")
        if "points" in info:
            lines.append(f"Total points: {info['points']:,}")
        if "max_non_ortho" in info:
            lines.append(f"Non-orthogonality: max={info['max_non_ortho']:.1f}°, avg={info['avg_non_ortho']:.1f}°")
        if "max_skewness" in info:
            lines.append(f"Max skewness: {info['max_skewness']:.2f}")
        return params, "\n".join(lines), {}, [], None

    if tool_name == "create_simulation":
        sc = {
            "name": tool_args.get("name", "new_case"),
            "yaw_deg": float(tool_args.get("yaw_deg", 0)),
            "pitch_deg": float(tool_args.get("pitch_deg", 0)),
            "roll_deg": float(tool_args.get("roll_deg", 0)),
            "velocity_mps": float(tool_args.get("velocity_mps", 20)),
            "solver_type": tool_args.get("solver_type", "STEADY"),
        }
        return params, f"Queued creation: {sc['name']} (yaw={sc['yaw_deg']}°)", {}, [sc], None

    if tool_name == "submit_jobs":
        js: dict = {}
        if tool_args.get("submit_all_pending"):
            js["submit_all_pending"] = True
        if tool_args.get("names"):
            js["names"] = tool_args["names"]
        return params, f"Queued job submission: {js}", {}, [], js

    return current_params, f"Unknown tool: {tool_name}", {}, [], None


async def chat(
    messages: list[dict],
    current_params: dict,
    case_dir: Path = Path("/dev/null"),
) -> tuple[str, dict, dict, list[dict], dict]:
    """Run one chat turn.
    Returns (assistant_reply, updated_params, angle_updates, sim_creations, job_submission).
    """
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    response = await acompletion(
        model=f"openai/{settings.LLM_MODEL}",
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        messages=full_messages,
        tools=TOOLS,
        tool_choice="auto",
        temperature=0.2,
    )

    message = response.choices[0].message
    angle_updates: dict = {}
    sim_creations: list[dict] = []
    job_submission: dict = {}

    # Process tool calls if any
    if message.tool_calls:
        params = current_params.copy()
        tool_results = []
        for tc in message.tool_calls:
            tool_args = json.loads(tc.function.arguments)
            params, result_msg, angles, creations, js = _execute_tool(
                tc.function.name, tool_args, params, case_dir
            )
            angle_updates.update({k: v for k, v in angles.items() if v is not None})
            sim_creations.extend(creations)
            if js:
                job_submission.update(js)
            tool_results.append({"tool_call_id": tc.id, "role": "tool", "content": result_msg})

        # Second pass: get final assistant reply after tool execution
        follow_up = await acompletion(
            model=f"openai/{settings.LLM_MODEL}",
            api_base=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            messages=full_messages + [message] + tool_results,
            temperature=0.2,
        )
        reply = follow_up.choices[0].message.content or ""
        return reply, params, angle_updates, sim_creations, job_submission

    return message.content or "", current_params, angle_updates, sim_creations, job_submission
