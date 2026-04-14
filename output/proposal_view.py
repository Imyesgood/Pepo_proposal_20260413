"""
output/proposal_view.py — 제안서 테이블
bond_groups, rc_scenarios를 Tab4에서 전달받아 사용.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from datetime import date

from data.loader import load_excel, MATURITY_LABELS
from config.constants import BOK_DATES, CASH_RESERVE_RATIO, REPO_SPREAD
from core.repo_cost import calc_weighted_avg_rate
from core.return_calculator import get_slope
from output.tables import build_asset_rows, build_ytm_table, build_rolling_matrix

RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "raw.xlsx"

COLORS = {
    "deep_green": "#1B5E20",
    "gray_light": "#F5F7F5",
    "gray_mid":   "#9E9E9E",
    "total_bg":   "#C8E6C9",
}
BP_OPTIONS = [-75, -50, -25, 0, 25, 50, 75]
BP_LABELS  = {v: f"{v:+d}bp" if v != 0 else "동결" for v in BP_OPTIONS}
PALETTES = {
    "틸-세이지":      ["#a7d7b7","#6c9d7a","#4b816f","#2d5c5a","#1f3b3d"],
    "그린 그라데이션": ["#31572c","#409151","#8ba955","#ecf39e","#f4f7de"],
    "혼합":           ["#168654","#b1a74f","#f6985c","#f5ce83","#4d6fb7"],
}

@st.cache_data
def _load():
    d = load_excel(RAW_PATH)
    return (
        {k.removeprefix("S_"): v for k, v in d.items() if k.startswith("S_")},
        d["I_CD"], d["기준금리"],
    )

def ml(m): return MATURITY_LABELS.get(round(m, 6), str(m))


def render(bond_groups=None, rc_scenarios=None, group_weights=None):
    sector_data, i_cd, base_rate_df = _load()
    cur_rate  = float(base_rate_df.iloc[0]["rate"])
    all_mats  = sorted(set.intersection(*[set(df["maturity"]) for df in sector_data.values()]))
    all_dates = sorted(set.intersection(*[set(df["date"]) for df in sector_data.values()]), reverse=True)
    cd_avg    = i_cd[abs(i_cd["maturity"] - 0.25) < 0.001]["yield"].mean()
    ref_date  = pd.Timestamp(all_dates[0]).strftime("%Y.%m.%d")

    # ── 펀드 설정 ─────────────────────────────────────────────────────────────
    st.subheader("펀드 설정")
    c1, c2, c3, c4 = st.columns(4)
    leverage  = c1.number_input("레버리지(%)", value=200, step=50, key="pv_lev") / 100
    fund_mat  = c2.selectbox("펀드만기", all_mats, format_func=ml,
                              index=all_mats.index(1.5) if 1.5 in all_mats else 0, key="pv_mat")
    hold_m    = c3.slider("보유기간(개월)", 3, 12, 6, key="pv_hold")
    cash_w    = leverage * CASH_RESERVE_RATIO
    repo_w    = leverage

    c4.markdown(
        f"<div style='padding-top:8px;font-size:0.85rem;color:{COLORS['gray_mid']}'>"
        f"현금: {cash_w*100:.0f}% | 레포: {repo_w*100:.0f}% | 기준금리: {cur_rate*100:.2f}%"
        f"</div>", unsafe_allow_html=True
    )

    fp_start = date(2026, 8, 25)
    fp_end   = date(2027, 2, 25)

    # ── 편입 그룹 구성 ────────────────────────────────────────────────────────
    st.subheader("편입자산 구성")

    if not bond_groups:
        st.info("💡 수익률 계산 탭에서 그룹을 먼저 만들어주세요.")
        return

    # 그룹별 NAV 비중 & YTM 확인
    groups_config = []
    st.caption("각 그룹의 NAV 대비 투자비중을 확인하세요.")
    hdr = st.columns([3, 2, 2, 2])
    hdr[0].markdown("**그룹**"); hdr[1].markdown("**평균YTM**")
    hdr[2].markdown("**만기**"); hdr[3].markdown("**NAV비중(%)**")

    for i, grp in enumerate(bond_groups):
        df = grp.get("bonds_df", pd.DataFrame())
        if df.empty:
            continue
        ytm_avg = df["yield"].mean()
        row = st.columns([3, 2, 2, 2])
        row[0].markdown(f"**{grp['name']}**")
        row[1].markdown(f"`{ytm_avg:.3f}%`")
        row[2].markdown(f"`{ml(fund_mat)}`")
        default_wt = (group_weights or {}).get(grp["name"], 1.0)
        wt = row[3].number_input(
            "", value=round(default_wt*100, 1), step=10.0,
            key=f"pv_wt_{i}", label_visibility="collapsed"
        )
        groups_config.append({
            "name":         grp["name"],
            "ytm":          ytm_avg,
            "maturity_str": ml(fund_mat),
            "weight_nav":   wt / 100,
        })

    if not groups_config:
        st.warning("유효한 그룹이 없습니다.")
        return

    st.caption(
        f"REPO: {repo_w*100:.0f}% (NAV 대비 차감) | "
        f"현금: {cash_w*100:.0f}% @ CD {cd_avg:.3f}%"
    )

    # ── 시나리오 ─────────────────────────────────────────────────────────────
    st.subheader("금통위 시나리오")

    if rc_scenarios:
        st.caption("✅ 수익률 계산 탭의 시나리오 사용 중")
        scenarios = {s["name"]: s["changes"] for s in rc_scenarios}
    else:
        st.caption("수익률 계산 탭에서 시나리오를 설정하거나 직접 입력하세요.")
        bok_in = [d for d in BOK_DATES if fp_start < d <= fp_end]
        n_sc   = int(st.number_input("시나리오 수", 1, 6, 2, key="pv_ns"))
        name_cols = st.columns(n_sc)
        sc_names  = [
            name_cols[i].text_input(
                f"시나리오{i+1}",
                value=["동결", f"{bok_in[0].month}월 인하", "2회 인하", "S4", "S5", "S6"][i],
                key=f"pv_sn{i}"
            ) for i in range(n_sc)
        ]
        sc_changes = [{} for _ in range(n_sc)]
        hdr2 = st.columns([1] + [2]*n_sc)
        hdr2[0].markdown("**금통위**")
        for i, nm in enumerate(sc_names):
            hdr2[i+1].markdown(f"**{nm}**")
        for j, bok_d in enumerate(bok_in):
            row2 = st.columns([1] + [2]*n_sc)
            row2[0].markdown(
                f"<span style='color:{COLORS['gray_mid']}'>{bok_d.strftime('%m/%d')}</span>",
                unsafe_allow_html=True
            )
            for i in range(n_sc):
                default_bp = -25 if (i == 1 and j == 0) or (i == 2 and j < 2) else 0
                bp = row2[i+1].selectbox(
                    "", BP_OPTIONS, format_func=lambda x: BP_LABELS[x],
                    index=BP_OPTIONS.index(default_bp),
                    key=f"pv_bp_{i}_{j}", label_visibility="collapsed"
                )
                if bp != 0: sc_changes[i][bok_d] = bp
        scenarios = {
            sc_names[i]: {d: bp/10000 for d, bp in sc_changes[i].items()}
            for i in range(n_sc)
        }

    sc_cols = list(scenarios.keys())

    # 레포금리 미리보기
    rp = [
        f"**{sc}** {(calc_weighted_avg_rate(fp_start,fp_end,cur_rate,chg)+REPO_SPREAD)*100:.3f}%"
        for sc, chg in scenarios.items()
    ]
    st.caption("레포금리:  " + "  |  ".join(rp))

    # ── Table 1 ───────────────────────────────────────────────────────────────
    st.divider()
    st.markdown(
        f"<span style='color:{COLORS['deep_green']};font-size:1.05rem;font-weight:700'>"
        f"■ 만기 예상수익률 (롤링효과 감안 전)</span>"
        f"<span style='color:{COLORS['gray_mid']};font-size:0.8rem;margin-left:12px'>"
        f"연환산, {ref_date} 기준 / 비중: NAV 대비</span>",
        unsafe_allow_html=True,
    )

    asset_rows = build_asset_rows(
        groups      = groups_config,
        repo_weight = repo_w,
        cd_rate     = cd_avg,
        cash_weight = cash_w,
    )
    t1 = build_ytm_table(asset_rows, scenarios, fp_start, fp_end, cur_rate)

    def color_val(val):
        if not isinstance(val, float): return ""
        if val > 0: return f"color:{COLORS['deep_green']};font-weight:600"
        if val < 0: return "color:#C62828;font-weight:600"
        return ""

    def hl_total(row):
        if row["편입자산"] == "합 계 (보수공제전)":
            return [f"background-color:{COLORS['total_bg']};font-weight:700"] * len(row)
        return [""] * len(row)

    st.dataframe(
        t1.style
          .format(lambda x: f"{x:.3f}%" if isinstance(x, float) else x, subset=sc_cols)
          .map(color_val, subset=sc_cols)
          .apply(hl_total, axis=1),
        use_container_width=True, hide_index=True,
    )

    total_row = t1[t1["편입자산"] == "합 계 (보수공제전)"].iloc[0]

    # 투자비중 합계 검증
    actual_wt = sum(ar.weight for ar in asset_rows)
    if abs(actual_wt - 1.0) > 0.01:
        st.warning(f"투자비중 합계: **{actual_wt*100:.1f}%** — 100%와 차이 {(actual_wt-1)*100:+.1f}%p. 그룹 NAV 비중을 조정하세요.")

    mc = st.columns(len(sc_cols))
    for i, sc in enumerate(sc_cols):
        mc[i].metric(sc, f"{total_row[sc]:.3f}%")

    # ── Table 2 ───────────────────────────────────────────────────────────────
    st.divider()
    st.markdown(
        f"<span style='color:{COLORS['deep_green']};font-size:1.05rem;font-weight:700'>"
        f"■ 롤링 효과 반영 및 금리변동 시나리오별 예상수익률</span>",
        unsafe_allow_html=True,
    )

    hold_years = hold_m / 12
    duration   = fund_mat - hold_years
    tgt_date   = pd.Timestamp(all_dates[0])
    slope      = get_slope(sector_data, "공사채(AAA)", tgt_date,
                           max(fund_mat - hold_years, 0.25), fund_mat)
    rolldown   = hold_years * slope
    st.caption(f"rolldown={rolldown:.4f}%  |  잔존듀레이션={duration:.2f}Y  |  보유기간={hold_m}개월")

    dy_opts   = st.multiselect(
        "시중금리 변동폭", [-75,-50,-25,0,25,50,75], default=[-25,0,25],
        format_func=lambda x: f"{x:+d}bp" if x != 0 else "0bp", key="pv_dy",
    )
    dy_sorted = sorted(dy_opts)
    dy_labels = [f"{abs(x)}bp 하락" if x < 0 else "0bp" if x == 0 else f"{x}bp 상승"
                 for x in dy_sorted]

    base_totals = {sc: float(total_row[sc]) for sc in sc_cols}
    t2 = build_rolling_matrix(base_totals, rolldown, duration,
                              [x/100 for x in dy_sorted], dy_labels)

    def hl_zero(row):
        if row["시중금리 변동폭"] == "0bp":
            return [f"font-weight:700;color:{COLORS['deep_green']}"] * len(row)
        return [""] * len(row)

    st.dataframe(
        t2.style
          .format(lambda x: f"{x:.3f}%" if isinstance(x, float) else x, subset=sc_cols)
          .apply(hl_zero, axis=1),
        use_container_width=True, hide_index=True,
    )

    st.markdown(
        f"<div style='font-size:0.78rem;color:{COLORS['gray_mid']};margin-top:6px;line-height:1.6'>"
        f"※ 편입자산 금리는 {ref_date} 장중 거래 레벨을 반영. "
        f"롤링효과 반영 예상수익률은 현 시점 {ml(fund_mat)} 이하 AAA 공사채 수익률 곡선 유지 가정.<br>"
        f"※ Repo 조달금리는 기준금리+5bp 기준, 운용기간 가중평균 반영."
        f"</div>", unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render()