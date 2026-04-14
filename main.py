"""
main.py — CLI 전체 파이프라인 실행
python main.py [raw.xlsx 경로]
"""
import sys
from datetime import date
from pathlib import Path
from pprint import pprint

sys.path.insert(0, str(Path(__file__).parent))

from data.loader      import load_excel, save_processed
from config.fund_params  import FundParams
from core.repo_cost      import calc_repo_cost
from core.asset_selector import select_assets, group_by_rating_maturity
from core.return_calculator import calc_portfolio_return

RAW_PATH = Path("data/raw/raw.xlsx")

def run(xlsx_path: Path = RAW_PATH):
    print("\n" + "="*60)
    print("[ STEP 1. 데이터 로딩 & 저장 ]")
    data = load_excel(xlsx_path)
    save_processed(data)

    sector_data = {k.removeprefix("S_"): v for k, v in data.items() if k.startswith("S_")}
    i_bond      = data["I_BOND"]
    i_cd        = data["I_CD"]
    base_rate   = data["기준금리"]

    cur_rate = float(base_rate.iloc[0]["rate"])
    print(f"\n현재 기준금리: {cur_rate*100:.2f}%")

    # ── 펀드 파라미터 ────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("[ STEP 2. 펀드 파라미터 ]")
    fp = FundParams(
        net_asset      = 400,
        leverage_ratio = 2.0,
        start_date     = date(2026, 8, 25),
        end_date       = date(2027, 2, 25),
        base_rate      = cur_rate,
        rating_min     = "AA-",
        rating_max     = "AAA",
        scenarios      = {
            date(2026,  8, 27): -0.0025,
            date(2026, 10, 22):  0.0000,
            date(2026, 11, 26): -0.0025,
        },
    )
    pprint(fp.summary())

    # ── 레포 조달비용 ────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("[ STEP 3. 레포 조달비용 ]")
    repo = calc_repo_cost(
        fp.start_date, fp.end_date, fp.base_rate,
        fp.scenarios, fp.leverage_ratio,
    )
    print(f"  가중평균 기준금리: {repo['weighted_avg_base_rate']*100:.4f}%")
    print(f"  레포금리 (기준+5bp): {repo['repo_rate']*100:.4f}%")
    print(f"  레포 조달비용 (레버리지 반영): {repo['repo_cost']*100:.4f}%")
    print("  구간별 상세:")
    for seg in repo["schedule"]:
        print(f"    {seg['from']} ~ {seg['to']}  {seg['days']}일 @ {seg['rate']*100:.2f}%")

    # ── 편입자산 선택 ────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("[ STEP 4. 편입자산 선택 (AA-~AAA, 1Y~2Y) ]")
    selected = select_assets(i_bond,
                             rating_min="AA-", rating_max="AAA",
                             maturity_min=1.0, maturity_max=2.0,
                             top_n=15)
    print(selected[["issuer","rating","maturity","yield"]].to_string())

    grouped = group_by_rating_maturity(selected)
    print("\n  그룹별 평균:")
    print(grouped.to_string(index=False))

    # ── CD 3M 평균 (현금수익률) ──────────────────────────────────────────────
    cd_3m    = i_cd[abs(i_cd["maturity"] - 0.25) < 0.001]["yield"]
    cd_rate  = cd_3m.mean() / 100   # % → 소수
    print(f"\n  CD 3M 평균: {cd_rate*100:.4f}%")

    # ── 수익률 계산 ──────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("[ STEP 5. 포트폴리오 수익률 ]")

    # 단순 균등배분 예시 (추후 UI에서 사용자 입력으로 교체)
    n = len(grouped)
    allocs = {(row["rating"], row["maturity"]): fp.bond_weight / n
              for _, row in grouped.iterrows()}

    result = calc_portfolio_return(
        grouped_bonds = grouped,
        allocations   = allocs,
        cd_rate       = cd_rate,
        repo_cost     = repo["repo_rate"],
        bond_weight   = fp.bond_weight,
        cash_weight   = fp.cash_weight,
        repo_weight   = fp.repo_weight,
    )
    pprint(result.summary())

if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_PATH
    run(path)