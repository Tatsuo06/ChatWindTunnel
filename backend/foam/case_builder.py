"""Build OpenFOAM cases from templates using foamlib.

Geometry strategy: keep internal name "motorBike" throughout so that all
boundary condition files (0.orig/U, p, k …) referencing "motorBikeGroup"
continue to work.  Only the file extension changes: .obj → .stl.
"""
import json
import re
import shutil
import textwrap
from pathlib import Path

from backend.cad.converter import rotate_stl
from backend.core.config import settings
from backend.db.models import SimulatorType

STEADY_TEMPLATE = settings.TEMPLATES_DIR / "motorBike"
UNSTEADY_TEMPLATE = settings.TEMPLATES_DIR / "motorBike_LES" / "motorBike"
LES_FILES = settings.TEMPLATES_DIR / "motorBike_LES" / "lesFiles"

# ---------------------------------------------------------------------------
# Custom Allrun scripts (replace template Allrun that copies motorBike.obj.gz)
# ---------------------------------------------------------------------------

_ALLRUN_STEADY = textwrap.dedent("""\
    #!/bin/sh
    cd "${0%/*}" || exit
    . ${WM_PROJECT_DIR:?}/bin/tools/RunFunctions

    runApplication surfaceFeatureExtract

    runApplication blockMesh

    rm -rf processor*
    runApplication decomposePar -decomposeParDict system/decomposeParDict

    (
        while true; do
            ps aux | grep "[s]nappyHexMesh" | awk '{sum+=$6} END {if(sum>0) print sum}' >> log.mem_snappy.tmp
            sleep 1
        done
    ) &
    _MON_SNAPPY=$!

    runParallel snappyHexMesh -overwrite

    kill $_MON_SNAPPY 2>/dev/null
    if [ -s log.mem_snappy.tmp ]; then
        peak=$(sort -n log.mem_snappy.tmp | tail -1)
        echo "Peak RSS (all snappyHexMesh processes): ${peak} kB" > log.mem_snappy
    fi
    rm -f log.mem_snappy.tmp

    runParallel topoSet

    restore0Dir -processor

    runParallel patchSummary

    runParallel potentialFoam -writephi

    runParallel checkMesh -writeFields '(nonOrthoAngle)' -constant

    # Memory monitor: poll RSS of all solver processes every 5 s
    (
        while true; do
            ps aux | grep "[s]impleFoam" | awk '{sum+=$6} END {if(sum>0) print sum}' >> log.mem_monitor.tmp
            sleep 5
        done
    ) &
    _MON_PID=$!

    runParallel $(getApplication)

    kill $_MON_PID 2>/dev/null
    if [ -s log.mem_monitor.tmp ]; then
        peak=$(sort -n log.mem_monitor.tmp | tail -1)
        echo "Peak RSS (all simpleFoam processes): ${peak} kB" > log.mem_monitor
    fi
    rm -f log.mem_monitor.tmp

    runApplication reconstructParMesh -constant

    runApplication reconstructPar -latestTime
""")

