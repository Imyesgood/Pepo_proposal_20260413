"""
core/return_calculator.py
=========================
포트폴리오 최종 수익률 계산.

핵심 구조:
    1. 사용자가 선택한 채권 리스트 (Bond)
    2. 그룹핑(sector, rating, maturity) → 그룹별 평균 YTM
    3. 그룹별 평균 YTM × 투자비중 → 채권 포트폴리오 수익률
    4. 현금 수익률 (CD금리) × 현금 비중
    5. 레포 조달비용 × 레버리지 비중 (차감)
    6. 롤링 미적용 / 롤링 적용 두 버전 병렬 계산

롤링 수익률:
    실제 S-type 커브 데이터에서 해당 만기 구간 기울기(bp/년) 추출
    rolling_yield = hold_years × slope(mat_from → mat_to)
"""

import pandas as pd
from dataclasses import dataclass, field


# =============================================================================
# 채권 데이터 구조
# =============================================================================

@dataclass
class Bond:
    """
    채권 한 종목.
    그룹핑 키: (sector, rating, maturity) — issuer는 참고용.
    """
    sector:   str    # S-type 라벨 e.g. "은행채(AAA)"
    rating:   str    # e.g. "AA+"
    issuer:   str    # 발행사명 (참고용)
    maturity: float  # 만기 (년)
    ytm:      float  # 수익률 (%) e.g. 3.41

    def group_key(self) -> tuple:
        return (self.sector, self.rating, self.maturity)


# =============================================================================
# 그룹핑 및 평균 YTM
# =============================================================================

def group_bonds(bonds: list[Bond]) -> dict[tuple, float]:
    """
    (sector, rating, maturity) 기준 그룹핑 → 그룹별 평균 YTM(%) 반환.

    예시:
        전북은행  AA+ 1.5Y 3.41%  ┐
        제주은행  AA+ 1.5Y 3.40%  ├→ ("은행채(AAA)", "AA+", 1.5): 3.405%
        광주은행  AA+ 1.5Y 3.40%  ┘
    """
    groups: dict[tuple, list[float]] = {}
    for b in bonds:
        groups.setdefault(b.group_key(), []).append(b.ytm)
    return {k: sum(v) / len(v) for k, v in groups.items()}


# =============================================================================
# 롤링 수익률 — 실제 커브 기울기 사용
# =============================================================================

def get_slope(
    sector_data: dict,          # {섹터라벨: DataFrame[date, maturity, yield]}
    sector:      str,           # Bond.sector e.g. "은행채(AAA)"
    target_date: pd.Timestamp,
    mat_from:    float,         # 롤다운 후 만기 (= target_mat - hold_years)
    mat_to:      float,         # 매입 만기
) -> float:
    """
    S-type 커브에서 (mat_from → mat_to) 구간 기울기 반환 (%/년).
    해당 만기가 없으면 인접 구간 보간.
    """
    df = sector_data.get(sector, pd.DataFrame())
    if df.empty:
        return 0.0

    day  = df[df["date"] == target_date].set_index("maturity")["yield"]
    mats = sorted(day.index.tolist())

    # mat_from, mat_to와 가장 가까운 값 찾기
    lower = min(mats, key=lambda m: abs(m - mat_from))
    upper = min(mats, key=lambda m: abs(m - mat_to))

    if lower == upper:
        return 0.0

    return (day[upper] - day[lower]) / (upper - lower)   # %/년


def calc_rolling_yield(
    bond:        Bond,
    sector_data: dict,
    target_date: pd.Timestamp,
    hold_years:  float = 0.5,
) -> float:
    """
    롤링 수익률 계산 (%).

    개념:
        1.5Y 채권을 6개월 보유 → 만기 1.0Y로 롤다운
        이때 커브 기울기(1.0Y→1.5Y) × 0.5년 = 추가 수익

    공식:
        rolling_yield(%) = hold_years × slope(mat_from→mat_to)
        where mat_from = bond.maturity - hold_years
              mat_to   = bond.maturity
    """
    mat_from = bond.maturity - hold_years
    if mat_from <= 0:
        return 0.0

    slope   = get_slope(sector_data, bond.sector, target_date, mat_from, bond.maturity)
    rolling = hold_years * slope   # %
    return rolling


# =============================================================================
# 포트폴리오 수익률 결과
# =============================================================================

