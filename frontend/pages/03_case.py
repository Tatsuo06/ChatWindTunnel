"""Case setup, execution, and results page."""
import streamlit as st

import frontend.api_client as api
from frontend.i18n import t


def _box_trace(xmin, xmax, ymin, ymax, zmin, zmax, color, name):
    import plotly.graph_objects as go
    x, y, z = [], [], []
    for p1, p2 in [
        [(xmin,ymin,zmin),(xmax,ymin,zmin)], [(xmax,ymin,zmin),(xmax,ymax,zmin)],
        [(xmax,ymax,zmin),(xmin,ymax,zmin)], [(xmin,ymax,zmin),(xmin,ymin,zmin)],
        [(xmin,ymin,zmax),(xmax,ymin,zmax)], [(xmax,ymin,zmax),(xmax,ymax,zmax)],
        [(xmax,ymax,zmax),(xmin,ymax,zmax)], [(xmin,ymax,zmax),(xmin,ymin,zmax)],
        [(xmin,ymin,zmin),(xmin,ymin,zmax)], [(xmax,ymin,zmin),(xmax,ymin,zmax)],
        [(xmax,ymax,zmin),(xmax,ymax,zmax)], [(xmin,ymax,zmin),(xmin,ymax,zmax)],
    ]:
        x += [p1[0], p2[0], None]; y += [p1[1], p2[1], None]; z += [p1[2], p2[2], None]
    return go.Scatter3d(x=x, y=y, z=z, mode="lines",
                        line=dict(color=color, width=2),
                        name=name, showlegend=True, hoverinfo="skip")


def _show_geometry_3d(data: dict) -> None:
    import plotly.graph_objects as go
    import pyvista as pv

    try:
        mesh = pv.read(data["stl_path"]).triangulate()
    except Exception:
        return

    # Cap triangle count for the browser: a full-resolution STL (e.g. 600k+ tris)
    # produces a huge Mesh3d JSON payload that freezes the WebGL renderer. A
    # decimated preview is visually indistinguishable at this scale.
    PREVIEW_MAX_FACES = 80000
    if mesh.n_cells > PREVIEW_MAX_FACES:
        try:
            mesh = mesh.decimate_pro(
                1.0 - PREVIEW_MAX_FACES / mesh.n_cells,
                preserve_topology=False,
            )
        except Exception:
            pass

    verts = mesh.points
    faces = mesh.faces.reshape(-1, 4)[:, 1:]

    fig = go.Figure()
    fig.add_trace(go.Mesh3d(
        x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
        i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
        color="lightsteelblue", opacity=0.85, name="Geometry",
        flatshading=False, showlegend=True,
        lighting=dict(ambient=0.4, diffuse=0.8, specular=0.3),
        lightposition=dict(x=1, y=1, z=2),
    ))
    d = data["domain"]
    fig.add_trace(_box_trace(d["xmin"], d["xmax"], d["ymin"], d["ymax"],
                             d["zmin"], d["zmax"], "royalblue", t("domain_box")))
    r = data["refbox"]
    fig.add_trace(_box_trace(r["xmin"], r["xmax"], r["ymin"], r["ymax"],
                             r["zmin"], r["zmax"], "limegreen", t("refine_box")))

    # Wind direction arrow (+X) at inlet face
    zmid = (d["zmin"] + d["zmax"]) / 2
    arrow_len = (d["xmax"] - d["xmin"]) * 0.15
    ax = d["xmin"] + arrow_len * 0.1
    fig.add_trace(go.Scatter3d(
        x=[d["xmin"], ax], y=[0, 0], z=[zmid, zmid],
        mode="lines", line=dict(color="orange", width=4),
        name=t("wind_dir"), showlegend=True, hoverinfo="skip",
    ))
    fig.add_trace(go.Cone(
        x=[ax], y=[0], z=[zmid],
        u=[arrow_len], v=[0], w=[0],
        colorscale=[[0, "orange"], [1, "orange"]],
        showscale=False, sizemode="absolute", sizeref=arrow_len * 0.6,
        name="", showlegend=False, hoverinfo="skip",
    ))
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=500,
        legend=dict(x=0.01, y=0.99, bgcolor="rgba(0,0,0,0.4)", font=dict(color="white")),
        scene=dict(
            aspectmode="data",
            xaxis_title="X (m)", yaxis_title="Y (m)", zaxis_title="Z (m)",
            bgcolor="rgb(15,15,25)",
            xaxis=dict(gridcolor="rgb(60,60,80)", zerolinecolor="rgb(120,120,160)"),
            yaxis=dict(gridcolor="rgb(60,60,80)", zerolinecolor="rgb(120,120,160)"),
            zaxis=dict(gridcolor="rgb(60,60,80)", zerolinecolor="rgb(120,120,160)"),
        ),
        paper_bgcolor="rgb(15,15,25)",
    )
    st.plotly_chart(fig)


def _gas_settings_widgets(params: dict, key_prefix: str = "",
                          rate_label_key: str = "source_rate",
                          rate_help_key: str | None = None,
                          rate_default: float = 1.0,
                          rate_step: float = 0.5,
                          rate_format: str | None = None,
                          disabled: bool = False) -> dict:
    """Shared gas-release widgets (preset / density ratio / source / rate).

    The emission rate has different semantics per context (relative units in
    the Boussinesq dispersion case, kg/s in the gas-LES restart), so its
    label/help/default are injectable. Returns {"gas_density_ratio",
    "source_rate", "source_position"}; source_position None = auto (top
    centre of the geometry).
    """
    _presets = {t("gas_preset_co2"): 1.53, t("gas_preset_methane"): 0.55,
                t("gas_preset_hydrogen"): 0.07, t("gas_preset_custom"): None}
    gcol1, gcol2, gcol3 = st.columns(3)
    with gcol1:
        preset_sel = st.selectbox(t("gas_preset"), list(_presets.keys()), index=2,
                                  key=f"{key_prefix}gas_preset", disabled=disabled)
    with gcol2:
        _preset_ratio = _presets[preset_sel]
        ratio = st.number_input(
            t("gas_density_ratio"),
            value=float(_preset_ratio if _preset_ratio is not None
                        else params.get("gas_density_ratio", 0.07)),
            min_value=0.01, step=0.1, format="%.2f",
            disabled=disabled or _preset_ratio is not None,
            key=f"{key_prefix}gas_ratio",
        )
        if _preset_ratio is not None:
            ratio = _preset_ratio
    with gcol3:
        rate = st.number_input(
            t(rate_label_key),
            value=float(params.get("source_rate", rate_default)),
            min_value=0.0, step=rate_step, format=rate_format,
            help=t(rate_help_key) if rate_help_key else None,
            key=f"{key_prefix}gas_rate", disabled=disabled,
        )
    auto_src = st.checkbox(t("source_auto"),
                           value=params.get("source_position") is None,
                           key=f"{key_prefix}gas_autosrc", disabled=disabled)
    src_pos = None
    if not auto_src:
        _sp = params.get("source_position") or [0.0, 0.0, 1.0]
        scol1, scol2, scol3 = st.columns(3)
        with scol1:
            sx = st.number_input(t("source_x"), value=float(_sp[0]), step=0.1,
                                 format="%.3f", key=f"{key_prefix}gas_sx", disabled=disabled)
        with scol2:
            sy = st.number_input(t("source_y"), value=float(_sp[1]), step=0.1,
                                 format="%.3f", key=f"{key_prefix}gas_sy", disabled=disabled)
        with scol3:
            sz = st.number_input(t("source_z"), value=float(_sp[2]), step=0.1,
                                 format="%.3f", key=f"{key_prefix}gas_sz", disabled=disabled)
        src_pos = [sx, sy, sz]
    return {"gas_density_ratio": ratio, "source_rate": rate, "source_position": src_pos}


