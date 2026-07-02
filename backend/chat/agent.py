"""LLM chat agent with tool use via LiteLLM.

LM Studio (local) is used by default. Switch to Claude/OpenAI by changing
LLM_BASE_URL and LLM_MODEL in config or .env.
"""
import json
from pathlib import Path
from typing import AsyncIterator

from litellm import acompletion

from backend.core.config import settings, get_llm_model, get_llm_base_url, get_llm_api_key
from backend.visualization.parsers import (
    parse_force_coefficients, parse_mesh_info, parse_residuals,
    parse_clock_time, parse_peak_memory,
    parse_solver_diagnostics, parse_mesh_diagnostics,
)

_BASE_SYSTEM_PROMPT = """\
You are a CFD simulation assistant for the ChatWindTunnel system.
Your role is to help users configure wind tunnel simulations using OpenFOAM v2206.
When the user specifies simulation conditions in natural language, call the appropriate tools to set parameters.
When the user asks to create multiple cases (e.g., sweep yaw angles), call create_simulation once per case.
When results are available, explain Cd (drag coefficient), Cl (lift coefficient), and convergence behavior clearly.
Always confirm parameter changes with the user before finalizing.
Respond in the same language as the user.

TOOL-USE RULES (mandatory):
- delete cases → MUST call delete_simulations tool. Example: "delete case_10" → delete_simulations(names=["case_10"])
- submit jobs  → MUST call submit_jobs tool. Example: "submit all" → submit_jobs(submit_all_pending=true)
- create cases → MUST call create_simulation (once per case)
- NEVER claim an action succeeded without first calling the corresponding tool.
"""


def _build_system_prompt(case_dir: Path) -> str:
    """Append mesh and solver diagnostics to system prompt when logs are available."""
    sections: list[str] = [_BASE_SYSTEM_PROMPT]

    mesh_diag = parse_mesh_diagnostics(case_dir) if case_dir and case_dir.exists() else {}
    if mesh_diag.get("summary"):
        sections.append("MESH DIAGNOSTICS (from log.checkMesh):\n" + mesh_diag["summary"])

    solver_diag = parse_solver_diagnostics(case_dir) if case_dir and case_dir.exists() else {}
    if solver_diag.get("summary"):
        sections.append("SOLVER DIAGNOSTICS (from solver log):\n" + solver_diag["summary"])

    if mesh_diag or solver_diag:
        sections.append(
            "Use the above diagnostics to answer questions about why the simulation diverged or "
            "whether the mesh or initial conditions are problematic. "
            "Reference specific values (e.g., 'bounding omega at Time=1, min=-14156') when relevant."
        )

    return "\n\n".join(sections)

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
                    "nu": {"type": "number", "description": "Kinematic viscosity in m²/s (default 1.5e-5 for air at 15°C)"},
                },
                "required": ["velocity_mps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_solver_settings",
            "description": "Set solver type, turbulence model, and time control parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "solver_type": {"type": "string", "enum": ["STEADY", "UNSTEADY"]},
                    "turbulence_model": {
                        "type": "string",
                        "enum": ["kOmegaSST", "SpalartAllmaras", "realizableKE", "laminar"],
                        "description": "Turbulence model (default kOmegaSST). Use 'laminar' for low-Re laminar flow (no turbulence equations).",
                    },
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
            "name": "compare_cases",
            "description": (
                "Return a summary table of all simulation cases for the same geometry. "
                "Each row contains: name, status, yaw_deg, pitch_deg, Cx, Cy, Cz, CmYaw, "
                "mesh_cells, clock_time_s, peak_memory_kb. "
                "Use this to answer questions like: which case has the highest Cx, "
                "which took the longest, which used the most memory, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "enum": ["all", "done", "running", "pending", "failed"],
                        "description": "Filter cases by status (default: all)",
                    }
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
    {
        "type": "function",
        "function": {
            "name": "delete_simulations",
            "description": "Delete simulation cases by name. Only PENDING or FAILED cases can be deleted (not RUNNING or MESHING). Use names list to specify which cases to delete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of case names to delete",
                    },
                    "delete_all_pending": {
                        "type": "boolean",
                        "description": "If true, delete all PENDING cases for this geometry",
                    },
                },
                "required": [],
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
    tool_name: str, tool_args: dict, current_params: dict, case_dir: Path,
    all_cases: list[dict] | None = None,
) -> tuple[dict, str, dict, list[dict], dict | None, dict | None]:
    """Apply tool call to current_params dict.
    Returns (updated_params, result_message, angle_updates, sim_creations, job_submission, sim_deletions).
    sim_deletions: dict with 'names' and/or 'delete_all_pending', or None.
    """
    params = current_params.copy()

    if tool_name == "set_flow_conditions":
        if "velocity_mps" in tool_args:
            params["velocity_mps"] = tool_args["velocity_mps"]
        if "nu" in tool_args:
            params["nu"] = tool_args["nu"]
        if "turbulence_intensity" in tool_args:
            turb = _turbulence_from_intensity(
                params["velocity_mps"], tool_args["turbulence_intensity"]
            )
            params.update(turb)
        result = (
            f"Flow conditions set: velocity={params['velocity_mps']} m/s, "
            f"nu={params.get('nu', 1.5e-5):.3g} m²/s, "
            f"k={params.get('turbulent_ke')}, ω={params.get('turbulent_omega')}"
        )
        yaw = tool_args.get("yaw_deg")
        pitch = tool_args.get("pitch_deg")
        roll = tool_args.get("roll_deg")
        return params, result, {"yaw_deg": yaw, "pitch_deg": pitch, "roll_deg": roll}, [], None, None

    if tool_name == "set_solver_settings":
        params.update({k: v for k, v in tool_args.items() if k in ("end_time", "delta_t", "n_processors")})
        if "solver_type" in tool_args:
            params["solver_type"] = tool_args["solver_type"]
        if "turbulence_model" in tool_args:
            params["turbulence_model"] = tool_args["turbulence_model"]
        return params, f"Solver settings updated: {tool_args}", {}, [], None, None

    if tool_name == "set_mesh_settings":
        params.update(tool_args)
        return params, f"Mesh settings updated: {tool_args}", {}, [], None, None

    if tool_name == "set_force_reference":
        params.update(tool_args)
        return params, f"Force reference updated: {tool_args}", {}, [], None, None

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
        return params, result, {}, [], None, None

    if tool_name == "get_mesh_info":
        info = parse_mesh_info(case_dir) if case_dir.exists() else {}
        if not info:
            return params, "Mesh info not available (log.checkMesh not found).", {}, [], None, None
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
        return params, "\n".join(lines), {}, [], None, None

    if tool_name == "compare_cases":
        cases = all_cases or []
        status_filter = tool_args.get("status_filter", "all")
        if status_filter != "all":
            cases = [c for c in cases if c.get("status") == status_filter]
        if not cases:
            return params, "No cases found.", {}, [], None, None
        import json as _json
        return params, _json.dumps(cases, ensure_ascii=False), {}, [], None, None

    if tool_name == "create_simulation":
        sc = {
            "name": tool_args.get("name", "new_case"),
            "yaw_deg": float(tool_args.get("yaw_deg", 0)),
            "pitch_deg": float(tool_args.get("pitch_deg", 0)),
            "roll_deg": float(tool_args.get("roll_deg", 0)),
            "velocity_mps": float(tool_args.get("velocity_mps", 20)),
            "solver_type": tool_args.get("solver_type", "STEADY"),
        }
        return params, f"Queued creation: {sc['name']} (yaw={sc['yaw_deg']}°)", {}, [sc], None, None

    if tool_name == "submit_jobs":
        js: dict = {}
        if tool_args.get("submit_all_pending"):
            js["submit_all_pending"] = True
        if tool_args.get("names"):
            js["names"] = tool_args["names"]
        return params, f"Queued job submission: {js}", {}, [], js, None

    if tool_name == "delete_simulations":
        sd: dict = {}
        if tool_args.get("names"):
            sd["names"] = tool_args["names"]
        if tool_args.get("delete_all_pending"):
            sd["delete_all_pending"] = True
        return params, f"Queued deletion: {sd}", {}, [], None, sd

    return current_params, f"Unknown tool: {tool_name}", {}, [], None, None