_ALLRUN_LES = textwrap.dedent("""\
    #!/bin/sh
    cd "${0%/*}" || exit
    . ${WM_PROJECT_DIR:?}/bin/tools/RunFunctions

    # === Phase 1: Steady-state (SpalartAllmaras RAS) ===
    runApplication surfaceFeatureExtract
    runApplication blockMesh
    rm -rf processor*
    runApplication decomposePar -decomposeParDict system/decomposeParDict
    (
        while true; do
            ps aux | grep "[s]nappyHexMesh" | awk '{sum+=$6} END {if(sum>0) print sum}' >> log.mem_snappy.tmp
            sleep 1
        done
    ) &
    _MON_SNAPPY=$!
    runParallel snappyHexMesh -overwrite
    kill $_MON_SNAPPY 2>/dev/null
    if [ -s log.mem_snappy.tmp ]; then
        peak=$(sort -n log.mem_snappy.tmp | tail -1)
        echo "Peak RSS (all snappyHexMesh processes): ${peak} kB" > log.mem_snappy
    fi
    rm -f log.mem_snappy.tmp
    runParallel topoSet
    restore0Dir -processor
    runParallel patchSummary
    runParallel potentialFoam -writephi
    runParallel checkMesh -writeFields '(nonOrthoAngle)' -constant
    (
        while true; do
            ps aux | grep "[s]impleFoam" | awk '{sum+=$6} END {if(sum>0) print sum}' >> log.mem_monitor.tmp
            sleep 5
        done
    ) &
    _MON_PID=$!
    runParallel simpleFoam
    kill $_MON_PID 2>/dev/null
    if [ -s log.mem_monitor.tmp ]; then
        peak=$(sort -n log.mem_monitor.tmp | tail -1)
        echo "Peak RSS (all simpleFoam processes): ${peak} kB" > log.mem_monitor
    fi
    rm -f log.mem_monitor.tmp

    # === Phase 2: LES (SpalartAllmarasDDES) ===
    # Swap steady-state solution as LES initial condition
    ls -d processor* | xargs -I {} sh -c 'cp -r {}/500 {}/0_les; rm -rf {}/0; mv {}/0_les {}/0; rm -rf {}/0/uniform'

    # Replace config files with LES versions
    cp -f system/controlDict.les   system/controlDict
    cp -f system/fvSchemes.les     system/fvSchemes
    cp -f system/fvSolution.les    system/fvSolution
    cp -f constant/turbulenceProperties.les  constant/turbulenceProperties

    runParallel pisoFoam

    runApplication reconstructParMesh -constant
    runApplication reconstructPar
""")

_ALLRUN_RESTART = textwrap.dedent("""\
    #!/bin/sh
    cd "${0%/*}" || exit
    . ${WM_PROJECT_DIR:?}/bin/tools/RunFunctions

    _i=1; while [ -f "log.simpleFoam.$_i" ]; do _i=$((_i+1)); done
    [ -f log.simpleFoam ] && mv log.simpleFoam log.simpleFoam.$_i

    (
        while true; do
            ps aux | grep "[s]impleFoam" | awk '{sum+=$6} END {if(sum>0) print sum}' >> log.mem_monitor.tmp
            sleep 5
        done
    ) &
    _MON_PID=$!

    runParallel $(getApplication)

    kill $_MON_PID 2>/dev/null
    if [ -s log.mem_monitor.tmp ]; then
        peak=$(sort -n log.mem_monitor.tmp | tail -1)
        echo "Peak RSS (all simpleFoam processes): ${peak} kB" > log.mem_monitor
    fi
    rm -f log.mem_monitor.tmp

    runApplication reconstructPar -latestTime
""")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_case(
    sim_id: int,
    stl_path: Path,
    solver_type: SimulatorType,
    params: dict,
    yaw_deg: float = 0.0,
    pitch_deg: float = 0.0,
    roll_deg: float = 0.0,
) -> Path:
    """Copy template, apply rotation, write parameters. Returns case dir."""
    case_dir = settings.CASES_DIR / str(sim_id)
    if case_dir.exists():
        shutil.rmtree(case_dir)

    template = STEADY_TEMPLATE if solver_type == SimulatorType.steady else UNSTEADY_TEMPLATE
    shutil.copytree(template, case_dir)

    # Place geometry — keep name "motorBike" so BC files stay valid
    tri_surface_dir = case_dir / "constant" / "triSurface"
    tri_surface_dir.mkdir(parents=True, exist_ok=True)

    target_stl = tri_surface_dir / "motorBike.stl"
    if yaw_deg != 0.0 or pitch_deg != 0.0 or roll_deg != 0.0:
        rotate_stl(stl_path, target_stl, yaw_deg=yaw_deg, pitch_deg=pitch_deg, roll_deg=roll_deg)
    else:
        shutil.copy2(stl_path, target_stl)

    # Auto-compute domain/mesh/force-reference params from the original (pre-rotation) STL
    if params.get("auto_domain", True):
        nx = int(params.get("blockmesh_nx", 80))
        auto = _auto_domain_params(stl_path, nx=nx)
        # Override refinementBox with rotated-STL bounding box + 20% margin
        auto.update(_refbox_from_rotated_stl(target_stl))
        params = {**params, **auto}

    # Write OpenFOAM parameter files
    _write_initial_conditions(case_dir, params)
    _write_control_dict(case_dir, solver_type, params)
    _write_decompose_par(case_dir, params)
    _write_block_mesh_dict(case_dir, params)
    _write_force_coeffs(case_dir, params)
    _write_snappy_hex_mesh(case_dir, params)
    _write_surface_feature_extract(case_dir)
    _write_streamlines(case_dir, params)

    # Empty .foam file required by pyvista.OpenFOAMReader
    (case_dir / "case.foam").touch()

    # Save the parameters actually used for this computation
    (case_dir / "case_params.json").write_text(json.dumps({
        "yaw_deg": yaw_deg,
        "pitch_deg": pitch_deg,
        **params,
    }, indent=2))

    # Replace Allrun with our custom version
    allrun = case_dir / "Allrun"
    script = _ALLRUN_LES if solver_type == SimulatorType.unsteady else _ALLRUN_STEADY
    allrun.write_text(script)
    allrun.chmod(0o755)

    # For LES: copy LES config files alongside steady files so Allrun can swap them
    if solver_type == SimulatorType.unsteady:
        _prepare_les_files(case_dir)

    return case_dir


