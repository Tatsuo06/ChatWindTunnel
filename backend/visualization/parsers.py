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


def parse_solver_diagnostics(case_dir: Path) -> dict:
    """Extract solver health diagnostics from the solver log.

    Returns dict with:
      log_name, steps_completed, bounding_warnings (list), fatal_errors (list),
      p_residual_overflow (bool), diverged (bool), summary (str).
    """
    if not case_dir or not case_dir.exists():
        return {}
    # Prefer the main solver log (simpleFoam / pisoFoam) over potentialFoam
    log = None
    for name in ("log.simpleFoam", "log.pisoFoam", "log.icoFoam", "log.buoyantSimpleFoam"):
        candidate = case_dir / name
        if candidate.exists():
            log = candidate
            break
    if log is None:
        # Fall back to any *Foam log that isn't potentialFoam
        candidates = [
            p for p in case_dir.glob("log.*Foam")
            if "potential" not in p.name.lower()
        ]
        log = candidates[0] if candidates else None
    if not log:
        return {}

    text = log.read_text(errors="replace")

    # Count completed time steps
    time_steps = re.findall(r"^Time = ([\d.eE+\-]+)", text, re.MULTILINE)
    steps_completed = len(time_steps)
    last_time = float(time_steps[-1]) if time_steps else 0

    # bounding warnings: bounding k/omega/epsilon/nuTilda with min/max
    bounding = []
    for m in re.finditer(
        r"bounding (\w+),\s*min:\s*([-\d.eE+]+)\s+max:\s*([-\d.eE+]+).*?^Time = ([\d.eE+\-]+)",
        text, re.MULTILINE | re.DOTALL
    ):
        field, vmin, vmax = m.group(1), float(m.group(2)), float(m.group(3))
        # find which Time step this occurred in by scanning backwards
        pos = m.start()
        times_before = re.findall(r"^Time = ([\d.eE+\-]+)", text[:pos], re.MULTILINE)
        at_time = float(times_before[-1]) if times_before else 0
        bounding.append({"field": field, "min": vmin, "max": vmax, "at_time": at_time})

    # Deduplicate: keep first occurrence per field
    seen_fields: set[str] = set()
    bounding_unique = []
    for b in bounding:
        if b["field"] not in seen_fields:
            seen_fields.add(b["field"])
            bounding_unique.append(b)

    # Fatal errors and SIGFPE (exclude "trapping enabled" startup message)
    fatal_errors = [
        m for m in re.findall(
            r"(SIGFPE|FATAL ERROR|DIV\(erg\)|[Ff]loating point exception[^\n]*|[Ss]egmentation fault)[^\n]*",
            text
        )
        if "enabled" not in m.lower() and "trapping" not in m.lower()
    ]

    # p residual overflow (residual >> 1)
    p_overflow_vals = re.findall(
        r"Solving for p.*?Final residual = ([\d.eE+\-]+)", text
    )
    p_residual_overflow = any(
        float(v) > 1e10 for v in p_overflow_vals if re.match(r"[\d.eE+\-]+", v)
    )

    diverged = bool(fatal_errors) or p_residual_overflow or (
        bool(bounding_unique) and steps_completed < 50
    )

    # Build human-readable summary
    lines = [f"Solver log: {log.name}, completed {steps_completed} steps (last Time={last_time})"]
    if bounding_unique:
        for b in bounding_unique:
            neg = " ← NEGATIVE (critical)" if b["min"] < 0 else ""
            lines.append(
                f"  WARNING bounding {b['field']} at Time={b['at_time']}: "
                f"min={b['min']:.4g}, max={b['max']:.4g}{neg}"
            )
    if p_residual_overflow:
        lines.append("  ERROR p residual overflow (>> 1) detected → diverged")
    for err in fatal_errors[:3]:
        lines.append(f"  FATAL: {err.strip()}")
    if diverged:
        if bounding_unique and any(b["min"] < 0 for b in bounding_unique):
            lines.append(
                "  Diagnosis: negative turbulence field at step 1 → likely bad initial flow field "
                "(potentialFoam generated non-physical velocity for this geometry/angle)"
            )
        elif p_residual_overflow:
            lines.append("  Diagnosis: pressure solve blew up → likely diverged flow field")

    return {
        "log_name": log.name,
        "steps_completed": steps_completed,
        "bounding_warnings": bounding_unique,
        "fatal_errors": fatal_errors[:5],
        "p_residual_overflow": p_residual_overflow,
        "diverged": diverged,
        "summary": "\n".join(lines),
    }


def parse_mesh_diagnostics(case_dir: Path) -> dict:
    """Parse checkMesh log for quality assessment with pass/warn/fail thresholds.

    Returns dict with all parse_mesh_info fields plus:
      non_ortho_status, skewness_status, overall_status, summary (str).
    """
    info = parse_mesh_info(case_dir)
    if not info:
        log = case_dir / "log.checkMesh" if case_dir else None
        if not log or not log.exists():
            return {}

    max_no = info.get("max_non_ortho", 0)
    max_sk = info.get("max_skewness", 0)

    # Thresholds: non-ortho >70 = FAIL, >50 = WARN; skewness >4 = FAIL, >3 = WARN
    def _grade(val, warn, fail):
        if val >= fail:
            return "FAIL"
        if val >= warn:
            return "WARN"
        return "OK"

    no_status = _grade(max_no, 50, 70)
    sk_status = _grade(max_sk, 3, 4)
    overall = "FAIL" if "FAIL" in (no_status, sk_status) else ("WARN" if "WARN" in (no_status, sk_status) else "OK")

    log_path = case_dir / "log.checkMesh"
    mesh_ok = "Mesh OK" in (log_path.read_text(errors="replace") if log_path.exists() else "")

    lines = [
        f"Mesh quality ({overall}):",
        f"  Cells: {info.get('cells', '?'):,}",
        f"  Non-orthogonality: max={max_no:.1f}° avg={info.get('avg_non_ortho', 0):.1f}° [{no_status}]"
        + (" (>70° causes solver instability)" if no_status == "FAIL" else ""),
        f"  Max skewness: {max_sk:.2f} [{sk_status}]"
        + (" (>4 causes divergence)" if sk_status == "FAIL" else ""),
        f"  checkMesh verdict: {'OK' if mesh_ok else 'FAILED'}",
    ]

    return {
        **info,
        "non_ortho_status": no_status,
        "skewness_status": sk_status,
        "overall_status": overall,
        "mesh_ok": mesh_ok,
        "summary": "\n".join(lines),
    }
