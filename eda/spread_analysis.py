"""
eda/spread_analysis.py — 순수 함수만. Streamlit 없음.
"""
import pandas as pd
from pathlib import Path
from itertools import combinations

MATURITY_LABELS = {
    0.25:"3M", 0.5:"6M", 0.75:"9M", 1.0:"1Y", 1.5:"1Y6M",
    2.0:"2Y", 2.5:"2Y6M", 3.0:"3Y", 4.0:"4Y", 5.0:"5Y",
    7.0:"7Y", 10.0:"10Y", 15.0:"15Y", 20.0:"20Y", 30.0:"30Y",
}


def build_spread_snapshot(sector_data, target_date) -> pd.DataFrame:
    """
    모든 nC2 조합 × 공통 만기 → 현재 스프레드 (bp).
    Returns: DataFrame[pair, 만기, 스프레드(bp)]
    """
    labels = list(sector_data.keys())
    rows   = []
    for a, b in combinations(labels, 2):
        da = sector_data[a][sector_data[a]["date"] == target_date].set_index("maturity")["yield"]
        db = sector_data[b][sector_data[b]["date"] == target_date].set_index("maturity")["yield"]
        for m in sorted(set(da.index) & set(db.index)):
            rows.append({
                "pair":       f"{b} − {a}",
                "만기":       MATURITY_LABELS.get(m, str(m)),
                "maturity":   m,
                "스프레드(bp)": round((db[m] - da[m]) * 100, 2),
            })
    return pd.DataFrame(rows)


def build_spread_pivot(snapshot: pd.DataFrame, mat_order: list[str]) -> pd.DataFrame:
    """snapshot → pair×만기 pivot (표시용)"""
    pivot = snapshot.pivot_table(index="pair", columns="만기", values="스프레드(bp)")
    ordered = [m for m in mat_order if m in pivot.columns]
    return pivot[ordered]


def build_spread_timeseries(sector_data, base, compare, maturity) -> pd.DataFrame:
    """(compare − base) 스프레드 시계열. 단위: bp"""
    df_b = sector_data[base][sector_data[base]["maturity"] == maturity][["date","yield"]].rename(columns={"yield":"base"})
    df_c = sector_data[compare][sector_data[compare]["maturity"] == maturity][["date","yield"]].rename(columns={"yield":"compare"})
    merged = df_b.merge(df_c, on="date").dropna()
    merged["spread_bp"] = ((merged["compare"] - merged["base"]) * 100).round(2)
    return merged[["date", "spread_bp"]].sort_values("date")


if __name__ == "__main__":
    import sys, warnings
    sys.path.insert(0, str(Path(__file__).parent.parent))
    warnings.filterwarnings('ignore')
    from data.loader import load_excel
    from pathlib import Path

    RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "raw.xlsx"
    data        = load_excel(RAW_PATH)
    sector_data = {k.removeprefix("S_"): v for k, v in data.items() if k.startswith("S_")}

    target   = sector_data[list(sector_data.keys())[0]]["date"].max()
    labels   = list(sector_data.keys())
    all_mats = sorted(set.intersection(*[set(df["maturity"]) for df in sector_data.values()]))
    mat_lbls = [MATURITY_LABELS.get(m, str(m)) for m in all_mats]

    print(f"\n[ 스프레드 스냅샷 (bp) | {target.date()} ]")
    snap  = build_spread_snapshot(sector_data, target)
    pivot = build_spread_pivot(snap, mat_lbls)
    print(pivot.to_string())

    print(f"\n[ 시계열 스프레드 | 기타금융채(AA-) − 은행채(AAA) | 1Y6M | 최근 10일 ]")
    ts = build_spread_timeseries(sector_data, labels[0], labels[-1], 1.5)
    print(ts.sort_values("date", ascending=False).head(10).to_string(index=False))


def build_vs_base_timeseries(
    sector_data:  dict,
    base_rate_df: pd.DataFrame,
    selections:   list[tuple],   # [(섹터라벨, maturity), ...]
    start: pd.Timestamp = None,
    end:   pd.Timestamp = None,
) -> pd.DataFrame:
    """
    기준금리 대비 스프레드 시계열.
    spread_bp = (sector_yield - base_rate) × 100
    Returns: DataFrame[date, spread_bp, label]
    """
    frames = []
    for (label, mat) in selections:
        df = sector_data.get(label, pd.DataFrame())
        ts = df[df["maturity"] == mat][["date", "yield"]].copy()
        if start: ts = ts[ts["date"] >= start]
        if end:   ts = ts[ts["date"] <= end]

        br = base_rate_df[["date", "rate"]].rename(columns={"rate": "base"})
        br["base"] = br["base"] * 100   # 소수 → %
        merged = ts.merge(br, on="date", how="left").dropna()
        merged["spread_bp"] = ((merged["yield"] - merged["base"]) * 100).round(2)
        merged["label"] = f"{label} {MATURITY_LABELS.get(mat, str(mat))}"
        frames.append(merged[["date", "spread_bp", "label"]])

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()