"""Geometry management page."""
import streamlit as st

import frontend.api_client as api
from frontend.i18n import t


def _show_geometry_3d(stl_path: str) -> None:
    import plotly.graph_objects as go
    import pyvista as pv

    try:
        mesh = pv.read(stl_path).triangulate()
    except Exception:
        return

    verts = mesh.points
    faces = mesh.faces.reshape(-1, 4)[:, 1:]

    fig = go.Figure(go.Mesh3d(
        x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
        i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
        color="lightsteelblue", opacity=0.85,
        flatshading=False,
        lighting=dict(ambient=0.4, diffuse=0.8, specular=0.3),
        lightposition=dict(x=1, y=1, z=2),
    ))
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=400,
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

if "token" not in st.session_state:
    st.warning(t("login_required"))
    st.stop()

project_id = st.session_state.get("project_id")
if not project_id:
    st.warning(t("select_project"))
    if st.button(t("go_projects")):
        st.switch_page("pages/01_projects.py")
    st.stop()

st.title(f"🔷 {t('geometry_title')} — {st.session_state.get('project_name', '')}")


@st.dialog(t("delete_confirm_geo_title"))
def _confirm_delete_geo(gid: int, name: str):
    st.warning(t("delete_confirm_geo_msg", name))
    c1, c2 = st.columns(2)
    with c1:
        if st.button(t("delete_confirm_yes"), type="primary", width="stretch",
                     key=f"cdg_yes_{gid}"):
            if api.delete_geometry(gid):
                if st.session_state.get("geo_id") == gid:
                    st.session_state.pop("geo_id", None)
                    st.session_state.pop("geo_name", None)
                    st.session_state.pop("sim_id", None)
                st.rerun()
            else:
                st.error(t("delete_failed"))
    with c2:
        if st.button(t("cancel"), width="stretch", key=f"cdg_no_{gid}"):
            st.rerun()


with st.expander(t("add_geometry"), expanded=False):
    with st.form("new_geo_form"):
        geo_name = st.text_input(t("geo_name_hint"))
        uploaded = st.file_uploader(
            t("file_drop"),
            type=["stl", "step", "stp", "iges", "igs", "obj"],
        )
        upload_scale = st.number_input(
            t("scale_factor"), value=1.0, min_value=1e-6, format="%.6f",
            help="1.0 = no change, 0.001 = mm→m",
        )
        st.info(t("rotation_wind_note"))
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            upload_yaw   = st.number_input("Yaw (°)",   value=0.0, format="%.1f", help="Z軸回転")
        with rc2:
            upload_pitch = st.number_input("Pitch (°)", value=0.0, format="%.1f", help="Y軸回転")
        with rc3:
            upload_roll  = st.number_input("Roll (°)",  value=0.0, format="%.1f", help="X軸回転")
        if st.form_submit_button(t("upload"), width="stretch"):
            if uploaded:
                with st.spinner(t("converting")):
                    name = geo_name or uploaded.name.rsplit(".", 1)[0]
                    geo = api.create_geometry(project_id, name)
                    result = api.upload_cad(geo["id"], uploaded.read(), uploaded.name,
                                            scale=upload_scale, yaw=upload_yaw,
                                            pitch=upload_pitch, roll=upload_roll) if geo else None
                if result:
                    st.session_state["geo_id"] = result["id"]
                    st.session_state["geo_name"] = result["name"]
                    st.session_state.pop("sim_id", None)
                    st.success(t("geo_uploaded", result["name"]))
                    st.rerun()
                else:
                    st.error(t("upload_fail"))
            else:
                st.warning(t("select_file"))

geos = api.list_geometries(project_id)
geo_id = st.session_state.get("geo_id")

if not geos:
    st.info(t("no_geometries"))