async def chat(
    messages: list[dict],
    current_params: dict,
    case_dir: Path = Path("/dev/null"),
    all_cases: list[dict] | None = None,
) -> tuple[str, dict, dict, list[dict], dict, dict]:
    """Run one chat turn.
    Returns (assistant_reply, updated_params, angle_updates, sim_creations, job_submission, sim_deletions).
    """
    full_messages = [{"role": "system", "content": _build_system_prompt(case_dir)}] + messages

    response = await acompletion(
        model=f"openai/{get_llm_model()}",
        api_base=get_llm_base_url(),
        api_key=get_llm_api_key(),
        messages=full_messages,
        tools=TOOLS,
        tool_choice="auto",
        temperature=0.2,
    )

    message = response.choices[0].message
    angle_updates: dict = {}
    sim_creations: list[dict] = []
    job_submission: dict = {}
    sim_deletions: dict = {}

    # Process tool calls if any
    if message.tool_calls:
        params = current_params.copy()
        tool_results = []
        for tc in message.tool_calls:
            tool_args = json.loads(tc.function.arguments)
            params, result_msg, angles, creations, js, sd = _execute_tool(
                tc.function.name, tool_args, params, case_dir, all_cases=all_cases
            )
            angle_updates.update({k: v for k, v in angles.items() if v is not None})
            sim_creations.extend(creations)
            if js:
                job_submission.update(js)
            if sd:
                sim_deletions.update(sd)
            tool_results.append({"tool_call_id": tc.id, "role": "tool", "content": result_msg})

        # Second pass: get final assistant reply after tool execution
        follow_up = await acompletion(
            model=f"openai/{get_llm_model()}",
            api_base=get_llm_base_url(),
            api_key=get_llm_api_key(),
            messages=full_messages + [message] + tool_results,
            temperature=0.2,
        )
        reply = follow_up.choices[0].message.content or ""
        return reply, params, angle_updates, sim_creations, job_submission, sim_deletions

    return message.content or "", current_params, angle_updates, sim_creations, job_submission, sim_deletions
