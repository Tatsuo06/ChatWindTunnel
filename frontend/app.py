"""ChatWindTunnel - Streamlit entry point."""
from pathlib import Path

import streamlit as st

from frontend.api_client import change_password, get_me, get_status_summary, login
from frontend.i18n import t

_STATIC = Path(__file__).parent / "static"


def _generate_tunnel_illustration(out_path: Path) -> None:
    """Create a wind tunnel cross-section illustration and save as PNG."""
    from PIL import Image, ImageDraw
    import numpy as np

    W, H = 720, 200

    data = np.zeros((H, W, 4), dtype=np.uint8)
    for x in range(W):
        f = x / (W - 1)
        data[:, x] = [int(242 - 14 * f), int(248 - 10 * f), 255, 255]
    img = Image.fromarray(data, "RGBA")
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 20], fill=(62, 67, 78))
    draw.rectangle([0, H - 20, W, H], fill=(62, 67, 78))
    for x in range(0, W, 22):
        draw.line([(x, 0), (x + 13, 20)], fill=(82, 87, 98), width=1)
        draw.line([(x, H), (x + 13, H - 20)], fill=(82, 87, 98), width=1)

    yf = H - 20
    deck_y = yf - 42

    hull = [
        (158, yf), (146, yf - 22), (163, deck_y),
        (483, deck_y), (491, yf - 14), (491, yf),
    ]
    draw.polygon(hull, fill=(32, 48, 72))
    draw.line(hull + [hull[0]], fill=(18, 28, 50), width=1)

    draw.rectangle([163, deck_y, 483, deck_y + 5], fill=(55, 62, 75))

    CW, CH, CG = 38, 19, 2
    cx_starts = [174, 215, 256, 297, 338]
    ccolors = [
        (72, 82, 100), (55, 68, 90), (85, 88, 72),
        (60, 78, 82),  (82, 68, 58), (70, 75, 88),
    ]
    for row in range(3):
        for col in range(5):
            x0 = cx_starts[col]
            top = deck_y - (row + 1) * (CH + CG)
            bot = top + CH
            color = ccolors[(row * 3 + col) % len(ccolors)]
            draw.rectangle([x0, top, x0 + CW, bot], fill=color)
            draw.rectangle([x0, top, x0 + CW, bot], outline=(30, 30, 40), width=1)

    bx0, bx1 = 397, 471
    by0 = deck_y - 50
    draw.rectangle([bx0, by0, bx1, deck_y], fill=(150, 158, 170))
    draw.rectangle([bx0, by0, bx1, deck_y], outline=(115, 122, 132), width=1)
    for wx in range(bx0 + 7, bx1 - 5, 14):
        draw.rectangle([wx, by0 + 7, wx + 8, by0 + 18], fill=(88, 145, 205))

    fx0, fx1 = 425, 441
    fy1, fy0 = by0, by0 - 20
    draw.rectangle([fx0, fy0, fx1, fy1], fill=(28, 32, 42))
    draw.rectangle([fx0, fy0 + 6, fx1, fy0 + 12], fill=(55, 68, 90))

    def _cubic(p0, p1, p2, p3, n=160):
        pts = []
        for i in range(n + 1):
            t_ = i / n
            mt = 1 - t_
            x = mt**3*p0[0] + 3*mt**2*t_*p1[0] + 3*mt*t_**2*p2[0] + t_**3*p3[0]
            y = mt**3*p0[1] + 3*mt**2*t_*p1[1] + 3*mt*t_**2*p2[1] + t_**3*p3[1]
            pts.append((round(x), round(y)))
        return pts

    for i, y_s in enumerate([26, 34, 42, 50, 58, 68, 78]):
        deflect = int(i / 6 * 22)
        yc = y_s - deflect
        pts = _cubic((0, y_s), (205, yc), (515, yc), (W, y_s))
        for j in range(len(pts) - 1):
            draw.line([pts[j], pts[j + 1]], fill=(31, 97, 183, 150), width=2)

    for ya in [26, 42, 58, 72]:
        draw.line([(4, ya), (27, ya)], fill=(31, 97, 183), width=2)
        draw.polygon([(27, ya), (19, ya - 5), (19, ya + 5)], fill=(31, 97, 183))

    img.save(str(out_path))


