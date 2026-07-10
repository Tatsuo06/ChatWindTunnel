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
# kOmegaSST-based DES configs for restarting a converged steady case as LES
LES_FILES_KOSST = settings.TEMPLATES_DIR / "lesFiles_kOmegaSST"
LES_RESTART_MODELS = ("kOmegaSSTDDES", "kOmegaSSTIDDES")
# Buoyant gas dispersion (Boussinesq, T = normalized concentration)
DISPERSION_TEMPLATE = settings.TEMPLATES_DIR / "dispersion"
# Compressible 2-species gas-dispersion LES restart (rhoReactingBuoyantFoam)
GAS_FILES = settings.TEMPLATES_DIR / "gasFiles"

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

    runApplication foamToVTK -no-internal -latestTime -fields '()'
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
    # Remember Phase 1's final iteration index (its time directory) before the
    # swap, so it can be dropped after reconstruction and post-processing then
    # targets the LES end time rather than this leftover RAS solution.
    PHASE1_TIME=$(ls processor0 | grep -E "^[0-9]+(\.[0-9]+)?$" | sort -g | tail -1)

    # Swap steady-state solution as LES initial condition (use the latest
    # written time directory rather than a hardcoded value, since Phase 1's
    # endTime is configurable)
    ls -d processor* | xargs -I {} sh -c 'latest=$(ls {} | grep -E "^[0-9]+(\.[0-9]+)?$" | sort -g | tail -1); cp -r {}/$latest {}/0_les; rm -rf {}/0; mv {}/0_les {}/0; rm -rf {}/0/uniform'

    # Replace config files with LES versions
    cp -f system/controlDict.les   system/controlDict
    cp -f system/fvSchemes.les     system/fvSchemes
    cp -f system/fvSolution.les    system/fvSolution
    cp -f constant/turbulenceProperties.les  constant/turbulenceProperties

    (
        while true; do
            ps aux | grep "[p]isoFoam" | awk '{sum+=$6} END {if(sum>0) print sum}' >> log.mem_piso.tmp
            sleep 5
        done
    ) &
    _MON_PISO=$!
    runParallel pisoFoam
    kill $_MON_PISO 2>/dev/null
    if [ -s log.mem_piso.tmp ]; then
        peak=$(sort -n log.mem_piso.tmp | tail -1)
        echo "Peak RSS (all pisoFoam processes): ${peak} kB" > log.mem_piso
    fi
    rm -f log.mem_piso.tmp

    runApplication reconstructParMesh -constant
    runApplication reconstructPar

    # Drop the leftover Phase-1 (RAS) time directory so -latestTime post-processing
    # targets the LES end time. The RAS solution survives as the LES time-0 field.
    rm -rf "./${PHASE1_TIME}" processor*/"${PHASE1_TIME}"

    # Surface mesh (Mesh tab) and y=0 cutting plane of the LES field (Cutting-plane tab)
    runApplication foamToVTK -no-internal -latestTime -fields '()'
    runApplication postProcess -func cuttingPlane -latestTime
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

_ALLRUN_GAS_RESTART = textwrap.dedent("""\
    #!/bin/sh
    cd "${0%/*}" || exit
    . ${WM_PROJECT_DIR:?}/bin/tools/RunFunctions

    # === Gas-dispersion LES restart from a finished aero LES solution ===
    # rhoReactingBuoyantFoam (2 species, chemistry off) is seeded from the
    # aero LES instantaneous field: U/k/omega/nut carry over; kinematic p and
    # volumetric phi are replaced by uniform absolute-pressure fields.
    GAS_PREV=$(ls processor0 | grep -E "^[0-9]+(\\.[0-9]+)?$" | sort -g | tail -1)
    if [ -z "$GAS_PREV" ] || [ "$GAS_PREV" = "0" ]; then
        echo "ERROR: no written LES solution in processor dirs (latest time: '${GAS_PREV}')" >&2
        exit 1
    fi

    # The gas stage reuses the same time range as the aero LES, so archive the
    # aero postProcessing before the function objects start writing
    [ -d postProcessing ] && mv postProcessing postProcessing_aero

    # Aero LES solution -> gas time 0, drop all other time dirs and the fields
    # that don't survive the incompressible->compressible transition
    ls -d processor* | xargs -I {} sh -c 'cp -r {}/'"$GAS_PREV"' {}/0_gas; for t in $(ls {} | grep -E "^[0-9]+(\\.[0-9]+)?$"); do rm -rf {}/$t; done; mv {}/0_gas {}/0; rm -rf {}/0/uniform; rm -f {}/0/p {}/0/phi'

    # Overlay the uniform compressible fields (T, absolute p, p_rgh, species, alphat)
    for f in gas0/*; do
        ls -d processor* | xargs -I {} cp "$f" {}/0/
    done

    # Drop reconstructed time dirs at the case root for the same reason
    for t in $(ls . | grep -E "^[0-9]+(\\.[0-9]+)?$"); do rm -rf "./$t"; done

    # Swap in the gas-stage configs prepared by build_gas_les_restart_case
    cp -f system/controlDict.gas   system/controlDict
    cp -f system/fvSchemes.gas     system/fvSchemes
    cp -f system/fvSolution.gas    system/fvSolution
    cp -f constant/turbulenceProperties.gas  constant/turbulenceProperties

    # Build the gas-source cellSet (sphere of fluid cells) when a radius was set;
    # the point-source case has no topoSetDict and skips this. Remove any inherited
    # log.topoSet first (from the parent's meshing) or runParallel would skip it.
    [ -f system/topoSetDict ] && { rm -f log.topoSet; runParallel topoSet; }

    rm -f log.rhoReactingBuoyantFoam log.reconstructParMesh log.reconstructPar log.foamToVTK log.postProcess

    (
        while true; do
            ps aux | grep "[r]hoReactingBuoyantFoam" | awk '{sum+=$6} END {if(sum>0) print sum}' >> log.mem_gas.tmp
            sleep 5
        done
    ) &
    _MON_GAS=$!
    runParallel rhoReactingBuoyantFoam
    kill $_MON_GAS 2>/dev/null
    if [ -s log.mem_gas.tmp ]; then
        peak=$(sort -n log.mem_gas.tmp | tail -1)
        echo "Peak RSS (all rhoReactingBuoyantFoam processes): ${peak} kB" > log.mem_gas
    fi
    rm -f log.mem_gas.tmp

    runApplication reconstructParMesh -constant
    runApplication reconstructPar

    # Surface mesh (Mesh tab) and y=0 cutting plane of the gas field (Visualization tab)
    runApplication foamToVTK -no-internal -latestTime -fields '()'
    runApplication postProcess -func cuttingPlane -latestTime
""")


