"""
output/tables.py — 제안서용 수익률 테이블

Table 1: 만기 예상수익률 (롤링 전)
    행: 섹터(그룹) | REPO | 현금 | 합계
    열: 시나리오별 기여수익률 (NAV 대비)

Table 2: 롤링 + 금리변동 시나리오 매트릭스
"""
import pandas as pd
from dataclasses import dataclass
from core.repo_cost import calc_weighted_avg_rate
from config.constants import REPO_SPREAD


@dataclass
class AssetRow:
    name:     str
    ytm:      float | str   # % 또는 "기준금리+5bp"
    maturity: str
    weight:   float          # NAV 대비 (소수)
    is_repo:  bool = False


def build_asset_rows(
    groups:      list[dict],   # [{"name", "ytm", "maturity_str", "weight_nav"}]
    repo_weight: float,        # NAV 대비 (e.g. 2.0 = 200%)
    cd_rate:     float,        # CD 3M (%)
    cash_weight: float,        # NAV 대비 (e.g. 0.40)
) -> list[AssetRow]:
    """
    섹터 그룹들 + REPO + 현금 → AssetRow 리스트
    """
    rows = []
    for g in groups:
        rows.append(AssetRow(
            name     = g["name"],
            ytm      = g["ytm"],
            maturity = g["maturity_str"],
            weight   = g["weight_nav"],
        ))
    rows.append(AssetRow("REPO 매도", "기준금리+5bp", "1일", -repo_weight, is_repo=True))
    rows.append(AssetRow("현금성자산", cd_rate, "6M 내외", cash_weight))
    return rows


def build_ytm_table(
    asset_rows: list[AssetRow],
    scenarios:  dict,          # {"시나리오명": {date: 변동폭(소수)}}
    start_date,
    end_date,
    base_rate:  float,         # 소수
) -> pd.DataFrame:
    """
    NAV 대비 기여수익률 테이블.
    columns: 편입자산 | YTM | 만기 | 투자비중 | [시나리오별 기여수익률]
    마지막 행: 합계
    """
    repo_rates = {}
    for name, sc in scenarios.items():
        avg_base = calc_weighted_avg_rate(start_date, end_date, base_rate, sc)
        repo_rates[name] = (avg_base + REPO_SPREAD) * 100   # %

    rows, totals = [], {n: 0.0 for n in scenarios}

    for ar in asset_rows:
        row = {
            "편입자산": ar.name,
            "YTM":      f"{ar.ytm:.3f}%" if isinstance(ar.ytm, float) else ar.ytm,
            "만기":     ar.maturity,
            "투자비중": f"{ar.weight*100:.0f}%",
        }
        for sc_name in scenarios:
            if ar.is_repo:
                contrib = -repo_rates[sc_name] * abs(ar.weight)
            else:
                ytm = ar.ytm if isinstance(ar.ytm, float) else 0.0
                contrib = ytm * ar.weight
            row[sc_name]      = round(contrib, 4)
            totals[sc_name]  += contrib
        rows.append(row)

    total_row = {"편입자산": "합 계 (보수공제전)", "YTM": "", "만기": "", "투자비중": "100%"}
    for sc_name in scenarios:
        total_row[sc_name] = round(totals[sc_name], 4)
    rows.append(total_row)

    return pd.DataFrame(rows)


def build_rolling_matrix(
    base_total:   dict,     # {"시나리오명": 기본수익률(%)}
    rolldown:     float,    # 롤다운 수익률 (%)
    duration:     float,    # 잔존 듀레이션 (년)
    delta_y_list: list,     # 시중금리 변동폭 (%)
    dy_labels:    list,
) -> pd.DataFrame:
    """
    total = base_total + rolldown + (-duration × Δy)
    """
    rows = []
    for dy, label in zip(delta_y_list, dy_labels):
        row = {"시중금리 변동폭": label}
        for sc_name, base in base_total.items():
            row[sc_name] = round(base + rolldown + (-duration * dy), 4)
        rows.append(row)
    return pd.DataFrame(rows)