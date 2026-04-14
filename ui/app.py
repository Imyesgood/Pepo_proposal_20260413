"""
ui/app.py — Streamlit 메인 진입점
실행: streamlit run ui/app.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from data.loader import load_excel, MATURITY_LABELS
from eda.yield_explorer  import build_curve_table, build_curve_chart_data, build_timeseries
from eda.curve_snapshot  import build_slope_table, build_rolling_table
from eda.spread_analysis import build_spread_snapshot, build_spread_pivot, build_spread_timeseries
from core.return_calculator import Bond, group_bonds, calc_portfolio_return
from core.repo_cost import calc_repo_cost
from config.fund_params import FundParams
from core.portfolio_allocator import select_assets

RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "raw.xlsx"

# ── 데이터 로딩 (캐시) ──────────────────────────────────────────────────────

@st.cache_data
def load_data():
    data        = load_excel(RAW_PATH)
    sector_data = {k.removeprefix("S_"): v for k, v in data.items() if k.startswith("S_")}
    return sector_data, data["기준금리"]

# ── 공통 헬퍼 ───────────────────────────────────────────────────────────────

def mat_label(m): return MATURITY_LABELS.get(round(m, 6), str(m))

# ── 앱 시작 ─────────────────────────────────────────────────────────────────

st.set_page_config(page_title="채권펀드 분석", layout="wide")
st.title("채권펀드 분석 툴")

sector_data, base_rate_df = load_data()
all_sectors   = list(sector_data.keys())
all_dates     = sorted(set.intersection(*[set(df["date"]) for df in sector_data.values()]), reverse=True)
all_mats      = sorted(set.intersection(*[set(df["maturity"]) for df in sector_data.values()]))
mat_order_lbl = [mat_label(m) for m in all_mats]

tab1, tab2, tab3, tab4 = st.tabs(["📈 수익률 커브", "📐 커브 구조", "↔️ 스프레드", "💰 수익률 계산"])

# ============================================================================
# TAB 1: 수익률 커브
# ============================================================================
with tab1:
    mode = st.radio("모드", ["커브 (날짜 고정)", "시계열 (만기 고정)"], horizontal=True)
    sectors = st.multiselect("섹터", all_sectors, default=all_sectors, key="t1_sectors")

    if mode == "커브 (날짜 고정)":
        col1, col2 = st.columns([1, 3])
        with col1:
            sel_date = st.selectbox("날짜", all_dates,
                         format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"))
            sel_mats = st.multiselect("만기", all_mats,
                         default=[m for m in all_mats if m <= 3.0],
                         format_func=mat_label, key="t1_mats")
        if not sel_mats:
            st.warning("만기를 선택하세요")
            st.stop()

        ts = pd.Timestamp(sel_date)

        with col2:
            st.subheader(f"만기별 금리 ({ts.strftime('%Y-%m-%d')})")
            table = build_curve_table(sector_data, base_rate_df, ts, sel_mats)
            st.dataframe(table.style.format("{:.3f}"), width="stretch")

        chart = build_curve_chart_data(sector_data, ts, sectors, sel_mats)
        fig   = go.Figure()
        for label in sectors:
            sub = chart[chart["sector"] == label].sort_values("maturity")
            fig.add_trace(go.Scatter(
                x=[mat_label(m) for m in sub["maturity"]],
                y=sub["yield"], mode="lines+markers", name=label,
            ))
        br = base_rate_df[base_rate_df["date"] <= ts]
        if not br.empty:
            fig.add_hline(y=float(br.iloc[0]["rate"]) * 100, line_dash="dot",
                          line_color="gray", annotation_text=f"기준금리 {float(br.iloc[0]['rate'])*100:.2f}%")
        fig.update_layout(xaxis_title="만기", yaxis_title="금리 (%)", height=420, margin=dict(t=20))
        st.plotly_chart(fig, width="stretch")

    else:
        col1, col2 = st.columns([1, 3])
        with col1:
            sel_mat = st.selectbox("만기", all_mats, format_func=mat_label)
            dr = st.date_input("기간", value=(
                pd.Timestamp(all_dates[-1]).date(),
                pd.Timestamp(all_dates[0]).date(),
            ))
        start_ts = pd.Timestamp(dr[0]) if len(dr) > 0 else None
        end_ts   = pd.Timestamp(dr[1]) if len(dr) > 1 else None
        ts_data  = build_timeseries(sector_data, sectors, sel_mat, start_ts, end_ts)

        fig = go.Figure()
        for label in sectors:
            sub = ts_data[ts_data["sector"] == label].sort_values("date")
            fig.add_trace(go.Scatter(x=sub["date"], y=sub["yield"], mode="lines", name=label))
        br_ts = base_rate_df.copy()
        if start_ts: br_ts = br_ts[br_ts["date"] >= start_ts]
        if end_ts:   br_ts = br_ts[br_ts["date"] <= end_ts]
        fig.add_trace(go.Scatter(x=br_ts["date"], y=br_ts["rate"] * 100,
                                 mode="lines", name="기준금리",
                                 line=dict(dash="dot", color="gray")))
        fig.update_layout(xaxis_title="날짜", yaxis_title="금리 (%)",
                          height=420, margin=dict(t=20))
        st.plotly_chart(fig, width="stretch")

# ============================================================================
# TAB 2: 커브 구조
# ============================================================================
with tab2:
    col1, col2 = st.columns([1, 3])
    with col1:
        sel_date2 = st.selectbox("날짜", all_dates,
                       format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"), key="t2_date")
        hold_m = st.slider("롤링 보유기간 (개월)", 3, 12, 6, key="t2_hold")
    ts2 = pd.Timestamp(sel_date2)

    with col2:
        st.subheader("구간별 기울기 (bp/년)")
        slope_df = build_slope_table(sector_data, ts2)
        st.dataframe(slope_df.style.format("{:.1f}"), width="stretch")

        st.subheader(f"롤링 수익률 근사 ({hold_m}개월 보유)")
        roll_df = build_rolling_table(sector_data, ts2, hold_years=hold_m/12)
        pos = roll_df["롤링수익률(bp)"] > 0
        st.dataframe(
            roll_df.style.format({"기울기(bp/년)": "{:.2f}", "롤링수익률(bp)": "{:.2f}"})
                         .apply(lambda col: ["color:green" if v else "color:red" for v in pos],
                                subset=["롤링수익률(bp)"]),
            width="stretch",
        )

# ============================================================================
# TAB 3: 스프레드
# ============================================================================
with tab3:
    inner_tab1, inner_tab2 = st.tabs(["현재 스냅샷", "시계열"])

    with inner_tab1:
        sel_date3 = st.selectbox("날짜", all_dates,
                       format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"), key="t3_date")
        snap  = build_spread_snapshot(sector_data, pd.Timestamp(sel_date3))
        pivot = build_spread_pivot(snap, mat_order_lbl)
        st.dataframe(pivot.style.format("{:.1f}"), width="stretch")

    with inner_tab2:
        col1, col2, col3 = st.columns(3)
        base    = col1.selectbox("기준 섹터", all_sectors, key="t3_base")
        compare = col2.selectbox("비교 섹터", [s for s in all_sectors if s != base], key="t3_cmp")
        sel_mat3= col3.selectbox("만기", all_mats, format_func=mat_label, key="t3_mat")

        ts_sp = build_spread_timeseries(sector_data, base, compare, sel_mat3)
        if ts_sp.empty:
            st.warning("데이터 없음")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=ts_sp["date"], y=ts_sp["spread_bp"],
                                     mode="lines", fill="tozeroy",
                                     fillcolor="rgba(99,110,250,0.1)", name="스프레드(bp)"))
            fig.add_hline(y=0, line_dash="dash", line_color="gray")
            fig.update_layout(
                title=f"{compare} − {base} ({mat_label(sel_mat3)})",
                xaxis_title="날짜", yaxis_title="스프레드 (bp)",
                height=400, margin=dict(t=40),
            )
            st.plotly_chart(fig, width="stretch")

            c = st.columns(4)
            c[0].metric("현재", f"{ts_sp['spread_bp'].iloc[-1]:.1f}bp")
            c[1].metric("평균", f"{ts_sp['spread_bp'].mean():.1f}bp")
            c[2].metric("최대", f"{ts_sp['spread_bp'].max():.1f}bp")
            c[3].metric("최소", f"{ts_sp['spread_bp'].min():.1f}bp")

# ============================================================================
# TAB 4: 수익률 계산 (issuer 선택 + 결과)
# ============================================================================
# ui/app.py 상단 import에 추가 필요:
# from core.return_calculator import Bond, group_bonds, calc_portfolio_return
# from core.repo_cost import calc_repo_cost
# from config.fund_params import FundParams
# from core.portfolio_allocator import select_assets

# ============================================================================
# TAB 4: 수익률 계산
# ============================================================================
with tab4:
    st.subheader("펀드 파라미터")
    col1, col2, col3, col4 = st.columns(4)
    nav          = col1.number_input("설정액 (억)", value=400, step=50)
    leverage     = col2.number_input("레버리지 (%)", value=200, step=50) / 100
    rating_max   = col3.selectbox("등급 상한", ["AAA","AA+","AA"], index=0)
    rating_min   = col4.selectbox("등급 하한", ["AAA","AA+","AA","AA-"], index=3)

    col5, col6, col7 = st.columns(3)
    fund_mat     = col5.selectbox("펀드만기", all_mats,
                       format_func=lambda m: MATURITY_LABELS.get(m,str(m)),
                       index=all_mats.index(1.5) if 1.5 in all_mats else 0)
    hold_m       = col6.slider("보유기간 (개월)", 3, 12, 6)
    top_n        = col7.number_input("섹터별 후보 채권수", value=5, min_value=1, max_value=20)

    # ── 데이터 로딩 (캐시된 sector_data, base_rate_df 재활용) ──────────────
    i_bond_data = load_data()[0]   # sector_data 아님 — I_BOND 필요
    # I_BOND는 캐시에 없으니 별도 로드
    @st.cache_data
    def load_ibond():
        d = load_excel(RAW_PATH)
        return d['I_BOND'], d['I_CD']

    i_bond, i_cd = load_ibond()

    # ── 후보 채권 선택 ────────────────────────────────────────────────────
    st.subheader("편입 채권 선택")
    candidates = select_assets(i_bond, fund_maturity=fund_mat,
                               rating_min=rating_min, rating_max=rating_max,
                               top_n=int(top_n))

    if candidates.empty:
        st.warning("조건에 맞는 채권이 없습니다.")
        st.stop()

    # 테이블로 보여주고 사용자가 체크박스로 선택
    display_df = candidates[["issuer","rating","yield_target","yield_next"]].copy()
    display_df.columns = ["발행사","등급","YTM(%)","다음만기YTM(%)"]
    display_df.insert(0, "선택", True)   # 기본 전체 선택

    edited = st.data_editor(
        display_df,
        column_config={"선택": st.column_config.CheckboxColumn("선택", default=True)},
        use_container_width=True,
        hide_index=False,
    )
    selected_issuers = edited[edited["선택"]]["발행사"].tolist()

    if not selected_issuers:
        st.warning("최소 1개 채권을 선택하세요.")
        st.stop()

    # ── 선택된 채권 → Bond 객체 ──────────────────────────────────────────
    def to_sector_label(cat: str) -> str:
        if "은행채" in cat: return "은행채(AAA)"
        if "공사" in cat:   return "공사채(AAA)"
        return "기타금융채(AA-)"

    sel_rows = candidates[candidates["issuer"].isin(selected_issuers)]
    bonds = [
        Bond(sector=to_sector_label(r["category"]), rating=r["rating"],
             issuer=r["issuer"], maturity=fund_mat, ytm=r["yield_target"])
        for _, r in sel_rows.iterrows()
    ]

    # ── 배분 (균등) ───────────────────────────────────────────────────────
    fp = FundParams(
        net_asset=nav, leverage_ratio=leverage,
        start_date=pd.Timestamp("2026-08-25").date(),
        end_date=pd.Timestamp("2027-02-25").date(),
        base_rate=float(base_rate_df.iloc[0]["rate"]),
        rating_min=rating_min, rating_max=rating_max,
    )
    grouped_keys = group_bonds(bonds)
    allocations  = {k: fp.bond_weight / len(grouped_keys) for k in grouped_keys}

    # ── CD / 레포 ─────────────────────────────────────────────────────────
    cd_avg = i_cd[abs(i_cd["maturity"] - 0.25) < 0.001]["yield"].mean()

    st.subheader("금통위 시나리오")
    from config.constants import BOK_DATES
    from datetime import date
    bok_in_range = [d for d in BOK_DATES
                    if fp.start_date < d <= fp.end_date]
    scenarios = {}
    s_cols = st.columns(min(len(bok_in_range), 4))
    for i, bok_d in enumerate(bok_in_range):
        bp = s_cols[i % 4].number_input(
            f"{bok_d.strftime('%m/%d')}", value=0, step=25,
            min_value=-100, max_value=50, key=f"bok_{bok_d}"
        )
        scenarios[bok_d] = bp / 10000

    repo = calc_repo_cost(fp.start_date, fp.end_date, fp.base_rate,
                          scenarios, leverage)

    # ── 수익률 계산 ───────────────────────────────────────────────────────
    result, group_detail = calc_portfolio_return(
        bonds       = bonds,
        allocations = allocations,
        cd_rate     = cd_avg,
        repo_cost   = repo["repo_rate"] * 100,
        bond_weight = fp.bond_weight,
        cash_weight = fp.cash_weight,
        repo_weight = fp.repo_weight,
        sector_data = sector_data,
        target_date = pd.Timestamp(all_dates[0]),
        hold_years  = hold_m / 12,
    )

    # ── 결과 출력 ─────────────────────────────────────────────────────────
    st.subheader("수익률 계산 결과")

    # 그룹별 상세
    rows = []
    for key, (ytm, roll, w, issuers) in group_detail.items():
        sector, rating, mat = key
        rows.append({
            "섹터/등급/만기": f"{sector}/{rating}/{mat}Y",
            "발행사": issuers,
            "YTM(%)": round(ytm, 4),
            "롤링(%)": round(roll, 4),
            "비중(%)": round(w * 100, 2),
            "기여_롤X(%)": round(ytm * w, 4),
            "기여_롤O(%)": round((ytm + roll) * w, 4),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # 최종 수익률
    c = st.columns(4)
    c[0].metric("채권YTM (롤링X)", f"{result.bond_yield_plain:.3f}%")
    c[1].metric("채권YTM (롤링O)", f"{result.bond_yield_rolling:.3f}%",
                delta=f"+{result.bond_yield_rolling-result.bond_yield_plain:.3f}%")
    c[2].metric("포트폴리오 (롤링X)", f"{result.total_plain:.3f}%")
    c[3].metric("포트폴리오 (롤링O)", f"{result.total_rolling:.3f}%",
                delta=f"+{result.total_rolling-result.total_plain:.3f}%")

    # 공식 설명
    with st.expander("계산 공식 보기"):
        st.markdown(f"""
**포트폴리오 수익률 공식:**
```
total = 채권YTM × {fp.bond_weight*100:.1f}% (bond_weight)
      + CD금리  × {fp.cash_weight*100:.1f}% (cash_weight)
      + 레포금리 × {fp.repo_weight*100:.1f}% (repo_weight, 음수=차감)
```
**롤링 수익률:**
```
rolling = hold_years({hold_m/12:.2f}Y) × slope(커브 {mat_label(fund_mat-hold_m/12)}→{mat_label(fund_mat)})
```
**현재 값:**
- 채권YTM(롤링X): {result.bond_yield_plain:.4f}% × {fp.bond_weight*100:.1f}% = {result.bond_yield_plain*fp.bond_weight:.4f}%
- CD금리: {cd_avg:.4f}% × {fp.cash_weight*100:.1f}% = {cd_avg*fp.cash_weight:.4f}%
- 레포금리: {repo['repo_rate']*100:.4f}% × {fp.repo_weight*100:.1f}% = {repo['repo_rate']*100*fp.repo_weight:.4f}%
        """)