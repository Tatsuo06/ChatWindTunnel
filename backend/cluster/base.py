"""Abstract base for job runners (local subprocess or cluster qsub)."""
from abc import ABC, abstractmethod
from pathlib import Path


class JobRunner(ABC):
    @abstractmethod
    def submit(self, case_dir: Path, n_processors: int, job_name: str,
               seed_processors_from: Path | None = None,
               depend_on_job_id: str | None = None,
               seed_from_in_script: Path | None = None) -> str:
        """Submit job. Returns job_id string.

        If ``seed_processors_from`` is given (a parent case dir), the parent's
        decomposed ``processor*`` directories are copied into this case before
        the solver runs — used to seed a restart child from its parent.

        ``depend_on_job_id`` + ``seed_from_in_script`` reserve a restart child on
        a scheduler that supports job dependencies: the job is held until the
        parent job finishes OK and seeds itself at run time. Runners without
        dependency support may ignore these (scheduling is cluster-only).
        """

    @abstractmethod
    def status(self, job_id: str) -> str:
        """Return status string: PENDING | SCHEDULED | RUNNING | DONE | FAILED."""

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        """Cancel a running job."""

    def fetch_results(self, job_id: str, case_dir: Path) -> None:
        """Download results from remote to local case_dir. No-op for local runner."""
