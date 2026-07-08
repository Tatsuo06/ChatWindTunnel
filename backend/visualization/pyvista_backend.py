"""PyVista-based visualization backend (server-side PNG rendering)."""
import io
from pathlib import Path

import pyvista as pv
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from backend.visualization.base import VisualizationBackend
from backend.visualization.parsers import parse_force_coefficients, parse_residuals

# Use offscreen rendering (no display needed on server)
pv.start_xvfb() if hasattr(pv, "start_xvfb") else None

# Suppress VTK color buffer warnings on macOS Cocoa offscreen context
try:
    import vtkmodules.vtkCommonCore as _vtk_core
    _vtk_core.vtkObject.GlobalWarningDisplayOff()
except Exception:
    pass


class PyVistaBackend(VisualizationBackend):

    def preview_geometry(self, stl_path: Path, label: str = "",
                         domain: dict | None = None, refbox: dict | None = None) -> bytes:
        mesh = pv.read(str(stl_path))
        pl = pv.Plotter(off_screen=True, window_size=(900, 600))
        pl.add_mesh(mesh, color="lightblue", show_edges=False)

        if domain:
            xmin  = domain.get("domain_xmin",  -5)
            xmax  = domain.get("domain_xmax",  15)
            yhalf = domain.get("domain_yhalf",  4)
            zmin  = domain.get("domain_zmin",   0.0)
            zmax  = domain.get("domain_zmax",   8)
            box = pv.Box(bounds=(xmin, xmax, -yhalf, yhalf, zmin, zmax))
            pl.add_mesh(box, style="wireframe", color="orange",
                        line_width=2, opacity=0.9, render_lines_as_tubes=False)

        if refbox:
            rmin = refbox.get("refbox_min", [-1, -0.7, 0])
            rmax = refbox.get("refbox_max", [8, 0.7, 2.5])
            rb = pv.Box(bounds=(rmin[0], rmax[0], rmin[1], rmax[1], rmin[2], rmax[2]))
            pl.add_mesh(rb, style="wireframe", color="cyan",
                        line_width=1.5, opacity=0.9, render_lines_as_tubes=False)

        # Origin crosshair lines through domain extents (or geometry bounds as fallback)
        if domain:
            x0l, x1l = domain.get("domain_xmin", -5), domain.get("domain_xmax", 15)
            yhl = domain.get("domain_yhalf", 4)
            x0l, x1l, y0l, y1l, z0l, z1l = x0l, x1l, -yhl, yhl, domain.get("domain_zmin", 0.0), domain.get("domain_zmax", 8)
        else:
            b = mesh.bounds  # (xmin, xmax, ymin, ymax, zmin, zmax)
            x0l, x1l, y0l, y1l, z0l, z1l = b[0], b[1], b[2], b[3], b[4], b[5]
        for pts, color in [
            ([(x0l, 0, 0), (x1l, 0, 0)], "red"),
            ([(0, y0l, 0), (0, y1l, 0)], "green"),
            ([(0, 0, z0l), (0, 0, z1l)], "blue"),
        ]:
            line = pv.Line(*pts)
            pl.add_mesh(line, color=color, line_width=2)

        pl.add_axes()
        pl.view_vector((-1, -0.7, 0.5), viewup=(0, 0, 1))
        pl.reset_camera()
        if label:
            pl.add_text(label, position="upper_left", font_size=12, color="black")
        img = pl.screenshot(return_img=True)
        pl.close()
        return _array_to_png(img)

    def plot_residuals(self, log_path: Path, x_max: float | None = None) -> bytes:
        df = parse_residuals(log_path)
        if df.empty:
            return _empty_plot("No residual data found")

        fig = go.Figure()
        for col in df.columns:
            if col == "Time":
                continue
            fig.add_trace(go.Scatter(x=df["Time"], y=df[col], name=col, mode="lines"))

        fig.update_layout(
            title="Residual Convergence",
            xaxis_title="Iteration / Time",
            yaxis_title="Residual",
            yaxis_type="log",
            template="plotly_white",
            height=400,
        )
        # Fix the x-axis to the planned endTime so progress toward the target is
        # visible (the trace stops partway while the run is still in progress).
        if x_max and x_max > 0:
            left = min(0.0, float(df["Time"].min()))
            fig.update_xaxes(range=[left, x_max])
        return pio.to_image(fig, format="png")

    def plot_force_coefficients(self, postproc_dir: Path, only_last_phase: bool = False,
                                phase_end: dict | None = None,
                                single_x_max: float | None = None,
                                single_x_title: str | None = None,
                                clamp_time: float | None = None) -> bytes:
        df = parse_force_coefficients(postproc_dir)
        if df.empty:
            return _empty_plot("No force coefficient data found")

        # Unsteady child: its own force data is in seconds (Time <= end time). Any
        # rows beyond that are stale inherited parent iterations — drop them so a
        # steady parent's iteration-axis history can't pollute the child's plot.
        if clamp_time and clamp_time > 0:
            df = df[df["Time"] <= clamp_time * 1.01]
            if df.empty:
                return _empty_plot("No force coefficient data found")

        # Restart children inherit the parent's forceCoeffs history (earlier
        # phases); show only the child's own stage.
        if only_last_phase and "Phase" in df.columns and df["Phase"].nunique() > 1:
            df = df[df["Phase"] == df["Phase"].max()]

        def _phase_range(pdf, phase):
            # x-axis upper bound = planned endTime for this phase (progress view)
            xm = float((phase_end or {}).get(phase, 0) or 0)
            return [min(0.0, float(pdf["Time"].min())), xm] if xm > 0 else None

        n_phases = df["Phase"].nunique() if "Phase" in df.columns else 1
        if n_phases > 1:
            # Unsteady (LES) case: Phase 1 (RAS, iterations) and Phase 2 (LES, seconds)
            # share the same postProcessing folder but have incompatible Time axes —
            # render them as separate subplots rather than one continuous line.
            colors = {"Cx": "#636efa", "Cz": "#ef553b", "CmYaw": "#00cc96", "Cy": "#ab63fa"}
            phases = sorted(df["Phase"].unique())
            fig = make_subplots(rows=n_phases, cols=1, vertical_spacing=0.16,
                                 subplot_titles=[f"Phase {p}" for p in phases])
            for i, phase in enumerate(phases, start=1):
                phase_df = df[df["Phase"] == phase]
                for col in ("Cx", "Cz", "CmYaw", "Cy"):
                    if col in phase_df.columns:
                        fig.add_trace(
                            go.Scatter(x=phase_df["Time"], y=phase_df[col], name=col,
                                       mode="lines", line=dict(color=colors[col]),
                                       legendgroup=col, showlegend=(i == 1)),
                            row=i, col=1,
                        )
                xaxis_title = "Iteration" if phase == 1 else "Time [s]"
                _rng = _phase_range(phase_df, phase)
                fig.update_xaxes(title_text=xaxis_title, row=i, col=1,
                                 **({"range": _rng} if _rng else {}))
                fig.update_yaxes(title_text="Coefficient", row=i, col=1)
            fig.update_layout(
                title="Force / Moment Coefficients",
                template="plotly_white",
                height=320 * n_phases,
            )
            return pio.to_image(fig, format="png")

        fig = go.Figure()
        for col in ("Cx", "Cz", "CmYaw", "Cy"):
            if col in df.columns:
                fig.add_trace(go.Scatter(x=df["Time"], y=df[col], name=col, mode="lines"))

        fig.update_layout(
            title="Force / Moment Coefficients",
            xaxis_title=single_x_title or "Iteration / Time",
            yaxis_title="Coefficient",
            template="plotly_white",
            height=400,
        )
        # x-axis upper bound = planned endTime (caller knows the unit: iterations
        # for steady, seconds for an unsteady child's own stage)
        if single_x_max and single_x_max > 0:
            fig.update_xaxes(range=[min(0.0, float(df["Time"].min())), single_x_max])
        return pio.to_image(fig, format="png")

    def plot_cutting_plane(self, vtk_path: Path, field: str = "p",
                           geo_bounds: dict | None = None,
                           stl_path: Path | None = None,
                           clim: tuple | None = None,
                           time_label: str | None = None,
                           return_array: bool = False):
        mesh = pv.read(str(vtk_path))
        pl = pv.Plotter(off_screen=True, window_size=(1200, 600))

        if field == "mesh":
            pl.set_background("white")
            pl.add_mesh(mesh, show_edges=True,
                        color=[0.95, 0.95, 0.95], edge_color=[0.4, 0.4, 0.4],
                        line_width=0.5, ambient=1.0, diffuse=0.0, specular=0.0)
        else:
            scalars = (field if field in mesh.point_data
                       else field if field in mesh.cell_data
                       else mesh.array_names[0] if mesh.array_names else None)
            pl.add_mesh(
                mesh,
                scalars=scalars,
                cmap="jet",
                clim=clim,
                show_scalar_bar=True,
            )

        # Overlay STL silhouette sliced at y=0 to show geometry outline
        if stl_path and Path(stl_path).exists():
            try:
                stl = pv.read(str(stl_path))
                slice_y = stl.slice(normal="y", origin=(0, 0, 0))
                if slice_y.n_points > 0:
                    pl.add_mesh(slice_y, color="black", line_width=2.0)
            except Exception:
                pass

        if time_label:
            pl.add_text(time_label, position="upper_left", font_size=14, color="black")

        pl.add_axes()
        pl.view_xz()

        if geo_bounds:
            x0, x1 = geo_bounds["xmin"], geo_bounds["xmax"]
            z0, z1 = geo_bounds["zmin"], geo_bounds["zmax"]
            dx = x1 - x0
            dz = z1 - z0
            pl.reset_camera(bounds=[
                x0, x1,
                -1, 1,
                max(0.0, z0), z1,
            ])
        else:
            pl.reset_camera()
        pl.camera.zoom(1.4)

        img = pl.screenshot(return_img=True)
        pl.close()
        if return_array:
            return img
        return _array_to_png(img)

    def cutting_plane_data(self, vtk_path: Path, field: str = "p") -> dict:
        import numpy as np
        from scipy.interpolate import griddata

        mesh = pv.read(str(vtk_path))
        pts = mesh.points  # (N, 3)
        x = pts[:, 0]
        z = pts[:, 2]

        if field == "U":
            U = mesh.point_data.get("U")
            if U is None:
                U = mesh.cell_data.get("U")
            if U is None:
                return {}
            vals = np.linalg.norm(U, axis=1)
        else:
            vals = mesh.point_data.get(field)
            if vals is None:
                vals = mesh.cell_data.get(field)
            if vals is None:
                return {}
            vals = np.asarray(vals)

        nx, nz = 300, 150
        xi = np.linspace(x.min(), x.max(), nx)
        zi = np.linspace(z.min(), z.max(), nz)
        Xi, Zi = np.meshgrid(xi, zi)
        Vi = griddata((x, z), vals, (Xi, Zi), method="linear")

        return {
            "x": xi.tolist(),
            "z": zi.tolist(),
            "values": Vi.tolist(),
            "field": field,
            "vmin": float(np.nanmin(vals)),
            "vmax": float(np.nanmax(vals)),
        }

    def plot_mesh_surface(self, case_dir: Path, patch: str = "motorBike",
                          view: str = "iso") -> bytes:
        import json, numpy as np

        # Prefer pre-generated VTK from foamToVTK -noInternal (much smaller than full case)
        vtk_candidates = sorted(
            list(case_dir.glob(f"VTK/**/*{patch}*.vtp")) +
            list(case_dir.glob(f"VTK/**/*{patch}*.vtk")),
        )
        if vtk_candidates:
            try:
                mesh = pv.read(str(vtk_candidates[-1]))
            except Exception:
                mesh = None
        else:
            mesh = None

        if mesh is None:
            foam_file = case_dir / "case.foam"
            reader = pv.OpenFOAMReader(str(foam_file))
            if not reader.time_values:
                return _empty_plot("No mesh data found")
            reader.set_active_time_value(reader.time_values[-1])
            dataset = reader.read()
            try:
                mesh = dataset["boundary"][patch]
            except (KeyError, TypeError):
                return _empty_plot(f"Patch '{patch}' not found")

        pl = pv.Plotter(off_screen=True, window_size=(1200, 800))
        pl.set_background("white")
        pl.add_mesh(mesh, show_edges=True,
                    color=[0.95, 0.95, 0.95], edge_color=[0.4, 0.4, 0.4],
                    line_width=0.5, ambient=1.0, diffuse=0.0, specular=0.0)

        # Wind direction arrow: flow is always +X in OpenFOAM frame
        # Arrow placed upstream of the object
        bounds = mesh.bounds
        length_x = bounds[1] - bounds[0]
        arrow_len = length_x * 0.15
        arrow_x = bounds[0] - length_x * 0.30
        arrow_y = (bounds[2] + bounds[3]) / 2
        arrow_z = (bounds[4] + bounds[5]) / 2
        arrow = pv.Arrow(start=(arrow_x, arrow_y, arrow_z),
                         direction=(1, 0, 0), scale=arrow_len)
        pl.add_mesh(arrow, color="red")
        pl.add_text("→ 流れ方向", position="upper_left", font_size=12, color="red")

        # Load actual yaw used at computation time
        params_file = case_dir / "case_params.json"
        if params_file.exists():
            params = json.loads(params_file.read_text())
            yaw = params.get("yaw_deg", 0.0)
            pitch = params.get("pitch_deg", 0.0)
            pl.add_text(f"計算時ヨー: {yaw}°  ピッチ: {pitch}°",
                        position="upper_right", font_size=12, color="black")

        if view == "top":
            pl.view_xy()
        elif view == "side":
            pl.view_xz()
        else:
            pl.view_vector((-1, -0.7, 0.5), viewup=(0, 0, 1))
        pl.reset_camera()
        pl.camera.zoom(2.5)
        img = pl.screenshot(return_img=True)
        pl.close()
        return _array_to_png(img)

    def plot_streamlines(self, vtk_paths: "Path | list[Path]", geo_bounds: dict | None = None,
                         stl_path: "Path | None" = None,
                         clim: "tuple | None" = None,
                         time_label: "str | None" = None,
                         return_array: bool = False):
        import numpy as np

        if not isinstance(vtk_paths, list):
            vtk_paths = [vtk_paths]

        pl = pv.Plotter(off_screen=True, window_size=(1200, 600))

        # Load all meshes first to compute global U_mag range
        meshes = []
        for vtk_path in vtk_paths:
            try:
                mesh = pv.read(str(vtk_path))
            except Exception:
                continue
            if mesh.n_points == 0:
                continue
            if "U" in mesh.point_data and mesh.point_data["U"].ndim == 2:
                mesh["U_mag"] = np.linalg.norm(mesh.point_data["U"], axis=1)
            meshes.append(mesh)

        if clim is None:
            u_max = max(
                (float(m["U_mag"].max()) for m in meshes if "U_mag" in m.point_data),
                default=None,
            )
            clim = [0, u_max]

        scalar_bar_added = False
        for mesh in meshes:
            scalars = "U_mag" if "U_mag" in mesh.point_data else (
                mesh.array_names[0] if mesh.array_names else None)
            pl.add_mesh(mesh, scalars=scalars, cmap="jet", line_width=2,
                        show_scalar_bar=not scalar_bar_added,
                        clim=clim)
            scalar_bar_added = True

        if stl_path and Path(stl_path).exists():
            try:
                stl = pv.read(str(stl_path))
                pl.add_mesh(stl, color="lightgray", opacity=0.4, smooth_shading=True)
            except Exception:
                pass

        if time_label:
            pl.add_text(time_label, position="upper_left", font_size=14, color="black")

        pl.add_axes()
        pl.view_xz()
        if geo_bounds:
            pl.reset_camera(bounds=[
                geo_bounds["xmin"], geo_bounds["xmax"],
                -1, 1,
                max(0.0, geo_bounds["zmin"]), geo_bounds["zmax"],
            ])
        else:
            pl.reset_camera()
        img = pl.screenshot(return_img=True)
        pl.close()
        if return_array:
            return img
        return _array_to_png(img)

    def render_animation(self, case_dir: Path, kind: str = "plane", field: str = "U",
                         fps: int = 10, geo_bounds: dict | None = None,
                         stl_path: "Path | None" = None) -> Path:
        """Render an MP4 of Phase-2 (LES) frames and return its path.

        kind="plane":       postProcessing/cuttingPlane/<t>/yNormal.vtp frames
        kind="streamlines": postProcessing/sets/streamLines(<Back>)/<t>/track0.vtp frames

        All frames share one color scale (global min/max) and camera so the
        video is temporally coherent. Raises ValueError with fewer than 2 frames.
        """
        import json
        import numpy as np
        import imageio.v2 as iio

        def _tval(p: Path) -> float:
            try:
                return float(p.name)
            except ValueError:
                return -1.0

        # Only physical LES times are animation frames — a leftover Phase-1
        # iteration directory (e.g. "1000") would otherwise sort last and
        # append a bogus RAS frame at the end of the video.
        t_max = float("inf")
        params_file = case_dir / "case_params.json"
        if params_file.exists():
            try:
                les_end = json.loads(params_file.read_text()).get("les_end_time")
                if les_end:
                    t_max = float(les_end) * 1.001
            except Exception:
                pass

        if kind == "plane":
            frame_dirs = sorted(
                (d for d in (case_dir / "postProcessing" / "cuttingPlane").glob("*")
                 if d.is_dir() and (d / "yNormal.vtp").exists() and 0 <= _tval(d) <= t_max),
                key=_tval,
            )
            if len(frame_dirs) < 2:
                raise ValueError(f"Only {len(frame_dirs)} cutting-plane frame(s) available")

            # Pass 1: global color range for the field across all frames
            vmin, vmax = float("inf"), float("-inf")
            for d in frame_dirs:
                mesh = pv.read(str(d / "yNormal.vtp"))
                vals = mesh.point_data.get(field, mesh.cell_data.get(field))
                if vals is None:
                    continue
                vals = np.asarray(vals)
                if vals.ndim == 2:  # vector field (U) → magnitude
                    vals = np.linalg.norm(vals, axis=1)
                vmin = min(vmin, float(vals.min()))
                vmax = max(vmax, float(vals.max()))
            clim = (vmin, vmax) if vmin < vmax else None

            imgs = [
                self.plot_cutting_plane(
                    d / "yNormal.vtp", field=field, geo_bounds=geo_bounds,
                    stl_path=stl_path, clim=clim,
                    time_label=f"t = {_tval(d):g} s", return_array=True,
                )
                for d in frame_dirs
            ]
            out_name = f"plane_{field}.mp4"

        elif kind == "streamlines":
            fwd_root = case_dir / "postProcessing" / "sets" / "streamLines"
            back_root = case_dir / "postProcessing" / "sets" / "streamLinesBack"
            frame_dirs = sorted(
                (d for d in fwd_root.glob("*")
                 if d.is_dir() and (d / "track0.vtp").exists() and 0 <= _tval(d) <= t_max),
                key=_tval,
            )
            if len(frame_dirs) < 2:
                raise ValueError(f"Only {len(frame_dirs)} streamline frame(s) available")

            # Pass 1: global |U| range across all frames
            u_max = 0.0
            for d in frame_dirs:
                for root in (fwd_root, back_root):
                    tp = root / d.name / "track0.vtp"
                    if tp.exists():
                        m = pv.read(str(tp))
                        if "U" in m.point_data and m.point_data["U"].ndim == 2:
                            u_max = max(u_max, float(np.linalg.norm(m.point_data["U"], axis=1).max()))
            clim = (0.0, u_max) if u_max > 0 else None

            imgs = []
            for d in frame_dirs:
                paths = [d / "track0.vtp"]
                back = back_root / d.name / "track0.vtp"
                if back.exists():
                    paths.append(back)
                imgs.append(self.plot_streamlines(
                    paths, geo_bounds=geo_bounds, stl_path=stl_path,
                    clim=clim, time_label=f"t = {_tval(d):g} s", return_array=True,
                ))
            out_name = "streamlines.mp4"

        else:
            raise ValueError(f"Unknown animation kind: {kind}")

        out_dir = case_dir / "animations"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / out_name
        # yuv420p + even frame dimensions (1200x600) for browser-compatible H.264
        writer = iio.get_writer(str(out_path), fps=fps, codec="libx264",
                                pixelformat="yuv420p", macro_block_size=1)
        try:
            for img in imgs:
                writer.append_data(img)
        finally:
            writer.close()
        return out_path


def _array_to_png(arr) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _empty_plot(message: str) -> bytes:
    fig = go.Figure()
    fig.add_annotation(text=message, x=0.5, y=0.5, showarrow=False, font=dict(size=16))
    fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False), height=300)
    return pio.to_image(fig, format="png")


# Singleton instance used by the API
backend = PyVistaBackend()
