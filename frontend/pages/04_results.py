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

# Geometry filter
geo_names = [geo["geo_name"] for geo in data]
selected_geos = st.multiselect(
    t("select_geometries"),
    options=geo_names,
    default=geo_names,
)
data = [geo for geo in data if geo["geo_name"] in selected_geos]
if not data:
    st.info(t("no_done_cases"))
    st.stop()

# Case exclusion filter
excluded_ids_key = f"excluded_sim_ids_{project_id}"
exclude_widget_key = f"exclude_select_{project_id}"

# 発散ケースを自動検出
auto_diverged = {
    p["sim_id"]
    for geo in data for p in geo["points"]
    if p.get("diverged")
}

# ラベル付きケース一覧（⚠️アイコン付き）
all_cases = [
    (p["sim_id"],
     f"{'⚠️ ' if p.get('diverged') else ''}[#{p['sim_id']}] {geo['geo_name']} / {p['sim_name']}"
     + (f"  ({p['diverged_reason']})" if p.get('diverged_reason') else ""))
    for geo in data for p in geo["points"]
]
label_to_id = {label: sid for sid, label in all_cases}
id_to_label = {sid: label for sid, label in all_cases}

# ウィジェットが未描画の場合のみ発散ケースを初期選択としてセット
if exclude_widget_key not in st.session_state:
    diverged_labels = [id_to_label[sid] for sid in auto_diverged if sid in id_to_label]
    st.session_state[excluded_ids_key] = list(auto_diverged)
    st.session_state[exclude_widget_key] = diverged_labels

with st.expander(t("exclude_diverged_expander"), expanded=bool(st.session_state.get(excluded_ids_key))):
    if auto_diverged:
        st.caption(t("exclude_diverged_caption", len(auto_diverged)))
    excluded_labels = st.multiselect(
        t("exclude_diverged_label"),
        options=[label for _, label in all_cases],
        key=exclude_widget_key,
    )
    st.session_state[excluded_ids_key] = [label_to_id[l] for l in excluded_labels if l in label_to_id]

excluded_ids = set(st.session_state[excluded_ids_key])
if excluded_ids:
    for geo in data:
        geo["points"] = [p for p in geo["points"] if p["sim_id"] not in excluded_ids]
    data = [geo for geo in data if geo["points"]]

# y軸デフォルト値リセット用バージョン文字列（除外セットが変わるたびに変化）
_range_ver = str(hash(frozenset(excluded_ids)) & 0xFFFFFF)


def _body_frame(p: dict) -> tuple[float | None, float | None, float | None, float | None]:
    """Convert wind-axis coefficients to SAE body-frame (forward=+X, down=+Z).

    bx = -(Cx·cos ψ − Cy·sin ψ)   forward is +X (negate wind-axis drag direction)
    by =   Cx·sin ψ + Cy·cos ψ     lateral Y unchanged
    bz = -Cz                        down is +Z (negate wind-axis lift direction)
    bmz = -CmYaw                    yaw moment sign flips with Z reversal
    """
    cx, cy, yaw = p.get("Cx"), p.get("Cy"), p.get("yaw_deg", 0.0)
    cz    = p.get("Cz")
    cmyaw = p.get("CmYaw")
    if cx is None or cy is None:
        return None, None, None, None
    psi = math.radians(yaw)
    bx  = round(-(cx * math.cos(psi) - cy * math.sin(psi)), 4)
    by  = round(  cx * math.sin(psi) + cy * math.cos(psi),  4)
    bz  = round(-cz,    4) if cz    is not None else None
    bmz = round(-cmyaw, 4) if cmyaw is not None else None
    return bx, by, bz, bmz


# Collect all values to set sensible defaults
all_cx    = [p["Cx"] for geo in data for p in geo["points"] if p.get("Cx") is not None]
all_cz    = [p["Cz"] for geo in data for p in geo["points"] if p.get("Cz") is not None]
all_cy    = [p["Cy"] for geo in data for p in geo["points"] if p.get("Cy") is not None]
all_cmyaw = []
all_bcmyaw = []
for _geo in data:
    for _p in _geo["points"]:
        _c = _p.get("CmYaw")
        if _c is not None:
            all_cmyaw.append(_c)
            all_bcmyaw.append(round(-_c, 4))