def build_restart_case(case_dir: Path, new_end_time: int) -> Path:
    """Update endTime/startFrom in controlDict and replace Allrun with solver-only restart script."""
    ctrl_path = case_dir / "system" / "controlDict"
    content = ctrl_path.read_text()
    content = _set_value(content, "endTime", str(new_end_time))
    content = _set_value(content, "startFrom", "latestTime")
    ctrl_path.write_text(content)

    allrun = case_dir / "Allrun"
    allrun.write_text(_ALLRUN_RESTART)
    allrun.chmod(0o755)

    return case_dir


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _turbulence_from_velocity(velocity: float, intensity: float = 0.05, lref: float = 1.0) -> tuple[float, float]:
    """Compute k and omega from velocity, turbulence intensity and reference length.
    Uses standard k-omega relations: k = 1.5*(U*I)^2, omega = k^0.5 / (Cmu^0.25 * L)
    where L = 0.07 * lref (mixing-length estimate).
    """
    k = 1.5 * (velocity * intensity) ** 2
    L = 0.07 * lref
    omega = (k ** 0.5) / (0.09 ** 0.25 * L)
    return round(k, 6), round(omega, 4)


def _write_initial_conditions(case_dir: Path, params: dict) -> None:
    velocity = params.get("velocity_mps", 20.0)
    flow_velocity = f"({velocity} 0 0)"
    lref = params.get("lref", 1.0)
    intensity = params.get("turbulence_intensity", 0.05)
    turbulent_ke, turbulent_omega = _turbulence_from_velocity(velocity, intensity, lref)

    ic_path = case_dir / "0.orig" / "include" / "initialConditions"
    content = ic_path.read_text()
    content = _set_value(content, "flowVelocity", flow_velocity)
    content = _set_value(content, "turbulentKE", str(turbulent_ke))
    content = _set_value(content, "turbulentOmega", str(turbulent_omega))
    ic_path.write_text(content)


def _write_control_dict(case_dir: Path, solver_type: SimulatorType, params: dict) -> None:
    ctrl_path = case_dir / "system" / "controlDict"
    content = ctrl_path.read_text()
    if solver_type == SimulatorType.steady:
        content = _set_value(content, "endTime", str(int(params.get("end_time", 500))))
    ctrl_path.write_text(content)


_BLOCK_MESH_TEMPLATE = """\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

scale   {scale};

vertices
(
    ({xmin} {ymin}  0)
    ({xmax} {ymin}  0)
    ({xmax}  {ymax}  0)
    ({xmin}  {ymax}  0)
    ({xmin} {ymin}  {zmax})
    ({xmax} {ymin}  {zmax})
    ({xmax}  {ymax}  {zmax})
    ({xmin}  {ymax}  {zmax})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)
);

edges
(
);

boundary
(
    frontAndBack
    {{
        type patch;
        faces
        (
            (3 7 6 2)
            (1 5 4 0)
        );
    }}
    inlet
    {{
        type patch;
        faces
        (
            (0 4 7 3)
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            (2 6 5 1)
        );
    }}
    lowerWall
    {{
        type wall;
        faces
        (
            (0 3 2 1)
        );
    }}
    upperWall
    {{
        type patch;
        faces
        (
            (4 5 6 7)
        );
    }}
);

// ************************************************************************* //
"""

