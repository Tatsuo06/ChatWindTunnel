"""Parse OpenFOAM log files and postProcessing output."""
import re
from pathlib import Path

import pandas as pd


def parse_residuals(log_path: Path) -> pd.DataFrame:
    """Extract residual history from simpleFoam/pisoFoam log.

    Returns DataFrame with columns: Time, Ux, Uy, Uz, p, k, omega (where available).
    """
    pattern = re.compile(
        r"^Time = ([\d.eE+\-]+).*?"
        r"Solving for Ux.*?Initial residual = ([\d.eE+\-]+).*?"
        r"Solving for Uy.*?Initial residual = ([\d.eE+\-]+).*?"
        r"Solving for Uz.*?Initial residual = ([\d.eE+\-]+).*?"
        r"Solving for p.*?Initial residual = ([\d.eE+\-]+)",
        re.DOTALL | re.MULTILINE,
    )

    records = []
    text = log_path.read_text(errors="replace")

    # Simpler per-field extraction
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
        # File format: # Time Cd Cs Cl CmRoll CmPitch CmYaw
        df = pd.read_csv(dat, sep=r"\s+", comment="#", header=None)
        df = df.iloc[:, :4]
        df.columns = ["Time", "Cd", "Cs", "Cl"]
        dfs.append(df)

    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