all_bx  = [_body_frame(p)[0] for geo in data for p in geo["points"] if _body_frame(p)[0] is not None]
all_by  = [_body_frame(p)[1] for geo in data for p in geo["points"] if _body_frame(p)[1] is not None]
all_bcz = [_body_frame(p)[2] for geo in data for p in geo["points"] if _body_frame(p)[2] is not None]


def _axis_controls(key_prefix: str, values: list[float], range_ver: str = "") -> tuple[float | None, float | None]:
    """Render y-axis range controls. Returns (ymin, ymax) or (None, None) for auto."""
    if not values:
        return None, None
    vmin, vmax = min(values), max(values)
    margin = (vmax - vmin) * 0.1 or 0.1
    # range_ver は除外セットのハッシュ。変わるとウィジェットキーが変わりデフォルト値がリセットされる。
    ver = f"_{range_ver}" if range_ver else ""
    with st.expander("Y-axis range", expanded=False):
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            ymin = st.number_input("Min", value=round(vmin - margin, 4),
                                   step=0.01, format="%.4f", key=f"{key_prefix}_ymin{ver}")
        with col2:
            ymax = st.number_input("Max", value=round(vmax + margin, 4),
                                   step=0.01, format="%.4f", key=f"{key_prefix}_ymax{ver}")
        with col3:
            st.write("")
            st.write("")
            auto = st.checkbox("Auto", value=False, key=f"{key_prefix}_auto{ver}")
    return (None, None) if auto else (ymin, ymax)


