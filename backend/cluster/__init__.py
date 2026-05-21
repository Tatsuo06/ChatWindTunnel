from backend.cluster.base import JobRunner
from backend.cluster.local_runner import LocalRunner
from backend.cluster.cluster_runner import ClusterRunner
from backend.core.config import settings


def get_runner() -> JobRunner:
    """Return the appropriate runner based on CLUSTER_HOST setting."""
    if settings.CLUSTER_HOST and settings.CLUSTER_USER:
        return ClusterRunner()
    return LocalRunner()
