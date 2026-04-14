"""
ui/app.py — streamlit run ui/app.py
"""
import sys, re as _re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date

from data.loader        import load_excel, MATURITY_LABELS
from config.constants   import BOK_DATES, CASH_RESERVE_RATIO, REPO_SPREAD
from config.fund_params import FundParams
from core.repo_cost     import calc_weighted_avg_rate
from core.return_calculator import (Bond, group_bonds, calc_portfolio_return,
                                    get_slope, calc_rolling_total)
from core.portfolio_allocator import select_assets
from eda.yield_explorer  import build_curve_table, build_curve_chart_data, build_timeseries
from eda.curve_snapshot  import build_slope_table, build_rolling_table
from eda.spread_analysis import (build_spread_snapshot, build_spread_pivot,
                                 build_spread_timeseries, build_vs_base_timeseries)
from output.tables import build_rolling_matrix

RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "raw.xlsx"

# ── 컬러 ──────────────────────────────────────────────────────────────────────
COLORS = {
    "deep_green": "#1B5E20", "forest": "#2D6A4F", "sage": "#52796F",
    "slate_teal": "#354F52", "olive": "#606C38", "warm_brown": "#6B4226",
    "steel": "#4A5568", "muted_teal": "#2C7873",
    "gray_light": "#F5F7F5", "gray_mid": "#9E9E9E",
    "gray_dark": "#4A4A4A", "base_rate": "#9E9E9E", "total_bg": "#C8E6C9",
}
SECTOR_COLORS = {"은행채(AAA)": COLORS["deep_green"],
                 "공사채(AAA)": COLORS["sage"],
                 "기타금융채(AA-)": COLORS["olive"]}
DEFAULT_COLORS = [COLORS["deep_green"], COLORS["slate_teal"], COLORS["olive"],
                  COLORS["warm_brown"], COLORS["muted_teal"], COLORS["steel"],
                  COLORS["forest"], COLORS["sage"]]

