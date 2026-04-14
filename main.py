"""
main.py — CLI 전체 파이프라인 실행
python main.py [raw.xlsx 경로]
"""
import sys
from datetime import date
from pathlib import Path
from pprint import pprint
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from data.loader              import load_excel, save_processed
from config.fund_params       import FundParams
from config.constants         import REPO_SPREAD
from core.repo_cost           import calc_repo_cost
from core.portfolio_allocator import select_assets
from core.return_calculator   import Bond, group_bonds, calc_portfolio_return

RAW_PATH = Path("data/raw/raw.xlsx")


def _to_sector(cat: str) -> str:
    if "은행채" in cat: return "은행채(AAA)"
    if "공사"   in cat: return "공사채(AAA)"
    return "기타금융채(AA-)"


def run(xlsx_path: Path = RAW_PATH):
    print("\n" + "="*60)
    print("[ STEP 1. 데이터 로딩 & 저장 ]")
    data = load_excel(xlsx_path)
    save_processed(data)

    sector_data  = {k.removeprefix("S_"): v for k, v in data.items() if k.startswith("S_")}
    i_bond       = data["I_BOND"]
    i_cd         = data["I_CD"]
    base_rate_df = data["기준금리"]

    cur_rate = float(base_rate_df.iloc[0]["rate"])
    print(f"\n현재 기준금리: {cur_rate*100:.2f}%")

    # ── 펀드 파라미터 ──────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("[ STEP 2. 펀드 파라미터 ]")
    fp = FundParams(
        net_asset=400, leverage_ratio=2.0,
        start_date=date(2026, 8, 25), end_date=date(2027, 2, 25),
        base_rate=cur_rate, rating_min="AA-", rating_max="AAA",
        scenarios={
            date(2026,  8, 27): -0.0025,
            date(2026, 10, 22):  0.0000,
            date(2026, 11, 26): -0.0025,
        },
    )
    pprint(fp.summary())

    # ── 레포 조달비용 ──────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("[ STEP 3. 레포 조달비용 ]")
    repo = calc_repo_cost(fp.start_date, fp.end_date, fp.base_rate,
                          fp.scenarios, fp.leverage_ratio)
    print(f"  REPO_SPREAD          : {REPO_SPREAD*100:.2f}% ({REPO_SPREAD*10000:.0f}bp)")
    print(f"  가중평균 기준금리     : {repo['weighted_avg_base_rate']*100:.4f}%")
    print(f"  레포금리 (기준+5bp)  : {repo['repo_rate']*100:.4f}%  ← 이 값을 사용")
    print(f"  레포 조달비용 (×레버): {repo['repo_cost']*100:.4f}%")
    for seg in repo["schedule"]:
        print(f"    {seg['from']} ~ {seg['to']}  {seg['days']}일 @ {seg['rate']*100:.2f}%")

    # ── 편입자산 선택 & Bond 생성 ─────────────────────────────────────────────
    print("\n" + "="*60)
    print("[ STEP 4. 편입자산 선택 (AA-~AAA, 1.5Y) ]")
    TARGET_MAT = 1.5
    selected = select_assets(i_bond, fund_maturity=TARGET_MAT,
                             rating_min=fp.rating_min, rating_max=fp.rating_max, top_n=15)
    print(selected[["issuer","rating","target_mat","yield_target"]].to_string())

    bonds = [
        Bond(sector=_to_sector(row["category"]), rating=row["rating"],
             issuer=row["issuer"], maturity=TARGET_MAT, ytm=row["yield_target"])
        for _, row in selected.iterrows()
    ]

    # ── 섹터별 비중 배분 ──────────────────────────────────────────────────────
    # 고유 섹터 추출 → 섹터당 bond_weight 균등 배분
    # 각 섹터 내 그룹(rating)은 해당 섹터 비중을 다시 균등 분할
    print("\n" + "="*60)
    print("[ STEP 5. 섹터별 비중 배분 ]")

    grouped = group_bonds(bonds)   # {(sector, rating, maturity): avg_ytm}
    unique_sectors = list(dict.fromkeys(k[0] for k in grouped))   # 순서 유지
    sector_weight  = fp.bond_weight / len(unique_sectors)          # 섹터당 AUM 비중

    allocations = {}
    for sector in unique_sectors:
        sector_keys = [k for k in grouped if k[0] == sector]
        per_group   = sector_weight / len(sector_keys)             # 섹터 내 그룹 균등
        for k in sector_keys:
            allocations[k] = per_group

    print(f"  고유 섹터: {unique_sectors}")
    print(f"  섹터당 AUM비중: {sector_weight*100:.2f}%")
    print(f"  그룹별 배분:")
    for k, w in allocations.items():
        print(f"    {k[0]:15s} / {k[1]:5s} / {k[2]}Y  →  {w*100:.3f}%")

    # ── CD 3M ─────────────────────────────────────────────────────────────────
    cd_avg = i_cd[abs(i_cd["maturity"]-0.25)<0.001]["yield"].mean()
    print(f"\n  CD 3M 평균: {cd_avg:.4f}%")

    # ── 수익률 계산 ───────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("[ STEP 6. 포트폴리오 수익률 ]")
    result, group_detail = calc_portfolio_return(
        bonds       = bonds,
        allocations = allocations,
        cd_rate     = cd_avg,
        repo_cost   = repo["repo_rate"] * 100,   # 소수 → % (e.g. 0.02181 → 2.181%)
        bond_weight = fp.bond_weight,
        cash_weight = fp.cash_weight,
        repo_weight = fp.repo_weight,
        sector_data = sector_data,
        target_date = pd.Timestamp("2026-04-08"),
        hold_years  = 0.5,
    )
    result.print_detail(group_detail)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_PATH
    run(path)