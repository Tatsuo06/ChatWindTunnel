"""Admin-only endpoints: LLM settings, available models."""
import httpx

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.api.deps import AdminUser
from backend.core.config import settings, get_llm_model, get_llm_base_url, set_llm_model

router = APIRouter(prefix="/admin", tags=["admin"])


class LLMSettings(BaseModel):
    model: str
    base_url: str


class LLMSettingsUpdate(BaseModel):
    model: str


@router.get("/llm-settings", response_model=LLMSettings)
async def get_llm_settings(_: AdminUser):
    return LLMSettings(model=get_llm_model(), base_url=get_llm_base_url())


@router.patch("/llm-settings", response_model=LLMSettings)
async def update_llm_settings(body: LLMSettingsUpdate, _: AdminUser):
    set_llm_model(body.model)
    return LLMSettings(model=get_llm_model(), base_url=get_llm_base_url())


@router.get("/llm-models")
async def list_llm_models(_: AdminUser):
    """Fetch available models from the LM Studio /v1/models endpoint."""
    base_url = get_llm_base_url().rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base_url}/models")
        r.raise_for_status()
        data = r.json()
        return [m["id"] for m in data.get("data", [])]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach LLM server: {e}",
        )
