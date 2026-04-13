"""
return_calculator.py
====================
포트폴리오 최종 수익률 계산.

핵심 구조:
    1. 채권 리스트 → 그룹핑(sector, rating, maturity) → 그룹별 YTM 평균
    2. 그룹별 평균 YTM × 투자비중 → 채권 포트폴리오 수익률
    3. 현금 수익률 (CD금리) × 현금 비중
    4. 레포 조달비용 × 레버리지 비중 (차감)
    5. 롤링 미적용 / 롤링 적용 두 버전 병렬 계산

롤링 수익률 계산 (TODO: 고도화 필요):
    현재: maturity × 커브 기울기 근사치 (임시)
    추후: 실제 커브 피팅 후 교체 예정
    → 주석 [ROLLING_TODO] 표시된 부분 수정하면 됨

용어 정리:
    - maturity  : 만기 (년), 현재 사용하는 값
    - duration  : 수정듀레이션, 고도화 시 교체 예정 (현재 미사용)
"""

from dataclasses import dataclass, field


# =============================================================================
# 채권 데이터 구조
# =============================================================================

@dataclass
class Bond:
    """
    채권 한 종목.

    그룹핑 키: (sector, rating, maturity)
    issuer는 참고용 - 그룹핑에 사용하지 않음.

    maturity vs duration:
        현재는 maturity로 롤링 계산.
        고도화 시 duration 필드 추가 후 교체 예정.
    """
    sector:   str    # 섹터 (시트명 S_ / I_ 뒤 이름)
    rating:   str    # 신용등급 e.g. "AA-"
    issuer:   str    # 발행사 (참고용, 그룹핑 키 아님)
    maturity: float  # 만기 (년) e.g. 1.0, 2.0, 3.0
    ytm:      float  # 수익률 (소수) e.g. 0.0310

    def group_key(self) -> tuple:
        """그룹핑 키 반환: (sector, rating, maturity)"""
        return (self.sector, self.rating, self.maturity)


# =============================================================================
# 그룹핑 및 평균 YTM
# =============================================================================

def group_bonds(bonds: list[Bond]) -> dict[tuple, float]:
    """
    채권 리스트를 (sector, rating, maturity) 기준으로 그룹핑 후
    그룹별 YTM 단순평균 반환.

    Returns:
        {(sector, rating, maturity): 평균YTM}

    예시:
        삼성 AA- 1Y 3.10%
        현대 AA- 1Y 3.15%  ->  ("기타금융채", "AA-", 1.0): 0.03125
        롯데 AA- 1Y 3.08%
    """
    groups: dict[tuple, list[float]] = {}
    for bond in bonds:
        key = bond.group_key()
        groups.setdefault(key, []).append(bond.ytm)

    return {
        key: sum(ytms) / len(ytms)
        for key, ytms in groups.items()
    }


# =============================================================================
# 롤링 수익률 계산
# =============================================================================

def calc_rolling_yield(maturity: float, curve_slope: float) -> float:
    """
    롤링 수익률 근사 계산.

    [ROLLING_TODO] 현재 임시 공식: maturity × curve_slope
    추후 실제 커브 피팅 결과로 교체 필요.
    고도화 시 duration으로 교체 예정 (현재 maturity 사용).

    Args:
        maturity    : 만기 (년)
        curve_slope : 커브 기울기 근사치 (소수/년)
                      e.g. 10bp/년 기울기 -> 0.0010
                      [ROLLING_TODO] curve_analysis.py 완성 후 자동 계산으로 교체

    Returns:
        롤링 수익률 (소수)
    """
    # [ROLLING_TODO] 임시 공식, 추후 교체
    return maturity * curve_slope


# =============================================================================
# 포트폴리오 수익률 계산
# =============================================================================

@dataclass
class ReturnResult:
    """
    수익률 계산 결과.
    롤링 유/무 두 버전 항상 같이 반환.
    significant 기준 없음 - 사용자가 직접 판단.
    """

    # 컴포넌트별 수익률 (비중 반영 전, 연율)
    bond_yield_plain:   float   # 채권 단순 YTM (롤링 미적용)
    bond_yield_rolling: float   # 채권 YTM + 롤링 (롤링 적용)
    cash_yield:         float   # 현금 수익률 (CD금리)
    repo_cost:          float   # 레포 조달비용 (기준금리 + 5bp)

    # 비중 (AUM 대비 소수)
    bond_weight:  float         # e.g. 2.60
    cash_weight:  float         # e.g. 0.40
    repo_weight:  float         # e.g. -2.00 (음수)

    # 최종 포트폴리오 수익률 (비중 반영, 자동 계산)
    total_plain:   float = field(init=False)
    total_rolling: float = field(init=False)

    def __post_init__(self):
        # 채권수익 + 현금수익 - 레포조달비용
        # repo_weight가 음수라 자동으로 차감됨
        self.total_plain = (
              self.bond_yield_plain   * self.bond_weight
            + self.cash_yield         * self.cash_weight
            + self.repo_cost          * self.repo_weight
        )
        self.total_rolling = (
              self.bond_yield_rolling * self.bond_weight
            + self.cash_yield         * self.cash_weight
            + self.repo_cost          * self.repo_weight
        )

    def summary(self) -> dict:
        return {
            "채권수익률(롤링X)":  f"{self.bond_yield_plain*100:.4f}%",
            "채권수익률(롤링O)":  f"{self.bond_yield_rolling*100:.4f}%",
            "현금수익률(CD)":     f"{self.cash_yield*100:.4f}%",
            "레포조달비용":       f"{self.repo_cost*100:.4f}%",
            "───────────────":    "──────────",
            "포트폴리오(롤링X)":  f"{self.total_plain*100:.4f}%",
            "포트폴리오(롤링O)":  f"{self.total_rolling*100:.4f}%",
            "롤링 기여":          f"{(self.total_rolling - self.total_plain)*100:.4f}%",
        }


