"""Local subprocess runner for development/testing.

Requires FOAM_LOCAL_APP to be set in .env to the OpenFOAM app path.
Leave CLUSTER_USER empty to activate this runner; set CLUSTER_USER to use the cluster instead.
"""
import subprocess
import threading
from pathlib import Path

from backend.cluster.base import JobRunner
from backend.core.config import settings


def _foam_cmd() -> str:
    """Return the OpenFOAM launch command path, or raise if not available."""
    app = settings.FOAM_LOCAL_APP
    cmd = str(Path(app) / "Contents" / "Resources" / "etc" / "openfoam")
    if not Path(cmd).exists():
        raise RuntimeError(
            f"Local OpenFOAM not found at {cmd}. "
            "Set FOAM_LOCAL_APP in .env to your OpenFOAM app path."
        )
    return cmd


def local_foam_available() -> bool:
    """Return True if local OpenFOAM is installed and runnable."""
    try:
        _foam_cmd()
        return True
    except RuntimeError:
        return False

# Map job_id → status dict
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_counter = 0


def _next_id() -> str:
    global _counter
    with _lock:
        _counter += 1
        return f"local-{_counter}"


def _run_allrun(case_dir: Path, job_id: str) -> None:
    log_path = case_dir / "log.job"
    with _lock:
        _jobs[job_id]["status"] = "RUNNING"

    cmd = [_foam_cmd(), "bash", "./Allrun"]

    with open(log_path, "w") as log:
        result = subprocess.run(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=case_dir,
        )

    with _lock:
        _jobs[job_id]["status"] = "DONE" if result.returncode == 0 else "FAILED"


class LocalRunner(JobRunner):
    def submit(self, case_dir: Path, n_processors: int, job_name: str,
               seed_processors_from: Path | None = None,
               depend_on_job_id: str | None = None,
               seed_from_in_script: Path | None = None) -> str:
        # The local runner has no scheduler dependency support, so reserved
        # (scheduled) restarts are not offered for it; seed immediately instead.
        if seed_from_in_script is not None and seed_processors_from is None:
            seed_processors_from = seed_from_in_script
        _foam_cmd()  # raises immediately if OpenFOAM not installed

        # Seed the decomposed solution from the parent case (local copy) so a
        # restart child continues from the parent's converged solution.
        if seed_processors_from is not None:
            import shutil
            for proc in sorted(Path(seed_processors_from).glob("processor*")):
                if proc.is_dir() and proc.name[len("processor"):].isdigit():
                    dest = case_dir / proc.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(proc, dest, symlinks=True)

        job_id = _next_id()
        with _lock:
            _jobs[job_id] = {"status": "PENDING", "case_dir": str(case_dir)}

        thread = threading.Thread(
            target=_run_allrun,
            args=(case_dir, job_id),
            daemon=True,
        )
        thread.start()
        return job_id

    def status(self, job_id: str) -> str:
        with _lock:
            return _jobs.get(job_id, {}).get("status", "FAILED")

    def cancel(self, job_id: str) -> None:
        with _lock:
            if job_id in _jobs:
                _jobs[job_id]["status"] = "FAILED"
