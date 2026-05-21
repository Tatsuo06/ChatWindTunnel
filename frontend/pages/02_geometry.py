"""Geometry management page."""
import streamlit as st

import frontend.api_client as api
from frontend.i18n import t

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

with st.expander(t("add_geometry"), expanded=False):
    with st.form("new_geo_form"):
        geo_name = st.text_input(t("geo_name_hint"))
        uploaded = st.file_uploader(
            t("file_drop"),
            type=["stl", "step", "stp", "iges", "igs", "obj"],
        )
        if st.form_submit_button(t("upload"), use_container_width=True):
            if uploaded:
                with st.spinner(t("converting")):
                    name = geo_name or uploaded.name.rsplit(".", 1)[0]
                    geo = api.create_geometry(project_id, name)
                    result = api.upload_cad(geo["id"], uploaded.read(), uploaded.name) if geo else None
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
            col1, col2, col3 = st.columns([4, 1, 1])
            with col1:
                new_name = st.text_input(t("geo_name_hint"), value=geo["name"],
                                         key=f"rename_geo_{geo['id']}", label_visibility="collapsed")
            with col2:
                if st.button(t("save"), key=f"save_geo_{geo['id']}", use_container_width=True):
                    if new_name.strip() and api.rename_geometry(geo["id"], new_name.strip()):
                        if st.session_state.get("geo_id") == geo["id"]:
                            st.session_state["geo_name"] = new_name.strip()
                        st.session_state.pop(f"editing_geo_{geo['id']}", None)
                        st.rerun()
            with col3:
                if st.button(t("cancel"), key=f"cancel_geo_{geo['id']}", use_container_width=True):
                    st.session_state.pop(f"editing_geo_{geo['id']}", None)
                    st.rerun()
        else:
            cols = st.columns([4, 1, 1, 1])
            with cols[0]:
                prefix  = "▶ " if is_selected else ""
                name_md = f"**{prefix}{geo['name']}**" if is_selected else f"{prefix}{geo['name']}"
                stl_label = t("stl_ready") if geo.get("stl_file_path") else t("no_stl")
                st.markdown(f"{name_md}  \n{stl_label}")
            with cols[1]:
                if st.button(t("go_cases"), key=f"open_{geo['id']}", use_container_width=True):
                    st.session_state["geo_id"] = geo["id"]
                    st.session_state["geo_name"] = geo["name"]
                    st.session_state.pop("sim_id", None)
                    st.switch_page("pages/03_case.py")
            with cols[2]:
                if st.button("✏️", key=f"edit_geo_{geo['id']}", help=t("rename")):
                    st.session_state[f"editing_geo_{geo['id']}"] = True
                    st.rerun()
            with cols[3]:
                if st.button("🗑", key=f"del_{geo['id']}", help=t("delete_geo")):
                    if api.delete_geometry(geo["id"]):
                        if st.session_state.get("geo_id") == geo["id"]:
                            st.session_state.pop("geo_id", None)
                            st.session_state.pop("geo_name", None)
                            st.session_state.pop("sim_id", None)
                        st.rerun()
        if not geo.get("stl_file_path"):
            with st.container():
                up = st.file_uploader(
                    t("file_drop"), type=["stl", "step", "stp", "iges", "igs", "obj"],
                    key=f"reupload_{geo['id']}",
                )
                if up:
                    with st.spinner(t("converting")):
                        result = api.upload_cad(geo["id"], up.read(), up.name)
                    if result:
                        st.success(t("geo_uploaded", result["name"]))
                        st.rerun()
                    else:
                        st.error(t("upload_fail"))

        if is_selected and geo.get("stl_file_path"):
            bbox = api.get_geometry_bbox(geo["id"])
            if bbox:
                mn, mx = bbox["min"], bbox["max"]
                st.caption(
                    f"Bounding box — "
                    f"X: {mn[0]:.3f} → {mx[0]:.3f} m &nbsp;|&nbsp; "
                    f"Y: {mn[1]:.3f} → {mx[1]:.3f} m &nbsp;|&nbsp; "
                    f"Z: {mn[2]:.3f} → {mx[2]:.3f} m &nbsp;|&nbsp; "
                    f"L×W×H: {mx[0]-mn[0]:.3f} × {mx[1]-mn[1]:.3f} × {mx[2]-mn[2]:.3f} m"
                )
        st.divider()