def _gas_source_velocity_note(rate: float, radius: float, density_ratio: float) -> None:
    """Show the effective gas-injection velocity as a reference and warn when it
    is high. Conservative estimate: the gas leaves the release region through an
    area ~2*pi*R^2, so v = (mdot/rho_gas)/A (a full sphere in open flow would
    exit through 4*pi*R^2, i.e. half this — so this over-estimates, which is safe
    for the warning). A single-cell point source (radius 0) is unresolved."""
    import math
    rho_gas = max(float(density_ratio), 1e-6) * 1.161   # p*M/(RT), M = ratio * M_air
    if radius and radius > 0:
        area = 2.0 * math.pi * radius ** 2
        v = (float(rate) / rho_gas) / area
        if v >= 50.0:
            st.warning(t("gas_vel_warn", f"{v:.0f}"))
        else:
            st.caption(t("gas_vel_ref", f"{v:.1f}"))
    else:
        st.warning(t("gas_vel_point_warn"))


def _show_gas_restart_expander(sim: dict, sim_id: int) -> None:
    """Restart a finished aero LES case as a gas-dispersion LES (rhoReactingBuoyantFoam)."""
    params = sim.get("parameters", {})
    if params.get("gas_les"):
        return  # already ran the gas stage
    if params.get("les_model") not in ("kOmegaSSTDDES", "kOmegaSSTIDDES"):
        return  # legacy SA-based LES: no compatible restart path
    with st.expander(t("restart_gas_title"), expanded=False):
        st.caption(t("restart_gas_note"))
        # source_rate means kg/s here (Phase-A relative values don't carry over)
        gas_params = {k: v for k, v in params.items() if k != "source_rate"}
        gas = _gas_settings_widgets(
            gas_params, key_prefix=f"gr_{sim_id}_",
            rate_label_key="source_rate_abs",
            rate_help_key="source_rate_help_gas",
            rate_default=1e-4, rate_step=1e-4, rate_format="%.5f",
        )
        st.caption(t("source_rate_note_gas"))
        gc1, gc2, gc3 = st.columns(3)
        with gc1:
            gas_end = st.number_input(
                t("gas_end_time_lbl"),
                value=float(params.get("gas_end_time", params.get("les_end_time", 0.7))),
                min_value=0.001, step=0.1, format="%.4f", key=f"gr_end_{sim_id}",
            )
        with gc2:
            gas_dt = st.number_input(
                t("gas_delta_t_lbl"),
                value=float(params.get("gas_delta_t", params.get("les_delta_t", 1e-4))),
                min_value=1e-6, step=1e-5, format="%.6f", key=f"gr_dt_{sim_id}",
            )
        with gc3:
            gas_model = st.selectbox(
                t("les_model_lbl"),
                ["kOmegaSSTDDES", "kOmegaSSTIDDES"],
                index=0 if params.get("les_model") != "kOmegaSSTIDDES" else 1,
                key=f"gr_model_{sim_id}",
            )
        gs1, gs2 = st.columns(2)
        with gs1:
            gas_start = st.number_input(
                t("gas_source_start_time_lbl"),
                value=float(params.get("gas_source_start_time", 0.1)),
                min_value=0.0, step=0.05, format="%.4f", key=f"gr_start_{sim_id}",
                help=t("gas_source_start_time_help"),
            )
        with gs2:
            gas_stop = st.number_input(
                t("gas_source_stop_time_lbl"),
                value=float(params.get("gas_source_stop_time", 0.6)),
                min_value=0.0, step=0.05, format="%.4f", key=f"gr_stop_{sim_id}",
                help=t("gas_source_stop_time_help"),
            )
        ga1, ga2 = st.columns(2)
        with ga1:
            gas_anim = st.number_input(
                t("les_anim_interval_lbl"),
                value=int(params.get("les_anim_interval", 100)),
                min_value=1, step=10, key=f"gr_anim_{sim_id}",
                help=t("anim_interval_help"),
            )
        with ga2:
            gas_maxco = st.number_input(
                t("gas_max_co_lbl"),
                value=float(params.get("gas_max_co", 1.5)),
                min_value=0.1, step=0.5, format="%.1f", key=f"gr_maxco_{sim_id}",
                help=t("gas_max_co_help"),
            )
        gas_radius = st.number_input(
            t("source_radius_lbl"),
            value=float(params.get("source_radius", 0.05)),
            min_value=0.0, step=0.01, format="%.3f", key=f"gr_radius_{sim_id}",
            help=t("source_radius_help"),
        )
        _gas_source_velocity_note(gas["source_rate"], gas_radius, gas["gas_density_ratio"])
        child_name = st.text_input(
            t("child_case_name"),
            value=f"{sim.get('name', '')} — GAS",
            key=f"gr_child_name_{sim_id}",
        )
        if st.button(t("restart_gas_btn"), key=f"gr_btn_{sim_id}", width="stretch"):
            if not child_name.strip():
                st.error(t("child_case_name_required"))
            elif gas_start >= gas_end:
                st.error(t("gas_start_after_end"))
            elif gas_stop > 0 and gas_stop <= gas_start:
                st.error(t("gas_stop_before_start"))
            else:
                with st.spinner(t("restarting")):
                    result = api.restart_job_gas(
                        sim_id, child_name.strip(), gas_end, gas_dt, gas_model,
                        gas["gas_density_ratio"], gas["source_position"], gas["source_rate"],
                        gas_source_start_time=gas_start, gas_source_stop_time=gas_stop,
                        les_anim_interval=int(gas_anim), gas_max_co=float(gas_maxco),
                        source_radius=float(gas_radius),
                    )
                if result:
                    st.success(t("restart_ok", result.get("job_id")))
                    st.session_state["sim_id"] = result.get("sim_id")
                    st.rerun()
                else:
                    st.error(t("restart_fail"))


def _show_gas_direct_form(sim: dict, sim_id: int, params: dict) -> None:
    """Steady → gas-dispersion LES in one restart, with a delayed emission start.

    A single rhoReactingBuoyantFoam run seeded from the steady solution; the LES
    develops turbulence gas-free until gas_source_start_time, then emits.
    """
    turb = params.get("turbulence_model", "kOmegaSST")
    if turb != "kOmegaSST":
        st.warning(t("restart_les_kosst_only", turb))
        return
    st.caption(t("restart_gas_direct_note"))
    # Drop the steady parent's source_rate (relative units) so the kg/s rate
    # defaults to rate_default rather than the parent's value.
    gas_params = {k: v for k, v in params.items() if k != "source_rate"}
    gas = _gas_settings_widgets(
        gas_params, key_prefix=f"grs_{sim_id}_",
        rate_label_key="source_rate_abs",
        rate_help_key="source_rate_help_gas",
        rate_default=1e-4, rate_step=1e-4, rate_format="%.5f",
    )
    st.caption(t("source_rate_note_gas"))
    gc1, gc2 = st.columns(2)
    with gc1:
        gas_end = st.number_input(
            t("gas_end_time_lbl"),
            value=float(params.get("gas_end_time", 0.7)),
            min_value=0.001, step=0.1, format="%.4f", key=f"grs_end_{sim_id}",
        )
    with gc2:
        gas_dt = st.number_input(
            t("gas_delta_t_lbl"),
            value=float(params.get("gas_delta_t", 1e-4)),
            min_value=1e-6, step=1e-5, format="%.6f", key=f"grs_dt_{sim_id}",
        )
    gc3, gc4 = st.columns(2)
    with gc3:
        gas_start = st.number_input(
            t("gas_source_start_time_lbl"),
            value=float(params.get("gas_source_start_time", 0.1)),
            min_value=0.0, step=0.05, format="%.4f", key=f"grs_start_{sim_id}",
            help=t("gas_source_start_time_help"),
        )
    with gc4:
        gas_stop = st.number_input(
            t("gas_source_stop_time_lbl"),
            value=float(params.get("gas_source_stop_time", 0.6)),
            min_value=0.0, step=0.05, format="%.4f", key=f"grs_stop_{sim_id}",
            help=t("gas_source_stop_time_help"),
        )
    gm1, gm2, gm3 = st.columns(3)
    with gm1:
        gas_model = st.selectbox(
            t("les_model_lbl"), ["kOmegaSSTDDES", "kOmegaSSTIDDES"], key=f"grs_model_{sim_id}",
        )
    with gm2:
        gas_anim = st.number_input(
            t("les_anim_interval_lbl"),
            value=int(params.get("les_anim_interval", 100)),
            min_value=1, step=10, key=f"grs_anim_{sim_id}",
            help=t("anim_interval_help"),
        )
    with gm3:
        gas_maxco = st.number_input(
            t("gas_max_co_lbl"),
            value=float(params.get("gas_max_co", 1.5)),
            min_value=0.1, step=0.5, format="%.1f", key=f"grs_maxco_{sim_id}",
            help=t("gas_max_co_help"),
        )
    gas_radius = st.number_input(
        t("source_radius_lbl"),
        value=float(params.get("source_radius", 0.05)),
        min_value=0.0, step=0.01, format="%.3f", key=f"grs_radius_{sim_id}",
        help=t("source_radius_help"),
    )
    _gas_source_velocity_note(gas["source_rate"], gas_radius, gas["gas_density_ratio"])
    child_name = st.text_input(
        t("child_case_name"),
        value=f"{sim.get('name', '')} — GAS",
        key=f"grs_child_name_{sim_id}",
    )
    if st.button(t("restart_gas_btn"), key=f"grs_btn_{sim_id}", width="stretch"):
        if not child_name.strip():
            st.error(t("child_case_name_required"))
        elif gas_start >= gas_end:
            st.error(t("gas_start_after_end"))
        elif gas_stop > 0 and gas_stop <= gas_start:
            st.error(t("gas_stop_before_start"))
        else:
            with st.spinner(t("restarting")):
                result = api.restart_job_gas(
                    sim_id, child_name.strip(), gas_end, gas_dt, gas_model,
                    gas["gas_density_ratio"], gas["source_position"], gas["source_rate"],
                    gas_source_start_time=gas_start, gas_source_stop_time=gas_stop,
                    les_anim_interval=int(gas_anim),
                    gas_max_co=float(gas_maxco),
                    source_radius=float(gas_radius),
                )
            if result:
                st.success(t("restart_ok", result.get("job_id")))
                st.session_state["sim_id"] = result.get("sim_id")
                st.rerun()
            else:
                st.error(t("restart_fail"))


