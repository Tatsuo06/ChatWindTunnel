"""Project management page."""
import streamlit as st

import frontend.api_client as api
from frontend.i18n import t

if "token" not in st.session_state:
    st.warning(t("login_required"))
    st.stop()

st.title(f"📁 {t('projects_title')}")

with st.expander(t("new_project"), expanded=False):
    with st.form("create_project"):
        name = st.text_input(t("project_name"))
        desc = st.text_area(t("description"), height=80)
        if st.form_submit_button(t("create"), width="stretch"):
            result = api.create_project(name, desc)
            if result:
                st.success(t("project_created", result["name"]))
                st.rerun()
            else:
                st.error(t("project_create_fail"))

projects = api.list_projects()
if not projects:
    st.info(t("no_projects"))
else:
    for proj in projects:
        editing = st.session_state.get(f"editing_proj_{proj['id']}", False)
        if editing:
            new_name = st.text_input(t("project_name"), value=proj["name"],
                                     key=f"rename_proj_{proj['id']}")
            new_desc = st.text_area(t("description"), value=proj.get("description", ""),
                                    key=f"redesc_proj_{proj['id']}", height=80)
            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if st.button(t("save"), key=f"save_proj_{proj['id']}", width="stretch"):
                    if new_name.strip():
                        api.rename_project(proj["id"], new_name.strip(), description=new_desc)
                        if st.session_state.get("project_id") == proj["id"]:
                            st.session_state["project_name"] = new_name.strip()
                        st.session_state.pop(f"editing_proj_{proj['id']}", None)
                        st.rerun()
            with bcol2:
                if st.button(t("cancel"), key=f"cancel_proj_{proj['id']}", width="stretch"):
                    st.session_state.pop(f"editing_proj_{proj['id']}", None)
                    st.rerun()
        else:
            col1, col2, col3, col4 = st.columns([4, 1, 1, 1])
            with col1:
                owner = proj.get("owner_username", "")
                desc  = proj.get("description", "")
                st.markdown(f"**{proj['name']}**  \n{desc}")
                st.caption(f"👤 {owner}")
            with col2:
                if st.button(t("open"), key=f"open_{proj['id']}"):
                    st.session_state["project_id"] = proj["id"]
                    st.session_state["project_name"] = proj["name"]
                    st.session_state.pop("geo_id", None)
                    st.session_state.pop("geo_name", None)
                    st.session_state.pop("sim_id", None)
                    st.switch_page("pages/02_geometry.py")
            with col3:
                if st.button("✏️", key=f"edit_proj_{proj['id']}", help=t("rename")):
                    st.session_state[f"editing_proj_{proj['id']}"] = True
                    st.rerun()
            with col4:
                if st.button("🗑", key=f"del_{proj['id']}", help=t("delete")):
                    if api.delete_project(proj["id"]):
                        if st.session_state.get("project_id") == proj["id"]:
                            st.session_state.pop("project_id", None)
                            st.session_state.pop("project_name", None)
                            st.session_state.pop("sim_id", None)
                        st.rerun()
        st.divider()
