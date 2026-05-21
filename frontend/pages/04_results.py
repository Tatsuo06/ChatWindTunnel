"""Project-level results: Cd/Cl vs yaw angle for all geometries."""
import plotly.graph_objects as go
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

st.title(f"📊 {t('results_title')} — {st.session_state.get('project_name', '')}")

data = api.get_cd_cl_summary(project_id)

if not data:
    st.info(t("no_done_cases"))
    st.stop()

# Collect all values to set sensible defaults
all_cd = [p["Cd"] for geo in data for p in geo["points"] if p["Cd"] is not None]
all_cl = [p["Cl"] for geo in data for p in geo["points"] if p["Cl"] is not None]


def _axis_controls(key_prefix: str, values: list[float]) -> tuple[float | None, float | None]:
    """Render y-axis range controls. Returns (ymin, ymax) or (None, None) for auto."""
    if not values:
        return None, None
    vmin, vmax = min(values), max(values)
    margin = (vmax - vmin) * 0.1 or 0.1
    with st.expander("Y-axis range", expanded=False):
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            ymin = st.number_input("Min", value=round(vmin - margin, 4),
                                   step=0.01, format="%.4f", key=f"{key_prefix}_ymin")
        with col2:
            ymax = st.number_input("Max", value=round(vmax + margin, 4),
                                   step=0.01, format="%.4f", key=f"{key_prefix}_ymax")
        with col3:
            st.write("")
            st.write("")
            auto = st.checkbox("Auto", value=False, key=f"{key_prefix}_auto")
    return (None, None) if auto else (ymin, ymax)


# ── Cd chart ───────────────────────────────────────────────────
st.subheader(t("cd_chart_title"))
cd_ymin, cd_ymax = _axis_controls("cd", all_cd)

fig_cd = go.Figure()
for geo in data:
    xs    = [p["yaw_deg"] for p in geo["points"]]
    ys    = [p["Cd"]      for p in geo["points"]]
    texts = [f"{p['sim_name']}<br>Cd={p['Cd']}" for p in geo["points"]]
    fig_cd.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines+markers",
        name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
    ))
fig_cd.update_layout(
    xaxis_title=t("xaxis_yaw"), yaxis_title="Cd",
    template="plotly_white", height=400, legend_title=t("legend_geo"),
    yaxis=dict(range=[cd_ymin, cd_ymax] if cd_ymin is not None else None),
)
st.plotly_chart(fig_cd, use_container_width=True)

# ── Cl chart ───────────────────────────────────────────────────
st.subheader(t("cl_chart_title"))
cl_ymin, cl_ymax = _axis_controls("cl", all_cl)

fig_cl = go.Figure()
for geo in data:
    xs    = [p["yaw_deg"] for p in geo["points"]]
    ys    = [p["Cl"]      for p in geo["points"]]
    texts = [f"{p['sim_name']}<br>Cl={p['Cl']}" for p in geo["points"]]
    fig_cl.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines+markers",
        name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
    ))
fig_cl.update_layout(
    xaxis_title=t("xaxis_yaw"), yaxis_title="Cl",
    template="plotly_white", height=400, legend_title=t("legend_geo"),
    yaxis=dict(range=[cl_ymin, cl_ymax] if cl_ymin is not None else None),
)
st.plotly_chart(fig_cl, use_container_width=True)

# ── Data table ─────────────────────────────────────────────────
st.subheader(t("numerical_data"))
for geo in data:
    st.markdown(f"**{geo['geo_name']}**")
    rows = [
        {t("col_case"): p["sim_name"], t("col_yaw"): p["yaw_deg"],
         t("col_pitch"): p["pitch_deg"], "Cd": p["Cd"], "Cl": p["Cl"]}
        for p in geo["points"]
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
