"""Case setup, execution, and results page."""
import streamlit as st

import frontend.api_client as api
from frontend.i18n import t


def _show_restart_expander(sim: dict, sim_id: int) -> None:
    with st.expander(t("restart_job"), expanded=False):
        current_end = int(sim.get("parameters", {}).get("end_time", 500))
        st.caption(f"{t('restart_current_end')}: {current_end}")
        add_steps = st.number_input(
            t("restart_add_steps"),
            min_value=1,
            value=500,
            step=100,
            key="restart_add_steps_input",
        )
        new_end = current_end + int(add_steps)
        st.caption(f"{t('restart_new_end')}: {new_end}")
        if st.button(t("restart_job"), key="restart_btn", use_container_width=True):
            with st.spinner(t("restarting")):
                result = api.restart_job(sim_id, new_end)
            if result:
                st.success(t("restart_ok", result.get("job_id")))
                st.rerun()
            else:
                st.error(t("restart_fail"))

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


def _chat_section(sim_id: int, chat_key: str, heading: str, caption: str, placeholder: str):
    st.divider()
    st.markdown(f"#### 💬 {heading}")
    st.caption(caption)
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


left, right = st.columns([1, 2])

# ── Left pane ──────────────────────────────────────────────────
with left:
    st.subheader(t("cases_title"))
    sims = api.list_simulations(geo_id)

    # Auto-create a default case if none exist so chat is immediately usable
    if not sims and not st.session_state.get("_autocreated_geo", 0) == geo_id:
        default = api.create_simulation(geo_id, "case_01", "STEADY")
        if default:
            st.session_state["sim_id"] = default["id"]
            st.session_state["_autocreated_geo"] = geo_id
            st.rerun()

    with st.expander(t("new_case"), expanded=not sims):
        with st.form("new_sim_form"):
            sim_name = st.text_input(t("case_name"))
            solver   = st.selectbox(t("solver"), ["STEADY", "UNSTEADY"])
            if st.form_submit_button(t("create"), use_container_width=True):
                result = api.create_simulation(geo_id, sim_name, solver)
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
            with yc1: sw_yaw_s = st.number_input("ys", value=0,  step=5,  label_visibility="collapsed")
            with yc2: sw_yaw_e = st.number_input("ye", value=0,  step=5,  label_visibility="collapsed")
            with yc3: sw_yaw_t = st.number_input("yt", value=10, step=5, min_value=1, label_visibility="collapsed")

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

            if st.form_submit_button(t("sweep_create"), use_container_width=True):
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
            use_container_width=True,
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
    for s in sims:
        status_icon = {"PENDING": "🔵", "MESHING": "🟡", "RUNNING": "🟡",
                       "DONE": "🟢", "FAILED": "🔴"}.get(s["status"], "⚪")
        is_selected = s["id"] == sim_id
        if st.session_state.get(f"editing_sim_{s['id']}"):
            new_name = st.text_input(t("case_name"), value=s["name"],
                                     key=f"rename_sim_{s['id']}", label_visibility="collapsed")
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if st.button(t("save"), key=f"save_sim_{s['id']}", use_container_width=True):
                    if new_name.strip() and api.update_simulation(s["id"], name=new_name.strip()):
                        st.session_state.pop(f"editing_sim_{s['id']}", None)
                        st.rerun()
            with bcol2:
                if st.button(t("cancel"), key=f"cancel_sim_{s['id']}", use_container_width=True):
                    st.session_state.pop(f"editing_sim_{s['id']}", None)
                    st.rerun()
        else:
            cols = st.columns([4, 1, 1])
            with cols[0]:
                prefix = "▶ " if is_selected else ""
                label  = f"{prefix}{status_icon} {s['name']}"
                if st.button(label, key=f"sel_{s['id']}", use_container_width=True):
                    st.session_state["sim_id"] = s["id"]
                    st.rerun()
            with cols[1]:
                if st.button("✏️", key=f"edit_sim_{s['id']}", help=t("rename")):
                    st.session_state[f"editing_sim_{s['id']}"] = True
                    st.rerun()
            with cols[2]:
                if st.button("🗑", key=f"del_{s['id']}", help=t("delete_case")):
                    if api.delete_simulation(s["id"]):
                        if st.session_state.get("sim_id") == s["id"]:
                            st.session_state.pop("sim_id", None)
                        st.rerun()

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

    geo = api.get_geometry(geo_id)

    if st.session_state.get("editing_sim_header"):
        hcol1, hcol2, hcol3 = st.columns([4, 1, 1])
        with hcol1:
            new_name = st.text_input(t("case_name"), value=sim["name"],
                                     key="rename_sim_header", label_visibility="collapsed")
        with hcol2:
            if st.button(t("save"), key="save_sim_header", use_container_width=True):
                if new_name.strip() and api.update_simulation(sim_id, name=new_name.strip()):
                    st.session_state.pop("editing_sim_header", None)
                    st.rerun()
        with hcol3:
            if st.button(t("cancel"), key="cancel_sim_header", use_container_width=True):
                st.session_state.pop("editing_sim_header", None)
                st.rerun()
    else:
        hcol1, hcol2, hcol3 = st.columns([5, 1, 1])
        with hcol1:
            st.subheader(f"{sim['name']} — {sim['solver_type']}")
        with hcol2:
            if st.button(t("rename_btn"), key="edit_sim_header"):
                st.session_state["editing_sim_header"] = True
                st.rerun()
        with hcol3:
            if sim.get("status") not in ("MESHING", "RUNNING"):
                if st.button(t("delete_btn"), key="del_current_sim"):
                    if api.delete_simulation(sim_id):
                        st.session_state.pop("sim_id", None)
                        st.rerun()

    tab_setup, tab_run = st.tabs([t("tab_setup"), t("tab_run")])

    # ── Setup ──────────────────────────────────────────────────
    with tab_setup:
        if geo and geo.get("stl_file_path"):
            preview = api.get_geometry_preview(sim_id)
            if preview:
                st.image(preview, caption=t("geo_preview"), use_container_width=True)

        st.markdown(f"#### {t('wind_settings')}")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            velocity = st.number_input(
                t("wind_speed"),
                value=float(sim.get("parameters", {}).get("velocity_mps", 20.0)),
                min_value=0.1, step=1.0,
            )
        with col2:
            yaw   = st.number_input(t("yaw_angle"),   value=float(sim.get("yaw_deg", 0.0)),   step=5.0)
        with col3:
            pitch = st.number_input(t("pitch_angle"), value=float(sim.get("pitch_deg", 0.0)), step=5.0)
        with col4:
            roll  = st.number_input(t("roll_angle"),  value=float(sim.get("roll_deg", 0.0)),  step=5.0)

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

        if st.button(t("save_settings")):
            params = {**sim.get("parameters", {}), "velocity_mps": velocity}
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
            "domain_yhalf", "domain_zmax",
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
                display_params["cofr"] = [
                    round((x0 + x1) / 2, 4),
                    round((y0 + y1) / 2, 4),
                    round((z0 + z1) / 2, 4),
                ]
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
        current_status = sim.get("status", "PENDING")
        status_color = {"PENDING": "🔵", "MESHING": "🟡", "RUNNING": "🟡",
                        "DONE": "🟢", "FAILED": "🔴"}
        st.markdown(f"{t('status_label')} {status_color.get(current_status, '')} {current_status}")

        if geo and not geo.get("stl_file_path"):
            st.warning(t("no_cad"))

        col1, col2, col3 = st.columns(3)
        with col1:
            disabled = current_status in ("MESHING", "RUNNING") or (geo and not geo.get("stl_file_path"))
            if st.button(t("submit_job"), disabled=disabled, use_container_width=True):
                with st.spinner(t("submitting")):
                    result = api.submit_job(sim_id)
                if result:
                    st.success(t("job_submitted", result.get("job_id")))
                    st.rerun()
                else:
                    st.error(t("submit_fail"))
        with col2:
            if st.button(t("refresh"), use_container_width=True):
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
                         disabled=current_status not in ("MESHING", "RUNNING"),
                         use_container_width=True):
                api.cancel_job(sim_id)
                st.rerun()

        prog = st.session_state.get(f"progress_{sim_id}")
        if prog and current_status in ("MESHING", "RUNNING"):
            if prog.get("pct") is not None:
                pct    = prog["pct"]
                cur    = prog["current_time"]
                end    = prog["end_time"]
                solver = prog.get("solver", "")
                st.progress(int(pct), text=f"{solver}  Time = {cur} / {end}  ({pct:.1f}%)")
            else:
                st.info(t("solver_log_wait"))

        if current_status == "FAILED" and sim.get("solver_type") == "STEADY":
            _show_restart_expander(sim, sim_id)
            st.stop()

        if current_status != "DONE":
            st.info(t("sim_pending_msg"))
            st.stop()

        st.success(t("sim_done"))

        started  = sim.get("started_at")
        finished = sim.get("finished_at")
        if started and finished:
            from datetime import datetime, timezone
            fmt = "%Y-%m-%dT%H:%M:%S"
            try:
                s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                f = datetime.fromisoformat(finished.replace("Z", "+00:00"))
                elapsed = int((f - s).total_seconds())
                h, rem = divmod(elapsed, 3600)
                m, sec = divmod(rem, 60)
                elapsed_str = f"{h}h {m}m {sec}s" if h else f"{m}m {sec}s"
                st.caption(f"{t('calc_time')}: {elapsed_str}")
            except Exception:
                pass

        stats = api.get_mesh_stats(sim_id)
        if stats:
            mem_parts = []
            simple_kb = stats.get("peak_memory_simple_kb")
            snappy_kb = stats.get("peak_memory_snappy_kb")
            if simple_kb:
                mem_parts.append(f"simpleFoam: {simple_kb/1024/1024:.1f} GB")
            if snappy_kb:
                mem_parts.append(f"snappyHexMesh: {snappy_kb/1024/1024:.1f} GB")
            if mem_parts:
                st.caption(f"{t('stat_peak_memory')}: {' / '.join(mem_parts)}")

        # ── Restart ────────────────────────────────────────────────
        if sim.get("solver_type") == "STEADY":
            _show_restart_expander(sim, sim_id)

        st.divider()

        tab_mesh, tab_conv, tab_fc, tab_plane, tab_stream = st.tabs([
            t("tab_mesh"), t("tab_conv"),
            t("tab_fc"),   t("tab_plane"), t("tab_stream"),
        ])

        with tab_mesh:
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
                st.image(img, use_container_width=True)
            else:
                st.info(t("mesh_not_found"))

        with tab_conv:
            st.subheader(t("residuals"))
            img = api.get_residuals_plot(sim_id)
            if img:
                st.image(img, use_container_width=True)
            else:
                st.info(t("log_not_found"))

        with tab_fc:
            st.subheader(t("force_coeffs"))
            img = api.get_force_coefficients_plot(sim_id)
            if img:
                st.image(img, use_container_width=True)
            else:
                st.info(t("fc_not_found"))

        with tab_plane:
            st.subheader(t("cutting_plane"))
            field_opts = [t("field_p"), t("field_u"), t("field_mesh")]
            field = st.selectbox(t("field"), field_opts, key="field_select")
            field_key = "p" if field == t("field_p") else ("mesh" if field == t("field_mesh") else "U")
            img = api.get_cutting_plane_plot(sim_id, field=field_key)
            if img:
                st.image(img, use_container_width=True)
            else:
                st.info(t("plane_not_found"))

        with tab_stream:
            st.subheader(t("streamlines"))
            interactive = st.toggle("インタラクティブ 3D", value=False, key="stream_3d")
            if interactive:
                try:
                    import pyvista as pv
                    import numpy as np
                    import plotly.graph_objects as go

                    paths = api.get_streamlines_paths(sim_id)
                    if paths:
                        # 全VTPのU_mag最大値を先に計算
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

                        # STL 物体を半透明メッシュで追加（67万→3万三角形に間引き）
                        if paths.get("stl_path"):
                            stl = pv.read(paths["stl_path"])
                            stl = stl.triangulate()
                            faces = stl.faces.reshape(-1, 4)[:, 1:]
                            fig.add_trace(go.Mesh3d(
                                x=stl.points[:, 0],
                                y=stl.points[:, 1],
                                z=stl.points[:, 2],
                                i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                                color="lightgray", opacity=1.0,
                                showlegend=False, hoverinfo="skip",
                                flatshading=True,
                                lighting=dict(ambient=0.8, diffuse=0.5),
                            ))

                        # VTPファイルごとに1トレース（全トラックをNoneで結合）
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
                                line=dict(width=2,
                                          color=cs if cs else "orange",
                                          colorscale="Jet", cmin=0, cmax=1),
                                showlegend=False, hoverinfo="skip",
                            ))

                        fig.update_layout(
                            scene=dict(
                                xaxis_title="X", yaxis_title="Y", zaxis_title="Z",
                                aspectmode="data",
                            ),
                            height=600,
                            margin=dict(l=0, r=0, t=0, b=0),
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info(t("stream_not_found"))
                except Exception as e:
                    st.error(f"3D表示エラー: {e}")
            else:
                img = api.get_streamlines_plot(sim_id)
                if img:
                    st.image(img, use_container_width=True)
                else:
                    st.info(t("stream_not_found"))

        _chat_section(
            sim_id,
            chat_key="chat_results",
            heading=t("chat_results_heading"),
            caption=t("chat_results_caption"),
            placeholder=t("chat_results_placeholder"),
        )