_DECOMPOSE_PAR_TEMPLATE = """\
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      decomposeParDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

numberOfSubdomains {n};

method          {method};

// ************************************************************************* //
"""


def _auto_domain_params(stl_path: Path, nx: int = 80) -> dict:
    """Compute blockMesh/snappyHexMesh/forceCoeffs params from STL bounding box.

    Domain sizing rule (L = x_max - x_min, representative length):
      X: geometry centre - 2L  to  geometry centre + 4L  (total 6L)
      Y: ±2.5L from centre  (total 5L)
      Z: 0 to 2.5L

    nx is passed in from user params so blockmesh cell count is not overridden.
    ny and nz are computed proportionally from nx and domain aspect ratios.

    forceCoeffs reference values:
      aref = W * H  (frontal projected area of unrotated STL, constant across yaw)
      lref = L      (flow-direction length of unrotated STL, = x_max - x_min)
      cofr = bounding-box centroid = rotation center used in rotate_stl()
    """
    from stl import mesh as stl_mesh

    m = stl_mesh.Mesh.from_file(str(stl_path))
    pts = m.vectors.reshape(-1, 3)
    x0, y0, z0 = float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 2].min())
    x1, y1, z1 = float(pts[:, 0].max()), float(pts[:, 1].max()), float(pts[:, 2].max())

    L  = x1 - x0            # representative length (flow direction)
    W  = y1 - y0            # span width
    H  = z1 - z0            # height
    cx = (x0 + x1) / 2      # geometry centre in X

    # ---- blockMesh domain ----
    dom_xmin  = round(cx - 2.0 * L, 2)
    dom_xmax  = round(cx + 4.0 * L, 2)
    dom_yhalf = round(2.5 * L, 2)
    dom_zmax  = round(2.5 * L, 2)

    Lx = dom_xmax - dom_xmin
    ny = max(8, round(nx * 2 * dom_yhalf / Lx))
    nz = max(8, round(nx * dom_zmax       / Lx))

    # ---- snappyHexMesh refinement box (pre-rotation fallback only) ----
    ref_y = round(0.8 * L, 2)
    refbox_min = [round(x0 - 0.3 * L, 2), -ref_y, 0.0]
    refbox_max = [round(x1 + 0.7 * L, 2),  ref_y, round(z1 + H, 2)]

    # ---- seed point: upstream of body, on centreline, mid-domain height ----
    loc = [round((dom_xmin + x0) / 2, 2), 0.0, round(dom_zmax / 2, 2)]

    # ---- forceCoeffs reference values ----
    aref = round(W * H, 4)
    lref = round(L, 4)
    cofr = [round(cx, 4), round((y0 + y1) / 2, 4), round((z0 + z1) / 2, 4)]

    return {
        "domain_scale": 1,
        "domain_xmin":  dom_xmin,
        "domain_xmax":  dom_xmax,
        "domain_yhalf": dom_yhalf,
        "domain_zmax":  dom_zmax,
        "blockmesh_nx": nx,
        "blockmesh_ny": ny,
        "blockmesh_nz": nz,
        "refbox_min":       refbox_min,
        "refbox_max":       refbox_max,
        "location_in_mesh": loc,
        "aref": aref,
        "lref": lref,
        "cofr": cofr,
        "streamline_radius":   round(max(W, H) / 2 * 1.1, 3),
        "streamline_n_points": 50,
    }


