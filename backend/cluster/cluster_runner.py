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
    """Download cluster remote_dir to local_dir via rsync.

    Excluded to reduce transfer size (~1 GB → ~50 MB per case):
    - processor[0-9]*/          : decomposed parallel dirs (not needed after reconstructPar)
    - postProcessing/ensightWrite: large ensight output, not used
    - postProcessing/sets/wallBoundedStreamLines: large; visualization uses postProcessing only
    - constant/polyMesh/        : ~250 MB full mesh; VTK/ from foamToVTK used instead
    - /[0-9]*/                  : reconstructed time directories (~180 MB); VTK/ used instead
                                  (anchored so postProcessing/forceCoeffs1/0/ is preserved)
    """
    key = str(Path(settings.CLUSTER_SSH_KEY).expanduser())
    remote = f"{settings.CLUSTER_USER}@{settings.CLUSTER_HOST}:{remote_dir}/"
    subprocess.run(
        ["rsync", "-az",
         "--exclude=processor[0-9]*",
         "--exclude=postProcessing/ensightWrite",
         "--exclude=postProcessing/sets/wallBoundedStreamLines",
         "--exclude=constant/polyMesh/",
         "--exclude=/[0-9]*/",
         "-e", f"ssh -i {key} -o StrictHostKeyChecking=no -o BatchMode=yes",
         remote, f"{local_dir}/"],
        check=True,
    )
    _cleanup_intermediate_timesteps(local_dir)


_PP_TARGETS = (
    "postProcessing/cuttingPlane",
    "postProcessing/sets/streamLines",
    "postProcessing/sets/streamLinesBack",
    "postProcessing/sets/wallBoundedStreamLines",
)


def _cleanup_intermediate_timesteps(case_dir: Path) -> None:
    """Keep only the latest numeric timestep in postProcessing visualisation dirs."""
    import shutil
    for rel in _PP_TARGETS:
        target = case_dir / rel
        if not target.is_dir():
            continue
        ts_dirs = sorted(
            [d for d in target.iterdir() if d.is_dir() and d.name.replace(".", "").isdigit()],
            key=lambda d: float(d.name),
        )
        for d in ts_dirs[:-1]:
            shutil.rmtree(d)