def make_gradient_palette(n):
    pool = ["#A8D5B5","#6DB88A","#3D9B62","#1B7A40","#1B5E20",
            "#B2DFDB","#80CBC4","#4DB6AC","#26A69A","#00796B",
            "#DCEDC8","#AED581","#9CCC65","#7CB342","#558B2F",
            "#CFD8DC","#90A4AE","#607D8B","#455A64","#263238"]
    step = max(1, len(pool) // max(n,1))
    return [pool[min(i*step, len(pool)-1)] for i in range(n)]

def sc(label, i=0): return SECTOR_COLORS.get(label, DEFAULT_COLORS[i % len(DEFAULT_COLORS)])
def ml(m): return MATURITY_LABELS.get(round(m,6), str(m))

# ── 카테고리 ──────────────────────────────────────────────────────────────────
RATING_ORDER = ["AAA","AA+","AA","AA-","A+","A","A-"]
SECTOR_ORDER = ["공사채","은행채","기타금융채"]

def _sector_from_cat(cat):
    if "은행채" in cat: return "은행채"
    if "공사"   in cat: return "공사채"
    if "기타금융채" in cat: return "기타금융채"
    return cat.split()[0]

def get_category_options(i_bond):
    result = {}
    for cat in i_bond["category"].unique():
        rating = i_bond[i_bond["category"]==cat]["rating"].iloc[0]
        suffix = ("산금" if "산금" in cat else "중금" if "중금" in cat
                  else "정부보증" if "정부보증" in cat else "")
        label  = f"{_sector_from_cat(cat)}({rating})"
        if suffix: label += f" [{suffix}]"
        if label in result and result[label] != cat:
            label = f"{label} ({cat[:8]})"
        result[label] = cat
    def _sort(x):
        lb = x[0]
        sp = next((i for i,s in enumerate(SECTOR_ORDER) if s in lb), len(SECTOR_ORDER))
        rp = next((RATING_ORDER.index(r) for r in RATING_ORDER if r in lb), len(RATING_ORDER))
        return (sp, rp, 1 if any(s in lb for s in ["산금","중금","정부보증"]) else 0, lb)
    return dict(sorted(result.items(), key=_sort))

def get_group_bonds(i_bond, bloomberg_cat, maturity, top_n=5):
    df = i_bond[(i_bond["category"]==bloomberg_cat)&(i_bond["maturity"]==maturity)].copy()
    return (df.sort_values("yield",ascending=False).head(top_n)
              [["issuer","rating","maturity","yield"]].reset_index(drop=True))

def to_sector_label(cat):
    if "은행채" in cat: return "은행채(AAA)"
    if "공사"   in cat: return "공사채(AAA)"
    return "기타금융채(AA-)"

# ── session state ─────────────────────────────────────────────────────────────
for k,v in [("bond_groups",[]), ("rc_scenarios",[]), ("group_weights",{}),
            ("rc_results",[]), ("rc_matrix_data",None), ("n_scenarios",2)]:
    if k not in st.session_state:
        st.session_state[k] = v

# ── 앱 설정 ───────────────────────────────────────────────────────────────────
st.set_page_config(page_title="채권펀드 분석", layout="wide")
st.markdown(f"""<style>
.block-container{{padding-top:1.5rem}}
div[data-testid="stTab"] button[aria-selected="true"]{{
    color:{COLORS['deep_green']} !important;
    border-bottom:2px solid {COLORS['deep_green']} !important;}}
</style>""", unsafe_allow_html=True)
st.title("채권펀드 분석")


# ── 파일 업로드 (사이드바) ────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 데이터 파일")
    uploaded = st.file_uploader("raw.xlsx 업로드", type=["xlsx"])
    if uploaded is not None:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(uploaded.read())
            st.session_state["uploaded_path"] = tmp.name
        st.success(f"{uploaded.name} 업로드 완료")
    if "uploaded_path" not in st.session_state and not RAW_PATH.exists():
        st.error("raw.xlsx 없음. 파일을 업로드하세요.")
        st.stop()
    _data_path = st.session_state.get("uploaded_path", str(RAW_PATH))

@st.cache_data
def load_data(path=str(RAW_PATH)):
    d = load_excel(path)
    return {k.removeprefix("S_"): v for k,v in d.items() if k.startswith("S_")}, d["기준금리"]

@st.cache_data
def load_ibond(path=str(RAW_PATH)):
    d = load_excel(path)
    return d["I_BOND"], d["I_CD"]

sector_data, base_rate_df = load_data(_data_path)
i_bond, i_cd = load_ibond(_data_path)

# CD 만기 라벨 매핑
_CD_MAT_LABELS = {
    round(1/12, 4): "1M", round(2/12, 4): "2M", 0.25: "3M",
    round(4/12, 4): "4M", 0.5: "6M", 1.0: "1Y", 2.0: "2Y",
}

with st.sidebar:
    st.markdown(f"기준금리: **{float(base_rate_df.iloc[0]['rate'])*100:.2f}%**  "
                f"({pd.Timestamp(base_rate_df.iloc[0]['date']).strftime('%Y-%m-%d')})")
    st.divider()
    st.markdown("**현금성자산 (CD) 만기**")
    _cd_mats_avail = sorted(i_cd["maturity"].unique())
    _cd_mat_sel = st.selectbox(
        "CD 월물", _cd_mats_avail,
        index=_cd_mats_avail.index(0.25) if 0.25 in _cd_mats_avail else 0,
        format_func=lambda m: _CD_MAT_LABELS.get(round(m,4), f"{round(m*12):.0f}M"),
        key="cd_mat_sel", label_visibility="collapsed"
    )
    cd_avg = i_cd[abs(i_cd["maturity"] - _cd_mat_sel) < 0.001]["yield"].mean()
    st.caption(f"CD {_CD_MAT_LABELS.get(round(_cd_mat_sel,4), '')}: **{cd_avg:.3f}%** "
               f"({i_cd[abs(i_cd['maturity']-_cd_mat_sel)<0.001]['issuer'].nunique()}개 발행사)")
    st.divider()

all_sectors   = list(sector_data.keys())
all_dates     = sorted(set.intersection(*[set(df["date"]) for df in sector_data.values()]),reverse=True)
all_mats      = sorted(set.intersection(*[set(df["maturity"]) for df in sector_data.values()]))
mat_order_lbl = [ml(m) for m in all_mats]
cur_rate      = float(base_rate_df.iloc[0]["rate"])
cat_options   = get_category_options(i_bond)

BP_OPTIONS = [-75,-50,-25,0,25,50,75]
BP_LABELS  = {v: f"{v:+d}bp" if v!=0 else "동결" for v in BP_OPTIONS}

tab1,tab2,tab3,tab4,tab5 = st.tabs(
    ["📈 수익률 커브","📐 커브 구조","↔️ 스프레드","💰 수익률 계산","📋 제안서"])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1
# ─────────────────────────────────────────────────────────────────────────────
with tab1:
    mode = st.radio("모드",["커브 (날짜 고정)","시계열 (만기 고정)"],horizontal=True)
    sectors = st.multiselect("섹터",all_sectors,default=all_sectors,key="t1_sec")
    if mode=="커브 (날짜 고정)":
        c1,c2=st.columns([1,3])
        with c1:
            sel_date=st.selectbox("날짜",all_dates,format_func=lambda d:pd.Timestamp(d).strftime("%Y-%m-%d"))
            sel_mats=st.multiselect("만기",all_mats,default=[m for m in all_mats if m<=3.0],format_func=ml,key="t1_mats")
        if not sel_mats: st.warning("만기를 선택하세요"); st.stop()
        ts=pd.Timestamp(sel_date)
        with c2:
            tbl=build_curve_table(sector_data,base_rate_df,ts,sel_mats)
            st.dataframe(tbl.style.format("{:.3f}"),use_container_width=True)
        fig=go.Figure()
        for i,lbl in enumerate(sectors):
            day=sector_data[lbl]; day=day[(day["date"]==ts)&(day["maturity"].isin(sel_mats))].sort_values("maturity")
            fig.add_trace(go.Scatter(x=[ml(m) for m in day["maturity"]],y=day["yield"],mode="lines+markers",name=lbl,line=dict(color=sc(lbl,i),width=2),marker=dict(size=6)))
        br=base_rate_df[base_rate_df["date"]<=ts]
        if not br.empty: fig.add_hline(y=float(br.iloc[0]["rate"])*100,line_dash="dot",line_color=COLORS["base_rate"],annotation_text=f"기준금리 {float(br.iloc[0]['rate'])*100:.2f}%")
        fig.update_layout(xaxis_title="만기",yaxis_title="금리 (%)",height=420,margin=dict(t=20),plot_bgcolor="white",paper_bgcolor="white",legend=dict(bgcolor=COLORS["gray_light"]))
        fig.update_xaxes(showgrid=True,gridcolor="#EEEEEE"); fig.update_yaxes(showgrid=True,gridcolor="#EEEEEE")
        st.plotly_chart(fig,use_container_width=True)
    else:
        c1,c2=st.columns([1,3])
        with c1:
            sel_mat=st.selectbox("만기",all_mats,format_func=ml)
            dr=st.date_input("기간",value=(pd.Timestamp(all_dates[-1]).date(),pd.Timestamp(all_dates[0]).date()))
        s_ts=pd.Timestamp(dr[0]) if len(dr)>0 else None; e_ts=pd.Timestamp(dr[1]) if len(dr)>1 else None
        td=build_timeseries(sector_data,sectors,sel_mat,s_ts,e_ts)
        fig=go.Figure()
        for i,lbl in enumerate(sectors):
            sub=td[td["sector"]==lbl].sort_values("date")
            fig.add_trace(go.Scatter(x=sub["date"],y=sub["yield"],mode="lines",name=lbl,line=dict(color=sc(lbl,i),width=2)))
        br_ts=base_rate_df.copy()
        if s_ts: br_ts=br_ts[br_ts["date"]>=s_ts]
        if e_ts: br_ts=br_ts[br_ts["date"]<=e_ts]
        fig.add_trace(go.Scatter(x=br_ts["date"],y=br_ts["rate"]*100,mode="lines",name="기준금리",line=dict(dash="dot",color=COLORS["base_rate"],width=1.5)))
        fig.update_layout(xaxis_title="날짜",yaxis_title="금리 (%)",height=420,margin=dict(t=20),plot_bgcolor="white",paper_bgcolor="white",legend=dict(bgcolor=COLORS["gray_light"]))
        fig.update_xaxes(showgrid=True,gridcolor="#EEEEEE"); fig.update_yaxes(showgrid=True,gridcolor="#EEEEEE")
        st.plotly_chart(fig,use_container_width=True)
    with st.expander("계산 논리"):
        st.markdown("S-type 시트 → `date | maturity | yield`. 기준금리는 한국은행 고시값.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2
# ─────────────────────────────────────────────────────────────────────────────
with tab2:
    c1,c2=st.columns([1,3])
    with c1:
        sd2=st.selectbox("날짜",all_dates,format_func=lambda d:pd.Timestamp(d).strftime("%Y-%m-%d"),key="t2_d")
        hm2=st.slider("보유기간(개월)",3,12,6,key="t2_h")
    ts2=pd.Timestamp(sd2)
    with c2:
        st.subheader("구간별 기울기 (bp/년)")
        st.dataframe(build_slope_table(sector_data,ts2).style.format("{:.1f}"),use_container_width=True)
        st.subheader(f"롤링 수익률 근사 ({hm2}개월 보유)")
        roll_df=build_rolling_table(sector_data,ts2,hold_years=hm2/12)
        pos=roll_df["롤링수익률(bp)"]>0
        st.dataframe(roll_df.style.format({"기울기(bp/년)":"{:.2f}","롤링수익률(bp)":"{:.2f}"})
                     .apply(lambda col:[f"color:{COLORS['deep_green']};font-weight:600" if v else f"color:{COLORS['gray_mid']}" for v in pos],subset=["롤링수익률(bp)"]),use_container_width=True)
    with st.expander("계산 논리"):
        st.markdown("기울기 = (yield[M2]-yield[M1])/(M2-M1) bp/년\n\nrolldown = hold_years × slope(매입만기 전 구간)")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3
# ─────────────────────────────────────────────────────────────────────────────
with tab3:
    in1,in2,in3=st.tabs(["현재 스냅샷","섹터 간 시계열","기준금리 대비"])
    with in1:
        sd3=st.selectbox("날짜",all_dates,format_func=lambda d:pd.Timestamp(d).strftime("%Y-%m-%d"),key="t3_d")
        snap=build_spread_snapshot(sector_data,pd.Timestamp(sd3))
        st.dataframe(build_spread_pivot(snap,mat_order_lbl).style.format("{:.1f}"),use_container_width=True)
    with in2:
        ca,cb,cc=st.columns(3)
        base=ca.selectbox("기준",all_sectors,key="t3_base")
        compare=cb.selectbox("비교",[s for s in all_sectors if s!=base],key="t3_cmp")
        sm3=cc.selectbox("만기",all_mats,format_func=ml,key="t3_mat")
        ts_sp=build_spread_timeseries(sector_data,base,compare,sm3)
        if ts_sp.empty: st.warning("데이터 없음")
        else:
            fig=go.Figure()
            fig.add_trace(go.Scatter(x=ts_sp["date"],y=ts_sp["spread_bp"],mode="lines",fill="tozeroy",fillcolor="rgba(27,94,32,0.06)",name="스프레드(bp)",line=dict(color=COLORS["deep_green"],width=2)))
            fig.add_hline(y=0,line_dash="dash",line_color=COLORS["gray_mid"])
            fig.update_layout(title=f"{compare} - {base} ({ml(sm3)})",xaxis_title="날짜",yaxis_title="bp",height=400,margin=dict(t=40),plot_bgcolor="white",paper_bgcolor="white")
            fig.update_xaxes(showgrid=True,gridcolor="#EEEEEE"); fig.update_yaxes(showgrid=True,gridcolor="#EEEEEE")
            st.plotly_chart(fig,use_container_width=True)
            cc2=st.columns(4)
            cc2[0].metric("현재",f"{ts_sp['spread_bp'].iloc[-1]:.1f}bp")
            cc2[1].metric("평균",f"{ts_sp['spread_bp'].mean():.1f}bp")
            cc2[2].metric("최대",f"{ts_sp['spread_bp'].max():.1f}bp")
            cc2[3].metric("최소",f"{ts_sp['spread_bp'].min():.1f}bp")
    with in3:
        st.caption("복수 섹터×만기 동시 비교")
        ca,cb,cc=st.columns(3)
        sv=ca.multiselect("섹터",all_sectors,default=all_sectors,key="t3v_sec")
        mv=cb.multiselect("만기",all_mats,default=[m for m in all_mats if m in [1.0,1.5,2.0]],format_func=ml,key="t3v_mat")
        dr_v=cc.date_input("기간",value=(pd.Timestamp(all_dates[-1]).date(),pd.Timestamp(all_dates[0]).date()),key="t3v_dr")
        sels=[(s,m) for s in sv for m in mv]
        if sels:
            sv_ts=pd.Timestamp(dr_v[0]) if len(dr_v)>0 else None; ev_ts=pd.Timestamp(dr_v[1]) if len(dr_v)>1 else None
            ts_vs=build_vs_base_timeseries(sector_data,base_rate_df,sels,sv_ts,ev_ts)
            if not ts_vs.empty:
                labels=list(ts_vs["label"].unique()); palette=make_gradient_palette(len(labels))
                fig=go.Figure()
                for i,lbl in enumerate(labels):
                    sub=ts_vs[ts_vs["label"]==lbl].sort_values("date")
                    fig.add_trace(go.Scatter(x=sub["date"],y=sub["spread_bp"],mode="lines",name=lbl,line=dict(color=palette[i],width=2)))
                fig.add_hline(y=0,line_dash="dot",line_color=COLORS["base_rate"],annotation_text="기준금리")
                fig.update_layout(xaxis_title="날짜",yaxis_title="vs 기준금리 (bp)",height=450,margin=dict(t=20),plot_bgcolor="white",paper_bgcolor="white",legend=dict(bgcolor=COLORS["gray_light"],borderwidth=0))
                fig.update_xaxes(showgrid=True,gridcolor="#EEEEEE"); fig.update_yaxes(showgrid=True,gridcolor="#EEEEEE")
                st.plotly_chart(fig,use_container_width=True)
                latest=ts_vs[ts_vs["date"]==ts_vs["date"].max()][["label","spread_bp"]].set_index("label")
                latest.columns=["현재 스프레드(bp)"]
                st.dataframe(latest.style.format("{:.1f}"),use_container_width=True)
    with st.expander("계산 논리"):
        st.markdown("섹터 간: (비교 yield - 기준 yield) × 100 bp\n\n기준금리 대비: (섹터 yield - base_rate) × 100 bp")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4: 수익률 계산
# ─────────────────────────────────────────────────────────────────────────────
with tab4:

    # ── 1. 펀드 파라미터 ──────────────────────────────────────────────────────
    st.subheader("1. 펀드 파라미터")
    c1,c2,c3=st.columns(3)
    nav      = c1.number_input("설정액 (억)",value=400,step=50,key="rc_nav")
    leverage = c2.number_input("레버리지 (%)",value=200,step=50,key="rc_lev")/100
    fund_mat = c3.selectbox("펀드만기",all_mats,format_func=ml,index=all_mats.index(1.5) if 1.5 in all_mats else 0,key="rc_mat")
    c4,c5,c6=st.columns(3)
    fp_start = c4.date_input("설정일",value=date(2026,8,25),key="rc_start")
    fp_end   = c5.date_input("만기일",value=date(2027,2,25),key="rc_end")
    hold_m   = c6.slider("보유기간 개월 (롤링용)",3,12,6,key="rc_hold")
    fp=FundParams(net_asset=nav,leverage_ratio=leverage,start_date=fp_start,end_date=fp_end,
                  base_rate=cur_rate,rating_min="AA-",rating_max="AAA")
    # cd_avg: 사이드바에서 전역 계산됨
    st.caption(f"AUM: **{fp.aum:.0f}억** | 채권: **{fp.bond_amount:.0f}억** ({fp.bond_weight*100:.1f}%) | 현금: **{fp.cash_amount:.0f}억** ({fp.cash_weight*100:.1f}%) | 레포: **-{fp.repo_amount:.0f}억** | CD 3M: **{cd_avg:.3f}%** | 기준금리: **{cur_rate*100:.2f}%**")
    st.divider()

    # ── 2. 기준금리 시나리오 ──────────────────────────────────────────────────
    st.subheader("2. 기준금리 시나리오 (금통위)")
    bok_in_range=[d for d in BOK_DATES if fp_start<d<=fp_end]
    if not bok_in_range:
        st.warning("설정일~만기일 사이 금통위 일정이 없습니다.")

    # +/- 버튼으로 시나리오 수 조절
    nc1,nc2,nc3=st.columns([1,1,8])
    if nc1.button("＋ 시나리오"):
        st.session_state.n_scenarios=min(st.session_state.n_scenarios+1,6); st.rerun()
    if nc2.button("－ 시나리오"):
        st.session_state.n_scenarios=max(st.session_state.n_scenarios-1,1); st.rerun()
    nc3.caption(f"현재 {st.session_state.n_scenarios}개")

    n_sc=st.session_state.n_scenarios
    sc_tabs=st.tabs([f"시나리오 {i+1}" for i in range(n_sc)])
    rc_scenarios=[]
    for i,sc_tab in enumerate(sc_tabs):
        with sc_tab:
            defaults=["동결",f"{bok_in_range[0].month}월 인하" if bok_in_range else "시나리오2",
                      "2회 인하","시나리오4","시나리오5","시나리오6"]
            sc_name=st.text_input("이름",value=defaults[i] if i<len(defaults) else f"시나리오{i+1}",key=f"rc_scname_{i}")
            changes={}
            hdr=st.columns([1,2,2])
            hdr[0].markdown("**금통위**"); hdr[1].markdown("**변동폭**"); hdr[2].markdown("**적용 기준금리**")
            running=cur_rate*100
            for bok_d in bok_in_range:
                row=st.columns([1,2,2])
                row[0].markdown(f"<span style='color:{COLORS['gray_mid']}'>{bok_d.strftime('%m/%d')}</span>",unsafe_allow_html=True)
                default_bp=-25 if (i==1 and bok_d==bok_in_range[0]) or (i==2 and bok_d in bok_in_range[:2]) else 0
                bp=row[1].selectbox("",BP_OPTIONS,format_func=lambda x:BP_LABELS[x],index=BP_OPTIONS.index(default_bp),key=f"rc_bp_{i}_{bok_d}",label_visibility="collapsed")
                running+=bp/100
                row[2].markdown(f"→ {running:.2f}%")
                if bp!=0: changes[bok_d]=bp
            avg_base=calc_weighted_avg_rate(fp_start,fp_end,cur_rate,{d:v/10000 for d,v in changes.items()})
            repo_rate=(avg_base+REPO_SPREAD)*100
            st.info(f"가중평균 기준금리: **{avg_base*100:.3f}%** | 레포금리: **{repo_rate:.3f}%**")
            rc_scenarios.append({"name":sc_name,"changes":{d:v/10000 for d,v in changes.items()},"repo_rate":repo_rate})
    st.session_state.rc_scenarios=rc_scenarios
    st.divider()

    # ── 3. 채권 그룹 선택 ─────────────────────────────────────────────────────
    st.subheader("3. 채권 그룹 선택")
    st.caption("카테고리 선택 = 그룹. 선택한 채권이 해당 섹터+등급의 그룹을 구성합니다.")
    if st.button("그룹 추가"):
        n=len(st.session_state.bond_groups)+1
        first=list(cat_options.keys())[0]
        st.session_state.bond_groups.append({"id":n,"name":first,"category":first,"bonds_df":pd.DataFrame()})
        st.rerun()
    groups_to_del=[]
    for idx,grp in enumerate(st.session_state.bond_groups):
        with st.expander(f"{grp['name']}",expanded=True):
            g1,g2=st.columns([4,1])
            cat_key=g1.selectbox("섹터+등급",list(cat_options.keys()),
                index=list(cat_options.keys()).index(grp["category"]) if grp["category"] in cat_options else 0,
                key=f"gcat_{idx}_{grp['id']}")
            if g2.button("삭제",key=f"gdel_{idx}_{grp['id']}"): groups_to_del.append(idx); continue
            if cat_key!=grp["category"] or grp["bonds_df"].empty:
                bloomberg_cat=cat_options[cat_key]
                bonds_df=get_group_bonds(i_bond,bloomberg_cat,fund_mat,top_n=5)
                st.session_state.bond_groups[idx].update({"category":cat_key,"name":cat_key,"bonds_df":bonds_df})
            bonds_df=st.session_state.bond_groups[idx]["bonds_df"]
            if not bonds_df.empty:
                bdf=bonds_df.copy(); bdf.insert(0,"선택",True)
                edited=st.data_editor(bdf,hide_index=True,use_container_width=True,
                    column_config={"선택":st.column_config.CheckboxColumn("선택",default=True)},
                    key=f"gedit_{idx}_{grp['id']}")
                st.session_state.bond_groups[idx]["bonds_df"]=\
                    edited[edited["선택"]][["issuer","rating","maturity","yield"]].reset_index(drop=True)
            else:
                st.warning("해당 만기 데이터 없음")
    for idx in sorted(groups_to_del,reverse=True): st.session_state.bond_groups.pop(idx)
    if groups_to_del: st.rerun()
    st.divider()

    # ── 4. 비중 설정 및 수익률 결과 ──────────────────────────────────────────
    st.subheader("4. 비중 설정 및 수익률 결과")
    valid_groups=[g for g in st.session_state.bond_groups if not g.get("bonds_df",pd.DataFrame()).empty]
    if not valid_groups:
        st.info("그룹을 추가하고 채권을 선택하세요.")
    elif not rc_scenarios:
        st.info("시나리오를 설정하세요.")
    else:
        n_groups=len(valid_groups)
        default_wt=round(fp.bond_weight/n_groups,4)
        st.markdown("**그룹별 NAV 비중 (AUM 대비)**")
        wt_cols=st.columns(n_groups)
        group_weights={}
        for i,grp in enumerate(valid_groups):
            stored=st.session_state.group_weights.get(grp["name"],default_wt)
            w=wt_cols[i].number_input(grp["name"],value=round(stored*100,1),step=10.0,key=f"gwt2_{grp['id']}")/100
            group_weights[grp["name"]]=w
        st.session_state.group_weights=group_weights
        total_wt=sum(group_weights.values())
        st.caption(f"채권 총 NAV비중: **{total_wt*100:.1f}%**")

        target_date=pd.Timestamp(all_dates[0])
        hold_years=hold_m/12

        # 시나리오별 계산
        rc_results=[]
        for sc in rc_scenarios:
            group_results=[]
            for grp in valid_groups:
                df=grp["bonds_df"]
                if df.empty: continue
                grp_w=group_weights.get(grp["name"],default_wt)
                bonds=[Bond(sector=to_sector_label(
                               i_bond[i_bond["issuer"]==r["issuer"]]["category"].iloc[0]
                               if not i_bond[i_bond["issuer"]==r["issuer"]].empty else ""),
                            rating=r["rating"],issuer=r["issuer"],maturity=r["maturity"],ytm=r["yield"])
                       for _,r in df.iterrows()]
                ytm_avg=df["yield"].mean()
                # 롤링: 그룹 대표 sector로
                rep=bonds[0] if bonds else None
                rolldown=0.0
                if rep:
                    rolldown=calc_rolling_total(rep,sector_data,target_date,hold_years,delta_y=0.0)["rolldown"]
                group_results.append({
                    "name":grp["name"],"ytm":ytm_avg,"rolldown":rolldown,
                    "weight":grp_w,
                    "contrib_plain": round(ytm_avg*grp_w,4),
                    "contrib_rolling": round((ytm_avg+rolldown)*grp_w,4),
                })
            # 합산
            bond_plain   = sum(g["contrib_plain"]   for g in group_results)/total_wt if total_wt>0 else 0
            bond_rolling = sum(g["contrib_rolling"]  for g in group_results)/total_wt if total_wt>0 else 0
            total_plain  = (bond_plain  *total_wt + cd_avg*fp.cash_weight + sc["repo_rate"]*fp.repo_weight)
            total_rolling= (bond_rolling*total_wt + cd_avg*fp.cash_weight + sc["repo_rate"]*fp.repo_weight)
            rc_results.append({
                "name":sc["name"],"repo_rate":sc["repo_rate"],
                "groups":group_results,
                "total_plain":round(total_plain,4),
                "total_rolling":round(total_rolling,4),
            })
        st.session_state.rc_results=rc_results

        # 결과 테이블 — 시나리오별 탭
        res_tabs=st.tabs([r["name"] for r in rc_results])
        for r,res_tab in zip(rc_results,res_tabs):
            with res_tab:
                rows=[]
                for g in r["groups"]:
                    rows.append({"구분":g["name"],"YTM(%)":round(g["ytm"],4),
                                 "롤다운(%)":round(g["rolldown"],4),
                                 "NAV비중(%)":round(g["weight"]*100,2),
                                 "기여_롤X(%)":round(g["contrib_plain"],4),
                                 "기여_롤O(%)":round(g["contrib_rolling"],4)})
                rows.append({"구분":"REPO 매도","YTM(%)":round(r["repo_rate"],4),
                             "롤다운(%)":0.0,"NAV비중(%)":round(fp.repo_weight*100,2),
                             "기여_롤X(%)":round(r["repo_rate"]*fp.repo_weight,4),
                             "기여_롤O(%)":round(r["repo_rate"]*fp.repo_weight,4)})
                rows.append({"구분":"현금성자산","YTM(%)":round(cd_avg,4),
                             "롤다운(%)":0.0,"NAV비중(%)":round(fp.cash_weight*100,2),
                             "기여_롤X(%)":round(cd_avg*fp.cash_weight,4),
                             "기여_롤O(%)":round(cd_avg*fp.cash_weight,4)})
                rows.append({"구분":"합계 (보수공제전)","YTM(%)":None,"롤다운(%)":None,
                             "NAV비중(%)":None,
                             "기여_롤X(%)":round(r["total_plain"],4),
                             "기여_롤O(%)":round(r["total_rolling"],4)})
                df_res=pd.DataFrame(rows)
                num_cols=[c for c in df_res.columns if c!="구분"]
                def cp(v):
                    if not isinstance(v,(int,float)): return ""
                    return f"color:{COLORS['deep_green']};font-weight:600" if v>0 else "color:#C62828" if v<0 else ""
                def hl_total(row):
                    if row["구분"]=="합계 (보수공제전)":
                        return [f"background-color:{COLORS['total_bg']};font-weight:700"]*len(row)
                    return [""]*len(row)
                st.dataframe(df_res.style.format({c:"{:.4f}%" for c in num_cols},na_rep="")
                             .map(cp,subset=num_cols).apply(hl_total,axis=1),
                             use_container_width=True,hide_index=True)
                mc=st.columns(2)
                mc[0].metric("포트폴리오 (롤X)",f"{r['total_plain']:.4f}%")
                mc[1].metric("포트폴리오 (롤O)",f"{r['total_rolling']:.4f}%",
                             delta=f"+{r['total_rolling']-r['total_plain']:.4f}%")

        st.divider()

        # ── 시장금리 변동 매트릭스 ────────────────────────────────────────────
        st.subheader("시장금리 변동 시나리오")
        st.caption("시장금리 Δy 변동 시 롤링 효과 반영 포트폴리오 수익률 (기준금리 시나리오 × 시장금리 조합)")
        dy_opts=st.multiselect("시장금리 변동폭 선택",
            options=[-100,-75,-50,-25,0,25,50,75,100],default=[-25,0,25],
            format_func=lambda x:f"{x:+d}bp" if x!=0 else "0bp (현행)",
            key="rc_dy")
        dy_sorted=sorted(dy_opts)
        dy_labels=[f"{abs(x)}bp 하락" if x<0 else "0bp" if x==0 else f"{x}bp 상승" for x in dy_sorted]

        slope=get_slope(sector_data,"공사채(AAA)",target_date,max(fund_mat-hold_years,0.25),fund_mat)
        rolldown=hold_years*slope
        duration=fund_mat-hold_years

        base_totals={r["name"]:r["total_plain"] for r in rc_results}
        t2=build_rolling_matrix(base_totals,rolldown,duration,[x/100 for x in dy_sorted],dy_labels)
        sc_names_t2=list(base_totals.keys())

        def hl_zero(row):
            if row["시중금리 변동폭"]=="0bp": return [f"font-weight:700;color:{COLORS['deep_green']}"]*len(row)
            return [""]*len(row)

        st.dataframe(t2.style.format(lambda x:f"{x:.3f}%" if isinstance(x,float) else x,subset=sc_names_t2)
                     .apply(hl_zero,axis=1),use_container_width=True,hide_index=True)

        # session state에 매트릭스 저장
        st.session_state.rc_matrix_data={"table":t2,"rolldown":rolldown,"duration":duration,"dy_labels":dy_labels}

    with st.expander("계산 논리"):
        st.markdown(f"""
**포트폴리오 수익률 (NAV 기준)**
```
total = Σ(그룹 YTM × 그룹 NAV비중) + CD금리 × {fp.cash_weight*100:.1f}% + 레포금리 × {fp.repo_weight*100:.1f}%
```
**롤다운**: hold_years × slope(커브 인접 구간)

**시장금리 효과**
```
rate_change = -(maturity - hold_years) × Δy   # maturity 사용, duration 아님
```
`기준금리 시나리오` = 금통위 결정 → 레포금리 변동
`시장금리 변동` = 채권 가격 변동 (롤링 효과에 반영)
        """)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 5: 제안서 (수익률 계산 탭 연동 — 인풋 없음)
# ─────────────────────────────────────────────────────────────────────────────
with tab5:
    rc_results    = st.session_state.get("rc_results",[])
    rc_scenarios  = st.session_state.get("rc_scenarios",[])
    bond_groups   = st.session_state.get("bond_groups",[])
    group_weights = st.session_state.get("group_weights",{})
    matrix_data   = st.session_state.get("rc_matrix_data",None)

    if not rc_results:
        st.info("수익률 계산 탭에서 계산을 먼저 실행하세요.")
    else:
        # 펀드 파라미터 요약 (탭4 값 그대로)
        nav_v=st.session_state.get("rc_nav",400)
        lev_v=st.session_state.get("rc_lev",200)/100
        mat_v=st.session_state.get("rc_mat",1.5 if 1.5 in all_mats else all_mats[0])
        hold_v=st.session_state.get("rc_hold",6)
        start_v=st.session_state.get("rc_start",date(2026,8,25))
        end_v=st.session_state.get("rc_end",date(2027,2,25))

        fp2=FundParams(net_asset=nav_v,leverage_ratio=lev_v,start_date=start_v,end_date=end_v,
                       base_rate=cur_rate,rating_min="AA-",rating_max="AAA")
        # cd_avg: 사이드바에서 전역 계산됨

        ref_date=pd.Timestamp(all_dates[0]).strftime("%Y.%m.%d")
        st.markdown(f"""
<div style='background:{COLORS["gray_light"]};padding:12px;border-radius:8px;margin-bottom:12px;font-size:0.9rem'>
설정액 <b>{nav_v}억</b> | 레버리지 <b>{int(lev_v*100)}%</b> | 펀드만기 <b>{ml(mat_v)}</b> |
보유기간 <b>{hold_v}개월</b> | 기준금리 <b>{cur_rate*100:.2f}%</b> | 기준일 <b>{ref_date}</b>
</div>""", unsafe_allow_html=True)

        # ── 만기 예상수익률 (기준금리 시나리오별) ────────────────────────────
        st.markdown(f"<span style='color:{COLORS['deep_green']};font-size:1.05rem;font-weight:700'>"
                    f"■ 만기 예상수익률 (롤링효과 감안 전)</span>"
                    f"<span style='color:{COLORS['gray_mid']};font-size:0.8rem;margin-left:12px'>"
                    f"연환산, {ref_date} 기준 / NAV 대비</span>", unsafe_allow_html=True)

        sc_names=[r["name"] for r in rc_results]
        # 행: 그룹 + REPO + 현금 + 합계
        all_groups_names=list(dict.fromkeys(
            g["name"] for r in rc_results for g in r["groups"]))

        prop_rows=[]
        for grp_name in all_groups_names:
            row={"편입자산":grp_name}
            for r in rc_results:
                g=next((x for x in r["groups"] if x["name"]==grp_name),None)
                row[r["name"]]=round(g["contrib_plain"],4) if g else 0.0
            # YTM, 만기, 비중
            g0=next((x for x in rc_results[0]["groups"] if x["name"]==grp_name),None)
            row["YTM"]=f"{g0['ytm']:.3f}%" if g0 else ""
            row["만기"]=ml(mat_v)
            row["투자비중"]=f"{round(group_weights.get(grp_name,0)*100,1)}%"
            prop_rows.append(row)

        # REPO
        repo_row={"편입자산":"REPO 매도","YTM":"기준금리+5bp","만기":"1일",
                  "투자비중":f"{fp2.repo_weight*100:.0f}%"}
        for r in rc_results: repo_row[r["name"]]=round(r["repo_rate"]*fp2.repo_weight,4)
        prop_rows.append(repo_row)

        # 현금
        cash_row={"편입자산":"현금성자산","YTM":f"{cd_avg2:.3f}%","만기":"6M 내외",
                  "투자비중":f"{fp2.cash_weight*100:.0f}%"}
        for r in rc_results: cash_row[r["name"]]=round(cd_avg2*fp2.cash_weight,4)
        prop_rows.append(cash_row)

        # 합계
        total_row={"편입자산":"합 계 (보수공제전)","YTM":"","만기":"","투자비중":"100%"}
        for r in rc_results: total_row[r["name"]]=r["total_plain"]
        prop_rows.append(total_row)

        t1_df=pd.DataFrame(prop_rows)
        # 컬럼 순서
        fixed_cols=["편입자산","YTM","만기","투자비중"]
        t1_df=t1_df[fixed_cols+sc_names]

        def cp2(v):
            if not isinstance(v,(int,float)): return ""
            return f"color:{COLORS['deep_green']};font-weight:600" if v>0 else "color:#C62828" if v<0 else ""
        def hl2(row):
            if row["편입자산"]=="합 계 (보수공제전)":
                return [f"background-color:{COLORS['total_bg']};font-weight:700"]*len(row)
            return [""]*len(row)

        st.dataframe(
            t1_df.style.format(lambda x:f"{x:.3f}%" if isinstance(x,float) else x,subset=sc_names)
                       .map(cp2,subset=sc_names).apply(hl2,axis=1),
            use_container_width=True,hide_index=True)

        mc=st.columns(len(sc_names))
        for i,r in enumerate(rc_results):
            mc[i].metric(r["name"],f"{r['total_plain']:.3f}%")

        # ── 롤링+시장금리 변동 매트릭스 ─────────────────────────────────────
        st.divider()
        st.markdown(f"<span style='color:{COLORS['deep_green']};font-size:1.05rem;font-weight:700'>"
                    f"■ 롤링 효과 반영 및 시장금리 변동 시나리오별 예상수익률</span>",
                    unsafe_allow_html=True)
        if matrix_data:
            t2=matrix_data["table"]
            st.caption(f"rolldown={matrix_data['rolldown']:.4f}% | 잔존듀레이션={matrix_data['duration']:.2f}Y | 보유기간={hold_v}개월")
            def hl_z2(row):
                if row["시중금리 변동폭"]=="0bp": return [f"font-weight:700;color:{COLORS['deep_green']}"]*len(row)
                return [""]*len(row)
            st.dataframe(t2.style.format(lambda x:f"{x:.3f}%" if isinstance(x,float) else x,subset=sc_names)
                         .apply(hl_z2,axis=1),use_container_width=True,hide_index=True)
        else:
            st.info("수익률 계산 탭에서 먼저 계산을 실행하세요.")

        st.markdown(f"<div style='font-size:0.78rem;color:{COLORS['gray_mid']};margin-top:6px;line-height:1.6'>"
                    f"※ 편입자산 금리는 {ref_date} 장중 거래 레벨을 반영. "
                    f"롤링효과 반영 예상수익률은 현 시점 {ml(mat_v)} 이하 AAA 공사채 수익률 곡선 유지 가정.<br>"
                    f"※ Repo 조달금리는 기준금리+5bp 기준, 운용기간 가중평균 반영.</div>",
                    unsafe_allow_html=True)