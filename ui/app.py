"""
ui/app.py
streamlit run ui/app.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from data.loader      import load_excel, MATURITY_LABELS
from config.constants import BOK_DATES, CASH_RESERVE_RATIO, REPO_SPREAD
from config.fund_params import FundParams
from core.repo_cost   import calc_weighted_avg_rate
from core.return_calculator import (Bond, group_bonds, calc_portfolio_return,
                                    build_rate_scenario_table, get_slope,
                                    get_delta_y_from_scenarios, calc_rolling_total)
from core.portfolio_allocator import select_assets
from eda.yield_explorer  import build_curve_table, build_curve_chart_data, build_timeseries
from eda.curve_snapshot  import build_slope_table, build_rolling_table
from eda.spread_analysis import (build_spread_snapshot, build_spread_pivot,
                                 build_spread_timeseries, build_vs_base_timeseries)
from output.proposal_view import render as render_proposal
from datetime import date

RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "raw.xlsx"

# ── 컬러 ──────────────────────────────────────────────────────────────────────
COLORS = {
    "deep_green": "#1B5E20",
    "forest":     "#2D6A4F",
    "sage":       "#52796F",
    "slate_teal": "#354F52",
    "olive":      "#606C38",
    "warm_brown": "#6B4226",
    "steel":      "#4A5568",
    "muted_teal": "#2C7873",
    "gray_light": "#F5F7F5",
    "gray_mid":   "#9E9E9E",
    "gray_dark":  "#4A4A4A",
    "base_rate":  "#9E9E9E",
    "total_bg":   "#C8E6C9",
}
SECTOR_COLORS = {"은행채(AAA)": COLORS["deep_green"],
                 "공사채(AAA)": COLORS["sage"],
                 "기타금융채(AA-)": COLORS["olive"]}
DEFAULT_COLORS = [COLORS["deep_green"], COLORS["slate_teal"], COLORS["olive"],
                  COLORS["warm_brown"], COLORS["muted_teal"], COLORS["steel"],
                  COLORS["forest"], COLORS["sage"]]
PALETTES = {
    "포인트 (그린 계열)": DEFAULT_COLORS,
    "차분 (뮤트 뉴트럴)": ["#5C6B73","#7D8E8E","#8C7B6B","#6B7C6B",
                            "#707070","#8A7F72","#4F6367","#7A6C5D"],
    "모노크롬":           ["#1A1A1A","#3D3D3D","#5C5C5C","#787878",
                            "#949494","#ABABAB","#C2C2C2","#D8D8D8"],
}
def sc(label, i=0): return SECTOR_COLORS.get(label, DEFAULT_COLORS[i % len(DEFAULT_COLORS)])
def ml(m): return MATURITY_LABELS.get(round(m,6), str(m))

# ── 데이터 ────────────────────────────────────────────────────────────────────
@st.cache_data
def load_data():
    d = load_excel(RAW_PATH)
    sd = {k.removeprefix("S_"): v for k, v in d.items() if k.startswith("S_")}
    return sd, d["기준금리"]

@st.cache_data
def load_ibond():
    d = load_excel(RAW_PATH)
    return d["I_BOND"], d["I_CD"]

# ── 카테고리 파싱 ─────────────────────────────────────────────────────────────
import re as _re
RATING_ORDER  = ["AAA","AA+","AA","AA-","A+","A","A-"]
SECTOR_ORDER  = ["공사채", "은행채", "기타금융채"]

# 알려진 Bloomberg category → 표시 라벨
_KNOWN_LABELS = {
    "공사/공단채 AAA":           "공사채 AAA",
    "공사/공단채 정부보증":        "공사채 (정부보증)",
    "공사/공단채 AA+":            "공사채 AA+",
    "공사/공단채 AA0":            "공사채 AA",
    "금융채 은행채 AAA":           "은행채 AAA",
    "금융채 은행채 AAA(산금-이표)": "은행채 AAA (산금-이표)",
    "금융채 은행채 AAA(중금-이표)": "은행채 AAA (중금-이표)",
    "금융채 은행채 AA+":           "은행채 AA+",
    "금융채 기타금융채 AA-":        "기타금융채 AA-",
    "금융채 기타금융채 AA":         "기타금융채 AA",
    "금융채 기타금융채 AA+":        "기타금융채 AA+",
}

def _auto_label(cat: str) -> str:
    """Bloomberg category → 깔끔한 표시 라벨 (알 수 없는 것도 자동 생성)"""
    if cat in _KNOWN_LABELS:
        return _KNOWN_LABELS[cat]
    # 자동 파싱: 마지막 토큰이 등급이면 섹터+등급
    tokens = cat.split()
    if tokens:
        last = tokens[-1]
        if _re.match(r"^(AAA|AA[+\-]?|A[+\-]?|BBB[+\-]?|정부보증)", last):
            sector = " ".join(tokens[:-1])
            sector = sector.replace("금융채 ", "").replace("공사/공단채", "공사채")
            return f"{sector} {last}".strip()
    return cat  # fallback: 원본 그대로

def get_category_options(i_bond) -> dict:
    """I_BOND에 실제로 있는 모든 category를 동적으로 읽어 반환.
    Returns: {표시라벨: Bloomberg_category_string}  (정렬된 순서)
    """
    cats   = i_bond["category"].unique()
    result = {_auto_label(c): c for c in cats}

    def _sort(item):
        label = item[0]
        sp = next((i for i,s in enumerate(SECTOR_ORDER) if s in label), len(SECTOR_ORDER))
        rp = next((RATING_ORDER.index(r) for r in RATING_ORDER if r in label), len(RATING_ORDER))
        sub = 1 if any(x in label for x in ["이표","정부보증"]) else 0
        return (sp, rp, sub, label)

    return dict(sorted(result.items(), key=_sort))

def get_group_bonds(i_bond, bloomberg_cat: str, maturity: float, top_n=5) -> pd.DataFrame:
    """Bloomberg category 정확히 매칭 → yield 내림차순 → 상위 top_n"""
    df = i_bond[(i_bond["category"] == bloomberg_cat) &
                (i_bond["maturity"]  == maturity)].copy()
    return (df.sort_values("yield", ascending=False)
              .head(top_n)[["issuer","rating","maturity","yield"]]
              .reset_index(drop=True))

# ── 시나리오 관련 ──────────────────────────────────────────────────────────────
BP_OPTIONS = [-75,-50,-25,0,25,50,75]
BP_LABELS  = {v: f"{v:+d}bp" if v!=0 else "동결" for v in BP_OPTIONS}

# ── 앱 설정 ───────────────────────────────────────────────────────────────────
st.set_page_config(page_title="채권펀드 분석", layout="wide")
st.markdown(f"""
<style>
.block-container {{ padding-top:1.5rem }}
div[data-testid="stTab"] button[aria-selected="true"] {{
    color:{COLORS['deep_green']} !important;
    border-bottom:2px solid {COLORS['deep_green']} !important;
}}
</style>""", unsafe_allow_html=True)
st.title("채권펀드 분석")

sector_data, base_rate_df = load_data()
i_bond, i_cd = load_ibond()

all_sectors   = list(sector_data.keys())
all_dates     = sorted(set.intersection(*[set(df["date"]) for df in sector_data.values()]), reverse=True)
all_mats      = sorted(set.intersection(*[set(df["maturity"]) for df in sector_data.values()]))
mat_order_lbl = [ml(m) for m in all_mats]
cur_rate      = float(base_rate_df.iloc[0]["rate"])
cat_options   = get_category_options(i_bond)

# ── session state 초기화 ──────────────────────────────────────────────────────
if "bond_groups" not in st.session_state:
    st.session_state.bond_groups = []      # [{id,name,category,bonds_df,weight_nav}]
if "rc_scenarios" not in st.session_state:
    st.session_state.rc_scenarios = []     # [{name,changes}]

tab1,tab2,tab3,tab4,tab5 = st.tabs(
    ["📈 수익률 커브","📐 커브 구조","↔️ 스프레드","💰 수익률 계산","📋 제안서"])

# =============================================================================
# TAB 1: 수익률 커브
# =============================================================================
with tab1:
    mode    = st.radio("모드",["커브 (날짜 고정)","시계열 (만기 고정)"],horizontal=True)
    sectors = st.multiselect("섹터", all_sectors, default=all_sectors, key="t1_sec")

    if mode == "커브 (날짜 고정)":
        c1,c2 = st.columns([1,3])
        with c1:
            sel_date = st.selectbox("날짜", all_dates,
                         format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"))
            sel_mats = st.multiselect("만기", all_mats, default=[m for m in all_mats if m<=3.0],
                                      format_func=ml, key="t1_mats")
        if not sel_mats: st.warning("만기를 선택하세요"); st.stop()
        ts = pd.Timestamp(sel_date)
        with c2:
            st.subheader(f"만기별 금리 ({ts.strftime('%Y-%m-%d')})")
            tbl = build_curve_table(sector_data, base_rate_df, ts, sel_mats)
            st.dataframe(tbl.style.format("{:.3f}"), use_container_width=True)
        fig = go.Figure()
        for i,lbl in enumerate(sectors):
            df  = sector_data[lbl]
            day = df[(df["date"]==ts)&(df["maturity"].isin(sel_mats))].sort_values("maturity")
            fig.add_trace(go.Scatter(x=[ml(m) for m in day["maturity"]], y=day["yield"],
                                     mode="lines+markers", name=lbl,
                                     line=dict(color=sc(lbl,i),width=2), marker=dict(size=6)))
        br = base_rate_df[base_rate_df["date"]<=ts]
        if not br.empty:
            fig.add_hline(y=float(br.iloc[0]["rate"])*100, line_dash="dot",
                          line_color=COLORS["base_rate"],
                          annotation_text=f"기준금리 {float(br.iloc[0]['rate'])*100:.2f}%")
        fig.update_layout(xaxis_title="만기", yaxis_title="금리 (%)", height=420,
                          margin=dict(t=20), plot_bgcolor="white", paper_bgcolor="white",
                          legend=dict(bgcolor=COLORS["gray_light"]))
        fig.update_xaxes(showgrid=True, gridcolor="#EEEEEE")
        fig.update_yaxes(showgrid=True, gridcolor="#EEEEEE")
        st.plotly_chart(fig, use_container_width=True)
    else:
        c1,c2 = st.columns([1,3])
        with c1:
            sel_mat = st.selectbox("만기", all_mats, format_func=ml)
            dr = st.date_input("기간", value=(pd.Timestamp(all_dates[-1]).date(),
                                              pd.Timestamp(all_dates[0]).date()))
        s_ts = pd.Timestamp(dr[0]) if len(dr)>0 else None
        e_ts = pd.Timestamp(dr[1]) if len(dr)>1 else None
        td   = build_timeseries(sector_data, sectors, sel_mat, s_ts, e_ts)
        fig  = go.Figure()
        for i,lbl in enumerate(sectors):
            sub = td[td["sector"]==lbl].sort_values("date")
            fig.add_trace(go.Scatter(x=sub["date"],y=sub["yield"],mode="lines",
                                     name=lbl, line=dict(color=sc(lbl,i),width=2)))
        br_ts = base_rate_df.copy()
        if s_ts: br_ts = br_ts[br_ts["date"]>=s_ts]
        if e_ts: br_ts = br_ts[br_ts["date"]<=e_ts]
        fig.add_trace(go.Scatter(x=br_ts["date"],y=br_ts["rate"]*100,mode="lines",
                                 name="기준금리",line=dict(dash="dot",color=COLORS["base_rate"],width=1.5)))
        fig.update_layout(xaxis_title="날짜",yaxis_title="금리 (%)",height=420,
                          margin=dict(t=20),plot_bgcolor="white",paper_bgcolor="white",
                          legend=dict(bgcolor=COLORS["gray_light"]))
        fig.update_xaxes(showgrid=True, gridcolor="#EEEEEE")
        fig.update_yaxes(showgrid=True, gridcolor="#EEEEEE")
        st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# TAB 2: 커브 구조
# =============================================================================
with tab2:
    c1,c2 = st.columns([1,3])
    with c1:
        sd2   = st.selectbox("날짜",all_dates,format_func=lambda d:pd.Timestamp(d).strftime("%Y-%m-%d"),key="t2_d")
        hm2   = st.slider("보유기간(개월)",3,12,6,key="t2_h")
    ts2 = pd.Timestamp(sd2)
    with c2:
        st.subheader("구간별 기울기 (bp/년)")
        st.dataframe(build_slope_table(sector_data,ts2).style.format("{:.1f}"),use_container_width=True)
        st.subheader(f"롤링 수익률 근사 ({hm2}개월 보유)")
        roll_df = build_rolling_table(sector_data,ts2,hold_years=hm2/12)
        pos = roll_df["롤링수익률(bp)"]>0
        st.dataframe(
            roll_df.style.format({"기울기(bp/년)":"{:.2f}","롤링수익률(bp)":"{:.2f}"})
                         .apply(lambda col:[f"color:{COLORS['deep_green']};font-weight:600"
                                if v else f"color:{COLORS['gray_mid']}" for v in pos],
                                subset=["롤링수익률(bp)"]),
            use_container_width=True)

# =============================================================================
# TAB 3: 스프레드
# =============================================================================
with tab3:
    in1,in2,in3 = st.tabs(["현재 스냅샷","섹터 간 시계열","기준금리 대비"])
    with in1:
        sd3 = st.selectbox("날짜",all_dates,format_func=lambda d:pd.Timestamp(d).strftime("%Y-%m-%d"),key="t3_d")
        snap  = build_spread_snapshot(sector_data,pd.Timestamp(sd3))
        pivot = build_spread_pivot(snap,mat_order_lbl)
        st.dataframe(pivot.style.format("{:.1f}"),use_container_width=True)
    with in2:
        ca,cb,cc = st.columns(3)
        base    = ca.selectbox("기준 섹터",all_sectors,key="t3_base")
        compare = cb.selectbox("비교 섹터",[s for s in all_sectors if s!=base],key="t3_cmp")
        sm3     = cc.selectbox("만기",all_mats,format_func=ml,key="t3_mat")
        ts_sp   = build_spread_timeseries(sector_data,base,compare,sm3)
        if ts_sp.empty: st.warning("데이터 없음")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=ts_sp["date"],y=ts_sp["spread_bp"],mode="lines",
                                     fill="tozeroy",fillcolor="rgba(27,94,32,0.08)",
                                     name="스프레드(bp)",line=dict(color=COLORS["deep_green"],width=2)))
            fig.add_hline(y=0,line_dash="dash",line_color=COLORS["gray_mid"])
            fig.update_layout(title=f"{compare} − {base} ({ml(sm3)})",
                              xaxis_title="날짜",yaxis_title="bp",height=400,
                              margin=dict(t=40),plot_bgcolor="white",paper_bgcolor="white")
            fig.update_xaxes(showgrid=True,gridcolor="#EEEEEE")
            fig.update_yaxes(showgrid=True,gridcolor="#EEEEEE")
            st.plotly_chart(fig,use_container_width=True)
            cc2 = st.columns(4)
            cc2[0].metric("현재",f"{ts_sp['spread_bp'].iloc[-1]:.1f}bp")
            cc2[1].metric("평균",f"{ts_sp['spread_bp'].mean():.1f}bp")
            cc2[2].metric("최대",f"{ts_sp['spread_bp'].max():.1f}bp")
            cc2[3].metric("최소",f"{ts_sp['spread_bp'].min():.1f}bp")
    with in3:
        st.caption("섹터 수익률 − 기준금리 | 복수 선택 가능")
        ca,cb,cc = st.columns(3)
        sv = ca.multiselect("섹터",all_sectors,default=all_sectors,key="t3v_sec")
        mv = cb.multiselect("만기",all_mats,default=[m for m in all_mats if m in[1.0,1.5,2.0]],
                             format_func=ml,key="t3v_mat")
        dr_v = cc.date_input("기간",value=(pd.Timestamp(all_dates[-1]).date(),
                                           pd.Timestamp(all_dates[0]).date()),key="t3v_dr")
        sels = [(s,m) for s in sv for m in mv]
        if not sels: st.warning("섹터와 만기를 선택하세요")
        else:
            sv_ts = pd.Timestamp(dr_v[0]) if len(dr_v)>0 else None
            ev_ts = pd.Timestamp(dr_v[1]) if len(dr_v)>1 else None
            ts_vs = build_vs_base_timeseries(sector_data,base_rate_df,sels,sv_ts,ev_ts)
            if ts_vs.empty: st.warning("데이터 없음")
            else:
                fig = go.Figure()
                for i,lbl in enumerate(ts_vs["label"].unique()):
                    sub = ts_vs[ts_vs["label"]==lbl].sort_values("date")
                    fig.add_trace(go.Scatter(x=sub["date"],y=sub["spread_bp"],mode="lines",
                                             name=lbl,line=dict(color=DEFAULT_COLORS[i%len(DEFAULT_COLORS)],width=2)))
                fig.add_hline(y=0,line_dash="dot",line_color=COLORS["base_rate"],annotation_text="기준금리")
                fig.update_layout(xaxis_title="날짜",yaxis_title="vs 기준금리 (bp)",height=450,
                                  margin=dict(t=20),plot_bgcolor="white",paper_bgcolor="white",
                                  legend=dict(bgcolor=COLORS["gray_light"],borderwidth=0))
                fig.update_xaxes(showgrid=True,gridcolor="#EEEEEE")
                fig.update_yaxes(showgrid=True,gridcolor="#EEEEEE")
                st.plotly_chart(fig,use_container_width=True)
                latest = ts_vs[ts_vs["date"]==ts_vs["date"].max()][["label","spread_bp"]].set_index("label")
                latest.columns = ["현재 스프레드(bp)"]
                st.dataframe(latest.style.format("{:.1f}"),use_container_width=True)

# =============================================================================
# TAB 4: 수익률 계산
# =============================================================================
with tab4:

    # ── Section 1: 펀드 파라미터 ─────────────────────────────────────────────
    st.subheader("① 펀드 파라미터")
    c1,c2,c3,c4,c5 = st.columns(5)
    nav       = c1.number_input("설정액 (억)", value=400, step=50, key="rc_nav")
    leverage  = c2.number_input("레버리지 (%)", value=200, step=50, key="rc_lev") / 100
    fund_mat  = c3.selectbox("펀드만기", all_mats, format_func=ml,
                              index=all_mats.index(1.5) if 1.5 in all_mats else 0, key="rc_mat")
    hold_m    = c4.slider("보유기간(개월)", 3, 12, 6, key="rc_hold")
    fp_start  = date(2026, 8, 25)
    fp_end    = date(2027, 2, 25)
    c5.markdown(f"<br><span style='color:{COLORS['gray_mid']};font-size:0.85rem'>"
                f"기준금리: **{cur_rate*100:.2f}%**<br>개시: {fp_start} ~ {fp_end}</span>",
                unsafe_allow_html=True)

    fp = FundParams(net_asset=nav, leverage_ratio=leverage,
                    start_date=fp_start, end_date=fp_end, base_rate=cur_rate,
                    rating_min="AA-", rating_max="AAA")
    cd_avg = i_cd[abs(i_cd["maturity"]-0.25)<0.001]["yield"].mean()
    st.caption(f"AUM: **{fp.aum:.0f}억** | 채권: **{fp.bond_amount:.0f}억** ({fp.bond_weight*100:.1f}%) "
               f"| 현금: **{fp.cash_amount:.0f}억** ({fp.cash_weight*100:.1f}%) "
               f"| 레포: **-{fp.repo_amount:.0f}억** ({fp.repo_weight*100:.1f}%) "
               f"| CD 3M: **{cd_avg:.3f}%**")

    st.divider()

    # ── Section 2: 금리 시나리오 ─────────────────────────────────────────────
    st.subheader("② 금리 시나리오")
    bok_in_range = [d for d in BOK_DATES if fp_start < d <= fp_end]
    n_sc = int(st.number_input("시나리오 수", 1, 6, 2, key="rc_nsc"))

    sc_tabs = st.tabs([f"시나리오 {i+1}" for i in range(n_sc)])
    rc_scenarios = []
    for i, sc_tab in enumerate(sc_tabs):
        with sc_tab:
            sc_name = st.text_input("이름", value=["동결", f"{bok_in_range[0].month}월 인하",
                                    "2회 인하","시나리오4","시나리오5","시나리오6"][i] if i<6 else f"시나리오{i+1}",
                                    key=f"rc_scname_{i}")
            changes = {}
            hdr = st.columns([1]+[3]*2)
            hdr[0].markdown("**금통위**"); hdr[1].markdown("**변동폭**"); hdr[2].markdown("**적용 기준금리**")
            running_rate = cur_rate * 100
            for bok_d in bok_in_range:
                row = st.columns([1]+[3]*2)
                row[0].markdown(f"<span style='color:{COLORS['sage']}'>{bok_d.strftime('%m/%d')}</span>",
                                unsafe_allow_html=True)
                default_bp = -25 if (i==1 and bok_d==bok_in_range[0]) or \
                                    (i==2 and bok_d in bok_in_range[:2]) else 0
                bp = row[1].selectbox("", BP_OPTIONS, format_func=lambda x: BP_LABELS[x],
                                      index=BP_OPTIONS.index(default_bp),
                                      key=f"rc_bp_{i}_{bok_d}", label_visibility="collapsed")
                running_rate += bp / 100
                row[2].markdown(f"<span style='color:{COLORS['gray_dark']};font-size:0.9rem'>"
                                 f"→ {running_rate:.2f}%</span>", unsafe_allow_html=True)
                if bp != 0: changes[bok_d] = bp
            avg_base = calc_weighted_avg_rate(fp_start, fp_end, cur_rate, {d:v/10000 for d,v in changes.items()})
            repo_rate = (avg_base + REPO_SPREAD) * 100
            st.info(f"가중평균 기준금리: **{avg_base*100:.3f}%** | 레포금리: **{repo_rate:.3f}%**")
            rc_scenarios.append({"name": sc_name, "changes": {d:v/10000 for d,v in changes.items()},
                                  "repo_rate": repo_rate})
    st.session_state.rc_scenarios = rc_scenarios

    st.divider()

    # ── Section 3: 채권 그룹 ──────────────────────────────────────────────────
    st.subheader("③ 채권 그룹")
    col_add, col_del = st.columns([1,5])
    if col_add.button("＋ 그룹 추가"):
        n = len(st.session_state.bond_groups) + 1
        st.session_state.bond_groups.append({
            "id": n, "name": f"그룹{n}",
            "category": list(cat_options.keys())[0],
            "bonds_df": pd.DataFrame(),
            "weight_nav": 1.0,
        })
        st.rerun()

    groups_to_delete = []
    for idx, grp in enumerate(st.session_state.bond_groups):
        with st.expander(f"📁 {grp['name']}", expanded=True):
            g1,g2,g3,g4 = st.columns([2,2,1,1])
            new_name = g1.text_input("그룹 이름", value=grp["name"], key=f"gname_{idx}_{grp['id']}")
            cat_key  = g2.selectbox("섹터+등급", list(cat_options.keys()),
                                     index=list(cat_options.keys()).index(grp["category"])
                                     if grp["category"] in cat_options else 0,
                                     key=f"gcat_{idx}_{grp['id']}")
            weight_n = g3.number_input("NAV비중(%)", value=int(grp["weight_nav"]*100),
                                        step=10, key=f"gwt_{idx}_{grp['id']}") / 100
            if g4.button("🗑 삭제", key=f"gdel_{idx}_{grp['id']}"):
                groups_to_delete.append(idx)
                continue

            st.session_state.bond_groups[idx]["name"]       = new_name
            st.session_state.bond_groups[idx]["weight_nav"] = weight_n

            # 카테고리 바뀌면 자동 로드
            if cat_key != grp["category"] or grp["bonds_df"].empty:
                bloomberg_cat = cat_options[cat_key]   # 표시라벨 → Bloomberg 원본
                bonds_df = get_group_bonds(i_bond, bloomberg_cat, fund_mat, top_n=5)
                st.session_state.bond_groups[idx]["category"] = cat_key
                st.session_state.bond_groups[idx]["bonds_df"] = bonds_df

            bonds_df = st.session_state.bond_groups[idx]["bonds_df"]
            if not bonds_df.empty:
                bonds_df_edit = bonds_df.copy()
                bonds_df_edit.insert(0, "선택", True)
                edited = st.data_editor(bonds_df_edit, hide_index=True,
                                        use_container_width=True,
                                        column_config={"선택": st.column_config.CheckboxColumn("선택", default=True)},
                                        key=f"gedit_{idx}_{grp['id']}")
                st.session_state.bond_groups[idx]["bonds_df"] = \
                    edited[edited["선택"]][["issuer","rating","maturity","yield"]].reset_index(drop=True)

    for idx in sorted(groups_to_delete, reverse=True):
        st.session_state.bond_groups.pop(idx)
    if groups_to_delete: st.rerun()

    st.divider()

    # ── Section 4: 수익률 계산 ────────────────────────────────────────────────
    st.subheader("④ 수익률 결과")
    if not st.session_state.bond_groups:
        st.info("그룹을 추가하고 채권을 선택하세요.")
    elif not rc_scenarios:
        st.info("시나리오를 설정하세요.")
    else:
        def to_sector_label(cat):
            if "은행채" in cat: return "은행채(AAA)"
            if "공사" in cat:   return "공사채(AAA)"
            return "기타금융채(AA-)"

        target_date = pd.Timestamp(all_dates[0])
        hold_years  = hold_m / 12

        result_rows = []
        for sc in rc_scenarios:
            bonds_all = []
            allocs    = {}
            for grp in st.session_state.bond_groups:
                df = grp["bonds_df"]
                if df.empty: continue
                for _, row in df.iterrows():
                    b = Bond(sector=to_sector_label(
                                 i_bond[i_bond["issuer"]==row["issuer"]]["category"].iloc[0]
                                 if not i_bond[i_bond["issuer"]==row["issuer"]].empty else ""),
                             rating=row["rating"], issuer=row["issuer"],
                             maturity=row["maturity"], ytm=row["yield"])
                    bonds_all.append(b)
                grouped_keys = group_bonds([Bond(to_sector_label(
                    i_bond[i_bond["issuer"]==r["issuer"]]["category"].iloc[0]
                    if not i_bond[i_bond["issuer"]==r["issuer"]].empty else ""),
                    r["rating"],r["issuer"],r["maturity"],r["yield"])
                    for _,r in df.iterrows()])
                w_each = grp["weight_nav"] / max(len(grouped_keys),1)
                for k in grouped_keys:
                    allocs[k] = w_each

            if not bonds_all: continue
            res, detail = calc_portfolio_return(
                bonds=bonds_all, allocations=allocs,
                cd_rate=cd_avg, repo_cost=sc["repo_rate"],
                bond_weight=fp.bond_weight, cash_weight=fp.cash_weight,
                repo_weight=fp.repo_weight,
                sector_data=sector_data, target_date=target_date, hold_years=hold_years,
            )
            result_rows.append({
                "시나리오": sc["name"], "레포금리(%)": round(sc["repo_rate"],4),
                "채권YTM(롤X)(%)": round(res.bond_yield_plain,4),
                "채권YTM(롤O)(%)": round(res.bond_yield_rolling,4),
                "포트폴리오(롤X)(%)": round(res.total_plain,4),
                "포트폴리오(롤O)(%)": round(res.total_rolling,4),
                "롤링기여(%)": round(res.total_rolling-res.total_plain,4),
            })

        if result_rows:
            res_df = pd.DataFrame(result_rows)
            def color_pos(val):
                if not isinstance(val,(int,float)): return ""
                return f"color:{COLORS['deep_green']};font-weight:600" if val>0 \
                       else "color:#C62828" if val<0 else ""
            num_cols = [c for c in res_df.columns if "%" in c]
            st.dataframe(
                res_df.style.format({c:"{:.4f}%" for c in num_cols})
                            .map(color_pos, subset=["포트폴리오(롤X)(%)","포트폴리오(롤O)(%)","롤링기여(%)"]),
                use_container_width=True, hide_index=True)

            # 금리변동 시나리오 테이블
            st.markdown("**금리변동 × 시나리오 매트릭스**")
            slope    = get_slope(sector_data,"공사채(AAA)",target_date,
                                 max(fund_mat-hold_years,0.25),fund_mat)
            rolldown = hold_years * slope
            duration = fund_mat - hold_years
            base_t   = {r["시나리오"]: r["포트폴리오(롤X)(%)"] for r in result_rows}
            dy_list  = [-0.50,-0.25,0.0,0.25,0.50]
            dy_lbl   = ["50bp 하락","25bp 하락","0bp","25bp 상승","50bp 상승"]
            from output.tables import build_rolling_matrix
            t2 = build_rolling_matrix(base_t, rolldown, duration, dy_list, dy_lbl)
            sc_names_t2 = list(base_t.keys())
            def hl_zero(row):
                if row["시중금리 변동폭"]=="0bp":
                    return [f"font-weight:700;color:{COLORS['deep_green']}"]*len(row)
                return [""]*len(row)
            st.dataframe(
                t2.style.format(lambda x:f"{x:.3f}%" if isinstance(x,float) else x, subset=sc_names_t2)
                        .apply(hl_zero, axis=1),
                use_container_width=True, hide_index=True)

# =============================================================================
# TAB 5: 제안서
# =============================================================================
with tab5:
    render_proposal(
        bond_groups=st.session_state.get("bond_groups",[]),
        rc_scenarios=st.session_state.get("rc_scenarios",[]),
    )