class ClusterRunner(JobRunner):

    def _remote_dir(self, case_dir: Path) -> str:
        base = settings.CLUSTER_WORK_DIR.format(user=settings.CLUSTER_USER)
        return f"{base}/{case_dir.name}"

    def submit(self, case_dir: Path, n_processors: int, job_name: str,
               seed_processors_from: Path | None = None) -> str:
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

        # 2. Upload case files (rsync excludes processor* — see _rsync_up)
        _rsync_up(case_dir, remote_case_dir)

        # 2b. Seed the decomposed solution from the parent case, remote→remote.
        # Use hard links (cp -al): the processor* dirs are large (GBs) and live
        # on the same NFS export, so hard-linking is near-instant instead of
        # duplicating the data. It is safe because the restart Allruns never
        # modify a seeded file in place — they `cp -r` the latest time dir into
        # a fresh "0", then `rm` (unlink) the other seeded time dirs, and only
        # read the (hard-linked) mesh. Fall back to a real copy if the platform
        # rejects cross-directory hard links.
        if seed_processors_from is not None:
            parent_remote = self._remote_dir(seed_processors_from)
            out, err = _ssh(
                f"cp -al {parent_remote}/processor* {remote_case_dir}/ 2>/dev/null "
                f"|| cp -a {parent_remote}/processor* {remote_case_dir}/; "
                f"[ -d {remote_case_dir}/processor0 ] && echo OK"
            )
            if "OK" not in out:
                raise RuntimeError(
                    f"failed to seed processor dirs from {parent_remote}: {err or out}"
                )

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

    def fetch_live_logs(self, case_dir: Path) -> None:
        """Lightweight mid-run sync: solver logs + force coefficients only.

        Lets the Convergence tab show residual/force histories while a job is
        still running, without pulling the full case (frames, VTK, ...).
        """
        remote = f"{settings.CLUSTER_USER}@{settings.CLUSTER_HOST}:{self._remote_dir(case_dir)}"
        key = str(Path(settings.CLUSTER_SSH_KEY).expanduser())
        ssh_opt = f"ssh -i {key} -o StrictHostKeyChecking=no -o BatchMode=yes"
        # Guard against pathological cases: a stalled gas run can grow its solver
        # log to tens of GB. --max-size skips such files (they can't be displayed
        # live anyway) and the timeouts stop a slow transfer from wedging the
        # backend (rsync --timeout = idle I/O; subprocess timeout = hard cap).
        for args in (
            ["rsync", "-az", "--timeout=30", "--max-size=300m",
             "--include=log.*Foam", "--exclude=*", "-e", ssh_opt,
             f"{remote}/", f"{case_dir}/"],
            ["rsync", "-az", "--timeout=30", "--max-size=300m", "-e", ssh_opt,
             f"{remote}/postProcessing/forceCoeffs1/",
             f"{case_dir / 'postProcessing' / 'forceCoeffs1'}/"],
        ):
            (case_dir / "postProcessing").mkdir(exist_ok=True)
            try:
                subprocess.run(args, check=False, timeout=90)
            except subprocess.TimeoutExpired:
                pass

    def cancel(self, job_id: str) -> None:
        _ssh(f"qdel {job_id}")

    def queue_counts(self) -> dict[str, int]:
        """Return number of Q (queued) and R (running) jobs for this user via qstat."""
        out, _ = _ssh(f"qstat -u {settings.CLUSTER_USER} 2>/dev/null || qstat 2>/dev/null")
        queued = sum(1 for line in out.splitlines() if re.search(r"\s+Q\s+", line))
        running = sum(1 for line in out.splitlines() if re.search(r"\s+R\s+", line))
        return {"queued": queued, "running": running}

    def cluster_load(self) -> dict[str, int]:
        """Cluster-wide load in a single ssh round trip. Jobs are parsed from the
        default `qstat` (all jobs on this shared account) and split by state
        (R running / Q queued) AND by project via the job name: `cwt*` is
        ChatWindTunnel, everything else (`case_*`, …) is ChatTowingTank.

        Nodes come from `pbsnodes -l all`; the usable count excludes both `down`
        and `offline` nodes, so free_nodes / usable_nodes reflects actual
        availability (total_nodes / down_nodes are kept for reference).
        """
        out, _ = _ssh(
            "qstat 2>/dev/null; echo '===NODES==='; pbsnodes -l all 2>/dev/null"
        )
        jobs_part, nodes_part = (out.split("===NODES===", 1) + [""])[:2]

        r_ctt = r_cwt = q_ctt = q_cwt = 0
        for ln in jobs_part.splitlines():
            f = ln.split()
            # a job row: "<id>.<host>  <name>  <user>  <time>  <S>  <queue>"
            if len(f) < 6 or not re.match(r"^\d+\.", f[0]):
                continue
            name, state = f[1], f[-2]
            is_cwt = name.startswith("cwt")
            if state == "R":
                r_cwt += is_cwt; r_ctt += not is_cwt
            elif state == "Q":
                q_cwt += is_cwt; q_ctt += not is_cwt

        node_lines = [ln for ln in nodes_part.splitlines()
                      if ln.strip() and "." in ln.split()[0]]
        free_nodes = sum(1 for ln in node_lines if re.search(r"\bfree\b", ln))
        down_nodes = sum(1 for ln in node_lines
                         if re.search(r"\b(down|offline)\b", ln))
        total_nodes = len(node_lines)
        return {
            "running_cttank": r_ctt, "running_cwt": r_cwt,
            "queued_cttank": q_ctt, "queued_cwt": q_cwt,
            "running_all": r_ctt + r_cwt, "queued_all": q_ctt + q_cwt,
            "free_nodes": free_nodes,
            "usable_nodes": total_nodes - down_nodes,
            "total_nodes": total_nodes, "down_nodes": down_nodes,
        }
