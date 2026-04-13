from dataclasses import dataclass, field
from datetime import date
from config.constants import CASH_RESERVE_RATIO


@dataclass
class FundParams:
    """
    사용자 입력값 + 파생 변수 전부 여기서 관리.
    나중에 UI에서 이 객체 하나 만들어서 core에 넘기는 구조.
    """

    # -------------------------------------------------------------------------
    # 사용자 입력 (Input)
    # -------------------------------------------------------------------------
    net_asset: float            # 설정액 (억원), e.g. 400
    leverage_ratio: float       # 레버리지 비율 (소수), e.g. 2.0 = 200%
    start_date: date            # 펀드 개시일
    end_date: date              # 펀드 만기일
    base_rate: float            # 현재 기준금리 (소수), e.g. 0.0275
    rating_min: str             # 편입자산 신용등급 하한, e.g. "AA-"
    rating_max: str             # 편입자산 신용등급 상한, e.g. "AAA"
    scenarios: dict = field(default_factory=dict)
                                # {금통위날짜: 변동폭} e.g. {date(2026,8,27): -0.0025}

    # -------------------------------------------------------------------------
    # 파생 변수 (자동 계산, __post_init__)
    # -------------------------------------------------------------------------
    aum: float = field(init=False)              # 총 운용 자산 (설정액 + 레버리지)
    repo_amount: float = field(init=False)      # 레포 차입액
    cash_amount: float = field(init=False)      # 현금성자산
    bond_amount: float = field(init=False)      # 채권 투자 가능액

    repo_weight: float = field(init=False)      # 레포 비중 (AUM 대비, 음수)
    cash_weight: float = field(init=False)      # 현금성자산 비중 (AUM 대비)
    bond_weight: float = field(init=False)      # 채권 비중 (AUM 대비)

    operating_days: int = field(init=False)     # 운용 일수

    def __post_init__(self):
        L = self.leverage_ratio

        # 금액
        self.aum          = self.net_asset * (1 + L)
        self.repo_amount  = self.net_asset * L               # 차입 = 음수 포지션
        self.cash_amount  = self.net_asset * L * CASH_RESERVE_RATIO
        self.bond_amount  = self.aum - self.cash_amount      # 채권 = AUM - 현금

        # 비중 (AUM 대비 %)
        self.repo_weight  = -L                               # e.g. -2.0 = -200%
        self.cash_weight  =  L * CASH_RESERVE_RATIO          # e.g. 0.40 = 40%
        self.bond_weight  =  1 + L * (1 - CASH_RESERVE_RATIO)  # e.g. 2.60 = 260%

        # 운용 일수
        self.operating_days = (self.end_date - self.start_date).days

    def summary(self) -> dict:
        """핵심 변수 딕셔너리로 반환 (UI 출력 / 디버깅용)"""
        return {
            "설정액":           f"{self.net_asset:.0f}억",
            "레버리지":         f"{self.leverage_ratio*100:.0f}%",
            "총운용자산(AUM)":   f"{self.aum:.0f}억",
            "레포차입":         f"{self.repo_amount:.0f}억  ({self.repo_weight*100:.1f}%)",
            "현금성자산":        f"{self.cash_amount:.0f}억  ({self.cash_weight*100:.1f}%)",
            "채권투자가능액":    f"{self.bond_amount:.0f}억  ({self.bond_weight*100:.1f}%)",
            "운용일수":         f"{self.operating_days}일",
            "개시일":           self.start_date.isoformat(),
            "만기일":           self.end_date.isoformat(),
            "현재기준금리":     f"{self.base_rate*100:.2f}%",
            "신용등급":         f"{self.rating_max} ~ {self.rating_min}",
        }

    def bond_weight_to_amount(self, weight: float) -> float:
        """채권 비중(AUM 대비 소수) → 금액(억원)"""
        return self.aum * weight

    def bond_amount_to_weight(self, amount: float) -> float:
        """채권 금액(억원) → 비중(AUM 대비 소수)"""
        return amount / self.aum


# -----------------------------------------------------------------------------
# 간단 테스트
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    from pprint import pprint

    fp = FundParams(
        net_asset      = 400,
        leverage_ratio = 2.0,
        start_date     = date(2026, 8, 25),
        end_date       = date(2027, 2, 25),
        base_rate      = 0.0275,
        rating_min     = "AA-",
        rating_max     = "AAA",
        scenarios      = {
            date(2026,  8, 27): -0.0025,
            date(2026, 10, 22):  0.0000,
            date(2026, 11, 26): -0.0025,
        },
    )

    pprint(fp.summary())
    print()
    print(f"채권 260% → {fp.bond_weight_to_amount(2.60):.0f}억")
    print(f"채권 1040억 → {fp.bond_amount_to_weight(1040)*100:.1f}%")
