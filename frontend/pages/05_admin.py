"""Admin page: user management + cluster sync."""
import time
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

# ── Cluster job sync ────────────────────────────────────────────────────────
st.subheader(t("cluster_sync"))
st.caption(t("cluster_sync_caption"))

# Preview: count running/meshing jobs
summary = api.get_status_summary()
n_running = (
    summary.get("CLUSTER_RUNNING", 0)
    or summary.get("RUNNING", 0) + summary.get("MESHING", 0)
)

# Estimate: ~3s per job for qstat SSH call + ~40s per job for result fetch (worst case all done)
est_min_s = n_running * 3
est_max_s = n_running * 3 + n_running * 40

def _fmt_sec(s: int) -> str:
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"

col_info, col_btn = st.columns([3, 1])
with col_info:
    if n_running:
        st.markdown(
            f"**{t('cluster_sync_jobs_active', n_running)}**  \n"
            f"{t('cluster_sync_estimate', _fmt_sec(est_min_s), _fmt_sec(est_max_s))}"
        )
    else:
        st.markdown(f"*{t('cluster_sync_none_active')}*")
with col_btn:
    do_sync = st.button(t("cluster_sync_run"))

if do_sync:
    active = api.get_active_jobs()
    if not active:
        st.info(t("cluster_sync_none_active"))
    else:
        t0 = time.time()
        progress_bar = st.progress(0.0)
        status_text  = st.empty()
        updated, errors = [], []

        for i, job in enumerate(active):
            label = f"{job['project']} / {job['geometry']} / {job['name']} (sim {job['sim_id']})"
            status_text.text(f"[{i+1}/{len(active)}] {label}")
            res = api.sync_one_job(job["sim_id"])
            if res.get("result") == "updated":
                updated.append(res)
            elif res.get("result") == "error":
                errors.append({"sim_id": res["sim_id"], "error": res.get("reason", "?")})
            progress_bar.progress((i + 1) / len(active))

        elapsed_str = _fmt_sec(int(time.time() - t0))
        status_text.empty()
        progress_bar.empty()

        if updated:
            st.success(t("cluster_sync_done", len(active), len(updated), elapsed_str))
            st.dataframe(
                [{"Sim ID": u["sim_id"], "Project": u["project"],
                  "Geometry": u["geometry"], "Case": u["name"],
                  "Old": u["old_status"], "New": u["new_status"]} for u in updated],
                use_container_width=True,
            )
        else:
            st.info(t("cluster_sync_no_update") + f"  (checked {len(active)}, {elapsed_str})")
        if errors:
            st.warning(t("cluster_sync_errors", len(errors)))
            for e in errors:
                st.text(f"  sim {e['sim_id']}: {e['error']}")

st.divider()

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
