"""Parse OpenFOAM log files and postProcessing output."""
import re
from pathlib import Path

import pandas as pd


def parse_residuals(log_path: Path) -> pd.DataFrame:
    """Extract residual history, combining restart logs (log.X.1, log.X.2, …, log.X).

    Returns DataFrame with columns: Time, Ux, Uy, Uz, p, k, omega (where available).
    """
    log_stem = log_path.name
    numbered = sorted(
        [p for p in log_path.parent.glob(f"{log_stem}.*")
         if p.suffix.lstrip(".").isdigit()],
        key=lambda p: int(p.suffix.lstrip(".")),
    )
    dfs = [_parse_single_log(lp) for lp in [*numbered, log_path] if lp.exists()]
    dfs = [df for df in dfs if not df.empty]
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _parse_single_log(log_path: Path) -> pd.DataFrame:
    text = log_path.read_text(errors="replace")
    time_vals = re.findall(r"^Time = ([\d.eE+\-]+)", text, re.MULTILINE)
    fields = {}
    for field in ("Ux", "Uy", "Uz", "p", "k", "omega", "nuTilda"):
        hits = re.findall(
            rf"Solving for {field}.*?Initial residual = ([\d.eE+\-]+)",
            text,
        )
        if hits:
            fields[field] = [float(v) for v in hits]
    if not time_vals or not fields:
        return pd.DataFrame()
    n = min(len(time_vals), *(len(v) for v in fields.values()))
    df = pd.DataFrame({"Time": [float(t) for t in time_vals[:n]]})
    for f, vals in fields.items():
        df[f] = vals[:n]
    return df


def parse_mesh_info(case_dir: Path) -> dict:
    """Extract cell count and mesh quality from log.checkMesh.

    Returns dict with keys: cells, faces, points, max_non_ortho, avg_non_ortho, max_skewness.
    Returns empty dict if log not found.
    """
    log = case_dir / "log.checkMesh"
    if not log.exists():
        return {}
    text = log.read_text(errors="replace")

    info: dict = {}
    for key, pattern in (
        ("cells",   r"cells:\s+([\d,]+)"),
        ("faces",   r"faces:\s+([\d,]+)"),
        ("points",  r"points:\s+([\d,]+)"),
    ):
        m = re.search(pattern, text)
        if m:
            info[key] = int(m.group(1).replace(",", ""))

    m = re.search(r"Mesh non-orthogonality Max:\s+([\d.]+)\s+average:\s+([\d.]+)", text)
    if m:
        info["max_non_ortho"] = float(m.group(1))
        info["avg_non_ortho"] = float(m.group(2))

    m = re.search(r"Max skewness\s*=\s*([\d.]+)", text)
    if m:
        info["max_skewness"] = float(m.group(1))

    return info


def parse_force_coefficients(postproc_dir: Path) -> pd.DataFrame:
    """Read forceCoeffs postProcessing data.

    Looks for: postProcessing/forceCoeffs1/<time>/coefficient.dat
    Returns DataFrame with columns: Time, Cd, Cl, Cm.
    """
    force_dir = postproc_dir / "postProcessing" / "forceCoeffs1"
    if not force_dir.exists():
        return pd.DataFrame()

    time_dirs = sorted(force_dir.iterdir(), key=lambda p: float(p.name) if p.name.replace(".", "").isdigit() else 0)
    dfs = []
    for td in time_dirs:
        dat = td / "coefficient.dat"
        if not dat.exists():
            continue
        # Columns: Time Cd Cd(f) Cd(r) Cl Cl(f) Cl(r) CmPitch CmRoll CmYaw Cs Cs(f) Cs(r)
        df = pd.read_csv(dat, sep=r"\s+", comment="#", header=None)
        df = df.iloc[:, [0, 1, 4, 9, 10]]
        df.columns = ["Time", "Cx", "Cz", "CmYaw", "Cy"]
        dfs.append(df)

    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _parse_mem_log(path: Path) -> int | None:
    if not path.exists():
        return None
    m = re.search(r"(\d+)\s+kB", path.read_text())
    return int(m.group(1)) if m else None


def parse_peak_memory(case_dir: Path) -> dict:
    """Return peak RSS in kB for simpleFoam and snappyHexMesh."""
    return {
        "simpleFoam":    _parse_mem_log(case_dir / "log.mem_monitor"),
        "snappyHexMesh": _parse_mem_log(case_dir / "log.mem_snappy"),
    }


def parse_clock_time(case_dir: Path) -> float | None:
    """Extract final ClockTime (seconds) from solver log."""
    log = next(case_dir.glob("log.*Foam"), None) if case_dir and case_dir.exists() else None
    if not log:
        return None
    matches = re.findall(r"ClockTime\s*=\s*([\d.]+)\s*s", log.read_text(errors="replace"))
    return float(matches[-1]) if matches else None
