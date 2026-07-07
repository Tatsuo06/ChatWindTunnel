"""Thin HTTP client wrapping the FastAPI backend."""
import requests
import streamlit as st

from backend.core.config import settings

BASE_URL = settings.BACKEND_URL


def _headers() -> dict:
    token = st.session_state.get("token", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get(path: str, **kwargs):
    return requests.get(f"{BASE_URL}{path}", headers=_headers(), **kwargs)


def _post(path: str, **kwargs):
    return requests.post(f"{BASE_URL}{path}", headers=_headers(), **kwargs)


def _patch(path: str, **kwargs):
    return requests.patch(f"{BASE_URL}{path}", headers=_headers(), **kwargs)


def _delete(path: str, **kwargs):
    return requests.delete(f"{BASE_URL}{path}", headers=_headers(), **kwargs)


# --- Auth ---
def login(username: str, password: str) -> str | None:
    r = requests.post(f"{BASE_URL}/auth/login", json={"username": username, "password": password})
    return r.json()["access_token"] if r.ok else None


def get_me() -> dict | None:
    r = _get("/auth/me")
    return r.json() if r.ok else None


def list_users() -> list[dict]:
    r = _get("/auth/users")
    return r.json() if r.ok else []


def create_user(username: str, password: str, role: str) -> dict | None:
    r = _post("/auth/users", json={"username": username, "password": password, "role": role})
    return r.json() if r.ok else None


def delete_user(user_id: int) -> bool:
    return _delete(f"/auth/users/{user_id}").status_code == 204


def change_password(current_password: str, new_password: str) -> bool:
    r = _post("/auth/me/password", json={"current_password": current_password, "new_password": new_password})
    return r.status_code == 204


# --- Projects ---
def list_projects() -> list[dict]:
    r = _get("/projects")
    return r.json() if r.ok else []


def create_project(name: str, description: str) -> dict | None:
    r = _post("/projects", json={"name": name, "description": description})
    return r.json() if r.ok else None


def rename_project(project_id: int, name: str, description: str | None = None) -> dict | None:
    body = {"name": name}
    if description is not None:
        body["description"] = description
    r = _patch(f"/projects/{project_id}", json=body)
    return r.json() if r.ok else None


def delete_project(project_id: int) -> bool:
    return _delete(f"/projects/{project_id}").status_code == 204


def get_cd_cl_summary(project_id: int) -> list[dict]:
    r = _get(f"/projects/{project_id}/results/cd-cl")
    return r.json() if r.ok else []


# --- Geometries ---
def list_geometries(project_id: int) -> list[dict]:
    r = _get(f"/projects/{project_id}/geometries")
    return r.json() if r.ok else []


def create_geometry(project_id: int, name: str) -> dict | None:
    r = _post(f"/projects/{project_id}/geometries", json={"name": name})
    return r.json() if r.ok else None


def get_geometry(geo_id: int) -> dict | None:
    r = _get(f"/geometries/{geo_id}")
    return r.json() if r.ok else None


def get_geometry_bbox(geo_id: int) -> dict | None:
    r = _get(f"/geometries/{geo_id}/bbox")
    return r.json() if r.ok else None


def download_cad(geo_id: int) -> tuple[bytes, str] | None:
    r = _get(f"/geometries/{geo_id}/download-cad")
    if not r.ok:
        return None
    cd = r.headers.get("content-disposition", "")
    filename = cd.split("filename=")[-1].strip('"') if "filename=" in cd else "model"
    return r.content, filename


def rename_geometry(geo_id: int, name: str) -> dict | None:
    r = _patch(f"/geometries/{geo_id}", json={"name": name})
    return r.json() if r.ok else None


def delete_geometry(geo_id: int) -> bool:
    return _delete(f"/geometries/{geo_id}").status_code == 204


def upload_cad(geo_id: int, file_bytes: bytes, filename: str, scale: float = 1.0,
               yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0) -> dict | None:
    params = {"scale": scale, "yaw": yaw, "pitch": pitch, "roll": roll}
    r = _post(f"/geometries/{geo_id}/upload-cad", files={"file": (filename, file_bytes)}, params=params)
    return r.json() if r.ok else None


def scale_geometry(geo_id: int, factor: float) -> dict | None:
    r = _post(f"/geometries/{geo_id}/scale", params={"factor": factor})
    return r.json() if r.ok else None


# --- Simulations ---
def get_status_summary() -> dict:
    r = _get("/simulations/status-summary")
    return r.json() if r.ok else {}


def get_active_jobs() -> list[dict]:
    r = _get("/simulations/active-jobs")
    return r.json() if r.ok else []


def sync_one_job(sim_id: int) -> dict:
    r = _post(f"/simulations/{sim_id}/sync-one")
    return r.json() if r.ok else {"sim_id": sim_id, "result": "error", "reason": f"HTTP {r.status_code}"}


def list_simulations(geo_id: int) -> list[dict]:
    r = _get(f"/simulations/geometry/{geo_id}")
    return r.json() if r.ok else []


def create_simulation(geo_id: int, name: str, solver_type: str,
                      parameters: dict | None = None) -> dict | None:
    body = {"geometry_id": geo_id, "name": name, "solver_type": solver_type}
    if parameters:
        body["parameters"] = parameters
    r = _post("/simulations", json=body)
    return r.json() if r.ok else None


def get_simulation(sim_id: int) -> dict | None:
    r = _get(f"/simulations/{sim_id}")
    return r.json() if r.ok else None


def update_simulation(sim_id: int, **kwargs) -> dict | None:
    r = _patch(f"/simulations/{sim_id}", json=kwargs)
    return r.json() if r.ok else None


def delete_simulation(sim_id: int) -> bool:
    return _delete(f"/simulations/{sim_id}").status_code == 204


# --- Jobs ---
def sync_cluster_jobs() -> dict | None:
    r = _post("/simulations/sync-cluster")
    return r.json() if r.ok else None


def get_runner_info() -> dict:
    r = _get("/simulations/runner-info")
    return r.json() if r and r.ok else {"cluster_available": False, "local_available": False}


def submit_job(sim_id: int, runner_type: str = "auto") -> dict | None:
    r = _post(f"/simulations/{sim_id}/job/submit", json={"runner_type": runner_type})
    return r.json() if r.ok else None


def poll_status(sim_id: int) -> dict | None:
    r = _get(f"/simulations/{sim_id}/job/status")
    return r.json() if r.ok else None


def cancel_job(sim_id: int) -> bool:
    return _post(f"/simulations/{sim_id}/job/cancel").status_code == 204


def restart_job(sim_id: int, new_end_time: int) -> dict | None:
    r = _post(f"/simulations/{sim_id}/job/restart",
              json={"mode": "steady", "new_end_time": new_end_time})
    return r.json() if r.ok else None


def restart_job_les(sim_id: int, les_end_time: float, les_delta_t: float,
                    les_anim_interval: int, les_model: str) -> dict | None:
    r = _post(f"/simulations/{sim_id}/job/restart", json={
        "mode": "unsteady",
        "les_end_time": les_end_time,
        "les_delta_t": les_delta_t,
        "les_anim_interval": les_anim_interval,
        "les_model": les_model,
    })
    return r.json() if r.ok else None


def get_job_progress(sim_id: int) -> dict | None:
    r = _get(f"/simulations/{sim_id}/job/progress")
    return r.json() if r.ok else None


# --- Chat ---
def send_chat(sim_id: int, message: str) -> dict | None:
    r = _post(f"/simulations/{sim_id}/chat", json={"message": message})
    return r.json() if r.ok else None


def get_chat_history(sim_id: int) -> list[dict]:
    r = _get(f"/simulations/{sim_id}/chat")
    return r.json() if r.ok else []


def send_project_chat(project_id: int, message: str, history: list[dict]) -> dict | None:
    r = _post(f"/projects/{project_id}/chat", json={"message": message, "history": history})
    if r.ok:
        return r.json()
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = ""
    return {"error": detail or f"HTTP {r.status_code}"}


# --- Admin: LLM settings ---
def get_llm_settings() -> dict | None:
    r = _get("/admin/llm-settings")
    return r.json() if r.ok else None


def get_llm_models() -> list[str]:
    r = _get("/admin/llm-models")
    return r.json() if r.ok else []


def update_llm_model(model: str) -> dict | None:
    r = _patch("/admin/llm-settings", json={"model": model})
    return r.json() if r.ok else None


# --- Results (PNG bytes) ---
def get_geometry_preview(sim_id: int) -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/geometry")
    return r.content if r.ok else None


def get_geometry_3d_data(sim_id: int) -> dict | None:
    r = _get(f"/simulations/{sim_id}/results/geometry-3d-data")
    return r.json() if r.ok else None


def get_geometry_preview_by_geo(geo_id: int) -> bytes | None:
    r = _get(f"/geometries/{geo_id}/preview")
    return r.content if r.ok else None


def get_geometry_domain_params(geo_id: int) -> dict | None:
    r = _get(f"/geometries/{geo_id}/domain-params")
    return r.json() if r.ok else None


def get_residuals_plot(sim_id: int, phase: int | None = None) -> bytes | None:
    params = {"phase": phase} if phase is not None else None
    r = _get(f"/simulations/{sim_id}/results/residuals", params=params)
    return r.content if r.ok else None


def get_force_coefficients_plot(sim_id: int) -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/force-coefficients")
    return r.content if r.ok else None


def get_cutting_plane_plot(sim_id: int, field: str = "p") -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/cutting-plane", params={"field": field})
    return r.content if r.ok else None


def get_cutting_plane_data(sim_id: int, field: str = "p") -> dict | None:
    r = _get(f"/simulations/{sim_id}/results/cutting-plane-data", params={"field": field})
    return r.json() if r.ok else None


def get_animation(sim_id: int, kind: str = "plane", field: str = "U") -> bytes | None:
    # First request renders the video (~1-2 min for a full LES run)
    r = _get(f"/simulations/{sim_id}/results/animation",
             params={"kind": kind, "field": field}, timeout=600)
    return r.content if r.ok else None


def get_mesh_plot(sim_id: int, view: str = "iso") -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/mesh", params={"view": view})
    return r.content if r.ok else None


def get_mesh_stats(sim_id: int) -> dict | None:
    r = _get(f"/simulations/{sim_id}/results/mesh-stats")
    return r.json() if r.ok else None


def get_streamlines_plot(sim_id: int) -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/streamlines")
    return r.content if r.ok else None


def get_streamlines_paths(sim_id: int) -> dict | None:
    r = _get(f"/simulations/{sim_id}/results/streamlines-paths")
    return r.json() if r.ok else None


def get_wall_streamlines_paths(sim_id: int) -> dict | None:
    r = _get(f"/simulations/{sim_id}/results/wall-streamlines-paths")
    return r.json() if r.ok else None
