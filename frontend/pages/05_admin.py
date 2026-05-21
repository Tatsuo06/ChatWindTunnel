"""Admin page: user management."""
import streamlit as st

import frontend.api_client as api
from frontend.i18n import t

st.set_page_config(page_title="Admin | ChatWindTunnel", layout="wide")

if "token" not in st.session_state:
    st.warning(t("login_required"))
    st.stop()

user = st.session_state.get("user", {})
if user.get("role") != "admin":
    st.error(t("admin_only"))
    st.stop()

st.title(f"⚙️ {t('user_management')}")

with st.expander(t("add_user"), expanded=False):
    with st.form("create_user"):
        username = st.text_input(t("username"))
        password = st.text_input(t("password"), type="password")
        role     = st.selectbox(t("role"), ["user", "admin"])
        if st.form_submit_button(t("add")):
            result = api.create_user(username, password, role)
            if result:
                st.success(t("user_created", username))
                st.rerun()
            else:
                st.error(t("user_create_fail"))

st.subheader(t("users_list"))
users = api.list_users()
for u in users:
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        st.markdown(f"**{u['username']}**")
    with col2:
        st.markdown(u["role"])
    with col3:
        if u["id"] != user.get("id"):
            if st.button(t("delete"), key=f"del_user_{u['id']}"):
                if api.delete_user(u["id"]):
                    st.rerun()
    st.divider()