# topoSetDict for distributing the gas source over a sphere of cells around the
# release point (only fluid cells are selected — clipped where the sphere overlaps
# the body; a hemisphere if the point sits on a surface, a full sphere if it is
# clear of it). Written by build_gas_les_restart_case when source_radius > 0.
_TOPOSET_SPHERE = textwrap.dedent("""\
    FoamFile
    {{
        version     2.0;
        format      ascii;
        class       dictionary;
        object      topoSetDict;
    }}
    actions
    (
        {{
            name    gasSourceCells;
            type    cellSet;
            action  new;
            source  sphereToCell;
            origin  ({ox} {oy} {oz});
            radius  {radius};
        }}
    );
""")

_ALLRUN_LES_RESTART = textwrap.dedent("""\
    #!/bin/sh
    cd "${0%/*}" || exit
    . ${WM_PROJECT_DIR:?}/bin/tools/RunFunctions

    # === LES restart from a converged steady (kOmegaSST) solution ===
    # The steady case's decomposed fields become the LES initial condition;
    # no separate RAS phase is needed (kOmegaSST DES-family models use the
    # same k/omega/nut fields the steady run produced).
    PHASE1_TIME=$(ls processor0 | grep -E "^[0-9]+(\\.[0-9]+)?$" | sort -g | tail -1)
    if [ -z "$PHASE1_TIME" ] || [ "$PHASE1_TIME" = "0" ]; then
        echo "ERROR: no written steady solution in processor dirs (latest time: '${PHASE1_TIME}')" >&2
        exit 1
    fi

    # Steady solution -> LES time 0, then drop all other steady time dirs so
    # reconstructPar only picks up LES output
    ls -d processor* | xargs -I {} sh -c 'cp -r {}/'"$PHASE1_TIME"' {}/0_les; for t in $(ls {} | grep -E "^[0-9]+(\\.[0-9]+)?$"); do rm -rf {}/$t; done; mv {}/0_les {}/0; rm -rf {}/0/uniform'

    # Drop reconstructed steady time dirs at the case root for the same reason
    # (also keeps -latestTime post-processing pointed at the LES end time)
    for t in $(ls . | grep -E "^[0-9]+(\\.[0-9]+)?$"); do rm -rf "./$t"; done

    # Swap in the LES configs prepared by build_les_restart_case
    cp -f system/controlDict.les   system/controlDict
    cp -f system/fvSchemes.les     system/fvSchemes
    cp -f system/fvSolution.les    system/fvSolution
    cp -f constant/turbulenceProperties.les  constant/turbulenceProperties

    # The steady run already wrote these logs; remove them so runApplication
    # re-runs the corresponding post-processing steps for the LES result
    rm -f log.pisoFoam log.reconstructParMesh log.reconstructPar log.foamToVTK log.postProcess

    (
        while true; do
            ps aux | grep "[p]isoFoam" | awk '{sum+=$6} END {if(sum>0) print sum}' >> log.mem_piso.tmp
            sleep 5
        done
    ) &
    _MON_PISO=$!
    runParallel pisoFoam
    kill $_MON_PISO 2>/dev/null
    if [ -s log.mem_piso.tmp ]; then
        peak=$(sort -n log.mem_piso.tmp | tail -1)
        echo "Peak RSS (all pisoFoam processes): ${peak} kB" > log.mem_piso
    fi
    rm -f log.mem_piso.tmp

    runApplication reconstructParMesh -constant
    runApplication reconstructPar

    # Surface mesh (Mesh tab) and y=0 cutting plane of the LES field (Visualization tab)
    runApplication foamToVTK -no-internal -latestTime -fields '()'
    runApplication postProcess -func cuttingPlane -latestTime
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

    is_dispersion = params.get("case_type") == "dispersion"
    if solver_type == SimulatorType.steady:
        template = DISPERSION_TEMPLATE if is_dispersion else STEADY_TEMPLATE
    else:
        template = UNSTEADY_TEMPLATE
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
    _write_transport_properties(case_dir, params)
    _write_control_dict(case_dir, solver_type, params)
    _write_decompose_par(case_dir, params)
    _write_block_mesh_dict(case_dir, params)
    _write_force_coeffs(case_dir, params)
    _write_snappy_hex_mesh(case_dir, params)
    _write_surface_feature_extract(case_dir)
    _write_streamlines(case_dir, params, solver_type=solver_type)

    if solver_type == SimulatorType.steady and not is_dispersion:
        turb_model = params.get("turbulence_model", "kOmegaSST")
        if turb_model == "SpalartAllmaras":
            _apply_spalart_allmaras(case_dir)
        elif turb_model == "realizableKE":
            _apply_kepsilon(case_dir, "realizableKE")
        elif turb_model == "laminar":
            _apply_laminar(case_dir)

    if is_dispersion and solver_type == SimulatorType.steady:
        params = _write_dispersion_props(case_dir, params, target_stl)

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
    if is_dispersion and solver_type == SimulatorType.steady:
        # The memory monitor's ps-grep is solver-name-based; the label text is
        # free-form (parse_peak_memory only reads the kB number)
        script = (script
                  .replace('"[s]impleFoam"', '"[b]uoyantBoussinesqSimpleFoam"')
                  .replace("all simpleFoam processes", "all buoyantBoussinesqSimpleFoam processes"))
    allrun.write_text(script)
    allrun.chmod(0o755)

    # For LES: copy LES config files alongside steady files so Allrun can swap them
    if solver_type == SimulatorType.unsteady:
        _prepare_les_files(case_dir, params)

    return case_dir


_TIME_DIR_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def copy_case_for_restart(parent_dir: Path, child_dir: Path) -> Path:
    """Copy a parent case directory into a fresh child directory for restart.

    The child reuses the parent's mesh + converged solution, but those live in
    the decomposed ``processor*`` directories (seeded separately — remotely on
    the cluster, locally for the local runner). We copy only the lightweight
    case definition (system/, constant dicts, 0.orig/, STL, case_params.json,
    Allrun) and the solver logs (so the phase-aware convergence display keeps
    working). Everything a restart does NOT need is skipped:

      - The parent's postProcessing: the child produces its OWN force
        coefficients / cutting planes / streamlines. Inheriting the parent's
        would pollute the child's plots (e.g. a steady parent's iteration-axis
        forceCoeffs showing up on an unsteady child).
      - Decomposed / reconstructed field data: processor*, VTK, polyMesh,
        root-level numeric time dirs (regenerated by the child's own run).
      - Meshing-only artifacts (a restart never re-meshes): the large
        extendedFeatureEdgeMesh (hundreds of MB), *.eMesh, and *.obj CAD.

    This keeps the local copy + upload small; the mesh comes from the remote
    processor* seed. The numeric-time-dir skip is restricted to the case ROOT.
    """
    parent_root = parent_dir.resolve()

    def _ignore(dir_path: str, names: list[str]) -> list[str]:
        at_root = Path(dir_path).resolve() == parent_root
        skip = []
        for name in names:
            if name in ("polyMesh", "VTK", "extendedFeatureEdgeMesh"):
                skip.append(name)
            elif at_root and name in ("postProcessing", "postProcessing_aero"):
                skip.append(name)
            elif name.endswith((".eMesh", ".obj")):
                skip.append(name)
            elif name.startswith("processor") and name[len("processor"):].isdigit():
                skip.append(name)
            elif at_root and name != "0.orig" and _TIME_DIR_RE.match(name) \
                    and (Path(dir_path) / name).is_dir():
                # root-level reconstructed time dir (e.g. "0", "1000", "0.7")
                skip.append(name)
        return skip

    if child_dir.exists():
        shutil.rmtree(child_dir)
    shutil.copytree(parent_dir, child_dir, ignore=_ignore, symlinks=True)
    return child_dir


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


def build_les_restart_case(case_dir: Path, params: dict) -> Path:
    """Convert a finished steady (kOmegaSST) case for an LES restart.

    Prepares kOmegaSSTDDES/IDDES configs as .les-suffixed files and an Allrun
    that seeds the LES from the steady solution (no separate RAS phase — the
    DES-family models reuse the steady run's k/omega/nut fields directly).
    """
    les_model = params.get("les_model", "kOmegaSSTDDES")
    if les_model not in LES_RESTART_MODELS:
        raise ValueError(f"les_model must be one of {LES_RESTART_MODELS}, got {les_model!r}")

    # Phase 2 configs (.les files the Allrun swaps in)
    _write_les_control_dict(case_dir, params, src_dir=LES_FILES_KOSST)
    for fname in ("fvSchemes", "fvSolution"):
        shutil.copy2(LES_FILES_KOSST / fname, case_dir / "system" / f"{fname}.les")
    tp_src = LES_FILES_KOSST / ("turbulenceProperties.IDDES" if les_model == "kOmegaSSTIDDES"
                                else "turbulenceProperties")
    tp_content = _set_value(tp_src.read_text(), "LESModel", les_model)
    (case_dir / "constant" / "turbulenceProperties.les").write_text(tp_content)

    # Animation-frame function objects (timeStep control)
    _write_anim_cutting_plane(case_dir, params)
    _write_streamlines(case_dir, params, solver_type=SimulatorType.unsteady)

    # Clear steady-time function-object output: the static cutting-plane and
    # streamline endpoints pick the numerically latest time directory, and
    # leftover steady iterations (e.g. 1000) would shadow the LES times (≤ ~1 s)
    for sub in ("cuttingPlane", "sets/streamLines", "sets/streamLinesBack"):
        d = case_dir / "postProcessing" / sub
        if d.exists():
            shutil.rmtree(d)

    # The animation time filter and the progress endpoint read LES parameters
    # from case_params.json — merge the restart values in
    params_file = case_dir / "case_params.json"
    merged = json.loads(params_file.read_text()) if params_file.exists() else {}
    for key in ("les_end_time", "les_delta_t", "les_anim_interval", "les_model"):
        if key in params:
            merged[key] = params[key]
    params_file.write_text(json.dumps(merged, indent=2))

    allrun = case_dir / "Allrun"
    allrun.write_text(_ALLRUN_LES_RESTART)
    allrun.chmod(0o755)

    return case_dir


def build_gas_les_restart_case(case_dir: Path, params: dict) -> Path:
    """Convert a finished aero LES case for a gas-dispersion LES restart.

    Runs rhoReactingBuoyantFoam (compressible, 2 species air/GAS, chemistry
    off) seeded from the aero LES instantaneous field: U/k/omega/nut carry
    over; kinematic p and volumetric phi are discarded and replaced by
    uniform absolute-pressure fields (pressure redevelops in a few steps
    while the turbulent velocity field is preserved). Buoyancy comes from
    the GAS molecular weight = gas_density_ratio x air.
    """
    gas_model = params.get("gas_model") or params.get("les_model", "kOmegaSSTDDES")
    if gas_model not in LES_RESTART_MODELS:
        raise ValueError(f"gas_model must be one of {LES_RESTART_MODELS}, got {gas_model!r}")

    gas_end = float(params.get("gas_end_time", 0.7))
    gas_dt = float(params.get("gas_delta_t", 1e-4))
    ratio = float(params.get("gas_density_ratio", 0.07))
    max_co = float(params.get("gas_max_co", 1.5))
    n_steps = max(1, round(gas_end / gas_dt))
    # Phase-time semantics for the shared writers (streamLines/cuttingPlane
    # intervals, animation t_max): the gas stage is now "the" LES stage
    stage_params = {**params, "les_end_time": gas_end, "les_delta_t": gas_dt}

    # --- swapped-in solver configs (.gas suffix; Allrun activates them) ---
    ctrl = (GAS_FILES / "controlDict").read_text()
    ctrl = _set_value(ctrl, "endTime", str(gas_end))
    ctrl = _set_value(ctrl, "deltaT", str(gas_dt))
    # Courant-adaptive stepping: cap dt at the requested value, shrink on demand;
    # maxCo controls how aggressively (higher = faster but less stable)
    ctrl = _set_value(ctrl, "maxDeltaT", str(gas_dt))
    ctrl = _set_value(ctrl, "maxCo", str(max_co))
    ctrl = _set_value(ctrl, "writeInterval", str(min(1000, n_steps)))
    (case_dir / "system" / "controlDict.gas").write_text(ctrl)
    for fname in ("fvSchemes", "fvSolution"):
        shutil.copy2(GAS_FILES / fname, case_dir / "system" / f"{fname}.gas")
    tp_src = GAS_FILES / ("turbulenceProperties.IDDES" if gas_model == "kOmegaSSTIDDES"
                          else "turbulenceProperties")
    tp = _set_value(tp_src.read_text(), "LESModel", gas_model)
    (case_dir / "constant" / "turbulenceProperties.gas").write_text(tp)

    # Compressible force coefficients (activated via controlDict.gas' #include
    # "forceCoeffs"). rho=rho integrates real forces; rhoInf = far-field air
    # density normalises Cd/Cl to match the incompressible aero LES convention,
    # so the two runs are directly comparable. Overwrites the copied aero one.
    fc = (GAS_FILES / "forceCoeffs").read_text()
    # aref/lref/cofr must come from the parent's AUTO-COMPUTED values, which live
    # in the copied case_params.json — the DB params only hold user/default
    # values, so reading from `params` would fall back to the motorBike defaults
    # and make the gas coefficients non-comparable to the aero (incompressible) run.
    _cp_file = case_dir / "case_params.json"
    _cp = json.loads(_cp_file.read_text()) if _cp_file.exists() else {}
    velocity = _cp.get("velocity_mps", params.get("velocity_mps", 20.0))
    aref = _cp.get("aref", params.get("aref", 0.75))
    lref = _cp.get("lref", params.get("lref", 1.42))
    cofr = _cp.get("cofr", params.get("cofr", [0.72, 0.0, 0.0]))
    rho_air = round(1.0e5 * 0.02896 / (8.314 * 300.0), 4)  # p*M/(R*T) at gas init state
    fc = _set_value(fc, "magUInf", str(velocity))
    fc = _set_value(fc, "Aref", str(aref))
    fc = _set_value(fc, "lRef", str(lref))
    fc = _set_value(fc, "CofR", f"({cofr[0]} {cofr[1]} {cofr[2]})")
    fc = _set_value(fc, "rhoInf", str(rho_air))
    (case_dir / "system" / "forceCoeffs").write_text(fc)

    # --- new constant files (only read by the gas solver) ---
    for fname in ("thermophysicalProperties", "reactions", "chemistryProperties",
                  "combustionProperties", "g"):
        shutil.copy2(GAS_FILES / fname, case_dir / "constant" / fname)
    mol_weight = round(28.96 * ratio, 4)
    thermo = (GAS_FILES / "thermo.compressibleGas").read_text()
    thermo = thermo.replace("molWeight       2.03;", f"molWeight       {mol_weight};")
    (case_dir / "constant" / "thermo.compressibleGas").write_text(thermo)

    # Release source: mass + species injected together (mass-consistent)
    rotated_stl = case_dir / "constant" / "triSurface" / "motorBike.stl"
    source = params.get("source_position") or _auto_source_position(rotated_stl)
    rate = float(params.get("source_rate", 1.0))
    radius = float(params.get("source_radius", 0.0) or 0.0)
    start = float(params.get("gas_source_start_time", 0.0) or 0.0)
    stop = float(params.get("gas_source_stop_time", 0.0) or 0.0)
    # Emission window end: an explicit stop within (start, gas_end], else run to gas_end
    emit_end = stop if (start < stop <= gas_end) else gas_end
    fv = (GAS_FILES / "fvOptions").read_text()
    fv = fv.replace("(0.0 0.0 1.0)", f"({source[0]} {source[1]} {source[2]})")
    fv = fv.replace("rho         (1.0 0);", f"rho         ({rate} 0);")
    fv = fv.replace("GAS         (1.0 0);", f"GAS         ({rate} 0);")
    # Distribute the source over the fluid cells within `radius` of the release
    # point (a sphere, clipped where it overlaps the body) instead of a single
    # cell, so the injection is not a point singularity. topoSet builds the
    # cellSet at run time (Allrun runs it when system/topoSetDict exists).
    # radius 0 = single-cell point source.
    topo = case_dir / "system" / "topoSetDict"
    if topo.exists():
        topo.unlink()
    if radius > 0:
        fv = fv.replace(
            "    selectionMode   points;\n\n    points\n    (\n"
            f"        ({source[0]} {source[1]} {source[2]})\n    );\n",
            "    selectionMode   cellSet;\n    cellSet         gasSourceCells;\n",
        )
        topo.write_text(_TOPOSET_SPHERE.format(
            ox=source[0], oy=source[1], oz=source[2], radius=radius))
    # Timed emission: gate the source with timeStart/duration (cellSetOption
    # base) so the LES develops turbulence gas-free until `start`, emits, then
    # stops at `stop` (emit_end). Only inject when a non-trivial window is set.
    if start > 0 or emit_end < gas_end:
        duration = round(max(gas_dt, emit_end - start), 9)
        fv = fv.replace(
            "    active          yes;\n",
            f"    active          yes;\n    timeStart       {start};\n    duration        {duration};\n",
        )
    (case_dir / "constant" / "fvOptions").write_text(fv)

    # --- uniform initial fields overlaid onto each processor's time 0 ---
    gas0 = case_dir / "gas0"
    if gas0.exists():
        shutil.rmtree(gas0)
    shutil.copytree(GAS_FILES / "0field", gas0)

    # Animation frames: cuttingPlane samples ( p U T GAS ), streamLines timeStep
    _write_anim_cutting_plane(case_dir, stage_params)
    cp_path = case_dir / "system" / "cuttingPlane"
    cp_path.write_text(cp_path.read_text().replace("fields          ( p U );",
                                                   "fields          ( p U T GAS );"))
    _write_streamlines(case_dir, stage_params, solver_type=SimulatorType.unsteady)

    # Fresh postProcessing for the gas stage (the aero LES output is archived
    # by the Allrun on the machine that runs the job); locally drop the frame
    # dirs so latest-time pickers can't mix aero and gas times
    for sub in ("cuttingPlane", "sets/streamLines", "sets/streamLinesBack"):
        d = case_dir / "postProcessing" / sub
        if d.exists():
            shutil.rmtree(d)

    # case_params.json: gas parameters + resolved source (t_max filter reads les_end_time)
    params_file = case_dir / "case_params.json"
    merged = json.loads(params_file.read_text()) if params_file.exists() else {}
    merged.update({
        "gas_les": True,
        "gas_end_time": gas_end,
        "gas_delta_t": gas_dt,
        "gas_model": gas_model,
        "gas_density_ratio": ratio,
        "source_position": source,
        "source_rate": rate,
        "source_radius": radius,
        "gas_source_start_time": start,
        "gas_source_stop_time": stop,
        "gas_max_co": max_co,
        "les_end_time": gas_end,
        "les_delta_t": gas_dt,
    })
    params_file.write_text(json.dumps(merged, indent=2))

    allrun = case_dir / "Allrun"
    allrun.write_text(_ALLRUN_GAS_RESTART)
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
    turbulence_model = params.get("turbulence_model", "kOmegaSST")

    ic_path = case_dir / "0.orig" / "include" / "initialConditions"
    content = ic_path.read_text()
    content = _set_value(content, "flowVelocity", flow_velocity)

    if turbulence_model == "laminar":
        content = _set_value(content, "flowVelocity", flow_velocity)
        ic_path.write_text(content)
        return
    elif turbulence_model == "SpalartAllmaras":
        nu = params.get("nu", 1.5e-5)
        nu_tilda_inlet = round(3.0 * nu, 8)
        content = re.sub(r"turbulentKE\s+[^;]+;", f"nuTildaInlet         {nu_tilda_inlet:.6g};", content)
        content = re.sub(r"turbulentOmega\s+[^;]+;", "", content)
        content = re.sub(r"\n{3,}", "\n\n", content)
    elif turbulence_model == "realizableKE":
        turbulent_ke, _ = _turbulence_from_velocity(velocity, intensity, lref)
        turbulent_epsilon = _turbulence_epsilon_from_velocity(velocity, intensity, lref)
        content = _set_value(content, "turbulentKE", str(turbulent_ke))
        content = re.sub(r"turbulentOmega\s+[^;]+;", f"turbulentEpsilon     {turbulent_epsilon};", content)
    else:
        turbulent_ke, turbulent_omega = _turbulence_from_velocity(velocity, intensity, lref)
        content = _set_value(content, "turbulentKE", str(turbulent_ke))
        content = _set_value(content, "turbulentOmega", str(turbulent_omega))

    ic_path.write_text(content)


def _write_control_dict(case_dir: Path, solver_type: SimulatorType, params: dict) -> None:
    ctrl_path = case_dir / "system" / "controlDict"
    content = ctrl_path.read_text()
    if solver_type == SimulatorType.steady:
        content = _set_value(content, "endTime", str(int(params.get("end_time", 500))))
    elif solver_type == SimulatorType.unsteady:
        # Phase 1 (RAS) iteration count, before switching to LES in Phase 2.
        # writeInterval must match endTime: the only purpose of Phase 1's output
        # is to provide the final state as the LES initial condition — if nothing
        # is written (endTime not a multiple of the template's writeInterval),
        # the phase swap would silently fall back to the time-0 uniform field.
        end_time = int(params.get("end_time", 500))
        content = _set_value(content, "endTime", str(end_time))
        content = _set_value(content, "writeInterval", str(end_time))
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
    ({xmin} {ymin}  {zmin})
    ({xmax} {ymin}  {zmin})
    ({xmax}  {ymax}  {zmin})
    ({xmin}  {ymax}  {zmin})
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
    dom_zmin  = round(z0, 4)            # ground plane = STL z_min (body sits on floor)
    dom_zmax  = round(z0 + 2.5 * L, 2) # absolute top; span (2.5L) unchanged

    Lx   = dom_xmax - dom_xmin
    Lz   = dom_zmax - dom_zmin          # = 2.5L
    ny = max(8, round(nx * 2 * dom_yhalf / Lx))
    nz = max(8, round(nx * Lz           / Lx))

    # ---- seed point: upstream of body, on centreline, mid-domain height ----
    loc = [round((dom_xmin + x0) / 2, 2), 0.0, round((dom_zmin + dom_zmax) / 2, 2)]

    # ---- forceCoeffs reference values ----
    aref = round(W * H, 4)
    lref = round(L, 4)
    cofr = [round((x0 + x1) / 2, 4), round((y0 + y1) / 2, 4), round((z0 + z1) / 2, 4)]

    return {
        "domain_scale": 1,
        "domain_xmin":  dom_xmin,
        "domain_xmax":  dom_xmax,
        "domain_yhalf": dom_yhalf,
        "domain_zmin":  dom_zmin,
        "domain_zmax":  dom_zmax,
        "blockmesh_nx": nx,
        "blockmesh_ny": ny,
        "blockmesh_nz": nz,
        "location_in_mesh": loc,
        "aref": aref,
        "lref": lref,
        "cofr": cofr,
        "streamline_radius":   round(max(W, H) / 2 * 1.1, 3),
        "streamline_n_points": 50,
    }


def _auto_source_position(rotated_stl: Path) -> list:
    """Default release point: top centre of the rotated geometry, slightly
    above the surface so the source cells sit in the fluid region."""
    from stl import mesh as stl_mesh

    m = stl_mesh.Mesh.from_file(str(rotated_stl))
    pts = m.vectors.reshape(-1, 3)
    x0, x1 = float(pts[:, 0].min()), float(pts[:, 0].max())
    z1 = float(pts[:, 2].max())
    dz = z1 - float(pts[:, 2].min())
    return [round((x0 + x1) / 2, 3), 0.0, round(z1 + 0.05 * max(dz, 0.1), 3)]


def _write_dispersion_props(case_dir: Path, params: dict, rotated_stl: Path) -> dict:
    """Write gas-dispersion settings: buoyancy beta and the release source.

    beta = 1 - rho_gas/rho_air maps the normalized concentration (T field)
    to Boussinesq buoyancy: ratio > 1 (heavy gas) gives beta < 0 (sinks).
    Returns params with the resolved source_position (for case_params.json).
    """
    from stl import mesh as stl_mesh

    ratio = float(params.get("gas_density_ratio", 0.07))
    beta = round(1.0 - ratio, 6)
    tp_path = case_dir / "constant" / "transportProperties"
    tp_path.write_text(_set_value(tp_path.read_text(), "beta", str(beta)))

    source = params.get("source_position") or _auto_source_position(rotated_stl)
    rate = float(params.get("source_rate", 1.0))
    fv_path = case_dir / "constant" / "fvOptions"
    content = fv_path.read_text()
    content = content.replace("(0.0 0.0 1.0)", f"({source[0]} {source[1]} {source[2]})")
    content = content.replace("T           (1.0 0);", f"T           ({rate} 0);")
    fv_path.write_text(content)

    return {**params, "source_position": source}


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
        round(z0, 4),  # refbox floor = geometry bottom = domain floor (no padding below)
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


def _upstream_grid_seeds(center: list, radius: float, x_upstream: float,
                          n_grid: int = 5) -> list[list[float]]:
    """Generate a uniform grid of seed points on a y-z plane upstream of the geometry."""
    pts = []
    y_vals = [round(center[1] + radius * (-1 + 2 * i / (n_grid - 1)), 4) for i in range(n_grid)]
    z_max = round(center[2] + radius, 4)
    z_min = round(max(0.01, center[2] - radius), 4)
    z_vals = [round(z_min + (z_max - z_min) * i / (n_grid - 1), 4) for i in range(n_grid)]
    for y in y_vals:
        for z in z_vals:
            pts.append([round(x_upstream, 4), y, z])
    return pts


_NUTILDA_FIELD = """\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2206                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      nuTilda;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

#include        "include/initialConditions"

dimensions      [0 2 -1 0 0 0 0];

internalField   uniform $nuTildaInlet;

boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"

    #include "include/fixedInlet"

    outlet
    {{
        type            zeroGradient;
    }}

    lowerWall
    {{
        type            fixedValue;
        value           uniform 0;
    }}

    motorBikeGroup
    {{
        type            fixedValue;
        value           uniform 0;
    }}

    #include "include/frontBackUpperPatches"
}}