def _body_frame_diagram():
    """Return a matplotlib Figure illustrating the body-fixed coordinate system."""
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Arc, Ellipse, FancyArrow

    phi_deg = 35
    fa = -np.radians(phi_deg)          # standard angle (CW from +X → negative)
    fore = np.array([np.cos(fa), np.sin(fa)])
    perp = np.array([np.sin(fa), -np.cos(fa)])  # 90° CW from fore

    fig, ax = plt.subplots(figsize=(8, 6.5), facecolor='white')
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_xlim(-3.0, 5.8)
    ax.set_ylim(-2.8, 3.2)

    R = 2.0

    # Dashed cross (negative halves of global axes)
    ax.plot([-2.8, 0], [0, 0], 'k--', lw=0.9)
    ax.plot([0, 0], [-2.8, 0], 'k--', lw=0.9)

    # Circle
    ax.add_patch(plt.Circle((0, 0), R, fill=False, ec='black', lw=1.5))

    # Global X / Y axes (solid arrows)
    ak = dict(arrowstyle='->', color='black', lw=1.8, mutation_scale=14)
    ax.annotate('', xy=(3.0, 0), xytext=(0, 0), arrowprops=ak)
    ax.annotate('', xy=(0, 3.0), xytext=(0, 0), arrowprops=ak)
    ax.text(3.13, 0.0, 'X', fontsize=15, va='center', fontweight='bold')
    ax.text(0.1, 3.13, 'Y', fontsize=15, ha='left', fontweight='bold')

    # Body ellipse (elongated hull shape)
    ax.add_patch(Ellipse((0, 0), 2*1.32, 2*0.27, angle=-phi_deg,
                         fill=False, ec='black', lw=1.5))

    # Rectangle inside body (cabin / superstructure)
    rl, rw = 0.33, 0.17
    pts = np.array([[-rl, -rw], [rl, -rw], [rl, rw], [-rl, rw], [-rl, -rw]])
    c, s = np.cos(fa), np.sin(fa)
    pts_r = pts @ np.array([[c, -s], [s, c]]).T
    ax.plot(pts_r[:, 0], pts_r[:, 1], 'k-', lw=1.0)

    # Body centerline (dash-dot)
    cl = 1.54
    ax.plot([-cl*fore[0], cl*fore[0]], [-cl*fore[1], cl*fore[1]], 'k-.', lw=0.9)

    # Fore / Aft labels
    ax.text(1.47*fore[0] + 0.07, 1.47*fore[1] - 0.10, 'Fore', fontsize=11)
    ax.text(-1.33*fore[0] - 0.07, -1.33*fore[1] + 0.07, 'Aft', fontsize=11)

    # Propeller / rotor symbol (two overlapping circles near upper body)
    for ox in [-0.09, 0.09]:
        ax.add_patch(plt.Circle((-0.12 + ox, 0.90), 0.14,
                                fill=False, ec='black', lw=1.2))

    # φ arc (clockwise from +X axis to Fore direction)
    ar = 0.72
    ax.add_patch(Arc((0, 0), 2*ar, 2*ar, angle=0,
                     theta1=np.degrees(fa), theta2=0, color='black', lw=1.2))
    ax.text(ar * 1.28 * np.cos(fa/2), ar * 1.28 * np.sin(fa/2) - 0.04,
            'φ', fontsize=15, ha='center', va='center', style='italic')

    # Fx arrow (along Fore from origin)
    fl = 1.9
    fk = dict(arrowstyle='->', color='black', lw=2.0, mutation_scale=14)
    ax.annotate('', xy=(fl*fore[0], fl*fore[1]), xytext=(0, 0), arrowprops=fk)
    ax.text(fl*fore[0] + 0.10, fl*fore[1] - 0.08, 'Fx', fontsize=13,
            fontweight='bold', va='top')

    # Fy arrow (90° CW from Fore = starboard)
    ax.annotate('', xy=(fl*perp[0], fl*perp[1]), xytext=(0, 0), arrowprops=fk)
    ax.text(fl*perp[0] - 0.06, fl*perp[1] - 0.13, 'Fy', fontsize=13,
            fontweight='bold', ha='right', va='top')

    # N yaw moment (clockwise curved arrow)
    nr = 0.50
    ax.add_patch(Arc((0, 0), 2*nr, 2*nr, angle=0,
                     theta1=50, theta2=310, color='black', lw=2.0))
    tt = np.radians(50)
    tx, ty = nr * np.cos(tt), nr * np.sin(tt)
    tng = np.array([np.sin(tt), -np.cos(tt)])
    ax.annotate('', xy=(tx, ty),
                xytext=(tx - 0.04*tng[0], ty - 0.04*tng[1]),
                arrowprops=dict(arrowstyle='->', color='black', lw=2.0, mutation_scale=12))
    ax.text(0.05, 0.73, 'N', fontsize=13, ha='center', va='center', fontweight='bold')

    # Vw solid block arrow (pointing left, to the right of the circle)
    ax.add_patch(FancyArrow(5.05, 0, -1.65, 0,
                             width=0.22, head_width=0.50, head_length=0.42,
                             fc='black', ec='black', length_includes_head=True))
    ax.text(4.30, -0.50, r'$V_w$', fontsize=14, ha='center', va='top')

    fig.tight_layout(pad=0.1)
    return fig


# ── Cx / Cy charts ─────────────────────────────────────────────
col_cx, col_cy = st.columns(2)

with col_cx:
    cx_ymin, cx_ymax = _axis_controls("cx", all_cx, _range_ver)
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
    st.plotly_chart(fig_cx)

with col_cy:
    if all_cy:
        cy_ymin, cy_ymax = _axis_controls("cy_wt", all_cy, _range_ver)
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
        st.plotly_chart(fig_cy)

# ── Cz / CmYaw charts ──────────────────────────────────────────
if all_cz or all_cmyaw:
    col_cz, col_cmyaw = st.columns(2)

    with col_cz:
        if all_cz:
            cz_ymin, cz_ymax = _axis_controls("cz", all_cz, _range_ver)
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
            st.plotly_chart(fig_cz)

    with col_cmyaw:
        if all_cmyaw:
            cmyaw_ymin, cmyaw_ymax = _axis_controls("cmyaw", all_cmyaw, _range_ver)
            fig_cmyaw = go.Figure()
            for geo in data:
                xs, ys, texts = [], [], []
                for p in geo["points"]:
                    c = p.get("CmYaw")
                    if c is not None:
                        xs.append(p["yaw_deg"]); ys.append(c)
                        texts.append(f"{p['sim_name']}<br>CmYaw={c}")
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
            st.plotly_chart(fig_cmyaw)

