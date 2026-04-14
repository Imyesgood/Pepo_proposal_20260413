"""
eda/curve_snapshot.py — 순수 함수만. Streamlit 없음.
"""
import pandas as pd
from pathlib import Path

MATURITY_LABELS = {
    0.25:"3M", 0.5:"6M", 0.75:"9M", 1.0:"1Y", 1.5:"1Y6M",
    2.0:"2Y", 2.5:"2Y6M", 3.0:"3Y", 4.0:"4Y", 5.0:"5Y",
    7.0:"7Y", 10.0:"10Y", 15.0:"15Y", 20.0:"20Y", 30.0:"30Y",
}


def build_slope_table(sector_data, target_date) -> pd.DataFrame:
    """
    인접 만기 구간별 기울기 테이블 (bp/년).
    index=구간, columns=섹터
    """
    snap = {}
    common_mats = None
    for label, df in sector_data.items():
        day = df[df["date"] == target_date].set_index("maturity")["yield"]
        snap[label] = day
        common_mats = set(day.index) if common_mats is None else common_mats & set(day.index)

    mats = sorted(common_mats)
    rows = []
    for i in range(len(mats) - 1):
        m1, m2 = mats[i], mats[i + 1]
        row = {"구간": f"{MATURITY_LABELS.get(m1,str(m1))}→{MATURITY_LABELS.get(m2,str(m2))}"}
        for label, day in snap.items():
            if m1 in day.index and m2 in day.index:
                row[label] = round((day[m2] - day[m1]) / (m2 - m1) * 100, 1)
        rows.append(row)
    return pd.DataFrame(rows).set_index("구간")


def build_rolling_table(sector_data, target_date, hold_years=0.5) -> pd.DataFrame:
    """
    롤링 수익률 근사 테이블.
    rolling = hold_years × 구간기울기(%/년)
    """
    rows = []
    for label, df in sector_data.items():
        day  = df[df["date"] == target_date].sort_values("maturity")
        mats = day["maturity"].values
        ytms = day["yield"].values
        for i in range(len(mats) - 1):
            m1, m2 = mats[i], mats[i + 1]
            slope   = (ytms[i + 1] - ytms[i]) / (m2 - m1)
            rolling = hold_years * slope
            rows.append({
                "섹터":            label,
                "매입만기":        MATURITY_LABELS.get(m2, str(m2)),
                "롤후만기":        MATURITY_LABELS.get(m1, str(m1)),
                "기울기(bp/년)":   round(slope * 100, 2),
                "롤링수익률(bp)":  round(rolling * 100, 2),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys, warnings
    sys.path.insert(0, str(Path(__file__).parent.parent))
    warnings.filterwarnings('ignore')
    from data.loader import load_excel
    from pathlib import Path

    RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "raw.xlsx"
    data        = load_excel(RAW_PATH)
    sector_data = {k.removeprefix("S_"): v for k, v in data.items() if k.startswith("S_")}

    target = sector_data[list(sector_data.keys())[0]]["date"].max()

    print(f"\n[ 구간별 기울기 (bp/년) | {target.date()} ]")
    print(build_slope_table(sector_data, target).to_string())

    print(f"\n[ 롤링 수익률 근사 | 6개월 보유 | {target.date()} ]")
    roll = build_rolling_table(sector_data, target, hold_years=0.5)
    print(roll.to_string(index=False))
