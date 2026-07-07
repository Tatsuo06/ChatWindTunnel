from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import admin, auth, projects, geometries, simulations, chat, jobs, results
from backend.core.config import settings
from backend.db.models import Base
from backend.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Lightweight migrations: create_all doesn't alter existing tables, so add
    # late-added columns here (errors mean the column already exists)
    from sqlalchemy import text
    for ddl in (
        "ALTER TABLE geometries ADD COLUMN description TEXT DEFAULT ''",
        "ALTER TABLE simulations ADD COLUMN description TEXT DEFAULT ''",
    ):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(ddl))
        except Exception:
            pass
    # Ensure data directories exist
    for d in (settings.UPLOAD_DIR, settings.CASES_DIR, settings.RESULTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="ChatWindTunnel API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],  # Streamlit default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(geometries.router)
app.include_router(simulations.router)
app.include_router(chat.router)
app.include_router(jobs.router)
app.include_router(results.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