def _show_restart_expander(sim: dict, sim_id: int, allow_les: bool = False) -> None:
    with st.expander(t("restart_job"), expanded=False):
        params = sim.get("parameters", {})
        mode_sel = t("restart_mode_steady")
        if allow_les:
            mode_sel = st.radio(
                t("restart_mode"),
                [t("restart_mode_steady"), t("restart_mode_les"), t("restart_mode_gas")],
                horizontal=True, key=f"restart_mode_{sim_id}",
            )
        les_selected = mode_sel == t("restart_mode_les")
        gas_selected = mode_sel == t("restart_mode_gas")

        if gas_selected:
            _show_gas_direct_form(sim, sim_id, params)
        elif not les_selected:
            current_end = int(params.get("end_time", 500))
            st.caption(f"{t('restart_current_end')}: {current_end}")
            add_steps = st.number_input(
                t("restart_add_steps"),
                min_value=1,
                value=500,
                step=100,
                key=f"restart_add_steps_input_{sim_id}",
            )
            new_end = current_end + int(add_steps)
            st.caption(f"{t('restart_new_end')}: {new_end}")
            child_name = st.text_input(
                t("child_case_name"),
                value=f"{sim.get('name', '')} — {new_end}it",
                key=f"restart_child_name_{sim_id}",
            )
            if st.button(t("restart_job"), key=f"restart_btn_{sim_id}", width="stretch"):
                if not child_name.strip():
                    st.error(t("child_case_name_required"))
                else:
                    with st.spinner(t("restarting")):
                        result = api.restart_job(sim_id, child_name.strip(), new_end)
                    if result:
                        st.success(t("restart_ok", result.get("job_id")))
                        st.session_state["sim_id"] = result.get("sim_id")
                        st.rerun()
                    else:
                        st.error(t("restart_fail"))
        else:
            turb = params.get("turbulence_model", "kOmegaSST")
            if turb != "kOmegaSST":
                st.warning(t("restart_les_kosst_only", turb))
                return
            st.caption(t("restart_les_note"))
            lc1, lc2 = st.columns(2)
            with lc1:
                les_end = st.number_input(
                    t("les_end_time"),
                    value=float(params.get("les_end_time", 0.7)),
                    min_value=0.001, step=0.1, format="%.4f", key=f"restart_les_end_{sim_id}",
                )
            with lc2:
                les_dt = st.number_input(
                    t("les_delta_t"),
                    value=float(params.get("les_delta_t", 1e-4)),
                    min_value=1e-6, step=1e-5, format="%.6f", key=f"restart_les_dt_{sim_id}",
                )
            lc3, lc4 = st.columns(2)
            with lc3:
                les_anim = st.number_input(
                    t("les_anim_interval_lbl"),
                    value=int(params.get("les_anim_interval", 100)),
                    min_value=1, step=10, key=f"restart_les_anim_{sim_id}",
                )
            with lc4:
                les_model = st.selectbox(
                    t("les_model_lbl"),
                    ["kOmegaSSTDDES", "kOmegaSSTIDDES"],
                    key=f"restart_les_model_{sim_id}",
                )
            child_name = st.text_input(
                t("child_case_name"),
                value=f"{sim.get('name', '')} — LES({'IDDES' if les_model == 'kOmegaSSTIDDES' else 'DDES'})",
                key=f"restart_les_child_name_{sim_id}",
            )
            if st.button(t("restart_les_btn"), key=f"restart_les_btn_{sim_id}", width="stretch"):
                if not child_name.strip():
                    st.error(t("child_case_name_required"))
                else:
                    with st.spinner(t("restarting")):
                        result = api.restart_job_les(
                            sim_id, child_name.strip(), les_end, les_dt, int(les_anim), les_model)
                    if result:
                        st.success(t("restart_ok", result.get("job_id")))
                        st.session_state["sim_id"] = result.get("sim_id")
                        st.rerun()
                    else:
                        st.error(t("restart_fail"))


def _show_run_conditions(sim: dict) -> None:
    """Compact read-only summary of what a case is running, shown in the Run tab
    below the timing info. Setup tab is unchanged. A restart child (parent_id set)
    shows its restart-specific settings; a root case shows its run conditions."""
    params = sim.get("parameters", {}) or {}
    solver = sim.get("solver_type", "STEADY")
    is_gas = bool(params.get("gas_les"))

    rows: list[tuple[str, str]] = []

    def add(label: str, val) -> None:
        if val is None or val == "":
            return
        if isinstance(val, bool):
            return
        if isinstance(val, (int, float)):
            val = "%g" % val
        rows.append((label, str(val)))

    if not sim.get("parent_id"):
        # Root case: show its run conditions (velocity, end iterations, attitude).
        title = t("run_conditions")
        header = title
        add(t("wind_speed"), params.get("velocity_mps"))
        add(t("end_iter_lbl"), params.get("end_time"))
        yaw, pitch, roll = sim.get("yaw_deg"), sim.get("pitch_deg"), sim.get("roll_deg")
        if any(float(a or 0) != 0 for a in (yaw, pitch, roll)):
            add(t("yaw_angle"), yaw)
            add(t("pitch_angle"), pitch)
            add(t("roll_angle"), roll)
        add(t("turb_intensity_lbl"), params.get("turbulence_intensity"))
        with st.container(border=True):
            st.markdown(f"**⚙️ {header}**")
            if rows:
                table = "| | |\n|---|---|\n" + "\n".join(
                    f"| {k} | `{v}` |" for k, v in rows
                )
                st.markdown(table)
        return

    if is_gas:
        mode = t("restart_mode_gas")
        add(t("les_model_lbl"), params.get("gas_model") or params.get("les_model"))
        add(t("gas_end_time_lbl"), params.get("gas_end_time"))
        add(t("gas_delta_t_lbl"), params.get("gas_delta_t"))
        add(t("gas_max_co_lbl"), params.get("gas_max_co"))
        add(t("gas_density_ratio_lbl"), params.get("gas_density_ratio"))
        add(t("gas_source_start_time_lbl"), params.get("gas_source_start_time"))
        add(t("gas_source_stop_time_lbl"), params.get("gas_source_stop_time"))
        add(t("source_rate"), params.get("source_rate"))
        add(t("source_radius_lbl"), params.get("source_radius"))
    elif solver == "UNSTEADY":
        mode = t("restart_mode_les")
        add(t("les_model_lbl"), params.get("les_model"))
        add(t("les_end_time_lbl"), params.get("les_end_time"))
        add(t("les_delta_t_lbl"), params.get("les_delta_t"))
        add(t("les_anim_interval_lbl"), params.get("les_anim_interval"))
    else:
        mode = t("restart_mode_steady")
        add(t("steady_end_lbl"), params.get("end_time"))

    with st.container(border=True):
        st.markdown(
            f"**🔁 {t('restart_summary')}** — {mode}  ·  "
            f"{t('restart_parent')}: #{sim['parent_id']}"
        )
        if rows:
            table = "| | |\n|---|---|\n" + "\n".join(
                f"| {k} | `{v}` |" for k, v in rows
            )
            st.markdown(table)