# ── Body-frame charts ──────────────────────────────────────────
if all_bx:
    st.subheader(t("body_frame_title"))
    st.caption(t("body_frame_caption"))

    _diag_col, _legend_col = st.columns([1, 1])
    with _diag_col:
        _diag_fig = _body_frame_diagram()
        st.pyplot(_diag_fig)
        import matplotlib.pyplot as _plt
        _plt.close(_diag_fig)
    with _legend_col:
        st.markdown(
            "**変換式 (Wind-tunnel → Body frame)**\n\n"
            r"$$F_x = -(C_x \cos\varphi - C_y \sin\varphi)$$" + "\n\n"
            r"$$F_y = \phantom{-(}C_x \sin\varphi + C_y \cos\varphi\phantom{)}$$" + "\n\n"
            r"$$M_N = -C_{mYaw}$$" + "\n\n"
            "- φ : ヨー角（X軸から前方方向までの時計回り角度）\n"
            "- Vw : 風向（+X方向から吹く）\n"
            "- Fx : 前方抵抗（前向き正）\n"
            "- Fy : 横力（右舷向き正）\n"
            "- N : ヨーモーメント（時計回り正）"
        )

    col_bx, col_by = st.columns(2)

    with col_bx:
        bx_ymin, bx_ymax = _axis_controls("bx", all_bx, _range_ver)
        fig_bx = go.Figure()
        for geo in data:
            xs, ys, texts = [], [], []
            for p in geo["points"]:
                bx, _, _, _ = _body_frame(p)
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
        st.plotly_chart(fig_bx)

    with col_by:
        by_ymin, by_ymax = _axis_controls("by", all_by, _range_ver)
        fig_by = go.Figure()
        for geo in data:
            xs, ys, texts = [], [], []
            for p in geo["points"]:
                _, by, _, _ = _body_frame(p)
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
        st.plotly_chart(fig_by)

    if all_bcz or all_bcmyaw:
        col_bcz, col_bcmyaw = st.columns(2)

        if all_bcz:
            with col_bcz:
                bcz_ymin, bcz_ymax = _axis_controls("bcz", all_bcz, _range_ver)
                fig_bcz = go.Figure()
                for geo in data:
                    xs, ys, texts = [], [], []
                    for p in geo["points"]:
                        _, _, bz, _ = _body_frame(p)
                        if bz is not None:
                            xs.append(p["yaw_deg"]); ys.append(bz)
                            texts.append(f"{p['sim_name']}<br>Cz(b)={bz}")
                    if xs:
                        fig_bcz.add_trace(go.Scatter(
                            x=xs, y=ys, mode="lines+markers",
                            name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
                        ))
                fig_bcz.update_layout(
                    title=t("cz_body_chart_title"), xaxis_title=t("xaxis_yaw"), yaxis_title="Cz(b)",
                    template="plotly_white", height=380, legend_title=t("legend_geo"),
                    yaxis=dict(range=[bcz_ymin, bcz_ymax] if bcz_ymin is not None else None),
                )
                st.plotly_chart(fig_bcz)

        if all_bcmyaw:
            with col_bcmyaw:
                bcmyaw_ymin, bcmyaw_ymax = _axis_controls("bcmyaw", all_bcmyaw, _range_ver)
                fig_bcmyaw = go.Figure()
                for geo in data:
                    xs, ys, texts = [], [], []
                    for p in geo["points"]:
                        c = p.get("CmYaw")
                        bmz = round(-c, 4) if c is not None else None
                        if bmz is not None:
                            xs.append(p["yaw_deg"]); ys.append(bmz)
                            texts.append(f"{p['sim_name']}<br>CmYaw(b)={bmz}")
                    if xs:
                        fig_bcmyaw.add_trace(go.Scatter(
                            x=xs, y=ys, mode="lines+markers",
                            name=geo["geo_name"], text=texts, hoverinfo="text", marker=dict(size=8),
                        ))
                fig_bcmyaw.update_layout(
                    title=t("cmyaw_body_chart_title"), xaxis_title=t("xaxis_yaw"), yaxis_title="CmYaw(b)",
                    template="plotly_white", height=380, legend_title=t("legend_geo"),
                    yaxis=dict(range=[bcmyaw_ymin, bcmyaw_ymax] if bcmyaw_ymin is not None else None),
                )
                st.plotly_chart(fig_bcmyaw)

