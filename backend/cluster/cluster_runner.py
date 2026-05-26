"""PBS/Torque cluster runner using system ssh/rsync via subprocess.

Using system OpenSSH avoids paramiko algorithm compatibility issues with
older HPC clusters. SSH key auth must be pre-configured (~/.ssh/id_rsa).

Workflow:
  submit()       : rsync case dir to cluster, write PBS script, qsub
  status()       : ssh qstat -f <job_id>
  fetch_results(): rsync results back (exclude processor*/)
"""
import re
import subprocess
import textwrap
import threading
from pathlib import Path

from backend.cluster.base import JobRunner
from backend.core.config import settings

_jobs: dict[str, dict] = {}
_lock = threading.Lock()

PBS_STATUS_MAP = {
    "Q": "PENDING",
    "R": "RUNNING",
    "C": "DONE",
    "E": "RUNNING",
    "H": "PENDING",
    "F": "DONE",
}

JOB_SCRIPT_TEMPLATE = textwrap.dedent("""\
    #!/bin/bash
    #PBS -l nodes=1:ppn={n_processors}
    #PBS -o {log_path}
    #PBS -e {log_path}.err
    #PBS -N {job_name}
    cd {case_dir} || exit 1
    source {foam_module}
    bash ./Allrun
""")


def _ssh(cmd: str) -> tuple[str, str]:
    """Run a command on the cluster via ssh. Returns (stdout, stderr)."""
    key = str(Path(settings.CLUSTER_SSH_KEY).expanduser())
    result = subprocess.run(
        ["ssh", "-i", key, "-o", "StrictHostKeyChecking=no",
         "-o", "BatchMode=yes",
         f"{settings.CLUSTER_USER}@{settings.CLUSTER_HOST}", cmd],
        capture_output=True, text=True,
    )
    return result.stdout.strip(), result.stderr.strip()


def _rsync_up(local_dir: Path, remote_dir: str) -> None:
    """Upload local_dir to cluster remote_dir via rsync, skipping processor*/."""
    key = str(Path(settings.CLUSTER_SSH_KEY).expanduser())
    remote = f"{settings.CLUSTER_USER}@{settings.CLUSTER_HOST}:{remote_dir}/"
    subprocess.run(
        ["rsync", "-az", "--delete",
         "--exclude=processor[0-9]*",
         "-e", f"ssh -i {key} -o StrictHostKeyChecking=no -o BatchMode=yes",
         f"{local_dir}/", remote],
        check=True,
    )


def _rsync_down(remote_dir: str, local_dir: Path) -> None:
    """Download cluster remote_dir to local_dir via rsync, skipping processor*/ and ensightWrite/."""
    key = str(Path(settings.CLUSTER_SSH_KEY).expanduser())
    remote = f"{settings.CLUSTER_USER}@{settings.CLUSTER_HOST}:{remote_dir}/"
    subprocess.run(
        ["rsync", "-az",
         "--exclude=processor[0-9]*",
         "--exclude=postProcessing/ensightWrite",
         "-e", f"ssh -i {key} -o StrictHostKeyChecking=no -o BatchMode=yes",
         remote, f"{local_dir}/"],
        check=True,
    )


class ClusterRunner(JobRunner):

    def _remote_dir(self, case_dir: Path) -> str:
        base = settings.CLUSTER_WORK_DIR.format(user=settings.CLUSTER_USER)
        return f"{base}/{case_dir.name}"

    def submit(self, case_dir: Path, n_processors: int, job_name: str) -> str:
        remote_case_dir = self._remote_dir(case_dir)
        log_path = f"{remote_case_dir}/log.job"
        pbs_path = f"{remote_case_dir}/run.pbs"

        script = JOB_SCRIPT_TEMPLATE.format(
            n_processors=n_processors,
            log_path=log_path,
            job_name=job_name,
            case_dir=remote_case_dir,
            foam_module=settings.CLUSTER_FOAM_MODULE,
        )

        # 1. Create remote directory
        _ssh(f"mkdir -p {remote_case_dir}")

        # 2. Upload case files
        _rsync_up(case_dir, remote_case_dir)

        # 3. Write PBS script and submit
        # Use printf to avoid heredoc quoting issues
        escaped = script.replace("'", "'\\''")
        out, err = _ssh(
            f"printf '%s' '{escaped}' > {pbs_path} && chmod +x {pbs_path} && qsub {pbs_path}"
        )
        if not out:
            raise RuntimeError(f"qsub returned no job ID. stderr: {err}")
        job_id = out.split(".")[0]

        with _lock:
            _jobs[job_id] = {
                "case_dir": str(case_dir),
                "remote_case_dir": remote_case_dir,
                "downloaded": False,
            }
        return job_id

    def status(self, job_id: str) -> str:
        out, _ = _ssh(f"qstat -f {job_id} 2>&1")
        match = re.search(r"job_state\s*=\s*(\w)", out)
        if match:
            return PBS_STATUS_MAP.get(match.group(1), "RUNNING")
        if any(kw in out for kw in
               ("Unknown Job Id", "does not exist", "No such job")):
            return "DONE"
        return "RUNNING"

    def fetch_results(self, job_id: str, case_dir: Path) -> None:
        """Rsync results from cluster back to local case_dir."""
        with _lock:
            info = _jobs.get(job_id, {})
            if info.get("downloaded"):
                return
            remote_case_dir = info.get("remote_case_dir") or self._remote_dir(case_dir)

        _rsync_down(remote_case_dir, case_dir)

        with _lock:
            if job_id in _jobs:
                _jobs[job_id]["downloaded"] = True

    def cancel(self, job_id: str) -> None:
        _ssh(f"qdel {job_id}")

    def queue_counts(self) -> dict[str, int]:
        """Return number of Q (queued) and R (running) jobs for this user via qstat."""
        out, _ = _ssh(f"qstat -u {settings.CLUSTER_USER} 2>/dev/null || qstat 2>/dev/null")
        queued = sum(1 for line in out.splitlines() if re.search(r"\s+Q\s+", line))
        running = sum(1 for line in out.splitlines() if re.search(r"\s+R\s+", line))
        return {"queued": queued, "running": running}