def _ordered_with_depth(sims: list[dict]) -> list[tuple[dict, int]]:
    """Flatten the case list into (sim, depth) with restart children nested
    directly under their parent. A sim whose parent is not in this list (or has
    no parent) is a depth-0 root; sibling order preserves the input order."""
    ids = {s["id"] for s in sims}
    children_of: dict = {}
    for s in sims:
        pid = s.get("parent_id")
        children_of.setdefault(pid if pid in ids else None, []).append(s)
    ordered: list[tuple[dict, int]] = []

    def walk(node_id, depth):
        for s in children_of.get(node_id, []):
            ordered.append((s, depth))
            walk(s["id"], depth + 1)

    walk(None, 0)
    return ordered


if "token" not in st.session_state:
    st.warning(t("login_required"))
    st.stop()

geo_id = st.session_state.get("geo_id")
if not geo_id:
    st.warning(t("select_geometry"))
    if st.button(t("go_geometry")):
        st.switch_page("pages/02_geometry.py")
    st.stop()

st.title(f"🌬️ {t('cases_title')} — {st.session_state.get('geo_name', '')}")


# Defined in the page body (re-runs each render) so the dialog title follows the
# current language. Opening it requires a confirm click before a case is deleted.
@st.dialog(t("delete_confirm_title"))
def _confirm_delete_case(sid: int, name: str):
    st.warning(t("delete_confirm_msg", name))
    c1, c2 = st.columns(2)
    with c1:
        if st.button(t("delete_confirm_yes"), type="primary", width="stretch",
                     key=f"cd_yes_{sid}"):
            if api.delete_simulation(sid):
                if st.session_state.get("sim_id") == sid:
                    st.session_state.pop("sim_id", None)
                st.rerun()
            else:
                st.error(t("delete_case_blocked"))
    with c2:
        if st.button(t("cancel"), width="stretch", key=f"cd_no_{sid}"):
            st.rerun()


def _llm_model_name() -> str:
    s = api.get_llm_settings()
    return s["model"] if s else "?"


def _chat_section(sim_id: int, chat_key: str, heading: str, caption: str, placeholder: str):
    st.divider()
    st.markdown(f"#### 💬 {heading}")
    st.caption(f"{caption}  \n`{_llm_model_name()}`")
    history = api.get_chat_history(sim_id)
    container = st.container(height=280)
    with container:
        for msg in history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
    user_input = st.chat_input(placeholder, key=chat_key)
    if user_input:
        with container:
            with st.chat_message("user"):
                st.markdown(user_input)
        with st.spinner("Processing..."):
            result = api.send_chat(sim_id, user_input)
        if result:
            with container:
                with st.chat_message("assistant"):
                    st.markdown(result["reply"])
            st.rerun()
        else:
            st.error("Chat failed. Check that LM Studio is running.")


def _turbulence_label(sim: dict) -> str:
    """Human-readable turbulence model for the current case.

    Unsteady cases report their LES model: the kOmegaSST DES model chosen at
    restart (les_model), or SpalartAllmarasDDES for legacy 2-phase cases.
    """
    params = sim.get("parameters", {})
    if sim.get("solver_type") == "UNSTEADY":
        return params.get("les_model") or "SpalartAllmarasDDES"
    return params.get("turbulence_model", "kOmegaSST")


def _fmt_secs(sec) -> str:
    """Format seconds as 'Xh Ym Zs' (dropping leading zero units)."""
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _results_chat(sim_id: int, key: str):
    """Render the shared results chat. All tabs use the same per-simulation
    backend history; `key` only keeps the input widgets distinct."""
    _chat_section(
        sim_id,
        chat_key=key,
        heading=t("chat_results_heading"),
        caption=t("chat_results_caption"),
        placeholder=t("chat_results_placeholder"),
    )


left, right = st.columns([1, 2])