# ── Data table ─────────────────────────────────────────────────
st.subheader(t("numerical_data"))

for geo in data:
    st.markdown(f"**{geo['geo_name']}**")
    rp = geo.get("ref_params") or {}
    if rp:
        cofr = rp.get("cofr")
        cofr_str = f"({cofr[0]:.3f}, {cofr[1]:.3f}, {cofr[2]:.3f}) m" if cofr else "—"
        u = rp.get("velocity_mps") or 0.0
        lref = rp.get("lref") or 0.0
        nu = rp.get("nu") or 1.5e-5
        re_str = f"{u * lref / nu:,.0f}" if u and lref else "—"
        st.caption(
            f"ρ_ref = {rp.get('rho_ref', 1.0)} kg/m³　　"
            f"U_ref = {u} m/s　　"
            f"A_ref = {rp.get('aref')} m²　　"
            f"L_ref = {lref} m　　"
            f"Re = {re_str}　　"
            f"CofR = {cofr_str}"
        )
    mismatch = geo.get("ref_params_mismatch")
    if mismatch:
        all_ref = geo.get("all_ref", [])
        detail = ", ".join(
            f"{k}: " + " / ".join(str(r[k]) for r in all_ref if r.get(k) is not None)
            for k in mismatch
        )
        st.warning(f"⚠️ 無次元化パラメータがケース間で一致していません: {detail}")
    rows = []
    for p in geo["points"]:
        bx, by, bz, _ = _body_frame(p)
        c_cmyaw = p.get("CmYaw")
        bmz = round(-c_cmyaw, 4) if c_cmyaw is not None else None
        rows.append({
            t("col_case"): p["sim_name"], t("col_yaw"): p["yaw_deg"],
            t("col_pitch"): p["pitch_deg"], t("col_roll"): p.get("roll_deg", 0.0),
            "Cx": p["Cx"], "Cy": p.get("Cy"), "Cz": p["Cz"], "CmYaw": c_cmyaw,
            "Cx(b)": bx, "Cy(b)": by, "Cz(b)": bz, "CmYaw(b)": bmz,
        })
    st.dataframe(rows, hide_index=True)

# ── Project-level chat ──────────────────────────────────────────
st.divider()
st.markdown(f"#### 💬 {t('summary_chat_heading')}")
_llm = api.get_llm_settings()
_model_name = _llm["model"] if _llm else "?"
st.caption(f"{t('summary_chat_caption')}  \n`{_model_name}`")

chat_history_key = f"summary_chat_history_{project_id}"
if chat_history_key not in st.session_state:
    st.session_state[chat_history_key] = []

chat_container = st.container(height=300)
with chat_container:
    for msg in st.session_state[chat_history_key]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

user_input = st.chat_input(t("summary_chat_placeholder"), key="summary_chat_input")
if user_input:
    with chat_container:
        with st.chat_message("user"):
            st.markdown(user_input)
    with st.spinner("Processing..."):
        result = api.send_project_chat(project_id, user_input, st.session_state[chat_history_key])
    if result and "error" not in result:
        reply = result["reply"]
        st.session_state[chat_history_key].append({"role": "user", "content": user_input})
        st.session_state[chat_history_key].append({"role": "assistant", "content": reply})
        with chat_container:
            with st.chat_message("assistant"):
                st.markdown(reply)
        st.rerun()
    elif result and "error" in result:
        st.error(f"Chat error: {result['error']}")
    else:
        st.error(t("chat_llm_unavailable"))