@dataclass
class ReturnResult:
    """롤링 유/무 두 버전 + 컴포넌트별 상세."""

    # 컴포넌트 (모두 %)
    bond_yield_plain:   float   # 채권 가중평균 YTM (롤링 미적용)
    bond_yield_rolling: float   # 채권 YTM + 롤링
    cash_yield:         float   # CD금리
    repo_cost:          float   # 레포금리

    # 비중 (AUM 대비 소수)
    bond_weight:  float         # e.g. 2.60
    cash_weight:  float         # e.g. 0.40
    repo_weight:  float         # e.g. -2.00

    # 최종 (자동 계산)
    total_plain:   float = field(init=False)
    total_rolling: float = field(init=False)

    def __post_init__(self):
        """
        포트폴리오 수익률 공식:
            total = 채권YTM × bond_weight
                  + CD금리   × cash_weight
                  + 레포금리 × repo_weight   ← repo_weight 음수 → 차감
        단위: 모두 % → 결과도 %
        """
        self.total_plain = (
            self.bond_yield_plain   * self.bond_weight
            + self.cash_yield       * self.cash_weight
            + self.repo_cost        * self.repo_weight
        )
        self.total_rolling = (
            self.bond_yield_rolling * self.bond_weight
            + self.cash_yield       * self.cash_weight
            + self.repo_cost        * self.repo_weight
        )

    def print_detail(self, group_detail: dict | None = None):
        """
        계산 과정 상세 출력.
        group_detail: {group_key: (avg_ytm, rolling, weight, allocations)} 옵션
        """
        print("\n" + "="*60)
        print("[ 포트폴리오 수익률 계산 ]")
        print("="*60)

        if group_detail:
            print("\n  ── 그룹별 상세 ──")
            print(f"  {'그룹':30s}  {'YTM':>7}  {'롤링':>8}  {'비중':>7}  "
                  f"{'기여(롤X)':>10}  {'기여(롤O)':>10}")
            print("  " + "-"*80)
            for key, (ytm, roll, w, issuers) in group_detail.items():
                sector, rating, mat = key
                contrib_plain   = ytm * w
                contrib_rolling = (ytm + roll) * w
                print(f"  {sector}/{rating}/{mat}Y  "
                      f"{ytm:>7.3f}%  {roll:>7.3f}%  {w*100:>6.1f}%  "
                      f"{contrib_plain:>10.4f}%  {contrib_rolling:>10.4f}%")
                print(f"    issuers: {issuers}")

        print(f"\n  ── 컴포넌트 수익률 ──")
        print(f"  채권 YTM   (롤링X): {self.bond_yield_plain:>8.4f}%  × {self.bond_weight*100:.1f}%"
              f"  = {self.bond_yield_plain * self.bond_weight:>8.4f}%")
        print(f"  채권 YTM   (롤링O): {self.bond_yield_rolling:>8.4f}%  × {self.bond_weight*100:.1f}%"
              f"  = {self.bond_yield_rolling * self.bond_weight:>8.4f}%")
        print(f"  CD금리             : {self.cash_yield:>8.4f}%  × {self.cash_weight*100:.1f}%"
              f"  = {self.cash_yield * self.cash_weight:>8.4f}%")
        print(f"  레포금리 (차감)    : {self.repo_cost:>8.4f}%  × {self.repo_weight*100:.1f}%"
              f"  = {self.repo_cost * self.repo_weight:>8.4f}%")
        print(f"\n  {'─'*55}")
        print(f"  포트폴리오 (롤링X): {self.total_plain:>8.4f}%")
        print(f"  포트폴리오 (롤링O): {self.total_rolling:>8.4f}%")
        print(f"  롤링 기여         : {self.total_rolling - self.total_plain:>8.4f}%")

    def summary(self) -> dict:
        return {
            "채권수익률(롤링X)":  f"{self.bond_yield_plain:.4f}%",
            "채권수익률(롤링O)":  f"{self.bond_yield_rolling:.4f}%",
            "현금수익률(CD)":     f"{self.cash_yield:.4f}%",
            "레포조달비용":       f"{self.repo_cost:.4f}%",
            "포트폴리오(롤링X)":  f"{self.total_plain:.4f}%",
            "포트폴리오(롤링O)":  f"{self.total_rolling:.4f}%",
            "롤링 기여":          f"{self.total_rolling - self.total_plain:.4f}%",
        }


# =============================================================================
# 메인 계산 함수
# =============================================================================