# ── Left pane ──────────────────────────────────────────────────
with left:
    st.subheader(t("cases_title"))
    sims = api.list_simulations(geo_id)

    with st.expander(t("new_case"), expanded=not sims):
        with st.form("new_sim_form"):
            sim_name = st.text_input(t("case_name"))
            # All cases start as steady aero; LES and gas dispersion are
            # reached afterwards via the Run-tab restarts
            if st.form_submit_button(t("create"), width="stretch"):
                result = api.create_simulation(geo_id, sim_name, "STEADY")
                if result:
                    st.session_state["sim_id"] = result["id"]
                    st.rerun()
                else:
                    st.error(t("case_create_fail"))

    with st.expander(t("yaw_sweep"), expanded=False):
        with st.form("sweep_form"):
            sw_name = st.text_input(t("sweep_base_name"), value="case")
            sw_vel  = st.number_input(t("wind_speed"), value=10.0, min_value=0.1, step=1.0)

            # Header row
            _, hc1, hc2, hc3 = st.columns([2, 1, 1, 1])
            with hc1: st.caption(t("sweep_start"))
            with hc2: st.caption(t("sweep_end"))
            with hc3: st.caption(t("sweep_step"))

            # Yaw row
            yc0, yc1, yc2, yc3 = st.columns([2, 1, 1, 1])
            with yc0: st.markdown(t("sweep_axis_yaw"))
            with yc1: sw_yaw_s = st.number_input("ys", value=-180, step=5,  label_visibility="collapsed")
            with yc2: sw_yaw_e = st.number_input("ye", value=170,  step=5,  label_visibility="collapsed")
            with yc3: sw_yaw_t = st.number_input("yt", value=10,   step=5, min_value=1, label_visibility="collapsed")

            # Pitch row
            pc0, pc1, pc2, pc3 = st.columns([2, 1, 1, 1])
            with pc0: st.markdown(t("sweep_axis_pitch"))
            with pc1: sw_pit_s = st.number_input("ps", value=0, step=5,  label_visibility="collapsed")
            with pc2: sw_pit_e = st.number_input("pe", value=0, step=5,  label_visibility="collapsed")
            with pc3: sw_pit_t = st.number_input("pt", value=5, step=5, min_value=1, label_visibility="collapsed")

            # Roll row
            rc0, rc1, rc2, rc3 = st.columns([2, 1, 1, 1])
            with rc0: st.markdown(t("sweep_axis_roll"))
            with rc1: sw_rol_s = st.number_input("rs", value=0, step=5,  label_visibility="collapsed")
            with rc2: sw_rol_e = st.number_input("re", value=0, step=5,  label_visibility="collapsed")
            with rc3: sw_rol_t = st.number_input("rt", value=5, step=5, min_value=1, label_visibility="collapsed")

            def _sweep_range(start, end, step):
                start, end, step = int(start), int(end), max(1, int(step))
                if end >= start:
                    return list(range(start, end + 1, step))
                else:
                    return list(range(start, end - 1, -step))

            yaws    = _sweep_range(sw_yaw_s, sw_yaw_e, sw_yaw_t)
            pitches = _sweep_range(sw_pit_s, sw_pit_e, sw_pit_t)
            rolls   = _sweep_range(sw_rol_s, sw_rol_e, sw_rol_t)
            total = len(yaws) * len(pitches) * len(rolls)
            st.caption(t("sweep_total", total))

            if st.form_submit_button(t("sweep_create"), width="stretch"):
                from itertools import product as _product
                pure_yaw = (pitches == [0] and rolls == [0])
                created, failed = 0, 0
                last_id = None
                for yaw_val, pitch_val, roll_val in _product(yaws, pitches, rolls):
                    if pure_yaw:
                        name = f"{sw_name}_{yaw_val:+.0f}deg" if yaw_val != 0 else f"{sw_name}_0deg"
                    else:
                        name = f"{sw_name}_y{yaw_val:+.0f}_p{pitch_val:+.0f}_r{roll_val:+.0f}"
                    sim = api.create_simulation(geo_id, name, "STEADY")
                    if sim:
                        params = {**sim.get("parameters", {}), "velocity_mps": sw_vel}
                        api.update_simulation(sim["id"],
                                              yaw_deg=float(yaw_val),
                                              pitch_deg=float(pitch_val),
                                              roll_deg=float(roll_val),
                                              parameters=params)
                        last_id = sim["id"]
                        created += 1
                    else:
                        failed += 1
                if last_id:
                    st.session_state["sim_id"] = last_id
                st.success(t("sweep_done", created))
                if failed:
                    st.error(t("sweep_fail", failed))
                st.rerun()

    # Batch submit button for pending cases
    pending = [s for s in sims if s["status"] == "PENDING"]
    if pending:
        if st.button(
            f"▶ {t('submit_all_pending', len(pending))}",
            type="primary",
        ):
            ok, fail = 0, 0
            for ps in pending:
                if api.submit_job(ps["id"]):
                    ok += 1
                else:
                    fail += 1
            if ok:
                st.success(t("submit_all_done", ok))
            if fail:
                st.error(t("submit_all_fail", fail))
            st.rerun()

    sim_id = st.session_state.get("sim_id")
    for s, depth in _ordered_with_depth(sims):
        status_icon = {"PENDING": "🔵", "SCHEDULED": "⏳", "MESHING": "🟡", "RUNNING": "🟡",
                       "DONE": "🟢", "FAILED": "🔴"}.get(s["status"], "⚪")
        is_selected = s["id"] == sim_id
        if st.session_state.get(f"editing_sim_{s['id']}"):
            new_name = st.text_input(t("case_name"), value=s["name"],
                                     key=f"rename_sim_{s['id']}", label_visibility="collapsed")
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if st.button(t("save"), key=f"save_sim_{s['id']}", width="stretch"):
                    if new_name.strip() and api.update_simulation(s["id"], name=new_name.strip()):
                        st.session_state.pop(f"editing_sim_{s['id']}", None)
                        st.rerun()
            with bcol2:
                if st.button(t("cancel"), key=f"cancel_sim_{s['id']}", width="stretch"):
                    st.session_state.pop(f"editing_sim_{s['id']}", None)
                    st.rerun()
        else:
            # Indent restart children under their parent with a leading spacer
            if depth > 0:
                cols = st.columns([depth, 4, 1, 1])
                label_col, edit_col, del_col = cols[1], cols[2], cols[3]
            else:
                cols = st.columns([4, 1, 1])
                label_col, edit_col, del_col = cols[0], cols[1], cols[2]
            with label_col:
                prefix = "▶ " if is_selected else ("↳ " if depth > 0 else "")
                label  = f"{prefix}{status_icon} {s['name']}"
                if st.button(label, key=f"sel_{s['id']}", width="stretch"):
                    st.session_state["sim_id"] = s["id"]
                    st.rerun()
            with edit_col:
                if st.button("✏️", key=f"edit_sim_{s['id']}", help=t("rename")):
                    st.session_state[f"editing_sim_{s['id']}"] = True
                    st.rerun()
            with del_col:
                if st.button("🗑", key=f"del_{s['id']}", help=t("delete_case")):
                    _confirm_delete_case(s["id"], s["name"])

    if not sims:
        st.info(t("no_cases"))

