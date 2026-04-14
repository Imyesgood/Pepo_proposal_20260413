"""
portfolio_allocator.py — 편입자산 선택 + 비중 배분

선택 규칙:
    1. 펀드 만기에 가장 근사한 만기 선택
    2. 해당 만기 yield 내림차순 정렬
    3. yield 동일하면 → 한 단계 긴 만기 yield 내림차순 (tiebreaker)

배분:
    - 비중 입력 → 금액 자동 계산
    - 금액 입력 → 비중 자동 계산
"""
import pandas as pd
from dataclasses import dataclass

RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-"]


# =============================================================================
# 편입자산 선택
# =============================================================================

def _rating_rank(r: str) -> int:
    try:
        return RATING_ORDER.index(r)
    except ValueError:
        return 999


def _nearest_maturity(available: list[float], target: float) -> float:
    return min(available, key=lambda m: abs(m - target))


def _next_longer_maturity(available: list[float], target_mat: float) -> float | None:
    longer = sorted([m for m in available if m > target_mat + 1e-9])
    return longer[0] if longer else None


def select_assets(
    i_bond:        pd.DataFrame,
    fund_maturity: float,
    rating_min:    str = "AA-",
    rating_max:    str = "AAA",
    top_n:         int = 20,
) -> pd.DataFrame:
    """
    편입 가능 자산 정렬 후 상위 top_n 반환.

    Returns:
        rank | issuer | category | rating | target_mat | yield_target | next_mat | yield_next
    """
    min_rank = _rating_rank(rating_max)
    max_rank = _rating_rank(rating_min)
    df = i_bond.copy()
    df["_rank"] = df["rating"].apply(_rating_rank)
    df = df[(df["_rank"] >= min_rank) & (df["_rank"] <= max_rank)].drop(columns="_rank")

    if df.empty:
        return pd.DataFrame()

    available_mats = sorted(df["maturity"].unique().tolist())
    target_mat     = _nearest_maturity(available_mats, fund_maturity)
    next_mat       = _next_longer_maturity(available_mats, target_mat)

    print(f"  펀드만기 {fund_maturity}Y → 타겟만기 {target_mat}Y", end="")
    print(f" / tiebreaker {next_mat}Y" if next_mat else " / tiebreaker 없음")

    target_df = (df[df["maturity"] == target_mat][["issuer", "category", "rating", "yield"]]
                 .rename(columns={"yield": "yield_target"}))

    if next_mat is not None:
        next_df = (df[df["maturity"] == next_mat][["issuer", "yield"]]
                   .rename(columns={"yield": "yield_next"}))
        merged = target_df.merge(next_df, on="issuer", how="left")
    else:
        merged = target_df.copy()
        merged["yield_next"] = float("nan")

    result = (merged
              .sort_values(["yield_target", "yield_next"], ascending=[False, False])
              .head(top_n)
              .reset_index(drop=True))
    result.index += 1
    result.index.name = "rank"
    result.insert(3, "target_mat", target_mat)
    result.insert(5, "next_mat",   next_mat)
    return result


# =============================================================================
# 비중 배분
# =============================================================================

@dataclass
class BondAllocation:
    name:   str
    weight: float   # AUM 대비 비중 (소수)
    amount: float   # 금액 (억원)

    def __str__(self):
        return f"{self.name:20s}  {self.weight*100:7.3f}%  {self.amount:10.3f}억"


class AssetAllocator:
    """비중 ↔ 금액 양방향 변환 + 합계 검증"""

    def __init__(self, bond_amount: float, bond_weight: float, aum: float):
        self.bond_amount = bond_amount
        self.bond_weight = bond_weight
        self.aum         = aum
        self.allocations: list[BondAllocation] = []

    def from_weights(self, items: dict[str, float]) -> list[BondAllocation]:
        """채권 내 비중(합계=1.0) 입력 → 금액 계산"""
        total = sum(items.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"비중 합계 {total*100:.3f}% ≠ 100%")
        self.allocations = [
            BondAllocation(name, w * self.bond_weight, w * self.bond_amount)
            for name, w in items.items()
        ]
        return self.allocations

    def from_amounts(self, items: dict[str, float]) -> list[BondAllocation]:
        """금액(억원) 입력 → 비중 계산"""
        total = sum(items.values())
        if abs(total - self.bond_amount) > 0.001:
            raise ValueError(f"금액 합계 {total:.3f}억 ≠ 채권투자가능액 {self.bond_amount:.3f}억")
        self.allocations = [
            BondAllocation(name, amount / self.aum, amount)
            for name, amount in items.items()
        ]
        return self.allocations

    def summary(self):
        print(f"{'종목':<20}  {'AUM비중':>8}  {'금액':>12}")
        print("-" * 46)
        for a in self.allocations:
            print(a)
        print("-" * 46)
        print(f"{'합계':<20}  {sum(a.weight for a in self.allocations)*100:7.3f}%  "
              f"{sum(a.amount for a in self.allocations):10.3f}억")

    def to_dict(self) -> list[dict]:
        return [{"name": a.name, "weight": a.weight, "amount": a.amount}
                for a in self.allocations]


# =============================================================================
# 실행 테스트
# =============================================================================

if __name__ == "__main__":
    import sys, warnings
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    warnings.filterwarnings('ignore')
    from data.loader import load_excel

    _here = Path(__file__).parent
    _root = _here.parent if (_here.parent / 'data' / 'raw' / 'raw.xlsx').exists() else _here
    data   = load_excel(_root / 'data' / 'raw' / 'raw.xlsx')
    i_bond = data['I_BOND']

    print("\n" + "="*60)
    print("[ 편입자산 선택 | 펀드만기 1.5Y | AA-~AAA | 상위 15 ]")
    print("="*60)
    selected = select_assets(i_bond, fund_maturity=1.5, rating_min="AA-", rating_max="AAA", top_n=15)
    print(selected[["issuer","rating","yield_target","yield_next"]].to_string())

    print("\n" + "="*60)
    print("[ 비중 배분 예시 | AUM=1200억, 채권=1040억 ]")
    print("="*60)
    alloc = AssetAllocator(bond_amount=1040, bond_weight=1040/1200, aum=1200)
    alloc.from_weights({"AAA은행채": 0.40, "AAA공사채": 0.40, "AA-기타금융채": 0.20})
    alloc.summary()
