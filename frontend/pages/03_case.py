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