# ── Right pane ─────────────────────────────────────────────────
with right:
    sim_id = st.session_state.get("sim_id")
    if not sim_id:
        st.info(t("select_case"))
        st.stop()

    sim = api.get_simulation(sim_id)
    if not sim:
        st.error(t("case_not_found"))
        st.stop()

    # Guard: sim belongs to a different geometry (stale session state)
    if sim.get("geometry_id") != geo_id:
        st.session_state.pop("sim_id", None)
        st.rerun()

    geo = api.get_geometry(geo_id)

    if st.session_state.get("editing_sim_header"):
        new_name = st.text_input(t("case_name"), value=sim["name"],
                                 key="rename_sim_header", label_visibility="collapsed")
        new_desc = st.text_area(t("description"), value=sim.get("description") or "",
                                key="redesc_sim_header", height=80)
        hcol1, hcol2 = st.columns(2)
        with hcol1:
            if st.button(t("save"), key="save_sim_header", width="stretch"):
                if new_name.strip() and api.update_simulation(sim_id, name=new_name.strip(),
                                                              description=new_desc):
                    st.session_state.pop("editing_sim_header", None)
                    st.rerun()
        with hcol2:
            if st.button(t("cancel"), key="cancel_sim_header", width="stretch"):
                st.session_state.pop("editing_sim_header", None)
                st.rerun()
    else:
        hcol1, hcol2, hcol3 = st.columns([5, 1, 1])
        with hcol1:
            st.subheader(f"{sim['name']} — {sim['solver_type']}")
            if sim.get("description"):
                st.caption(sim["description"])
        with hcol2:
            if st.button("✏️", key="edit_sim_header", help=t("rename")):
                st.session_state["editing_sim_header"] = True
                st.rerun()
        with hcol3:
            if sim.get("status") not in ("MESHING", "RUNNING"):
                if st.button(t("delete_btn"), key="del_current_sim"):
                    _confirm_delete_case(sim_id, sim.get("name", f"#{sim_id}"))

    tab_setup, tab_run, tab_mesh, tab_conv, tab_flow = st.tabs([
        t("tab_setup"), t("tab_run"),
        t("tab_mesh"), t("tab_conv"), t("tab_flow"),
    ])

    current_status = sim.get("status", "PENDING")
    _is_done = current_status == "DONE"
    stats = api.get_mesh_stats(sim_id) if _is_done else None

    # ── Setup ──────────────────────────────────────────────────
    with tab_setup:
        # Solver inputs are locked once the case has been submitted (scheduled /
        # meshing / running / done). The backend rejects such edits too; this
        # just makes the read-only state visible. PENDING and FAILED stay editable.
        _setup_locked = current_status in ("SCHEDULED", "MESHING", "RUNNING", "DONE")

        if geo and geo.get("stl_file_path"):
            data = api.get_geometry_3d_data(sim_id)
            if data:
                _show_geometry_3d(data)

        if _setup_locked:
            st.info(t("setup_locked_note"))

        st.markdown(f"#### {t('wind_settings')}")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            velocity = st.number_input(
                t("wind_speed"),
                value=float(sim.get("parameters", {}).get("velocity_mps", 20.0)),
                min_value=0.1, step=1.0, disabled=_setup_locked,
            )
        with col2:
            yaw   = st.number_input(t("yaw_angle"),   value=float(sim.get("yaw_deg", 0.0)),   step=5.0, disabled=_setup_locked)
        with col3:
            pitch = st.number_input(t("pitch_angle"), value=float(sim.get("pitch_deg", 0.0)), step=5.0, disabled=_setup_locked)
        with col4:
            roll  = st.number_input(t("roll_angle"),  value=float(sim.get("roll_deg", 0.0)),  step=5.0, disabled=_setup_locked)

        # ── Gas dispersion settings (dispersion cases only) ─────────
        gas_updates = {}
        if sim.get("parameters", {}).get("case_type") == "dispersion":
            st.markdown(f"#### {t('gas_settings')}")
            st.caption(t("boussinesq_note"))
            gas_updates = _gas_settings_widgets(sim.get("parameters", {}), disabled=_setup_locked)

        if geo and geo.get("stl_file_path"):
            bbox = api.get_geometry_bbox(geo_id)
            if bbox:
                cx, cy, cz = bbox["center"]
                st.caption(f"{t('rotation_center')} ({cx:.3f}, {cy:.3f}, {cz:.3f}) m")
                x0_b, _, _ = bbox["min"]
                x1_b, _, _ = bbox["max"]
                lref_b = x1_b - x0_b
                nu_b = float(sim.get("parameters", {}).get("nu", 1.5e-5))
                re_b = velocity * lref_b / nu_b
                st.caption(f"Re = {re_b:,.0f}  (U = {velocity} m/s, L = {lref_b:.3f} m, ν = {nu_b:.2g} m²/s)")

        if st.button(t("save_settings"), disabled=_setup_locked):
            params = {**sim.get("parameters", {}), "velocity_mps": velocity, **gas_updates}
            result = api.update_simulation(sim_id, yaw_deg=yaw, pitch_deg=pitch, roll_deg=roll, parameters=params)
            if result:
                st.success(t("settings_saved", velocity, yaw, pitch))
                st.rerun()
            else:
                st.error(t("settings_fail"))

        st.markdown(t("current_params"))
        params = sim.get("parameters", {})
        _AUTO_DOMAIN_KEYS = {
            "domain_scale", "domain_xmin", "domain_xmax",
            "domain_yhalf", "domain_zmin", "domain_zmax",
            "blockmesh_nx", "blockmesh_ny", "blockmesh_nz",
            "refbox_min", "refbox_max", "location_in_mesh",
            "auto_domain",
        }
        if params.get("auto_domain", True):
            display_params = {k: v for k, v in params.items() if k not in _AUTO_DOMAIN_KEYS}
            # Compute auto force-ref values from STL bounding box for display
            bbox = api.get_geometry_bbox(geo_id)
            if bbox:
                x0, y0, z0 = bbox["min"]
                x1, y1, z1 = bbox["max"]
                display_params["aref"] = round((y1 - y0) * (z1 - z0), 4)
                display_params["lref"] = round(x1 - x0, 4)
                display_params["cofr"] = [round((x0 + x1) / 2, 4), round((y0 + y1) / 2, 4), round((z0 + z1) / 2, 4)]
            st.caption(t("domain_auto_note"))
        else:
            display_params = params
        solver_type = sim.get("solver_type", "STEADY")
        display_params["nu"] = display_params.get("nu", 1.5e-5)
        display_params["rho_ref"] = 1.0
        default_turb = "kOmegaSST" if solver_type == "STEADY" else "SpalartAllmarasDDES"
        display_params["turbulence_model"] = display_params.get("turbulence_model", default_turb)
        st.json(display_params, expanded=False)

        _chat_section(
            sim_id,
            chat_key="chat_setup",
            heading=t("chat_setup_heading"),
            caption=t("chat_setup_caption"),
            placeholder=t("chat_setup_placeholder"),
        )

    # ── Run & Results ──────────────────────────────────────────
    with tab_run:
        status_color = {"PENDING": "🔵", "SCHEDULED": "⏳", "MESHING": "🟡", "RUNNING": "🟡",
                        "DONE": "🟢", "FAILED": "🔴"}
        st.markdown(f"{t('status_label')} {status_color.get(current_status, '')} {current_status}")
        st.caption(f"{t('turbulence_model_label')}: `{_turbulence_label(sim)}`")

        if geo and not geo.get("stl_file_path"):
            st.warning(t("no_cad"))

        # Runner selection (only shown when both options are available)
        _runner_info = api.get_runner_info()
        _both_available = _runner_info.get("cluster_available") and _runner_info.get("local_available")
        _only_local = _runner_info.get("local_available") and not _runner_info.get("cluster_available")
        if _both_available:
            _runner_choice = st.radio(
                t("runner_select"), ["Cluster", "Local"],
                horizontal=True, key=f"runner_{sim_id}",
            )
            _runner_type = "cluster" if _runner_choice == "Cluster" else "local"
        elif _only_local:
            _runner_type = "local"
        else:
            _runner_type = "cluster"

        # A restart child is built by the parent's restart controls; a plain
        # submit here would rebuild it from the template and destroy that setup.
        _is_child = sim.get("parent_id") is not None
        if _is_child:
            st.caption(t("child_no_submit_note"))

        col1, col2, col3 = st.columns(3)
        with col1:
            disabled = (current_status in ("MESHING", "RUNNING")
                        or (geo and not geo.get("stl_file_path"))
                        or _is_child)
            if st.button(t("submit_job"), disabled=disabled, width="stretch"):
                with st.spinner(t("submitting")):
                    result = api.submit_job(sim_id, runner_type=_runner_type)
                if result:
                    st.success(t("job_submitted", result.get("job_id")))
                    st.rerun()
                else:
                    st.error(t("submit_fail"))
        with col2:
            if st.button(t("refresh"), width="stretch"):
                with st.spinner(t("refresh_status")):
                    api.poll_status(sim_id)
                if current_status in ("MESHING", "RUNNING"):
                    with st.spinner(t("refresh_progress")):
                        st.session_state[f"progress_{sim_id}"] = api.get_job_progress(sim_id)
                else:
                    st.session_state.pop(f"progress_{sim_id}", None)
                st.rerun()
        with col3:
            if st.button(t("stop"),
                         disabled=current_status not in ("MESHING", "RUNNING", "SCHEDULED"),
                         width="stretch"):
                api.cancel_job(sim_id)
                st.rerun()

        prog = st.session_state.get(f"progress_{sim_id}")
        if prog and current_status in ("MESHING", "RUNNING"):
            if prog.get("pct") is not None:
                pct    = prog["pct"]
                cur    = prog["current_time"]
                end    = prog["end_time"]
                solver = prog.get("solver", "")
                phase  = prog.get("phase")
                if phase == 1:
                    label = t("phase1_label", solver)
                elif phase == 2:
                    label = t("phase2_label", solver)
                elif phase == 3:
                    label = t("phase3_label", solver)
                else:
                    label = solver
                st.progress(int(pct), text=f"{label}  Time = {cur} / {end}  ({pct:.1f}%)")
            else:
                st.info(t("solver_log_wait"))

        if current_status == "FAILED" and sim.get("solver_type") == "STEADY":
            _show_restart_expander(sim, sim_id)
        elif not _is_done:
            st.info(t("sim_pending_msg"))
            # Reserve a child now: on the cluster, Torque holds it (afterok) until
            # this job finishes, then runs it automatically. Local can't schedule.
            if (current_status in ("MESHING", "RUNNING", "SCHEDULED")
                    and _runner_info.get("cluster_available")):
                st.divider()
                st.caption(t("reserve_child_note"))
                if sim.get("solver_type") == "STEADY":
                    _show_restart_expander(sim, sim_id, allow_les=True)
                elif sim.get("solver_type") == "UNSTEADY":
                    _show_gas_restart_expander(sim, sim_id)
        else:
            st.success(t("sim_done"))

            # Wall-clock total (submission → completion; includes any queue wait)
            wall_str = None
            started  = sim.get("started_at")
            finished = sim.get("finished_at")
            if started and finished:
                from datetime import datetime
                try:
                    s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    f = datetime.fromisoformat(finished.replace("Z", "+00:00"))
                    wall_str = _fmt_secs((f - s).total_seconds())
                except Exception:
                    pass

            mesh_s   = stats.get("mesh_s") if stats else None
            phase1_s = stats.get("phase1_s") if stats else None
            phase2_s = stats.get("phase2_s") if stats else None
            gas_s    = stats.get("gas_s") if stats else None

            if sim.get("solver_type") == "UNSTEADY":
                # LES: total plus a mesh / Phase 1 / Phase 2 (/ Gas) breakdown
                if wall_str:
                    st.caption(f"{t('calc_time')}: {wall_str}")
                if mesh_s is not None:
                    st.caption(f"{t('mesh_gen_time')}: {_fmt_secs(mesh_s)}")
                if phase1_s is not None:
                    st.caption(f"{t('residuals_phase1')}: {_fmt_secs(phase1_s)}")
                if phase2_s is not None:
                    st.caption(f"{t('residuals_phase2')}: {_fmt_secs(phase2_s)}")
                if gas_s is not None:
                    st.caption(f"{t('residuals_phase3')}: {_fmt_secs(gas_s)}")
            else:
                # STEADY: mesh generation and the solver ClockTime (falls back to
                # the wall total when the solver log has no ClockTime)
                if mesh_s is not None:
                    st.caption(f"{t('mesh_gen_time')}: {_fmt_secs(mesh_s)}")
                if phase1_s is not None:
                    st.caption(f"{t('calc_time')}: {_fmt_secs(phase1_s)}")
                elif wall_str:
                    st.caption(f"{t('calc_time')}: {wall_str}")

            if stats:
                mem_parts = []
                snappy_kb = stats.get("peak_memory_snappy_kb")
                simple_kb = stats.get("peak_memory_simple_kb")
                piso_kb = stats.get("peak_memory_piso_kb")
                gas_kb = stats.get("peak_memory_gas_kb")
                if snappy_kb:
                    mem_parts.append(f"snappyHexMesh: {snappy_kb/1024/1024:.1f} GB")
                if simple_kb:
                    mem_parts.append(f"simpleFoam: {simple_kb/1024/1024:.1f} GB")
                if piso_kb:
                    mem_parts.append(f"pisoFoam: {piso_kb/1024/1024:.1f} GB")
                if gas_kb:
                    mem_parts.append(f"gasLES: {gas_kb/1024/1024:.1f} GB")
                if mem_parts:
                    st.caption(f"{t('stat_peak_memory')}: {' / '.join(mem_parts)}")

            if sim.get("solver_type") == "STEADY":
                _show_restart_expander(sim, sim_id, allow_les=True)
            elif sim.get("solver_type") == "UNSTEADY":
                _show_gas_restart_expander(sim, sim_id)

        _show_run_conditions(sim)

        _results_chat(sim_id, "chat_run")

    # ── Mesh ────────────────────────────────────
    with tab_mesh:
        _mesh_live = current_status in ("MESHING", "RUNNING")
        if sim.get("parent_id"):
            # Restart children reuse the parent's mesh — point to the parent.
            st.info(t("mesh_child_note"))
            if st.button(t("go_to_parent"), key=f"go_parent_{sim_id}"):
                st.session_state["sim_id"] = sim["parent_id"]
                st.rerun()
        elif not _is_done and not _mesh_live:
            st.info(t("sim_pending_msg"))
        elif _mesh_live:
            # The surface mesh is written when the job finishes; refresh polls
            # the cluster and, once the job is DONE, fetches the mesh.
            st.caption(t("mesh_live_note"))
            if st.button(t("refresh"), key="mesh_refresh"):
                with st.spinner(t("refresh_status")):
                    api.poll_status(sim_id)
                st.rerun()
        else:
            st.subheader(t("surface_mesh"))

            # Mesh stats
            if stats:
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric(t("stat_total_cells"),   f"{stats.get('cells', '-'):,}" if stats.get('cells') else "-")
                mc2.metric(t("stat_surface_cells"), f"{stats.get('surface_cells', '-'):,}" if stats.get('surface_cells') else "-")
                mc3.metric(t("stat_max_non_ortho"), f"{stats.get('max_non_ortho', '-'):.1f}°" if stats.get('max_non_ortho') else "-")
                mc4.metric(t("stat_max_skewness"),  f"{stats.get('max_skewness', '-'):.2f}" if stats.get('max_skewness') else "-")

            view_opt = st.radio(t("view"),
                                [t("view_iso"), t("view_top"), t("view_side")],
                                horizontal=True, key="mesh_view")
            view_map = {t("view_iso"): "iso", t("view_top"): "top", t("view_side"): "side"}
            img = api.get_mesh_plot(sim_id, view=view_map[view_opt])
            if img:
                st.image(img, width="stretch")
            else:
                st.info(t("mesh_not_found"))

        _results_chat(sim_id, "chat_mesh")

    # ── Convergence ──────────────────────────────
    with tab_conv:
        _conv_live = current_status in ("MESHING", "RUNNING")
        if not _is_done and not _conv_live:
            st.info(t("sim_pending_msg"))
        else:
            if _conv_live:
                st.caption(t("conv_live_note"))
                if st.button(t("refresh"), key="conv_refresh"):
                    with st.spinner(t("refresh_progress")):
                        api.sync_live_results(sim_id)
                    st.rerun()
            st.subheader(t("residuals"))
            if sim.get("solver_type") == "UNSTEADY" and sim.get("parent_id"):
                # Restart child: show only the stage its own run produced. The
                # parent's earlier history (steady/RAS, ancestor LES) belongs to
                # the parent case and is viewable there.
                own_phase = 3 if sim.get("parameters", {}).get("gas_les") else 2
                img = api.get_residuals_plot(sim_id, phase=own_phase)
                if img:
                    st.image(img, width="stretch")
                else:
                    st.info(t("log_not_found"))
            elif sim.get("solver_type") == "UNSTEADY":
                # Legacy standalone unsteady case: stack the phases top to bottom
                # (RAS, LES, and — if restarted into gas dispersion — the gas LES)
                _phases = [(1, t("residuals_phase1")), (2, t("residuals_phase2"))]
                if sim.get("parameters", {}).get("gas_les"):
                    _phases.append((3, t("residuals_phase3")))
                for phase, label in _phases:
                    st.markdown(f"**{label}**")
                    img = api.get_residuals_plot(sim_id, phase=phase)
                    if img:
                        st.image(img, width="stretch")
                    else:
                        st.info(t("log_not_found"))
            else:
                img = api.get_residuals_plot(sim_id)
                if img:
                    st.image(img, width="stretch")
                else:
                    st.info(t("log_not_found"))

            st.divider()
            st.subheader(t("force_coeffs"))
            img = api.get_force_coefficients_plot(sim_id)
            if img:
                st.image(img, width="stretch")
            else:
                st.info(t("fc_not_found"))

        _results_chat(sim_id, "chat_conv")

    # ── Visualization ────────────────────────────
    with tab_flow:
        # Live cutting-plane mid-run for any solver: steady writes yNormal at each
        # writeInterval, so the y=0 mesh (field="mesh") is viewable as soon as the
        # solver has written its first output — no need to wait for the run to finish.
        _flow_live = current_status in ("MESHING", "RUNNING")
        if not _is_done and not _flow_live:
            st.info(t("sim_pending_msg"))
        else:
            if _flow_live:
                st.caption(t("flow_live_note"))
                if st.button(t("refresh"), key="flow_refresh"):
                    st.rerun()
            st.subheader(t("cutting_plane"))
            field_opts = [t("field_p"), t("field_u"), t("field_mesh")]
            field_map = {t("field_p"): "p", t("field_u"): "U", t("field_mesh"): "mesh"}
            if sim.get("parameters", {}).get("gas_les"):
                # Compressible 2-species stage: concentration = GAS mass fraction
                field_opts.insert(2, t("field_c"))
                field_map[t("field_c")] = "GAS"
                field_opts.insert(3, t("field_t"))
                field_map[t("field_t")] = "T"
            elif sim.get("parameters", {}).get("case_type") == "dispersion":
                field_opts.insert(2, t("field_c"))
                field_map[t("field_c")] = "T"
            field = st.selectbox(t("field"), field_opts, key="field_select")
            field_key = field_map[field]
            img = api.get_cutting_plane_plot(sim_id, field=field_key)
            if img:
                st.image(img, width="stretch")
            else:
                st.info(t("plane_not_found"))

            st.divider()
            stream_type = st.radio(
                "Streamline type",
                ["Free-space streamlines", "Wall-bounded streamlines"],
                horizontal=True, key="stream_type",
            )
            st.subheader(t("streamlines") if stream_type == "Free-space streamlines" else "Wall-bounded streamlines")
            interactive = st.toggle("インタラクティブ 3D", value=False, key="stream_3d")

            if stream_type == "Wall-bounded streamlines":
                paths = api.get_wall_streamlines_paths(sim_id)
                if paths:
                    try:
                        import pyvista as pv
                        import numpy as np
                        import plotly.graph_objects as go

                        vtk_paths = paths["vtk_paths"]
                        meshes, u_mags = [], []
                        for vp in vtk_paths:
                            m = pv.read(vp)
                            if m.n_points == 0:
                                continue
                            u = np.linalg.norm(m.point_data["U"], axis=1) if (
                                "U" in m.point_data and m.point_data["U"].ndim == 2
                            ) else None
                            meshes.append(m)
                            u_mags.append(u)

                        if meshes:
                            u_max = max(
                                (float(u.max()) for u in u_mags if u is not None), default=1.0
                            )
                            fig = go.Figure()
                            if paths.get("stl_path"):
                                stl = pv.read(paths["stl_path"])
                                stl = stl.triangulate()
                                faces = stl.faces.reshape(-1, 4)[:, 1:]
                                fig.add_trace(go.Mesh3d(
                                    x=stl.points[:, 0], y=stl.points[:, 1], z=stl.points[:, 2],
                                    i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                                    color="lightgray", opacity=1.0,
                                    showlegend=False, hoverinfo="skip", flatshading=True,
                                    lighting=dict(ambient=0.8, diffuse=0.5),
                                ))
                            for mesh, u_mag in zip(meshes, u_mags):
                                pts = mesh.points
                                xs, ys, zs, cs = [], [], [], []
                                for i in range(mesh.n_cells):
                                    cell = mesh.get_cell(i)
                                    idx = [cell.point_ids[j] for j in range(cell.n_points)]
                                    xs.extend(pts[idx, 0].tolist()); xs.append(None)
                                    ys.extend(pts[idx, 1].tolist()); ys.append(None)
                                    zs.extend(pts[idx, 2].tolist()); zs.append(None)
                                    if u_mag is not None:
                                        cs.extend((u_mag[idx] / u_max).tolist()); cs.append(float("nan"))
                                fig.add_trace(go.Scatter3d(
                                    x=xs, y=ys, z=zs, mode="lines",
                                    line=dict(width=2, color=cs if cs else "orange",
                                              colorscale="Jet", cmin=0, cmax=1),
                                    showlegend=False, hoverinfo="skip",
                                ))
                            fig.update_layout(
                                scene=dict(xaxis_title="X", yaxis_title="Y", zaxis_title="Z",
                                           aspectmode="data"),
                                height=600, margin=dict(l=0, r=0, t=0, b=0),
                            )
                            st.plotly_chart(fig)
                        else:
                            st.info("Wall-bounded streamline files are empty.")
                    except Exception as e:
                        st.error(f"3D表示エラー: {e}")
                else:
                    st.info("Wall-bounded streamlines data not available locally. "
                            "This data is stored on the cluster and excluded from the default rsync "
                            "to reduce download size (~500 MB/case).")

            else:
                # --- Free-space streamlines ---
                if interactive:
                    try:
                        import pyvista as pv
                        import numpy as np
                        import plotly.graph_objects as go

                        paths = api.get_streamlines_paths(sim_id)
                        if paths:
                            meshes, u_mags = [], []
                            for vp in paths["vtk_paths"]:
                                m = pv.read(vp)
                                if m.n_points == 0:
                                    continue
                                u = np.linalg.norm(m.point_data["U"], axis=1) if (
                                    "U" in m.point_data and m.point_data["U"].ndim == 2
                                ) else None
                                meshes.append(m)
                                u_mags.append(u)
                            u_max = max(
                                (float(u.max()) for u in u_mags if u is not None), default=1.0
                            )
                            fig = go.Figure()
                            if paths.get("stl_path"):
                                stl = pv.read(paths["stl_path"])
                                stl = stl.triangulate()
                                faces = stl.faces.reshape(-1, 4)[:, 1:]
                                fig.add_trace(go.Mesh3d(
                                    x=stl.points[:, 0], y=stl.points[:, 1], z=stl.points[:, 2],
                                    i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                                    color="lightgray", opacity=1.0,
                                    showlegend=False, hoverinfo="skip", flatshading=True,
                                    lighting=dict(ambient=0.8, diffuse=0.5),
                                ))
                            for mesh, u_mag in zip(meshes, u_mags):
                                pts = mesh.points
                                xs, ys, zs, cs = [], [], [], []
                                for i in range(mesh.n_cells):
                                    cell = mesh.get_cell(i)
                                    idx = [cell.point_ids[j] for j in range(cell.n_points)]
                                    xs.extend(pts[idx, 0].tolist()); xs.append(None)
                                    ys.extend(pts[idx, 1].tolist()); ys.append(None)
                                    zs.extend(pts[idx, 2].tolist()); zs.append(None)
                                    if u_mag is not None:
                                        cs.extend((u_mag[idx] / u_max).tolist()); cs.append(float("nan"))
                                fig.add_trace(go.Scatter3d(
                                    x=xs, y=ys, z=zs, mode="lines",
                                    line=dict(width=2, color=cs if cs else "orange",
                                              colorscale="Jet", cmin=0, cmax=1),
                                    showlegend=False, hoverinfo="skip",
                                ))
                            fig.update_layout(
                                scene=dict(xaxis_title="X", yaxis_title="Y", zaxis_title="Z",
                                           aspectmode="data"),
                                height=600, margin=dict(l=0, r=0, t=0, b=0),
                            )
                            st.plotly_chart(fig)
                        else:
                            st.info(t("stream_not_found"))
                    except Exception as e:
                        st.error(f"3D表示エラー: {e}")
                else:
                    img = api.get_streamlines_plot(sim_id)
                    if img:
                        st.image(img, width="stretch")
                    else:
                        st.info(t("stream_not_found"))

            # ── LES animation (unsteady only) ─────────────────
            if sim.get("solver_type") == "UNSTEADY":
                st.divider()
                st.subheader(t("anim_section"))
                anim_map = {
                    t("anim_plane_u"): ("plane", "U"),
                    t("anim_plane_p"): ("plane", "p"),
                    t("anim_stream"):  ("streamlines", "U"),
                }
                if sim.get("parameters", {}).get("gas_les"):
                    anim_map[t("anim_plane_c")] = ("plane", "GAS")
                anim_sel = st.selectbox(t("anim_kind"), list(anim_map.keys()), key="anim_kind")
                anim_kind, anim_field = anim_map[anim_sel]
                anim_cache_key = f"anim_{sim_id}_{anim_kind}_{anim_field}"
                anim_force = st.checkbox(t("anim_force_refetch"), value=False, key="anim_force",
                                         help=t("anim_force_refetch_help"))
                if st.button(t("anim_generate"), key="anim_btn"):
                    with st.spinner(t("anim_generating")):
                        video = api.get_animation(sim_id, kind=anim_kind, field=anim_field,
                                                  force=anim_force)
                    if video:
                        st.session_state[anim_cache_key] = video
                    else:
                        st.error(t("anim_not_available"))
                if st.session_state.get(anim_cache_key):
                    # loop + muted autoplay so the LES animation repeats on its own
                    st.video(st.session_state[anim_cache_key],
                             loop=True, autoplay=True, muted=True)

        _results_chat(sim_id, "chat_flow")