def calc_portfolio_return(
    bonds:        list[Bond],
    allocations:  dict[tuple, float],  # {(sector, rating, maturity): 투자비중(AUM대비 소수)}
    cd_rate:      float,               # CD금리 (소수)
    repo_cost:    float,               # 레포 조달비용 (소수, repo_cost.py 결과값)
    bond_weight:  float,               # 채권 총 비중 (AUM 대비)
    cash_weight:  float,               # 현금 총 비중 (AUM 대비)
    repo_weight:  float,               # 레포 비중 (AUM 대비, 음수)
    curve_slope:  float = 0.0010,      # 커브 기울기 [ROLLING_TODO] 임시값
) -> ReturnResult:
    """
    포트폴리오 수익률 계산 메인 함수.

    Args:
        bonds       : 편입 채권 리스트 (Bond 객체)
        allocations : {그룹키: AUM대비 투자비중} - portfolio_allocator.py 결과 활용
        cd_rate     : CD금리
        repo_cost   : 레포 조달비용 (repo_cost.py에서 가져옴)
        curve_slope : 커브 기울기 [ROLLING_TODO] 임시값, 추후 교체

    Returns:
        ReturnResult (롤링 유/무 두 버전 포함)
    """
    grouped = group_bonds(bonds)

    # -------------------------------------------------------------------------
    # 채권 수익률 계산 (그룹별 평균 YTM × 투자비중 가중합)
    # -------------------------------------------------------------------------
    weighted_plain   = 0.0
    weighted_rolling = 0.0

    for key, avg_ytm in grouped.items():
        _, _, maturity = key
        weight  = allocations.get(key, 0.0)
        rolling = calc_rolling_yield(maturity, curve_slope)

        weighted_plain   += avg_ytm             * weight
        weighted_rolling += (avg_ytm + rolling) * weight

    # AUM 대비 비중 -> 채권 포트폴리오 내 비중으로 정규화
    if bond_weight > 0:
        bond_yield_plain   = weighted_plain   / bond_weight
        bond_yield_rolling = weighted_rolling / bond_weight
    else:
        bond_yield_plain = bond_yield_rolling = 0.0

    return ReturnResult(
        bond_yield_plain   = bond_yield_plain,
        bond_yield_rolling = bond_yield_rolling,
        cash_yield         = cd_rate,
        repo_cost          = repo_cost,
        bond_weight        = bond_weight,
        cash_weight        = cash_weight,
        repo_weight        = repo_weight,
    )


# =============================================================================
# 간단 테스트
# =============================================================================

if __name__ == "__main__":
    from pprint import pprint

    bonds = [
        Bond("기타금융채", "AA-", "삼성", 1.0, 0.0310),
        Bond("기타금융채", "AA-", "현대", 1.0, 0.0315),
        Bond("기타금융채", "AA-", "롯데", 1.0, 0.0308),
        Bond("기타금융채", "AA-", "삼성", 2.0, 0.0330),
        Bond("기타금융채", "AA-", "현대", 2.0, 0.0325),
        Bond("기타금융채", "AA+", "국민", 3.0, 0.0350),
    ]

    # 그룹별 투자비중 (AUM 대비)
    allocations = {
        ("기타금융채", "AA-", 1.0): 0.80,
        ("기타금융채", "AA-", 2.0): 0.90,
        ("기타금융채", "AA+", 3.0): 0.90,
    }

    # 그룹핑 결과 확인
    print("=== 그룹별 평균 YTM ===")
    for key, avg in group_bonds(bonds).items():
        print(f"  {key[0]:10s}  {key[1]:5s}  {key[2]}Y -> {avg*100:.4f}%")

    result = calc_portfolio_return(
        bonds        = bonds,
        allocations  = allocations,
        cd_rate      = 0.0310,
        repo_cost    = 0.0243,   # repo_cost.py 결과
        bond_weight  = 2.60,
        cash_weight  = 0.40,
        repo_weight  = -2.00,
        curve_slope  = 0.0010,   # [ROLLING_TODO] 임시값
    )

    print()
    pprint(result.summary())