// ************************************************************************* //
"""


def _apply_spalart_allmaras(case_dir: Path) -> None:
    """Convert a kOmegaSST case directory to use SpalartAllmaras turbulence model.

    Changes:
    - constant/turbulenceProperties: RASModel kOmegaSST → SpalartAllmaras
    - 0.orig/nuTilda: created with fixedValue 0 wall BCs
    - 0.orig/k, 0.orig/omega: removed
    - system/fvSchemes: div(phi,k) + div(phi,omega) → div(phi,nuTilda)
    - system/fvSolution: k/omega solvers/relaxation → nuTilda
    """
    # turbulenceProperties
    tp = case_dir / "constant" / "turbulenceProperties"
    tp.write_text(tp.read_text().replace("kOmegaSST", "SpalartAllmaras", 1))

    # 0.orig/nuTilda
    (case_dir / "0.orig" / "nuTilda").write_text(_NUTILDA_FIELD)

    # Remove k and omega fields
    for fname in ("k", "omega"):
        f = case_dir / "0.orig" / fname
        if f.exists():
            f.unlink()

    # fvSchemes: replace div(phi,k) and div(phi,omega) with div(phi,nuTilda)
    fvs_path = case_dir / "system" / "fvSchemes"
    fvs = fvs_path.read_text()
    fvs = re.sub(r"[ \t]*div\(phi,k\)\s+[^\n]+\n", "", fvs)
    fvs = re.sub(r"[ \t]*div\(phi,omega\)\s+[^\n]+\n", "    div(phi,nuTilda) $turbulence;\n", fvs)
    fvs_path.write_text(fvs)

    # fvSolution: replace k/omega solvers and relaxation with nuTilda
    fvsol_path = case_dir / "system" / "fvSolution"
    fvsol = fvsol_path.read_text()
    # Remove k solver block
    fvsol = re.sub(r"\n    k\s*\{[^}]+\}", "", fvsol)
    # Replace omega solver block with nuTilda
    fvsol = re.sub(r"(\n    )omega(\s*\{)", r"\1nuTilda\2", fvsol)
    # Replace relaxation entries
    fvsol = re.sub(r"[ \t]*k\s+[0-9.]+;\n", "", fvsol)
    fvsol = re.sub(r"([ \t]*)omega(\s+[0-9.]+;)", r"\1nuTilda\2", fvsol)
    fvsol_path.write_text(fvsol)


_EPSILON_FIELD = """\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2206                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      epsilon;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

