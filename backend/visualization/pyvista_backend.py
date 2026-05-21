"""PyVista-based visualization backend (server-side PNG rendering)."""
import io
from pathlib import Path

import pyvista as pv
import plotly.graph_objects as go
import plotly.io as pio

from backend.visualization.base import VisualizationBackend
from backend.visualization.parsers import parse_force_coefficients, parse_residuals

# Use offscreen rendering (no display needed on server)
pv.start_xvfb() if hasattr(pv, "start_xvfb") else None


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
            zmax  = domain.get("domain_zmax",   8)
            box = pv.Box(bounds=(xmin, xmax, -yhalf, yhalf, 0.0, zmax))
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
            x0l, x1l, y0l, y1l, z0l, z1l = x0l, x1l, -yhl, yhl, 0.0, domain.get("domain_zmax", 8)
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

    def plot_residuals(self, log_path: Path) -> bytes:
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
        return pio.to_image(fig, format="png")

    def plot_force_coefficients(self, postproc_dir: Path) -> bytes:
        df = parse_force_coefficients(postproc_dir)
        if df.empty:
            return _empty_plot("No force coefficient data found")

        fig = go.Figure()
        for col in ("Cx", "Cz", "CmYaw", "Cy"):
            if col in df.columns:
                fig.add_trace(go.Scatter(x=df["Time"], y=df[col], name=col, mode="lines"))

        fig.update_layout(
            title="Force / Moment Coefficients",
            xaxis_title="Iteration / Time",
            yaxis_title="Coefficient",
            template="plotly_white",
            height=400,
        )
        return pio.to_image(fig, format="png")

    def plot_cutting_plane(self, vtk_path: Path, field: str = "p",
                           geo_bounds: dict | None = None) -> bytes:
        mesh = pv.read(str(vtk_path))
        pl = pv.Plotter(off_screen=True, window_size=(1200, 600))

        if field == "mesh":
            pl.set_background("white")
            pl.add_mesh(mesh, style="wireframe", color="#444444", line_width=0.5)
        else:
            scalars = (field if field in mesh.point_data
                       else field if field in mesh.cell_data
                       else mesh.array_names[0] if mesh.array_names else None)
            pl.add_mesh(
                mesh,
                scalars=scalars,
                cmap="RdBu_r" if field == "p" else "viridis",
                show_scalar_bar=True,
            )

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

        img = pl.screenshot(return_img=True)
        pl.close()
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
        pl.add_mesh(mesh, show_edges=True, edge_color="black",
                    color="lightgray", line_width=0.5)

        # Wind direction arrow: flow is always +X in OpenFOAM frame
        # Arrow placed upstream of the object
        bounds = mesh.bounds
        arrow_x = bounds[0] - 0.3
        arrow_y = (bounds[2] + bounds[3]) / 2
        arrow_z = (bounds[4] + bounds[5]) / 2
        arrow_len = (bounds[1] - bounds[0]) * 0.25
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
        img = pl.screenshot(return_img=True)
        pl.close()
        return _array_to_png(img)

    def plot_streamlines(self, vtk_path: Path, geo_bounds: dict | None = None) -> bytes:
        import numpy as np
        mesh = pv.read(str(vtk_path))
        # Compute velocity magnitude for coloring
        if "U" in mesh.point_data and mesh.point_data["U"].ndim == 2:
            mesh["U_mag"] = np.linalg.norm(mesh.point_data["U"], axis=1)
            scalars = "U_mag"
        else:
            scalars = mesh.array_names[0] if mesh.array_names else None
        pl = pv.Plotter(off_screen=True, window_size=(1200, 600))
        pl.add_mesh(mesh, scalars=scalars, cmap="plasma", line_width=2, show_scalar_bar=True)
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
        return _array_to_png(img)


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
