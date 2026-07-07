# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ChatWindTunnel is a chat-based wind tunnel simulation system built on OpenFOAM v2206. Users upload CAD files, configure simulations via natural language chat, submit jobs to a local Mac or remote PBS cluster, and view results (residuals, force coefficients, cutting planes, streamlines).

## Commands

This project uses [uv](https://docs.astral.sh/uv/) for Python environment management.

```bash
# Install / sync all dependencies (creates .venv automatically)
uv sync --extra dev

# Start backend (FastAPI)
uv run uvicorn backend.main:app --reload --port 8000

# Start frontend (Streamlit)
uv run streamlit run frontend/app.py --server.port 8501

# Run tests
uv run pytest

# Create initial admin user (run once; creates tables + admin)
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

### Database setup

**SQLite (default, no setup required)**
The default `DATABASE_URL` in `.env` uses SQLite (`sqlite+aiosqlite:///./data/chatwt.db`). The file is created automatically on first run. This is the recommended option for development and single-user deployments.

**PostgreSQL (optional, multi-user production)**
```bash
createdb chatwt
createuser chatwt --pwprompt   # use password: chatwt
psql -c "GRANT ALL ON DATABASE chatwt TO chatwt;"
```
Then set `DATABASE_URL=postgresql+asyncpg://chatwt:chatwt@localhost:5432/chatwt` in `.env`.

## Architecture

```
ChatWindTunnel/
├── backend/              FastAPI backend (all business logic)
│   ├── main.py           App entry point, router registration, DB init on startup
│   ├── api/              REST endpoints
│   │   ├── deps.py       CurrentUser / AdminUser / DB dependency injection
│   │   ├── auth.py       Login, user CRUD (admin only)
│   │   ├── projects.py   Project CRUD
│   │   ├── simulations.py  Simulation CRUD + CAD upload + rotation
│   │   ├── jobs.py       Job submit / poll / cancel
│   │   ├── chat.py       Chat history + LLM call
│   │   └── results.py    PNG image endpoints (geometry, residuals, plots)
│   ├── core/
│   │   ├── config.py     All settings via pydantic-settings (.env)
│   │   └── security.py   JWT + bcrypt
│   ├── db/
│   │   ├── models.py     SQLAlchemy ORM: User, Project, Simulation, ChatMessage
│   │   └── session.py    Async engine + session factory
│   ├── foam/
│   │   └── case_builder.py  Copies template → case dir, writes all OpenFOAM params via regex
│   ├── cad/
│   │   └── converter.py  STL passthrough / STEP+IGES→STL via cadquery; STL rotation via numpy-stl+scipy
│   ├── cluster/
│   │   ├── base.py       JobRunner ABC
│   │   ├── local_runner.py   subprocess + threading (Mac test)
│   │   ├── cluster_runner.py SSH + qsub via paramiko
│   │   └── __init__.py   get_runner() factory: uses ClusterRunner when CLUSTER_USER is set
│   ├── chat/
│   │   └── agent.py      LiteLLM chat with 5 tools (set_flow_conditions, set_solver_settings, etc.)
│   └── visualization/
│       ├── base.py       VisualizationBackend ABC (swap backends without changing callers)
│       ├── pyvista_backend.py  Server-side PNG rendering via PyVista; 2D charts via plotly
│       └── parsers.py    Parse solver logs (residuals) and postProcessing/forceCoeffs1/*/coefficient.dat
├── frontend/             Streamlit UI (UI only — all logic goes through backend API)
│   ├── app.py            Login + sidebar navigation
│   ├── api_client.py     Thin requests wrapper for all backend endpoints
│   └── pages/
│       ├── 01_projects.py
│       ├── 02_simulation.py   Setup tab + Chat tab + Run tab
│       ├── 03_results.py      5-tab results view + chat-based interpretation
│       └── 04_admin.py        User management (admin role only)
├── foam_templates/
│   ├── motorBike/             Steady-state template (simpleFoam, kOmegaSST)
│   └── motorBike_LES/         Unsteady template (pisoFoam, SpalartAllmarasDDES)
│       ├── motorBike/         Phase-1 steady case (SpalartAllmaras RAS)
│       └── lesFiles/          Files swapped in for LES phase (controlDict, fvSchemes, turbulenceProperties)
└── data/                 Runtime data (gitignored)
    ├── uploads/           Original CAD + rotated STL, per simulation ID
    ├── cases/             OpenFOAM case directories, per simulation ID
    └── results/           (reserved for future use)
```

## Key Design Decisions

**Wind direction via geometry rotation**: Flow always enters at +X (20 m/s default). User-specified yaw/pitch/roll angles rotate the STL geometry in the opposite direction before meshing. Post-processing converts Cd/Cl back to the object frame. This avoids modifying blockMeshDict or boundary conditions.

Rotation is applied in `cad/converter.py:rotate_stl()` using scipy `Rotation.from_euler("zyx", [-yaw, -pitch, -roll])` (Z→Y→X order). The rotation centre is the bounding-box centroid of the original STL, which is also stored as `cofr` for forceCoeffs.

**STL coordinate system assumptions**: The domain auto-sizing (`_auto_domain_params`) assumes:
- **X=0 is arbitrary** — the domain is always sized relative to the geometry's own bounding box centre `cx`, so X=0 placement in the STL does not affect domain clearances (always 1.5L upstream, 3.5L downstream of the geometry faces).
- **Y=0 is the geometry's lateral centreline** — the crosswind domain is ±2.5L centred at Y=0. If the geometry's Y-centre deviates significantly from 0, it will sit asymmetrically in the domain.
- **Z=0 is the ground plane** — the domain runs from Z=0 to Z=2.5L. The geometry should be modelled with its lowest point at or near Z=0 (ground contact). A floating geometry (z0 >> 0) will have excessive clearance below it.

The geometry preview shows red/green/blue crosshair lines through the origin along the domain extents so users can verify their STL's coordinate placement before submitting a job.

**Turbulence initial conditions (k and ω) are always derived from wind speed**: Do NOT store fixed k/ω values — they must be recomputed whenever `velocity_mps` or `lref` changes. The formula used (`case_builder._turbulence_from_velocity`):

```
k     = 1.5 × (U × I)²          # I = turbulence intensity, default 0.05 (5%)
L     = 0.07 × lref              # mixing-length estimate; lref from forceCoeffs reference length
omega = sqrt(k) / (Cμ^0.25 × L) # Cμ = 0.09 (k-ω standard constant)
```

Recomputation is triggered at three points:
1. **Case creation** (`simulations.py:create_simulation`) — applied to DEFAULT_PARAMETERS
2. **Parameter update** (`simulations.py:update_simulation`) — triggered when `velocity_mps`, `lref`, or `turbulence_intensity` changes
3. **Job submission** (`case_builder._write_initial_conditions`) — final override before writing OpenFOAM `0.orig/include/initialConditions`

Background: fixing k/ω at default values (k=0.24, ω=1.78, calibrated for ~20 m/s) caused divergence at low velocities (e.g. 5 m/s) because the turbulence intensity was ~25× too high, destabilising the SIMPLE solver from iteration 1.

**Domain sizing and STL placement are auto-computed from the pre-rotation STL bounding box** (`case_builder._auto_domain_params()`). L = x_max − x_min is the representative length (flow direction). All dimensions scale with L:

```
Upstream inlet  : geometry centre − 2L   →  1.5L clearance from geometry front face
Downstream outlet: geometry centre + 4L  →  3.5L clearance from geometry rear face
Y (crosswind)   : ±2.5L from Y=0         (geometry assumed centred near Y=0)
Z (vertical)    : 0 to 2.5L              (geometry assumed sitting on ground, z0 ≈ 0)
```

`location_in_mesh` (snappyHexMesh seed point) is placed at the midpoint between the inlet and the geometry front face (0.75L upstream of front face), on the centreline (Y=0), at mid-domain height (Z=1.25L). This guarantees it is in the fluid region for any geometry that fits the domain assumptions above.

`blockmesh_nx` is taken from the user's parameter (default 80). `ny` and `nz` are derived from `nx` proportionally to the domain aspect ratios so that cell aspect ratios stay near 1:1.

**forceCoeffs reference values are auto-computed from the pre-rotation STL** (`_auto_domain_params()`):

```
aref = W × H   (Y-span × Z-height of unrotated bounding box — frontal projected area, constant across yaw sweep)
lref = L       (flow-direction length = x_max − x_min of unrotated STL)
cofr = bounding-box centroid [(x0+x1)/2, (y0+y1)/2, (z0+z1)/2] — matches rotate_stl() rotation centre
```

Using the **unrotated** STL for aref/lref/cofr is intentional: aref must be constant across yaw angles so Cd/Cl values on a yaw-sweep chart are normalised by the same reference area and remain directly comparable. Using the rotated frontal area would change aref at each yaw angle and mislead the comparison.

**refinementBox is computed from the rotated STL bounding box**: The snappyHexMesh refinementBox uses a uniform padding of `0.2 * max(dx, dy, dz)` in all directions. This avoids overly tight boxes for elongated geometries where per-axis 20% would be very small in the narrow directions.

```
dx = x1_rot - x0_rot  (and similarly dy, dz)
pad = 0.2 * max(dx, dy, dz)
refbox_min = [x0_rot - pad,  y0_rot - pad,  max(0, z0_rot - pad)]
refbox_max = [x1_rot + pad,  y1_rot + pad,  z1_rot + pad]
```

Implemented in `case_builder._refbox_from_rotated_stl()`. Called in `build_case()` after STL rotation, and in `results.py` geometry preview endpoint. This ensures the refinement region always tightly follows the actual rotated geometry orientation.

**insideSurfaces is always set in snappyHexMeshDict**: Both templates (`motorBike/system/snappyHexMeshDict` and `motorBike_LES/motorBike/system/snappyHexMeshDict`) include `insideSurfaces (motorBike);` in `castellatedMeshControls`. This explicitly removes cells inside the geometry surface during the castellated mesh phase. Unlike `locationInMesh` (which keeps cells reachable from the seed point), `insideSurfaces` works even for open/non-watertight STLs — it uses surface intersection tests rather than flood fill. This is required for geometries like ship hulls (e.g. JBC) that have open edges, where snappyHexMesh would otherwise mesh the interior of the hull. Cases built before Case #145 do not have this setting.

**LLM abstraction via LiteLLM**: `backend/chat/agent.py` calls LiteLLM with `model="openai/<model>"` and `api_base` pointing to LM Studio. To switch to Claude or OpenAI, only `.env` values change — no code changes needed.

**Job runner factory**: `cluster/__init__.py:get_runner()` returns `LocalRunner` when `CLUSTER_USER` is empty, `ClusterRunner` otherwise. Local runner uses background threads; cluster runner SSHes to the host set in `CLUSTER_HOST` and submits PBS jobs with `qsub`.

**Visualization backend abstraction**: `VisualizationBackend` ABC in `visualization/base.py` isolates PyVista. To add dash-vtk or another renderer, implement the ABC and swap the singleton in `pyvista_backend.py`.

**Case building**: `foam/case_builder.py` uses regex `_replace_value()` to modify OpenFOAM dictionary files rather than foamlib's dict API, because the template files use `#include` directives that foamlib cannot always resolve. snappyHexMeshDict geometry names are rewritten from `motorBike` to `object` to match the uploaded geometry.

## Turbulence Models

The steady-state solver (simpleFoam) supports three RANS models and a laminar option, selectable via `turbulence_model` in simulation parameters or via chat (`set_solver_settings`). The default is `kOmegaSST`.

| Model | Parameter value | Fields | Stability (bluff body) |
|---|---|---|---|
| k-ω SST | `kOmegaSST` | k, omega, nut | ✅ Excellent — default |
| Spalart-Allmaras | `SpalartAllmaras` | nuTilda, nut | ✅ Good — one-equation, fast |
| Realizable k-ε | `realizableKE` | k, epsilon, nut | ✅ Good — stable k-ε variant |
| Laminar | `laminar` | (none) | ✅ Valid for Re ≲ 10³; use to compare with turbulent at same Re |

**What `case_builder.py` does at build time** (`_apply_spalart_allmaras` / `_apply_kepsilon`):

For **SpalartAllmaras**:
- `constant/turbulenceProperties`: `RASModel SpalartAllmaras`
- `0.orig/nuTilda`: created; `fixedValue 0` at walls; `$nuTildaInlet` at inlet
- `0.orig/k`, `0.orig/omega`: removed
- `0.orig/include/initialConditions`: `nuTildaInlet = 3 × ν` (e.g. 4.5×10⁻⁵ for air)
- `system/fvSchemes`: `div(phi,k)` + `div(phi,omega)` → `div(phi,nuTilda)`
- `system/fvSolution`: k/omega solvers and relaxation → nuTilda

For **realizableKE**:
- `constant/turbulenceProperties`: `RASModel realizableKE`
- `0.orig/epsilon`: created; `epsilonWallFunction` at walls; `$turbulentEpsilon` at inlet
- `0.orig/omega`: removed; `0.orig/k` kept unchanged
- `0.orig/include/initialConditions`: `turbulentEpsilon = Cμ^(3/4) × k^(3/2) / L`  where `L = 0.07 × lref`
- `system/fvSchemes`: `div(phi,omega)` → `div(phi,epsilon)`
- `system/fvSolution`: omega solver and relaxation → epsilon

**streamLines** `fields` entry is automatically set to `(p U k)`, `(p U nuTilda)`, or `(p U epsilon)` to match the active model.

**Why not standard kEpsilon**: Local tests on bluff body geometry (m5480) showed kEpsilon and RNGkEpsilon both diverge at iteration ~8–10 when starting from potentialFoam initial conditions. The epsilon production at walls overwhelms dissipation. realizableKE avoids this via variable Cμ (realizability constraint). Standard kEpsilon is not exposed in the UI.

## OpenFOAM Templates

### Steady (motorBike / simpleFoam)
- Default turbulence: kOmegaSST (switchable — see Turbulence Models section above)
- Wind tunnel: X[-5,15] Y[-4,4] Z[0,8] m; inlet on -X face, outlet on +X face
- Key params written by case_builder: `flowVelocity`, `turbulentKE`, `turbulentOmega`, `endTime`, `numberOfSubdomains`, snappyHexMesh refinement levels, `forceCoeffs` Aref/lRef/CofR
- Outputs: `postProcessing/cuttingPlane/` (p,U on y=0), `postProcessing/streamLines/`, `postProcessing/forceCoeffs1/`

### Unsteady (motorBike_LES / pisoFoam + DDES)
- Phase 1: SpalartAllmaras RAS with simpleFoam. Iteration count follows the `end_time` parameter (default 500); `writeInterval` is set equal to `endTime` so the final state is always written for the phase swap.
- Phase 2: Copy the latest `processor*/<time>` to `processor*/0`, swap `lesFiles/` configs into system/ and constant/ (prepared as `.les`-suffixed files by `case_builder._prepare_les_files`), run pisoFoam.
- LES model: SpalartAllmarasDDES. `endTime`/`deltaT` follow the `les_end_time`/`les_delta_t` parameters (defaults 0.7 s / 1e-4 s); `writeInterval` (timeStep-based, template 1000) is capped at the total step count so short runs still write.
- Domain boundary conditions in `0.orig/` use `slip` on `upperWall`/`frontAndBack` (like the steady template). Do NOT use `symmetryPlane` — the auto-generated blockMeshDict declares those faces as plain `patch` type and any `symmetryPlane` field entry crashes the solver at startup.
- forceCoeffs function object is named `forceCoeffs1` (same as steady). Phase 2 restarts at time 0, so OpenFOAM writes its coefficients to `coefficient_0.dat` alongside Phase 1's `coefficient.dat` in `postProcessing/forceCoeffs1/0/`; `parse_force_coefficients` reads both and tags rows with a `Phase` column (boundary detected where Time decreases).
- Progress/results are phase-aware: `jobs.py /progress` reports `phase` 1 or 2 (detected from `log.pisoFoam` having Time markers), `/residuals?phase=` selects the per-phase log via `parsers.phase_logs()`, and the force-coefficient chart renders each phase as its own subplot (iteration vs seconds axes).
- Post-processing for the Mesh and Cutting-plane tabs runs at the END of the LES Allrun (steady does it inline). After `reconstructPar`, the leftover Phase-1 time directory (captured as `PHASE1_TIME` before the swap) is removed so `-latestTime` targets the LES end time, then `foamToVTK -no-internal -latestTime` (surface mesh) and `postProcess -func cuttingPlane -latestTime` (y=0 plane of the instantaneous LES field) run. The LES template therefore needs its own `system/cuttingPlane` dict (copied from the steady template). Without these steps the Mesh tab 500s and the Cutting-plane tab 404s for unsteady cases.

## Environment & Cluster

- Local OpenFOAM: set `FOAM_LOCAL_APP` in `.env` to the app path (e.g. `/Applications/OpenFOAM-v2206.app`). Leave `CLUSTER_USER` empty to use the local runner. If `FOAM_LOCAL_APP` is not set or the path does not exist, a clear error is raised.
- Cluster login node: configured via `CLUSTER_HOST` in `.env`; PBS/Torque scheduler, `qsub` submission
- PBS script: `nodes=1:ppn=16`, job directory via `$PBS_O_WORKDIR`
- File transfer: `TRANSPORT_MODE=nfs|scp` (set in `.env`); NFS preferred when available
- Set `CLUSTER_USER` in `.env` to activate cluster mode; leave empty for local testing