def calc_portfolio_return(
    bonds:        list[Bond],
    allocations:  dict[tuple, float],  # {(sector, rating, maturity): AUM대비 비중}
    cd_rate:      float,               # CD금리 (%)
    repo_cost:    float,               # 레포금리 (%)
    bond_weight:  float,               # 채권 총비중 (AUM대비)
    cash_weight:  float,
    repo_weight:  float,               # 음수
    sector_data:  dict       = None,   # 롤링 계산용 S-type 데이터
    target_date:  pd.Timestamp = None,
    hold_years:   float      = 0.5,    # 보유기간 (년)
) -> tuple[ReturnResult, dict]:
    """
    포트폴리오 수익률 계산.

    Returns:
        (ReturnResult, group_detail)
        group_detail: {group_key: (avg_ytm, rolling, weight, issuer_str)}
    """
    grouped = group_bonds(bonds)

    # 롤링 계산을 위한 bond 인덱스 (group_key → bonds)
    bond_by_group: dict[tuple, list[Bond]] = {}
    for b in bonds:
        bond_by_group.setdefault(b.group_key(), []).append(b)

    weighted_plain   = 0.0
    weighted_rolling = 0.0
    group_detail     = {}

    for key, avg_ytm in grouped.items():
        weight = allocations.get(key, 0.0)

        # 롤링: 그룹 내 첫 번째 Bond 기준으로 계산 (같은 sector/maturity면 동일)
        rolling = 0.0
        if sector_data and target_date:
            rep_bond = bond_by_group[key][0]
            rolling  = calc_rolling_yield(rep_bond, sector_data, target_date, hold_years)

        weighted_plain   += avg_ytm             * weight
        weighted_rolling += (avg_ytm + rolling) * weight

        issuers = ", ".join(b.issuer for b in bond_by_group[key])
        group_detail[key] = (avg_ytm, rolling, weight, issuers)

    # 채권 비중으로 정규화 (bond_weight로 나눠서 "채권 내 수익률" 계산)
    if bond_weight > 0:
        bond_plain   = weighted_plain   / bond_weight
        bond_rolling = weighted_rolling / bond_weight
    else:
        bond_plain = bond_rolling = 0.0

    result = ReturnResult(
        bond_yield_plain   = bond_plain,
        bond_yield_rolling = bond_rolling,
        cash_yield         = cd_rate,
        repo_cost          = repo_cost,
        bond_weight        = bond_weight,
        cash_weight        = cash_weight,
        repo_weight        = repo_weight,
    )
    return result, group_detail


# =============================================================================
# 실행 테스트
# =============================================================================

if __name__ == "__main__":
    import sys, warnings
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    warnings.filterwarnings('ignore')

    from data.loader import load_excel
    from core.repo_cost import calc_repo_cost
    from config.fund_params import FundParams
    from core.portfolio_allocator import select_assets
    from datetime import date

    _root = Path(__file__).parent.parent
    data  = load_excel(_root / "data" / "raw" / "raw.xlsx")
    i_bond      = data['I_BOND']
    i_cd        = data['I_CD']
    sector_data = {k.removeprefix("S_"): v for k, v in data.items() if k.startswith("S_")}
    base_rate_df = data['기준금리']

    fp = FundParams(
        net_asset=400, leverage_ratio=2.0,
        start_date=date(2026, 8, 25), end_date=date(2027, 2, 25),
        base_rate=float(base_rate_df.iloc[0]['rate']),
        rating_min="AA-", rating_max="AAA",
        scenarios={
            date(2026,  8, 27): -0.0025,
            date(2026, 10, 22):  0.0000,
            date(2026, 11, 26): -0.0025,
        },
    )

    TARGET_DATE = pd.Timestamp("2026-04-08")
    TARGET_MAT  = 1.5
    HOLD_YEARS  = 0.5

    # ── STEP 1: 상위 5개 채권 선택 (사용자가 UI에서 조정) ───────────────────
    print("\n[ STEP 1. 편입 채권 선택 (상위 5개, 사용자 조정 가능) ]")
    selected = select_assets(i_bond, fund_maturity=TARGET_MAT,
                             rating_min=fp.rating_min, rating_max=fp.rating_max, top_n=5)

    # I_BOND sector 컬럼 매핑 (category → S-type 라벨)
    def to_sector_label(cat: str) -> str:
        if "은행채" in cat:   return "은행채(AAA)"
        if "공사" in cat:     return "공사채(AAA)"
        return "기타금융채(AA-)"

    bonds = [
        Bond(
            sector   = to_sector_label(row["category"]),
            rating   = row["rating"],
            issuer   = row["issuer"],
            maturity = TARGET_MAT,
            ytm      = row["yield_target"],
        )
        for _, row in selected.iterrows()
    ]

    print(f"  선택 채권 {len(bonds)}개:")
    for b in bonds:
        print(f"    {b.issuer:20s}  {b.sector:15s}  {b.rating:5s}  {b.ytm:.3f}%")

    # ── STEP 2: 배분 (균등) ────────────────────────────────────────────────
    grouped = group_bonds(bonds)
    n = len(grouped)
    allocations = {k: fp.bond_weight / n for k in grouped}

    # ── STEP 3: CD 3M, 레포 ───────────────────────────────────────────────
    cd_avg  = i_cd[abs(i_cd["maturity"] - 0.25) < 0.001]["yield"].mean()
    repo    = calc_repo_cost(fp.start_date, fp.end_date, fp.base_rate,
                             fp.scenarios, fp.leverage_ratio)

    # ── STEP 4: 수익률 계산 ───────────────────────────────────────────────
    result, group_detail = calc_portfolio_return(
        bonds        = bonds,
        allocations  = allocations,
        cd_rate      = cd_avg,
        repo_cost    = repo["repo_rate"] * 100,
        bond_weight  = fp.bond_weight,
        cash_weight  = fp.cash_weight,
        repo_weight  = fp.repo_weight,
        sector_data  = sector_data,
        target_date  = TARGET_DATE,
        hold_years   = HOLD_YEARS,
    )

    result.print_detail(group_detail)