def _refbox_from_rotated_stl(stl_path: Path, margin: float = 0.2) -> dict:
    """Compute refinementBox and streamline sphere params from the rotated STL bounding box.

    Rule: margin = 0.2 * max(dx, dy, dz) applied equally in all directions.
    This avoids overly tight boxes for elongated geometries.
    Z min is clamped to 0 (ground plane).

    Streamline sphere: placed just upstream of the front face, centred on the geometry's
    Y/Z centroid, with radius covering the frontal profile.
    """
    from stl import mesh as stl_mesh

    m = stl_mesh.Mesh.from_file(str(stl_path))
    pts = m.vectors.reshape(-1, 3)
    x0, y0, z0 = float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 2].min())
    x1, y1, z1 = float(pts[:, 0].max()), float(pts[:, 1].max()), float(pts[:, 2].max())

    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    pad = margin * max(dx, dy, dz)

    refbox_min = [
        round(x0 - pad, 3),
        round(y0 - pad, 3),
        max(0.0, round(z0 - pad, 3)),
    ]
    refbox_max = [
        round(x1 + pad, 3),
        round(y1 + pad, 3),
        round(z1 + pad, 3),
    ]

    # Streamline sphere: centred on bounding-box centroid (= rotation centre)
    sl_cx = round((x0 + x1) / 2, 3)
    sl_cy = round((y0 + y1) / 2, 3)
    sl_cz = round((z0 + z1) / 2, 3)
    sl_r  = round(max(dx, dy, dz) / 2 * 1.1, 3)

    return {
        "refbox_min": refbox_min,
        "refbox_max": refbox_max,
        "streamline_center": [sl_cx, sl_cy, sl_cz],
    }


def _sphere_seed_points(center: list, radius: float, n_points: int) -> list[list[float]]:
    """Generate n_points uniformly distributed on a sphere surface (Fibonacci method).

    Points below ground (z < 0) are mirrored to z = abs(z) to keep seeds in the domain.
    """
    import math
    golden = (1 + math.sqrt(5)) / 2
    pts = []
    for i in range(n_points):
        theta = math.acos(1 - 2 * (i + 0.5) / n_points)
        phi = 2 * math.pi * i / golden
        x = center[0] + radius * math.sin(theta) * math.cos(phi)
        y = center[1] + radius * math.sin(theta) * math.sin(phi)
        z = center[2] + radius * math.cos(theta)
        z = abs(z)  # mirror below-ground points
        pts.append([round(x, 4), round(y, 4), round(z, 4)])
    return pts


def _write_block_mesh_dict(case_dir: Path, params: dict) -> None:
    scale  = params.get("domain_scale",  1)
    xmin   = params.get("domain_xmin",  -5)
    xmax   = params.get("domain_xmax",  15)
    yhalf  = params.get("domain_yhalf",  4)
    zmax   = params.get("domain_zmax",   8)
    nx     = int(params.get("blockmesh_nx", 20))
    ny     = int(params.get("blockmesh_ny",  8))
    nz     = int(params.get("blockmesh_nz",  8))
    (case_dir / "system" / "blockMeshDict").write_text(
        _BLOCK_MESH_TEMPLATE.format(
            scale=scale,
            xmin=xmin, xmax=xmax,
            ymin=-yhalf, ymax=yhalf,
            zmax=zmax,
            nx=nx, ny=ny, nz=nz,
        )
    )


def _write_decompose_par(case_dir: Path, params: dict) -> None:
    n_proc = int(params.get("n_processors", 6))
    method = params.get("decompose_method", "scotch")
    (case_dir / "system" / "decomposeParDict").write_text(
        _DECOMPOSE_PAR_TEMPLATE.format(n=n_proc, method=method)
    )


def _write_force_coeffs(case_dir: Path, params: dict) -> None:
    fc_path = case_dir / "system" / "forceCoeffs"
    if not fc_path.exists():
        return
    velocity = params.get("velocity_mps", 20.0)
    aref = params.get("aref", 0.75)
    lref = params.get("lref", 1.42)
    cofr = params.get("cofr", [0.72, 0.0, 0.0])
    cofr_str = f"({cofr[0]} {cofr[1]} {cofr[2]})"

    content = fc_path.read_text()
    content = _set_value(content, "magUInf", str(velocity))
    content = _set_value(content, "Aref", str(aref))
    content = _set_value(content, "lRef", str(lref))
    content = _set_value(content, "CofR", cofr_str)
    fc_path.write_text(content)


