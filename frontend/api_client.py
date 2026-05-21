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


def rename_project(project_id: int, name: str) -> dict | None:
    r = _patch(f"/projects/{project_id}", json={"name": name})
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


def rename_geometry(geo_id: int, name: str) -> dict | None:
    r = _patch(f"/geometries/{geo_id}", json={"name": name})
    return r.json() if r.ok else None


def delete_geometry(geo_id: int) -> bool:
    return _delete(f"/geometries/{geo_id}").status_code == 204


def upload_cad(geo_id: int, file_bytes: bytes, filename: str) -> dict | None:
    r = _post(f"/geometries/{geo_id}/upload-cad", files={"file": (filename, file_bytes)})
    return r.json() if r.ok else None


# --- Simulations ---
def get_status_summary() -> dict:
    r = _get("/simulations/status-summary")
    return r.json() if r.ok else {}


def list_simulations(geo_id: int) -> list[dict]:
    r = _get(f"/simulations/geometry/{geo_id}")
    return r.json() if r.ok else []


def create_simulation(geo_id: int, name: str, solver_type: str) -> dict | None:
    r = _post("/simulations", json={"geometry_id": geo_id, "name": name, "solver_type": solver_type})
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
def submit_job(sim_id: int) -> dict | None:
    r = _post(f"/simulations/{sim_id}/job/submit")
    return r.json() if r.ok else None


def poll_status(sim_id: int) -> dict | None:
    r = _get(f"/simulations/{sim_id}/job/status")
    return r.json() if r.ok else None


def cancel_job(sim_id: int) -> bool:
    return _post(f"/simulations/{sim_id}/job/cancel").status_code == 204


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


# --- Results (PNG bytes) ---
def get_geometry_preview(sim_id: int) -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/geometry")
    return r.content if r.ok else None


def get_residuals_plot(sim_id: int) -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/residuals")
    return r.content if r.ok else None


def get_force_coefficients_plot(sim_id: int) -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/force-coefficients")
    return r.content if r.ok else None


def get_cutting_plane_plot(sim_id: int, field: str = "p") -> bytes | None:
    r = _get(f"/simulations/{sim_id}/results/cutting-plane", params={"field": field})
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