def _ensure_logo() -> tuple[str, str]:
    logo_path = _STATIC / "logo.png"
    icon_path = _STATIC / "logo_icon.png"
    if not logo_path.exists() or not icon_path.exists():
        from PIL import Image, ImageDraw, ImageFont

        BLUE   = (31, 97, 183)
        NAVY   = (15, 50, 110)
        GREY   = (120, 120, 120)

        img  = Image.new("RGBA", (300, 52), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        try:
            font_main = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
            font_sub  = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
        except Exception:
            font_main = font_sub = ImageFont.load_default()
        draw.text((4,  2), "Chat Wind Tunnel",              fill=BLUE, font=font_main)
        draw.text((4, 34), "CFD Simulation Assistant by SRC", fill=GREY, font=font_sub)
        img.save(str(logo_path))

        icon = Image.new("RGBA", (48, 48), (255, 255, 255, 0))
        draw = ImageDraw.Draw(icon)
        try:
            ifont_s = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
            ifont   = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
        except Exception:
            ifont_s = ifont = ImageFont.load_default()
        draw.text((4,  2), "SRC",  fill=NAVY, font=ifont_s)
        draw.text((2, 16), "Chat", fill=BLUE, font=ifont)
        draw.text((8, 30), "WT",   fill=BLUE, font=ifont)
        icon.save(str(icon_path))
    return str(logo_path), str(icon_path)


st.set_page_config(
    page_title="ChatWindTunnel",
    page_icon="💨",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _login_page():
    illus_path = _STATIC / "tunnel_illustration.png"
    if not illus_path.exists():
        _generate_tunnel_illustration(illus_path)
    st.image(str(illus_path), use_container_width=True)

    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown(
            "<h1 style='text-align:center;color:#1f61b7;margin-bottom:0'>💨 Chat Wind Tunnel</h1>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='text-align:center;color:#888;margin-top:4px'>"
            "CFD Simulation Assistant by SRC</p>",
            unsafe_allow_html=True,
        )
        st.divider()
        with st.form("login_form"):
            username = st.text_input(t("username"))
            password = st.text_input(t("password"), type="password")
            submitted = st.form_submit_button(t("login"), use_container_width=True)
        if submitted:
            token = login(username, password)
            if token:
                st.session_state["token"] = token
                me = get_me()
                st.session_state["user"] = me
                st.rerun()
            else:
                st.error(t("login_error"))


def main():
    # Language default
    if "lang" not in st.session_state:
        st.session_state["lang"] = "en"

    if "token" not in st.session_state:
        # Show language toggle on login page too
        with st.sidebar:
            lang = st.session_state["lang"]
            if st.button("🇯🇵 日本語" if lang == "en" else "🇺🇸 English", use_container_width=True):
                st.session_state["lang"] = "ja" if lang == "en" else "en"
                st.rerun()
        _login_page()
        return

    user = st.session_state.get("user", {})

    pages = [
        st.Page("pages/01_projects.py",  title=t("projects_title"), icon="📁"),
        st.Page("pages/02_geometry.py",  title=t("geometry_title"), icon="🔷"),
        st.Page("pages/03_case.py",      title=t("cases_title"),    icon="🌬️"),
        st.Page("pages/04_results.py",   title=t("results_title"),  icon="📊"),
    ]
    if user.get("role") == "admin":
        pages.append(st.Page("pages/05_admin.py", title="Admin", icon="⚙️"))

    logo_path, icon_path = _ensure_logo()
    st.logo(logo_path, icon_image=icon_path)

    with st.sidebar:
        # Language toggle
        lang = st.session_state["lang"]
        if st.button("🇯🇵 日本語" if lang == "en" else "🇺🇸 English", use_container_width=True):
            st.session_state["lang"] = "ja" if lang == "en" else "en"
            st.rerun()

        st.markdown(f"**{user.get('username', '')}** ({user.get('role', '')})")
        if st.button(t("logout"), use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

        with st.expander(t("change_password")):
            with st.form("change_password_form"):
                current_pw = st.text_input(t("current_password"), type="password")
                new_pw     = st.text_input(t("new_password"), type="password")
                new_pw2    = st.text_input(t("confirm_password"), type="password")
                if st.form_submit_button(t("change"), use_container_width=True):
                    if not current_pw or not new_pw:
                        st.error(t("pw_empty"))
                    elif new_pw != new_pw2:
                        st.error(t("pw_mismatch"))
                    elif change_password(current_pw, new_pw):
                        st.success(t("pw_success"))
                    else:
                        st.error(t("pw_wrong"))

        st.divider()
        if st.session_state.get("project_name"):
            st.caption(t("current_work"))
            st.markdown(f"📁 {st.session_state['project_name']}")
        if st.session_state.get("geo_name"):
            st.markdown(f"🔷 {st.session_state['geo_name']}")
        if st.session_state.get("sim_id"):
            st.markdown(f"🌬️ {t('case_hash')}{st.session_state['sim_id']}")
        st.divider()

        summary = get_status_summary()
        cluster_running = summary.get("CLUSTER_RUNNING", 0)
        cluster_queued  = summary.get("QUEUED", 0)
        if cluster_running == 0 and cluster_queued == 0:
            cluster_running = summary.get("MESHING", 0) + summary.get("RUNNING", 0)
            cluster_queued  = 0
        if cluster_running or cluster_queued:
            st.caption(t("cluster_status"))
            if cluster_running:
                st.markdown(f"🟡 {t('running')} **{cluster_running}**")
            if cluster_queued:
                st.markdown(f"🔵 {t('queued')} **{cluster_queued}**")
            st.divider()

    pg = st.navigation(pages, position="sidebar")
    pg.run()


if __name__ == "__main__":
    main()
