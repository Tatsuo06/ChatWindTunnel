"""Tests for phase-aware parsing added to support unsteady (LES) cases.

STEADY cases must keep behaving exactly as before (single phase, Phase column
always 1); UNSTEADY cases must split Phase 1 (RAS/simpleFoam, iterations) from
Phase 2 (LES/pisoFoam, seconds) rather than concatenating incompatible Time axes.
"""
from pathlib import Path

from backend.db.models import SimulatorType
from backend.visualization.parsers import (
    parse_force_coefficients, phase_logs,
    PHASE1_LOGS, _first_existing_log,
)


def _write_coefficient_dat(path: Path, times: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Time Cd Cd(f) Cd(r) Cl Cl(f) Cl(r) CmPitch CmRoll CmYaw Cs Cs(f) Cs(r)"]
    for t in times:
        # columns: Time Cd Cd(f) Cd(r) Cl Cl(f) Cl(r) CmPitch CmRoll CmYaw Cs Cs(f) Cs(r)
        row = [t, 0.5, 0.25, 0.25, 0.1, 0.05, 0.05, 0.0, 0.0, 0.02, 0.0, 0.0, 0.0]
        lines.append(" ".join(str(v) for v in row))
    path.write_text("\n".join(lines) + "\n")


def test_phase_logs_steady(tmp_path):
    entries = phase_logs(tmp_path, SimulatorType.steady)
    assert len(entries) == 1
    assert entries[0]["log"] == tmp_path / "log.simpleFoam"
    assert entries[0]["unit"] == "iteration"


def test_phase_logs_unsteady(tmp_path):
    entries = phase_logs(tmp_path, SimulatorType.unsteady)
    assert [e["phase"] for e in entries] == [1, 2]
    assert entries[0]["log"] == tmp_path / "log.simpleFoam"
    assert entries[0]["unit"] == "iteration"
    assert entries[1]["log"] == tmp_path / "log.pisoFoam"
    assert entries[1]["unit"] == "s"


def test_parse_force_coefficients_steady_like_single_phase(tmp_path):
    # A steady-state case only ever appends monotonically increasing iteration counts.
    force_dir = tmp_path / "postProcessing" / "forceCoeffs1"
    _write_coefficient_dat(force_dir / "1" / "coefficient.dat", [1, 2, 3, 4, 5])

    df = parse_force_coefficients(tmp_path)

    assert not df.empty
    assert list(df["Phase"].unique()) == [1]


def test_parse_force_coefficients_unsteady_splits_phases_on_time_decrease(tmp_path):
    # Phase 1 (RAS) writes increasing iteration counts, then Phase 2 (LES) restarts
    # the same postProcessing/forceCoeffs1 folder with small physical-time values.
    force_dir = tmp_path / "postProcessing" / "forceCoeffs1"
    _write_coefficient_dat(force_dir / "1" / "coefficient.dat", [1, 2, 3, 4, 5])
    _write_coefficient_dat(force_dir / "2" / "coefficient.dat", [0.0001, 0.0002, 0.0003])

    df = parse_force_coefficients(tmp_path)

    assert not df.empty
    assert list(df["Phase"]) == [1, 1, 1, 1, 1, 2, 2, 2]


def test_parse_force_coefficients_empty_when_no_data(tmp_path):
    df = parse_force_coefficients(tmp_path)
    assert df.empty


def test_first_existing_log_prefers_canonical_and_falls_back(tmp_path):
    # Nothing exists -> canonical (first) name so legacy behavior is preserved
    assert _first_existing_log(tmp_path, PHASE1_LOGS) == tmp_path / "log.simpleFoam"
    # Dispersion solver log present -> resolved
    (tmp_path / "log.buoyantBoussinesqSimpleFoam").write_text("Time = 1\n")
    assert _first_existing_log(tmp_path, PHASE1_LOGS).name == "log.buoyantBoussinesqSimpleFoam"
    # Aero log wins when both exist (priority order)
    (tmp_path / "log.simpleFoam").write_text("Time = 1\n")
    assert _first_existing_log(tmp_path, PHASE1_LOGS).name == "log.simpleFoam"


def test_phase_logs_resolves_dispersion_solver(tmp_path):
    (tmp_path / "log.buoyantBoussinesqSimpleFoam").write_text("Time = 1\n")
    entries = phase_logs(tmp_path, SimulatorType.steady)
    assert entries[0]["log"].name == "log.buoyantBoussinesqSimpleFoam"