#include        "include/initialConditions"

dimensions      [0 2 -3 0 0 0 0];

internalField   uniform $turbulentEpsilon;

boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"

    #include "include/fixedInlet"

    outlet
    {{
        type            inletOutlet;
        inletValue      $internalField;
        value           $internalField;
    }}

    lowerWall
    {{
        type            epsilonWallFunction;
        value           $internalField;
    }}

    motorBikeGroup
    {{
        type            epsilonWallFunction;
        value           $internalField;
    }}

    #include "include/frontBackUpperPatches"
}}


// ************************************************************************* //
"""


def _turbulence_epsilon_from_velocity(
    velocity: float, intensity: float = 0.05, lref: float = 1.0
) -> float:
    """Compute epsilon from velocity, intensity and reference length.
    epsilon = Cmu^(3/4) * k^(3/2) / L  where L = 0.07 * lref.
    """
    k = 1.5 * (velocity * intensity) ** 2
    L = 0.07 * lref
    return round(0.09 ** 0.75 * k ** 1.5 / L, 6)


def _apply_kepsilon(case_dir: Path, model_name: str = "realizableKE") -> None:
    """Convert a kOmegaSST case directory to a k-epsilon variant (realizableKE).

    Changes:
    - constant/turbulenceProperties: kOmegaSST → model_name
    - 0.orig/epsilon: created with epsilonWallFunction at walls
    - 0.orig/omega: removed
    - system/fvSchemes: div(phi,omega) → div(phi,epsilon)
    - system/fvSolution: omega solver/relaxation → epsilon
    """
    # turbulenceProperties
    tp = case_dir / "constant" / "turbulenceProperties"
    tp.write_text(tp.read_text().replace("kOmegaSST", model_name, 1))

    # 0.orig/epsilon
    (case_dir / "0.orig" / "epsilon").write_text(_EPSILON_FIELD)

    # Remove omega field
    omega_f = case_dir / "0.orig" / "omega"
    if omega_f.exists():
        omega_f.unlink()

    # fvSchemes: replace div(phi,omega) with div(phi,epsilon)
    fvs_path = case_dir / "system" / "fvSchemes"
    fvs = fvs_path.read_text()
    fvs = re.sub(r"[ \t]*div\(phi,omega\)\s+[^\n]+\n", "    div(phi,epsilon) $turbulence;\n", fvs)
    fvs_path.write_text(fvs)

    # fvSolution: replace omega solver and relaxation with epsilon
    fvsol_path = case_dir / "system" / "fvSolution"
    fvsol = fvsol_path.read_text()
    fvsol = re.sub(r"(\n    )omega(\s*\{)", r"\1epsilon\2", fvsol)
    fvsol = re.sub(r"([ \t]*)omega(\s+[0-9.]+;)", r"\1epsilon\2", fvsol)
    fvsol_path.write_text(fvsol)


def _apply_laminar(case_dir: Path) -> None:
    """Convert a kOmegaSST case directory to pure laminar (no turbulence model).

    Changes:
    - constant/turbulenceProperties: simulationType RAS → laminar (drop RAS block)
    - 0.orig/k, 0.orig/omega, 0.orig/nut: removed
    - system/fvSchemes: remove div(phi,k) and div(phi,omega)
    - system/fvSolution: remove k/omega solvers and relaxation
    """
    # turbulenceProperties: replace entire RAS block with laminar
    tp = case_dir / "constant" / "turbulenceProperties"
    content = tp.read_text()
    content = re.sub(r"simulationType\s+\S+;", "simulationType  laminar;", content)
    content = re.sub(r"\nRAS\s*\{[^}]+\}", "", content, flags=re.DOTALL)
    content = re.sub(r"\n{3,}", "\n\n", content)
    tp.write_text(content)

    # Remove turbulence fields
    for fname in ("k", "omega", "nut"):
        f = case_dir / "0.orig" / fname
        if f.exists():
            f.unlink()

    # fvSchemes: remove div(phi,k) and div(phi,omega)
    fvs_path = case_dir / "system" / "fvSchemes"
    fvs = fvs_path.read_text()
    fvs = re.sub(r"[ \t]*div\(phi,k\)\s+[^\n]+\n", "", fvs)
    fvs = re.sub(r"[ \t]*div\(phi,omega\)\s+[^\n]+\n", "", fvs)
    fvs_path.write_text(fvs)

    # fvSolution: remove k/omega solvers and relaxation
    fvsol_path = case_dir / "system" / "fvSolution"
    fvsol = fvsol_path.read_text()
    fvsol = re.sub(r"\n    k\s*\{[^}]+\}", "", fvsol)
    fvsol = re.sub(r"\n    omega\s*\{[^}]+\}", "", fvsol)
    fvsol = re.sub(r"[ \t]*k\s+[0-9.]+;\n", "", fvsol)
    fvsol = re.sub(r"[ \t]*omega\s+[0-9.]+;\n", "", fvsol)
    fvsol_path.write_text(fvsol)



def _write_block_mesh_dict(case_dir: Path, params: dict) -> None:
    scale  = params.get("domain_scale",  1)
    xmin   = params.get("domain_xmin",  -5)
    xmax   = params.get("domain_xmax",  15)
    yhalf  = params.get("domain_yhalf",  4)
    zmin   = params.get("domain_zmin",   0)
    zmax   = params.get("domain_zmax",   8)
    nx     = int(params.get("blockmesh_nx", 20))
    ny     = int(params.get("blockmesh_ny",  8))
    nz     = int(params.get("blockmesh_nz",  8))
    (case_dir / "system" / "blockMeshDict").write_text(
        _BLOCK_MESH_TEMPLATE.format(
            scale=scale,
            xmin=xmin, xmax=xmax,
            ymin=-yhalf, ymax=yhalf,
            zmin=zmin, zmax=zmax,
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


def _write_transport_properties(case_dir: Path, params: dict) -> None:
    """Write kinematic viscosity nu to constant/transportProperties."""
    nu = params.get("nu", 1.5e-5)
    tp = case_dir / "constant" / "transportProperties"
    if not tp.exists():
        return
    tp.write_text(_set_value(tp.read_text(), "nu", f"{nu:.6g}"))


def _write_streamlines(case_dir: Path, params: dict, solver_type: SimulatorType | None = None) -> None:
    """Write system/streamLines with two function objects sharing sphere seeds.

    streamLines     — trackForward true  (downstream)
    streamLinesBack — trackForward false (upstream)
    Both use the same Fibonacci-sphere seed cloud around the geometry.

    For unsteady cases the write control switches to timeStep every
    les_anim_interval so Phase 2 produces animation frames.
    """
    sl_file = case_dir / "system" / "streamLines"
    if not sl_file.exists():
        return
    if solver_type == SimulatorType.unsteady:
        write_control_block = (f"writeControl    timeStep;\n"
                               f"    writeInterval   {_anim_interval(params)};")
    else:
        write_control_block = "writeControl    writeTime;"
    center   = params.get("streamline_center", [-1.0, 0.0, 0.5])
    radius   = params.get("streamline_radius", 0.5)
    n_points = int(params.get("streamline_n_points", 50))
    _tm = params.get("turbulence_model", "kOmegaSST")
    if _tm == "laminar":
        turb_field = None
    elif _tm == "SpalartAllmaras":
        turb_field = "nuTilda"
    elif _tm == "realizableKE":
        turb_field = "epsilon"
    else:
        turb_field = "k"

    pts = _sphere_seed_points(center, radius, n_points)
    pts_str = "\n        ".join(f"({p[0]} {p[1]} {p[2]})" for p in pts)

    seed_block = f"""\
    seedSampleSet
    {{
        type    cloud;
        axis    x;
        points  (
        {pts_str}
        );
    }}"""

    fields_str = "U"

    content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2206                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/

// Seeds: sphere center=({center[0]} {center[1]} {center[2]}) r={radius} n={n_points}

streamLines
{{
    libs            (fieldFunctionObjects);
    type            streamLine;
    {write_control_block}
    setFormat       vtk;
    trackForward    true;
    fields          ({fields_str});
    lifeTime        10000;
    nSubCycle       5;
    cloud           particleTracks;
{seed_block}
}}

streamLinesBack
{{
    libs            (fieldFunctionObjects);
    type            streamLine;
    {write_control_block}
    setFormat       vtk;
    trackForward    false;
    fields          ({fields_str});
    lifeTime        10000;
    nSubCycle       5;
    cloud           particleTracksBack;
{seed_block}
}}

// ************************************************************************* //
"""
    sl_file.write_text(content)


