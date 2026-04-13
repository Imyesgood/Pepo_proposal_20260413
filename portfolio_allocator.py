from dataclasses import dataclass


@dataclass
class BondAllocation:
    """채권 한 종목의 배분 정보"""
    name: str           # 종목명 / 라벨 (자유)
    weight: float       # AUM 대비 비중 (소수, e.g. 0.50 = 50%)
    amount: float       # 금액 (억원)

    def __str__(self):
        return f"{self.name:20s}  {self.weight*100:7.3f}%  {self.amount:10.3f}억"


class AssetAllocator:
    """
    채권 배분 계산기.
    비중 → 금액, 금액 → 비중 양방향 변환.
    합계 검증 포함.
    """

    def __init__(self, bond_amount: float, bond_weight: float, aum: float):
        """
        Args:
            bond_amount : 채권 투자 가능 총액 (억원)
            bond_weight : 채권 총 비중 (소수, AUM 대비)
            aum         : 총 운용자산 (억원)
        """
        self.bond_amount = bond_amount
        self.bond_weight = bond_weight
        self.aum         = aum
        self.allocations: list[BondAllocation] = []

    # -------------------------------------------------------------------------
    # 입력
    # -------------------------------------------------------------------------
    def from_weights(self, items: dict[str, float]) -> list[BondAllocation]:
        """
        비중 입력 → 금액 자동 계산.

        Args:
            items: {종목명: 채권총액 대비 비중(소수)} e.g. {"AAA": 0.50, "AA+": 0.30}
                   비중 합계가 1.0 이어야 함 (채권 내 비중)

        Returns:
            BondAllocation 리스트
        """
        self._validate_weights(items)
        self.allocations = [
            BondAllocation(
                name   = name,
                weight = w * self.bond_weight,          # AUM 대비 비중으로 변환
                amount = w * self.bond_amount,          # 금액
            )
            for name, w in items.items()
        ]
        return self.allocations

    def from_amounts(self, items: dict[str, float]) -> list[BondAllocation]:
        """
        금액 입력 → 비중 자동 계산.

        Args:
            items: {종목명: 금액(억원)} e.g. {"AAA": 500, "AA+": 300, "AA-": 240}
                   금액 합계가 bond_amount 이어야 함

        Returns:
            BondAllocation 리스트
        """
        self._validate_amounts(items)
        self.allocations = [
            BondAllocation(
                name   = name,
                weight = amount / self.aum,             # AUM 대비 비중
                amount = amount,
            )
            for name, amount in items.items()
        ]
        return self.allocations

    # -------------------------------------------------------------------------
    # 검증
    # -------------------------------------------------------------------------
    def _validate_weights(self, items: dict[str, float]):
        total = sum(items.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"비중 합계가 100%가 아님: {total*100:.3f}% "
                f"(차이: {(total-1)*100:+.3f}%)"
            )

    def _validate_amounts(self, items: dict[str, float]):
        total = sum(items.values())
        if abs(total - self.bond_amount) > 0.001:
            raise ValueError(
                f"금액 합계({total:.3f}억)가 채권 투자가능액({self.bond_amount:.3f}억)과 다름 "
                f"(차이: {total - self.bond_amount:+.3f}억)"
            )

    # -------------------------------------------------------------------------
    # 출력
    # -------------------------------------------------------------------------
    def summary(self):
        if not self.allocations:
            print("배분 없음")
            return

        print(f"{'종목':<20}  {'AUM비중':>8}  {'금액':>12}")
        print("-" * 46)
        for a in self.allocations:
            print(a)
        print("-" * 46)
        total_w = sum(a.weight for a in self.allocations)
        total_a = sum(a.amount for a in self.allocations)
        print(f"{'합계':<20}  {total_w*100:7.3f}%  {total_a:10.3f}억")

    def to_dict(self) -> list[dict]:
        return [
            {"name": a.name, "weight": a.weight, "amount": a.amount}
            for a in self.allocations
        ]


# -----------------------------------------------------------------------------
# 간단 테스트
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # fund_params 예시값 그대로
    aum         = 1200.0
    bond_amount = 1040.0
    bond_weight = 1040.0 / 1200.0

    alloc = AssetAllocator(bond_amount, bond_weight, aum)

    print("=== 비중 입력 → 금액 ===")
    alloc.from_weights({"AAA": 0.50, "AA+": 0.30, "AA-": 0.20})
    alloc.summary()

    print()
    print("=== 금액 입력 → 비중 ===")
    alloc.from_amounts({"AAA": 500.0, "AA+": 300.0, "AA-": 240.0})
    alloc.summary()

    print()
    print("=== 소수점 드럽게 떨어지는 케이스 ===")
    alloc.from_weights({"AAA": 0.333, "AA+": 0.333, "AA-": 0.334})
    alloc.summary()
