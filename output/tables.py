"""
output/tables.py — 제안서용 수익률 테이블 생성

Table 1: 만기 예상수익률 (롤링 전)
    - 자산별 YTM × NAV대비 비중 → 기여수익률
    - 금통위 시나리오별 REPO 비용 변동

Table 2: 롤링 + 금리변동 시나리오 매트릭스
    - 행: 시중금리 변동폭 (Δy)
    - 열: 금통위 시나리오
    - 값: 포트폴리오 수익률 (롤링 포함)

※ 비중 기준: NAV (설정액) 대비
    NAV 100% 기준으로 계산 → 레버리지 펀드 수익률 직관적으로 이해 가능
    기여수익률 합계 = NAV 대비 펀드 수익률
"""

import pandas as pd
from dataclasses import dataclass
from core.repo_cost import calc_weighted_avg_rate
from config.constants import REPO_SPREAD


# =============================================================================
# 자산 구성 정의
# =============================================================================

@dataclass
class AssetRow:
    """제안서 테이블 한 행"""
    group:    str     # 구분 (기본 포트폴리오 / 레버리지 포트폴리오)
    name:     str     # 편입자산명
    ytm:      float | str   # YTM (%) 또는 "기준금리+5bp"
    maturity: str     # 만기 표시
    weight:   float   # NAV 대비 비중 (소수, e.g. 1.0 = 100%)
    is_repo:  bool = False   # REPO 여부 (시나리오마다 다름)


def build_asset_rows(
    ytm_aaa:      float,   # 은행채+공사채(AAA) YTM (%)
    ytm_other:    float,   # 기타금융채(AA-) YTM (%)
    cd_rate:      float,   # 현금성자산 CD금리 (%)
    leverage:     float,   # 레버리지 비율 (소수, e.g. 2.0)
    cash_ratio:   float,   # 현금 비율 (레버리지 대비, e.g. 0.20)
    weight_other: float,   # 기타금융채 NAV대비 비중 (소수, e.g. 1.0)
    maturity_str: str = "1.5Y",
) -> list[AssetRow]:
    """
    자산 행 자동 생성.

    NAV대비 비중 계산:
        cash_weight     = leverage × cash_ratio
        other_weight    = weight_other  (사용자 입력)
        aaa_lev_weight  = leverage - cash_weight - other_weight (나머지)
        repo_weight     = -leverage
    """
    cash_w    = leverage * cash_ratio
    aaa_lev_w = leverage - cash_w - weight_other

    return [
        AssetRow("기본\n포트폴리오", "은행채 및 공사채(AAA)",
                 ytm_aaa, maturity_str, 1.0),
        AssetRow("레버리지\n포트폴리오", "REPO 매도",
                 "기준금리+5bp", "1일", -leverage, is_repo=True),
        AssetRow("레버리지\n포트폴리오", "은행채 및 공사채(AAA)",
                 ytm_aaa, maturity_str, aaa_lev_w),
        AssetRow("레버리지\n포트폴리오", f"기타금융채 등(AA- 이상)",
                 ytm_other, maturity_str, weight_other),
        AssetRow("레버리지\n포트폴리오", "현금성자산",
                 cd_rate, "6M 내외", cash_w),
    ]


# =============================================================================
# 시나리오 정의
# =============================================================================

def build_custom_scenarios(
    bok_dates: list,
    selections: dict,   # {date: bp_change (int, e.g. -25, 0, 25)}
    scenario_configs: list[dict],
    # [{'name': str, 'changes': {date: bp}}]
) -> dict:
    """
    사용자가 구성한 시나리오 dict 반환.
    scenario_configs: [{name, changes: {date: bp_int}}]
    """
    result = {}
    for cfg in scenario_configs:
        result[cfg['name']] = {d: bp/10000 for d, bp in cfg['changes'].items() if bp != 0}
    return result


# =============================================================================
# Table 1: 만기 예상수익률
# =============================================================================

def build_ytm_table(
    asset_rows:  list[AssetRow],
    scenarios:   dict,          # {"시나리오명": {date: 변동폭(소수)}}
    start_date,
    end_date,
    base_rate:   float,         # 현재 기준금리 (소수)
) -> pd.DataFrame:
    """
    만기 예상수익률 테이블 (롤링 전).

    Columns: 편입자산 | YTM | 만기 | 투자비중 | [시나리오별 기여수익률...]
    마지막 행: 합계
    """
    # 시나리오별 REPO 금리 계산
    repo_rates = {}
    for name, sc in scenarios.items():
        avg_base   = calc_weighted_avg_rate(start_date, end_date, base_rate, sc)
        repo_rates[name] = (avg_base + REPO_SPREAD) * 100   # %

    rows = []
    totals = {name: 0.0 for name in scenarios}

    for ar in asset_rows:
        row = {
            "구분":    ar.group,
            "편입자산": ar.name,
            "YTM":    f"{ar.ytm:.3f}%" if isinstance(ar.ytm, float) else ar.ytm,
            "만기":    ar.maturity,
            "투자비중": f"{ar.weight*100:.0f}%",
        }
        for sc_name in scenarios:
            if ar.is_repo:
                contrib = -repo_rates[sc_name] * abs(ar.weight)
            else:
                ytm = ar.ytm if isinstance(ar.ytm, float) else 0.0
                contrib = ytm * ar.weight
            row[sc_name] = round(contrib, 4)
            totals[sc_name] += contrib
        rows.append(row)

    # 합계 행
    total_row = {"구분": "", "편입자산": "합 계 (보수공제전)",
                 "YTM": "", "만기": "", "투자비중": "100%"}
    for sc_name in scenarios:
        total_row[sc_name] = round(totals[sc_name], 4)
    rows.append(total_row)

    return pd.DataFrame(rows)


# =============================================================================
# Table 2: 롤링 + 금리변동 시나리오 매트릭스
# =============================================================================

def build_rolling_matrix(
    base_total:     dict,    # {"시나리오명": 기본수익률(%)} ← Table1 합계
    rolldown:       float,   # 롤다운 수익률 (%)
    duration:       float,   # 잔존 듀레이션 (년) = target_mat - hold_years
    delta_y_list:   list,    # 시중금리 변동폭 (%, e.g. [-0.25, 0, 0.25])
    dy_labels:      list,    # 행 라벨 e.g. ["25bp 하락", "0bp", "25bp 상승"]
) -> pd.DataFrame:
    """
    롤링 효과 반영 + 금리변동 시나리오 매트릭스.

    공식:
        total = base_total(시나리오)
              + rolldown
              + (-duration × Δy)
    """
    rows = []
    for dy, label in zip(delta_y_list, dy_labels):
        row = {"시중금리 변동폭": label}
        rate_effect = -duration * dy   # %
        for sc_name, base in base_total.items():
            row[sc_name] = round(base + rolldown + rate_effect, 4)
        rows.append(row)
    return pd.DataFrame(rows)