# =============================================================================
# 금리 변동 반영 롤링 수익률
# =============================================================================

def calc_rate_change_effect(
    maturity:   float,   # 매입 만기 (년)
    hold_years: float,   # 보유기간 (년)
    delta_y:    float,   # 금리 변동폭 (%, e.g. -0.25 = -25bp)
) -> float:
    """
    금리 수준 변동에 따른 가격 효과 (%).

    공식:
        rate_change_effect = -(잔존만기) × Δy
        잔존만기 = maturity - hold_years  (simplified duration)

    예시:
        1.5Y 채권, 6개월 보유, 금리 -25bp 하락
        → 잔존만기 = 1.0Y
        → 효과 = -1.0 × (-0.25) = +0.25%  (금리하락 → 가격상승)
    """
    remaining_duration = maturity - hold_years  # 잔존만기 ≈ modified duration (근사)
    if remaining_duration <= 0:
        return 0.0
    return -remaining_duration * delta_y   # %


def calc_rolling_total(
    bond:        Bond,
    sector_data: dict,
    target_date: pd.Timestamp,
    hold_years:  float,
    delta_y:     float = 0.0,   # 금리 변동폭 (%, 0 = 현행 유지)
) -> dict:
    """
    롤링 수익률 전체 분해 반환.

    Returns:
        {
          "rolldown":      커브 형태 효과 (%),
          "rate_change":   금리 수준 변동 효과 (%),
          "total_rolling": 합계 (%),
          "delta_y":       입력 금리 변동폭 (%),
        }
    """
    rolldown    = calc_rolling_yield(bond, sector_data, target_date, hold_years)
    rate_effect = calc_rate_change_effect(bond.maturity, hold_years, delta_y)
    return {
        "rolldown":      round(rolldown, 4),
        "rate_change":   round(rate_effect, 4),
        "total_rolling": round(rolldown + rate_effect, 4),
        "delta_y":       delta_y,
    }


def build_rate_scenario_table(
    bonds:        list[Bond],
    sector_data:  dict,
    target_date:  pd.Timestamp,
    hold_years:   float,
    delta_y_list: list[float] | None = None,   # [%] e.g. [-0.5, -0.25, 0, 0.25, 0.5]
) -> pd.DataFrame:
    """
    금리 변동 시나리오별 롤링 수익률 테이블.

    Rows:   채권 (issuer)
    Cols:   Δy 시나리오별 total_rolling(%)
    마지막: 채권 평균

    예시 출력:
                     -50bp   -25bp    0bp   +25bp   +50bp
        용인도시공사   0.674   0.424  0.171  -0.079  -0.329
        안산도시공사   0.674   0.424  0.171  -0.079  -0.329
        ...
        평균          0.674   0.424  0.171  -0.079  -0.329
    """
    if delta_y_list is None:
        delta_y_list = [-0.50, -0.25, 0.0, 0.25, 0.50]

    col_labels = [f"{d*100:+.0f}bp" for d in delta_y_list]
    rows = []
    for b in bonds:
        row = {"발행사": b.issuer, "섹터": b.sector, "등급": b.rating, "만기": b.maturity}
        for dy, label in zip(delta_y_list, col_labels):
            result = calc_rolling_total(b, sector_data, target_date, hold_years, delta_y=dy)
            row[label] = result["total_rolling"]
        rows.append(row)

    df = pd.DataFrame(rows)

    # 평균 행 추가
    avg_row = {"발행사": "─ 평균 ─", "섹터": "", "등급": "", "만기": ""}
    for label in col_labels:
        avg_row[label] = round(df[label].mean(), 4)
    df = pd.concat([df, pd.DataFrame([avg_row])], ignore_index=True)

    return df


def get_delta_y_from_scenarios(
    base_rate:    float,       # 현재 기준금리 (%)
    scenarios:    dict,        # {date: 변동폭(소수)} e.g. {date: -0.0025}
    start_date,
    end_date,
) -> float:
    """
    금통위 시나리오에서 기간 내 총 금리 변동폭 계산 (%).
    레포 조달비용 계산과 동일한 구간 적용.
    """
    from config.constants import BOK_DATES
    changes = [
        bp for d, bp in scenarios.items()
        if start_date < d <= end_date
    ]
    return sum(changes) * 100   # 소수 → %