def _prepare_les_files(case_dir: Path, params: dict) -> None:
    """Copy LES config files alongside steady ones with .les suffix so Allrun can swap."""
    for fname in ("fvSchemes", "fvSolution"):
        src = LES_FILES / fname
        if src.exists():
            shutil.copy2(src, case_dir / "system" / f"{fname}.les")
    tp_src = LES_FILES / "turbulenceProperties"
    if tp_src.exists():
        shutil.copy2(tp_src, case_dir / "constant" / "turbulenceProperties.les")
    _write_les_control_dict(case_dir, params)

    _write_anim_cutting_plane(case_dir, params)


def _write_anim_cutting_plane(case_dir: Path, params: dict) -> None:
    """Install the timeStep-controlled cuttingPlane dict for LES animation frames.

    The function object runs during the LES phase (included from
    controlDict.les) and writes a yNormal surface every les_anim_interval
    time steps. Sourcing from the unsteady template also replaces a steady
    case's writeTime-controlled version on steady→LES restart.
    """
    src = UNSTEADY_TEMPLATE / "system" / "cuttingPlane"
    if not src.exists():
        return
    content = src.read_text()
    content = _set_value(content, "writeInterval", str(_anim_interval(params)))
    (case_dir / "system" / "cuttingPlane").write_text(content)


