"""Project-level results: Cx/Cz vs yaw angle for all geometries."""
import math
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

def _body_frame(p: dict) -> tuple[float | None, float | None]:
    """Convert wind-axis Cx/Cy to body-frame Cx(b)/Cy(b)."""
    cx, cy, yaw = p.get("Cx"), p.get("Cy"), p.get("yaw_deg", 0.0)
    if cx is None or cy is None:
        return None, None
    psi = math.radians(yaw)
    bx = cx * math.cos(psi) - cy * math.sin(psi)
    by = cx * math.sin(psi) + cy * math.cos(psi)
    return round(bx, 4), round(by, 4)


# Collect all values to set sensible defaults
all_cx    = [p["Cx"]    for geo in data for p in geo["points"] if p.get("Cx")    is not None]
all_cz    = [p["Cz"]    for geo in data for p in geo["points"] if p.get("Cz")    is not None]
all_cy    = [p["Cy"]    for geo in data for p in geo["points"] if p.get("Cy")    is not None]
all_cmyaw = [p["CmYaw"] for geo in data for p in geo["points"] if p.get("CmYaw") is not None]
all_bx = [_body_frame(p)[0] for geo in data for p in geo["points"] if _body_frame(p)[0] is not None]
all_by = [_body_frame(p)[1] for geo in data for p in geo["points"] if _body_frame(p)[1] is not None]


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


# ── Cx / Cy charts ─────────────────────────────────────────────
col_cx, col_cy = st.columns(2)

with col_cx:
    cx_ymin, cx_ymax = _axis_controls("cx", all_cx)
    fig_cx = go.Figure()
    for geo in data:
        xs    = [p["yaw_deg"] for p in geo["points"]]
        ys    = [p["Cx"]      for p in geo["points"]]
        texts = [f"{p['sim_name']}<br>Cx={p['Cx']}" for p in geo["points"]]
        fig_cx.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines+markers",
            name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
        ))
    fig_cx.update_layout(
        title=t("cd_chart_title"), xaxis_title=t("xaxis_yaw"), yaxis_title="Cx",
        template="plotly_white", height=380, legend_title=t("legend_geo"),
        yaxis=dict(range=[cx_ymin, cx_ymax] if cx_ymin is not None else None),
    )
    st.plotly_chart(fig_cx, use_container_width=True)

with col_cy:
    if all_cy:
        cy_ymin, cy_ymax = _axis_controls("cy_wt", all_cy)
        fig_cy = go.Figure()
        for geo in data:
            xs    = [p["yaw_deg"] for p in geo["points"] if p.get("Cy") is not None]
            ys    = [p["Cy"]      for p in geo["points"] if p.get("Cy") is not None]
            texts = [f"{p['sim_name']}<br>Cy={p['Cy']}" for p in geo["points"] if p.get("Cy") is not None]
            if xs:
                fig_cy.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers",
                    name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
                ))
        fig_cy.update_layout(
            title=t("cy_wt_chart_title"), xaxis_title=t("xaxis_yaw"), yaxis_title="Cy",
            template="plotly_white", height=380, legend_title=t("legend_geo"),
            yaxis=dict(range=[cy_ymin, cy_ymax] if cy_ymin is not None else None),
        )
        st.plotly_chart(fig_cy, use_container_width=True)