def _write_snappy_hex_mesh(case_dir: Path, params: dict) -> None:
    shmdict = case_dir / "system" / "snappyHexMeshDict"
    if not shmdict.exists():
        return
    content = shmdict.read_text()

    # Change file reference from .obj to .stl (keep internal name "motorBike")
    content = content.replace("motorBike.obj", "motorBike.stl")

    # Update refinement levels
    ref_min = int(params.get("refinement_min", 5))
    ref_max = int(params.get("refinement_max", 6))
    content = re.sub(
        r"(level\s*\()(\d+)\s+(\d+)(\s*\);)",
        lambda m: f"{m.group(1)}{ref_min} {ref_max}{m.group(4)}",
        content,
        count=1,
    )

    # Update refinementBox bounds
    rmin = params.get("refbox_min", [-1.0, -0.7, 0.0])
    rmax = params.get("refbox_max", [8.0, 0.7, 2.5])
    rmin_str = f"({rmin[0]} {rmin[1]} {rmin[2]})"
    rmax_str = f"({rmax[0]} {rmax[1]} {rmax[2]})"

    def _replace_refbox(m):
        block = re.sub(r'(min\s+)\([^)]+\)', r'\g<1>' + rmin_str, m.group(0), count=1)
        block = re.sub(r'(max\s+)\([^)]+\)', r'\g<1>' + rmax_str, block, count=1)
        return block

    content = re.sub(
        r'refinementBox\s*\{[^}]+\}',
        _replace_refbox,
        content,
        count=1,
        flags=re.DOTALL,
    )

    # Update locationInMesh
    loc = params.get("location_in_mesh", [3.0001, 3.0001, 0.43])
    content = re.sub(
        r'locationInMesh\s*\([^)]+\)\s*;',
        f'locationInMesh ({loc[0]} {loc[1]} {loc[2]});',
        content,
        count=1,
    )

    shmdict.write_text(content)


def _write_surface_feature_extract(case_dir: Path) -> None:
    sfed = case_dir / "system" / "surfaceFeatureExtractDict"
    if not sfed.exists():
        return
    content = sfed.read_text()
    # Change file reference from .obj to .stl
    content = content.replace("motorBike.obj", "motorBike.stl")
    sfed.write_text(content)


def _write_streamlines(case_dir: Path, params: dict) -> None:
    """Overwrite system/streamLines with cloud-type seeds on a sphere."""
    sl_file = case_dir / "system" / "streamLines"
    if not sl_file.exists():
        return
    center   = params.get("streamline_center", [-1.0, 0.0, 0.5])
    radius   = params.get("streamline_radius", 0.5)
    n_points = int(params.get("streamline_n_points", 50))
    pts = _sphere_seed_points(center, radius, n_points)
    pts_str = "\n        ".join(f"({p[0]} {p[1]} {p[2]})" for p in pts)
    content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2206                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/

streamLines
{{
    libs            (fieldFunctionObjects);
    type            streamLine;
    writeControl    writeTime;
    setFormat       vtk;
    trackForward    true;
    fields (p U k);
    lifeTime        10000;
    nSubCycle       5;
    cloud           particleTracks;

    // Sphere seed: center=({center[0]} {center[1]} {center[2]}) radius={radius} n={n_points}
    seedSampleSet
    {{
        type    cloud;
        axis    x;
        points  (
        {pts_str}
        );
    }}
}}

// ************************************************************************* //
"""
    sl_file.write_text(content)


def _prepare_les_files(case_dir: Path) -> None:
    """Copy LES config files alongside steady ones with .les suffix so Allrun can swap."""
    for fname in ("controlDict", "fvSchemes", "fvSolution"):
        src = LES_FILES / fname
        if src.exists():
            shutil.copy2(src, case_dir / "system" / f"{fname}.les")
    tp_src = LES_FILES / "turbulenceProperties"
    if tp_src.exists():
        shutil.copy2(tp_src, case_dir / "constant" / "turbulenceProperties.les")


def _set_value(content: str, key: str, value: str) -> str:
    """Replace 'key   oldvalue;' pattern in OpenFOAM dict content."""
    pattern = rf"({re.escape(key)}\s+)([^;]+)(;)"
    return re.sub(pattern, rf"\g<1>{value}\3", content, count=1)
