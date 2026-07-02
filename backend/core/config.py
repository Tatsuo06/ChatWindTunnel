from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://chatwt:chatwt@localhost:5432/chatwt"

    # JWT
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8

    # LLM (LiteLLM)
    LLM_BASE_URL: str = "http://localhost:1234/v1"
    LLM_MODEL: str = "google/gemma-4-e4b"
    LLM_API_KEY: str = "lm-studio"  # LM Studio requires a dummy key

    # File paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    CASES_DIR: Path = DATA_DIR / "cases"
    RESULTS_DIR: Path = DATA_DIR / "results"
    TEMPLATES_DIR: Path = BASE_DIR / "foam_templates"

    # OpenFOAM
    FOAM_LOCAL_APP: str = "/Applications/OpenFOAM-v2206.app"
    # Set false in production to hide/reject the local runner (cluster only)
    ALLOW_LOCAL_RUNNER: bool = True

    # Cluster
    CLUSTER_HOST: str = ""
    CLUSTER_USER: str = ""
    CLUSTER_SSH_KEY: str = "~/.ssh/id_rsa"
    CLUSTER_WORK_DIR: str = "/home/{user}/chatwt_cases"
    CLUSTER_FOAM_MODULE: str = "/path/to/OpenFOAM-v2206/etc/bashrc"
    CLUSTER_N_PROCESSORS: int = 16
    TRANSPORT_MODE: str = "nfs"  # "nfs" | "scp"

    # Frontend
    BACKEND_URL: str = "http://localhost:8000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

# Runtime overrides (admin UI can update these without restarting the server)
_runtime: dict = {}


def get_llm_model() -> str:
    return _runtime.get("LLM_MODEL") or settings.LLM_MODEL


def get_llm_base_url() -> str:
    return _runtime.get("LLM_BASE_URL") or settings.LLM_BASE_URL


def get_llm_api_key() -> str:
    return _runtime.get("LLM_API_KEY") or settings.LLM_API_KEY


def set_llm_model(model: str) -> None:
    _runtime["LLM_MODEL"] = model
