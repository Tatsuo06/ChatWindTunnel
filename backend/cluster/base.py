"""Abstract base for job runners (local subprocess or cluster qsub)."""
from abc import ABC, abstractmethod
from pathlib import Path


class JobRunner(ABC):
    @abstractmethod
    def submit(self, case_dir: Path, n_processors: int, job_name: str) -> str:
        """Submit job. Returns job_id string."""

    @abstractmethod
    def status(self, job_id: str) -> str:
        """Return status string: PENDING | RUNNING | DONE | FAILED."""

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        """Cancel a running job."""

    def fetch_results(self, job_id: str, case_dir: Path) -> None:
        """Download results from remote to local case_dir. No-op for local runner."""
