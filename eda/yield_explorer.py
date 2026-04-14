"""
eda/yield_explorer.py — 순수 함수만. Streamlit 없음.
UI는 ui/app.py에서 담당.
"""
import pandas as pd
import numpy as np
from pathlib import Path

MATURITY_LABELS = {
    0.25:"3M", 0.5:"6M", 0.75:"9M", 1.0:"1Y", 1.5:"1Y6M",
    2.0:"2Y", 2.5:"2Y6M", 3.0:"3Y", 4.0:"4Y", 5.0:"5Y",
    7.0:"7Y", 10.0:"10Y", 15.0:"15Y", 20.0:"20Y", 30.0:"30Y",
}


def build_curve_table(sector_data, base_rate_df, target_date, maturities) -> pd.DataFrame:
    """섹터×만기 테이블 + 기준금리 행. yield 단위: %"""
    rows = {}
    for label, df in sector_data.items():
        day = df[df["date"] == target_date]
        rows[label] = {
            m: round(float(day.loc[day["maturity"] == m, "yield"].iloc[0]), 3)
            if not day.loc[day["maturity"] == m].empty else np.nan
            for m in maturities
        }
    table = pd.DataFrame(rows).T
    br = base_rate_df[base_rate_df["date"] <= target_date]
    br_val = round(float(br.iloc[0]["rate"]) * 100, 2) if not br.empty else np.nan
    table.loc["기준금리"] = {m: br_val for m in maturities}
    table.columns = [MATURITY_LABELS.get(m, str(m)) for m in maturities]
    return table


def build_curve_chart_data(sector_data, target_date, sectors, maturities) -> pd.DataFrame:
    """커브 그래프용 long-form. date, maturity 고정."""
    frames = []
    for label in sectors:
        df  = sector_data.get(label, pd.DataFrame())
        day = df[(df["date"] == target_date) & (df["maturity"].isin(maturities))].copy()
        day["sector"] = label
        frames.append(day[["maturity", "yield", "sector"]])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_timeseries(sector_data, sectors, maturity, start=None, end=None) -> pd.DataFrame:
    """시계열 그래프용 long-form. maturity 고정."""
    frames = []
    for label in sectors:
        ts = sector_data.get(label, pd.DataFrame())
        ts = ts[ts["maturity"] == maturity][["date", "yield"]].copy()
        if start: ts = ts[ts["date"] >= start]
        if end:   ts = ts[ts["date"] <= end]
        ts["sector"] = label
        frames.append(ts)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
