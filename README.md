# ChatWindTunnel

A chat-based wind tunnel simulation system powered by OpenFOAM v2206.  
Upload a CAD file, configure the simulation through natural language chat, submit to a PBS cluster or local runner, and explore results interactively.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![OpenFOAM](https://img.shields.io/badge/OpenFOAM-v2206-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-orange)
![Streamlit](https://img.shields.io/badge/Streamlit-1.35+-red)

---

## Features

- **Chat-driven setup** — set wind speed, yaw/pitch/roll angles, and create parameter sweeps via natural language (powered by LiteLLM, compatible with LM Studio, Claude, OpenAI)
- **CAD support** — upload STL, STEP, or IGES files; automatic STL conversion and geometry rotation
- **Auto domain sizing** — blockMesh domain, snappyHexMesh refinement box, and forceCoeffs reference values are all computed automatically from the uploaded geometry
- **Job submission** — PBS/Torque cluster via SSH (paramiko + qsub) or local subprocess runner for development
- **Interactive results** — surface mesh, residual convergence, force coefficients (Cd/Cl/Cs), cutting plane contours, and streamlines rendered server-side via PyVista
- **Multi-user** — JWT authentication, admin/user roles, project and case management
- **Bilingual UI** — English / Japanese (switchable)

---

## Architecture

```
ChatWindTunnel/
├── backend/          FastAPI (all business logic, REST API)
│   ├── api/          Endpoints: auth, projects, simulations, jobs, chat, results
│   ├── chat/         LiteLLM agent with OpenFOAM-aware tools
│   ├── cluster/      JobRunner: LocalRunner (subprocess) / ClusterRunner (SSH + qsub)
│   ├── cad/          STL passthrough, STEP/IGES→STL (cadquery), geometry rotation (scipy)
│   ├── foam/         OpenFOAM case builder (regex-based parameter injection)
│   └── visualization/ PyVista server-side rendering, plotly charts, log parsers
├── frontend/         Streamlit UI (thin client, calls backend API only)
│   └── pages/        Projects → Geometry → Cases (Setup / Run / Results) → Admin
└── foam_templates/   OpenFOAM case templates (not included in this repository)
```

---

## Requirements

| Component | Requirement |
|-----------|-------------|
| Python | 3.11 or later |
| OpenFOAM | v2206 (local app or remote cluster) |
| LLM | LM Studio (local) or any OpenAI-compatible API |
| Cluster | PBS/Torque via SSH (optional; local runner available for testing) |
| OS | macOS or Linux (backend); any browser (frontend) |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Tatsuo06/ChatWindTunnel.git
cd ChatWindTunnel

# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies
uv sync --extra dev
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your settings (see Configuration below)
```

### 3. Initialize the database

```bash
uv run python -c "
import asyncio
from backend.db.session import engine, AsyncSessionLocal
from backend.db.models import Base, User, UserRole
from backend.core.security import hash_password
async def setup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        db.add(User(username='admin', hashed_password=hash_password('admin'), role=UserRole.admin))
        await db.commit()
asyncio.run(setup())
"
```

### 4. Start the servers

```bash
# Terminal 1 — backend
uv run uvicorn backend.main:app --reload --port 8000

# Terminal 2 — frontend
uv run streamlit run frontend/app.py --server.port 8501
```

Open [http://localhost:8501](http://localhost:8501) and log in with `admin` / `admin`.

---

## Configuration

Copy `.env.example` to `.env` and fill in your values.

### Key settings

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite (default) or PostgreSQL | `sqlite+aiosqlite:///./data/chatwt.db` |
| `SECRET_KEY` | JWT signing key | (change in production) |
| `LLM_BASE_URL` | LiteLLM API base URL | `http://localhost:1234/v1` |
| `LLM_MODEL` | Model name | your model name |
| `LLM_API_KEY` | API key | `lm-studio` |
| `CLUSTER_USER` | SSH user for PBS cluster (leave empty to use local runner) | `` |
| `CLUSTER_HOST` | Cluster hostname or IP | `` |
| `CLUSTER_FOAM_MODULE` | Path to OpenFOAM bashrc on cluster | `/path/to/OpenFOAM-v2206/etc/bashrc` |
| `FOAM_LOCAL_APP` | Path to OpenFOAM app bundle (macOS, local runner only) | `/Applications/OpenFOAM-v2206.app` |

### Runner selection

- **Local runner** (development): leave `CLUSTER_USER` empty and set `FOAM_LOCAL_APP`
- **Cluster runner** (production): set `CLUSTER_USER`, `CLUSTER_HOST`, `CLUSTER_FOAM_MODULE`

### LLM compatibility

ChatWindTunnel uses LiteLLM and works with any OpenAI-compatible endpoint:

```env
# LM Studio (local, default)
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL=your-model-name
LLM_API_KEY=lm-studio

# Anthropic Claude
LLM_BASE_URL=https://api.anthropic.com
LLM_MODEL=claude-sonnet-4-6
LLM_API_KEY=sk-ant-...

# OpenAI
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...
```

---

## OpenFOAM Templates

Case templates are included in `foam_templates/` and are derived from the OpenFOAM motorBike tutorial.

```
foam_templates/
├── motorBike/          Steady-state (simpleFoam, kOmegaSST)
└── motorBike_LES/      Unsteady (pisoFoam, SpalartAllmarasDDES)
```

See [`CLAUDE.md`](CLAUDE.md) for the expected directory structure and key parameter names that `case_builder.py` injects.

---

## Workflow

1. **Projects** — create a project
2. **Geometry** — upload a CAD file (STL / STEP / IGES); verify placement using the origin crosshair preview
3. **Cases** — create cases, configure wind speed and yaw/pitch/roll angles via sliders or chat
4. **Run** — submit the job; monitor status and solver log in real time
5. **Results** — inspect surface mesh, residuals, Cd/Cl/Cs convergence, cutting plane, and streamlines
6. **Results Summary** — compare Cd/Cl across cases on a yaw-angle sweep chart

---

## STL Coordinate Conventions

For correct domain sizing and ground clearance, the uploaded STL should follow these conventions:

- **Z = 0** is the ground plane (lowest point of geometry at or near Z = 0)
- **Y = 0** is the lateral centreline of the geometry
- **X** can be arbitrary (domain is sized relative to the geometry's own bounding box)

The geometry preview shows red (X), green (Y), blue (Z) crosshair lines through the origin so you can verify placement before submitting.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

The OpenFOAM templates and any files derived from OpenFOAM tutorials are subject to the [OpenFOAM license](https://openfoam.org/licence/).