# ── Cz / CmYaw charts ──────────────────────────────────────────
if all_cz or all_cmyaw:
    col_cz, col_cmyaw = st.columns(2)

    with col_cz:
        if all_cz:
            cz_ymin, cz_ymax = _axis_controls("cz", all_cz)
            fig_cz = go.Figure()
            for geo in data:
                xs    = [p["yaw_deg"] for p in geo["points"]]
                ys    = [p["Cz"]      for p in geo["points"]]
                texts = [f"{p['sim_name']}<br>Cz={p['Cz']}" for p in geo["points"]]
                if xs:
                    fig_cz.add_trace(go.Scatter(
                        x=xs, y=ys, mode="lines+markers",
                        name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
                    ))
            fig_cz.update_layout(
                title=t("cl_chart_title"), xaxis_title=t("xaxis_yaw"), yaxis_title="Cz",
                template="plotly_white", height=250, legend_title=t("legend_geo"),
                yaxis=dict(range=[cz_ymin, cz_ymax] if cz_ymin is not None else None),
            )
            st.plotly_chart(fig_cz, use_container_width=True)

    with col_cmyaw:
        if all_cmyaw:
            cmyaw_ymin, cmyaw_ymax = _axis_controls("cmyaw", all_cmyaw)
            fig_cmyaw = go.Figure()
            for geo in data:
                xs    = [p["yaw_deg"]  for p in geo["points"] if p.get("CmYaw") is not None]
                ys    = [p["CmYaw"]    for p in geo["points"] if p.get("CmYaw") is not None]
                texts = [f"{p['sim_name']}<br>CmYaw={p['CmYaw']}" for p in geo["points"] if p.get("CmYaw") is not None]
                if xs:
                    fig_cmyaw.add_trace(go.Scatter(
                        x=xs, y=ys, mode="lines+markers",
                        name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
                    ))
            fig_cmyaw.update_layout(
                title=t("cmyaw_chart_title"), xaxis_title=t("xaxis_yaw"), yaxis_title="CmYaw",
                template="plotly_white", height=250, legend_title=t("legend_geo"),
                yaxis=dict(range=[cmyaw_ymin, cmyaw_ymax] if cmyaw_ymin is not None else None),
            )
            st.plotly_chart(fig_cmyaw, use_container_width=True)

# ── Body-frame charts ──────────────────────────────────────────
if all_bx:
    st.subheader(t("body_frame_title"))
    st.caption(t("body_frame_caption"))

    col_bx, col_by = st.columns(2)

    with col_bx:
        bx_ymin, bx_ymax = _axis_controls("bx", all_bx)
        fig_bx = go.Figure()
        for geo in data:
            xs, ys, texts = [], [], []
            for p in geo["points"]:
                bx, _ = _body_frame(p)
                if bx is not None:
                    xs.append(p["yaw_deg"]); ys.append(bx)
                    texts.append(f"{p['sim_name']}<br>Cx(b)={bx}")
            if xs:
                fig_bx.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers",
                    name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
                ))
        fig_bx.update_layout(
            title=t("cx_chart_title"), xaxis_title=t("xaxis_yaw"), yaxis_title="Cx(b)",
            template="plotly_white", height=380, legend_title=t("legend_geo"),
            yaxis=dict(range=[bx_ymin, bx_ymax] if bx_ymin is not None else None),
        )
        st.plotly_chart(fig_bx, use_container_width=True)

    with col_by:
        by_ymin, by_ymax = _axis_controls("by", all_by)
        fig_by = go.Figure()
        for geo in data:
            xs, ys, texts = [], [], []
            for p in geo["points"]:
                _, by = _body_frame(p)
                if by is not None:
                    xs.append(p["yaw_deg"]); ys.append(by)
                    texts.append(f"{p['sim_name']}<br>Cy(b)={by}")
            if xs:
                fig_by.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers",
                    name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
                ))
        fig_by.update_layout(
            title=t("cy_chart_title"), xaxis_title=t("xaxis_yaw"), yaxis_title="Cy(b)",
            template="plotly_white", height=380, legend_title=t("legend_geo"),
            yaxis=dict(range=[by_ymin, by_ymax] if by_ymin is not None else None),
        )
        st.plotly_chart(fig_by, use_container_width=True)

# ── Data table ─────────────────────────────────────────────────
st.subheader(t("numerical_data"))
for geo in data:
    st.markdown(f"**{geo['geo_name']}**")
    rows = []
    for p in geo["points"]:
        bx, by = _body_frame(p)
        rows.append({
            t("col_case"): p["sim_name"], t("col_yaw"): p["yaw_deg"],
            t("col_pitch"): p["pitch_deg"], t("col_roll"): p.get("roll_deg", 0.0),
            "Cx": p["Cx"], "Cy": p.get("Cy"), "Cz": p["Cz"], "CmYaw": p.get("CmYaw"),
            "Cx(b)": bx, "Cy(b)": by,
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