else:
    for geo in geos:
        is_selected = geo["id"] == geo_id
        if st.session_state.get(f"editing_geo_{geo['id']}"):
            new_name = st.text_input(t("geo_name_hint"), value=geo["name"],
                                     key=f"rename_geo_{geo['id']}", label_visibility="collapsed")
            new_desc = st.text_area(t("description"), value=geo.get("description") or "",
                                    key=f"redesc_geo_{geo['id']}", height=80)
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if st.button(t("save"), key=f"save_geo_{geo['id']}", width="stretch"):
                    if new_name.strip() and api.rename_geometry(geo["id"], new_name.strip(),
                                                                description=new_desc):
                        if st.session_state.get("geo_id") == geo["id"]:
                            st.session_state["geo_name"] = new_name.strip()
                        st.session_state.pop(f"editing_geo_{geo['id']}", None)
                        st.rerun()
            with bcol2:
                if st.button(t("cancel"), key=f"cancel_geo_{geo['id']}", width="stretch"):
                    st.session_state.pop(f"editing_geo_{geo['id']}", None)
                    st.rerun()
        else:
            cols = st.columns([4, 1, 1, 1, 1])
            with cols[0]:
                prefix  = "▶ " if is_selected else ""
                name_md = f"**{prefix}{geo['name']}**" if is_selected else f"{prefix}{geo['name']}"
                stl_label = t("stl_ready") if geo.get("stl_file_path") else t("no_stl")
                st.markdown(f"{name_md}  \n{stl_label}")
                if geo.get("description"):
                    st.caption(geo["description"])
            with cols[1]:
                if st.button(t("go_cases"), key=f"open_{geo['id']}", width="stretch"):
                    st.session_state["geo_id"] = geo["id"]
                    st.session_state["geo_name"] = geo["name"]
                    st.session_state.pop("sim_id", None)
                    st.switch_page("pages/03_case.py")
            with cols[2]:
                if geo.get("cad_file_path"):
                    dl_key = f"dl_data_{geo['id']}"
                    if dl_key in st.session_state:
                        file_bytes, filename = st.session_state[dl_key]
                        st.download_button("⬇", data=file_bytes, file_name=filename,
                                           key=f"dl_{geo['id']}", help=t("download_cad"),
                                           width="stretch")
                    else:
                        if st.button("⬇", key=f"dl_fetch_{geo['id']}", help=t("download_cad"),
                                     width="stretch"):
                            result = api.download_cad(geo["id"])
                            if result:
                                st.session_state[dl_key] = result
                                st.rerun()
            with cols[3]:
                if st.button("✏️", key=f"edit_geo_{geo['id']}", help=t("rename")):
                    st.session_state[f"editing_geo_{geo['id']}"] = True
                    st.rerun()
            with cols[4]:
                if st.button("🗑", key=f"del_{geo['id']}", help=t("delete_geo")):
                    _confirm_delete_geo(geo["id"], geo["name"])
        if not geo.get("stl_file_path"):
            with st.container():
                up = st.file_uploader(
                    t("file_drop"), type=["stl", "step", "stp", "iges", "igs", "obj"],
                    key=f"reupload_{geo['id']}",
                )
                ru_scale = st.number_input(
                    t("scale_factor"), value=1.0, min_value=1e-6, format="%.6f",
                    help="1.0 = no change, 0.001 = mm→m", key=f"ru_scale_{geo['id']}",
                )
                st.info(t("rotation_wind_note"))
                rrc1, rrc2, rrc3 = st.columns(3)
                with rrc1:
                    ru_yaw   = st.number_input("Yaw (°)",   value=0.0, format="%.1f",
                                               help="Z軸回転", key=f"ru_yaw_{geo['id']}")
                with rrc2:
                    ru_pitch = st.number_input("Pitch (°)", value=0.0, format="%.1f",
                                               help="Y軸回転", key=f"ru_pitch_{geo['id']}")
                with rrc3:
                    ru_roll  = st.number_input("Roll (°)",  value=0.0, format="%.1f",
                                               help="X軸回転", key=f"ru_roll_{geo['id']}")
                if up:
                    with st.spinner(t("converting")):
                        result = api.upload_cad(geo["id"], up.read(), up.name, scale=ru_scale,
                                                yaw=ru_yaw, pitch=ru_pitch, roll=ru_roll)
                    if result:
                        st.success(t("geo_uploaded", result["name"]))
                        st.rerun()
                    else:
                        st.error(t("upload_fail"))

        if geo.get("stl_file_path"):
            try:
                import pyvista as pv
                _mesh = pv.read(geo["stl_file_path"])
                _mn = _mesh.bounds[0::2]  # xmin, ymin, zmin
                _mx = _mesh.bounds[1::2]  # xmax, ymax, zmax
                st.caption(
                    f"Bounding box — "
                    f"X: {_mn[0]:.3f} → {_mx[0]:.3f} m &nbsp;|&nbsp; "
                    f"Y: {_mn[1]:.3f} → {_mx[1]:.3f} m &nbsp;|&nbsp; "
                    f"Z: {_mn[2]:.3f} → {_mx[2]:.3f} m &nbsp;|&nbsp; "
                    f"L×W×H: {_mx[0]-_mn[0]:.3f} × {_mx[1]-_mn[1]:.3f} × {_mx[2]-_mn[2]:.3f} m"
                )
            except Exception:
                pass
            ti = geo.get("transform_info")
            if ti:
                steps = [f"📄 {ti.get('source', '?')}"]
                if ti.get("mm_to_m"):
                    steps.append("mm→m (×0.001)")
                if ti.get("scale", 1.0) != 1.0:
                    steps.append(f"×{ti['scale']:g}")
                rot = [f"{label} {ti[key]:g}°"
                       for key, label in (("yaw", "Yaw"), ("pitch", "Pitch"), ("roll", "Roll"))
                       if ti.get(key)]
                if rot:
                    steps.append(", ".join(rot))
                st.caption(" → ".join(steps))
            interactive = st.toggle(t("interactive_3d"), value=False, key=f"geo_3d_{geo['id']}")
            if interactive:
                _show_geometry_3d(geo["stl_file_path"])
            else:
                preview = api.get_geometry_preview_by_geo(geo["id"])
                if preview:
                    st.image(preview, width="stretch")
        st.divider()
