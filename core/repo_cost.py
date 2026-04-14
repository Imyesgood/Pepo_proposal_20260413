from datetime import date, timedelta
from config.constants import BOK_DATES, REPO_SPREAD


def get_rate_schedule(
    start_date: date,
    end_date: date,
    base_rate: float,
    scenarios: dict[date, float],
) -> list[tuple[date, date, float]]:
    """
    펀드 운용 기간 내 금통위 일정과 시나리오를 반영해
    구간별 (시작일, 종료일, 적용금리) 리스트 반환.

    Args:
        start_date  : 펀드 개시일 (포함)
        end_date    : 펀드 만기일 (포함)
        base_rate   : 개시일 기준 현재 기준금리 (e.g. 0.0350)
        scenarios   : {금통위날짜: 변동폭} (e.g. {date(2026,8,27): -0.0025})
                      변동 없으면 빈 dict

    Returns:
        [(구간시작, 구간종료, 적용금리), ...]
    """
    # 운용 기간 내 금통위 날짜만 필터 (당일 포함, 다음날부터 반영이므로 +1)
    breakpoints = sorted([
        bok_date + timedelta(days=1)
        for bok_date in BOK_DATES
        if start_date < bok_date + timedelta(days=1) <= end_date
        and bok_date in scenarios
    ])

    # 구간 경계 = 개시일 + breakpoints + 만기일 다음날
    boundaries = [start_date] + breakpoints + [end_date + timedelta(days=1)]

    schedule = []
    current_rate = base_rate

    for i in range(len(boundaries) - 1):
        seg_start = boundaries[i]
        seg_end   = boundaries[i + 1] - timedelta(days=1)

        schedule.append((seg_start, seg_end, current_rate))

        # 다음 구간 시작 전에 금리 업데이트
        if i + 1 < len(boundaries) - 1:
            bok_date = boundaries[i + 1] - timedelta(days=1)  # 역산
            current_rate += scenarios.get(bok_date, 0.0)

    return schedule


def calc_weighted_avg_rate(
    start_date: date,
    end_date: date,
    base_rate: float,
    scenarios: dict[date, float],
) -> float:
    """
    운용 기간 전체의 일수 가중평균 기준금리 반환.
    """
    schedule = get_rate_schedule(start_date, end_date, base_rate, scenarios)

    total_days = (end_date - start_date).days + 1
    weighted_sum = sum(
        ((seg_end - seg_start).days + 1) * rate
        for seg_start, seg_end, rate in schedule
    )

    return weighted_sum / total_days


def calc_repo_cost(
    start_date: date,
    end_date: date,
    base_rate: float,
    scenarios: dict[date, float],
    repo_leverage: float,
) -> dict:
    """
    레포 조달비용 최종 계산.

    Args:
        repo_leverage : 레포 차입 비율 (e.g. 2.0 = 200%)

    Returns:
        {
            "weighted_avg_base_rate" : 가중평균 기준금리,
            "repo_rate"              : 기준금리 + 스프레드,
            "repo_leverage"          : 레버리지 비율,
            "repo_cost"              : 레포 조달비용 (레버리지 반영),
            "schedule"               : 구간별 상세 내역,
        }
    """
    avg_base = calc_weighted_avg_rate(start_date, end_date, base_rate, scenarios)
    repo_rate = avg_base + REPO_SPREAD
    repo_cost = repo_rate * repo_leverage

    schedule = get_rate_schedule(start_date, end_date, base_rate, scenarios)

    return {
        "weighted_avg_base_rate": round(avg_base, 6),
        "repo_rate":              round(repo_rate, 6),
        "repo_leverage":          repo_leverage,
        "repo_cost":              round(repo_cost, 6),
        "schedule": [
            {
                "from": seg_start.isoformat(),
                "to":   seg_end.isoformat(),
                "days": (seg_end - seg_start).days + 1,
                "rate": round(rate, 6),
            }
            for seg_start, seg_end, rate in schedule
        ],
    }


# ---------------------------------------------------------------------------
# 간단 테스트
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from pprint import pprint

    result = calc_repo_cost(
        start_date    = date(2026, 4, 30),
        end_date      = date(2027, 4, 30),
        base_rate     = 0.0250,
        scenarios     = {
            date(2026,  8, 27): -0.0025,   # -25bp
            date(2026, 10, 22):  0.0000,   # 동결
            date(2026, 11, 26): -0.0025,   # -25bp
        },
        repo_leverage = 2.0,
    )
    pprint(result)