def _anim_interval(params: dict) -> int:
    """Phase 2 animation write interval in time steps, capped at the run length."""
    end_time = float(params.get("les_end_time", 0.7))
    delta_t = float(params.get("les_delta_t", 1e-4))
    n_steps = max(1, round(end_time / delta_t))
    return min(int(params.get("les_anim_interval", 100)), n_steps)


def _write_les_control_dict(case_dir: Path, params: dict, src_dir: Path = LES_FILES) -> None:
    """Write system/controlDict.les with Phase 2 (LES) endTime/deltaT from params."""
    src = src_dir / "controlDict"
    if not src.exists():
        return
    end_time = float(params.get("les_end_time", 0.7))
    delta_t = float(params.get("les_delta_t", 1e-4))
    # writeControl is timeStep-based; cap the template's interval (1000) at the
    # total step count so short runs still write at least their final state
    # (otherwise reconstructPar finds no times and the volume data is lost)
    n_steps = max(1, round(end_time / delta_t))
    write_interval = min(1000, n_steps)
    content = src.read_text()
    content = _set_value(content, "endTime", str(end_time))
    content = _set_value(content, "deltaT", str(delta_t))
    content = _set_value(content, "writeInterval", str(write_interval))
    (case_dir / "system" / "controlDict.les").write_text(content)


def _set_value(content: str, key: str, value: str) -> str:
    """Replace 'key   oldvalue;' pattern in OpenFOAM dict content."""
    pattern = rf"({re.escape(key)}\s+)([^;]+)(;)"
    return re.sub(pattern, rf"\g<1>{value}\3", content, count=1)
