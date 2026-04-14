"""
data/loader.py
==============
raw.xlsx → 파싱된 DataFrame + CSV 저장.

시트명 규칙:
    SECTOR_XXX    → S-type: 날짜별 섹터의 만기별 수익률 시계열
    ISSUER(BOND)  → I-type: 발행자별 현재 만기별 수익률
    ISSUER(CD)    → CD 금리
    기준금리       → 한국은행 기준금리 시계열
    PLOT_*        → 무시 (사용자 수작업 전처리)

저장 파일명 (processed/):
    SECTOR_AAA은행채    → S_은행채(AAA).csv     [date, maturity, yield]
    ISSUER(BOND)        → I_BOND.csv            [issuer, category, rating, maturity, yield]
    ISSUER(CD)          → I_CD.csv              [issuer, maturity, yield]
    기준금리             → 기준금리.csv           [date, rate]
"""

import re
import pandas as pd
from datetime import datetime
from pathlib import Path

PROCESSED_DIR = Path(__file__).parent / "processed"


# =============================================================================
# 만기 문자열 → 년(float)
# =============================================================================

def _mat_to_years(s) -> float | None:
    """
    '3M'→0.25, '9M'→0.75, '1Y'→1.0, '1Y6M'→1.5, '2Y6M'→2.5, '10Y'→10.0
    """
    s = str(s).strip().upper()
    m = re.match(r'^(\d+)Y(\d+)M$', s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 12
    m = re.match(r'^(\d+\.?\d*)Y$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^(\d+\.?\d*)M$', s)
    if m:
        return round(float(m.group(1)) / 12, 6)
    return None


MATURITY_LABELS = {
    round(v, 6): k
    for k, v in {
        '3M': 0.25, '6M': 0.5, '9M': 0.75,
        '1Y': 1.0, '1Y6M': 1.5, '2Y': 2.0, '2Y6M': 2.5,
        '3Y': 3.0, '4Y': 4.0, '5Y': 5.0, '7Y': 7.0,
        '10Y': 10.0, '15Y': 15.0, '20Y': 20.0, '30Y': 30.0,
    }.items()
}


# =============================================================================
# 헤더 행 자동 감지
# =============================================================================

def _find_header_row(raw: pd.DataFrame, keyword: str) -> int:
    """keyword가 처음 등장하는 행 인덱스 반환"""
    for i, row in raw.iterrows():
        if keyword in row.values:
            return i
    raise ValueError(f"헤더 키워드 '{keyword}' 없음")


# =============================================================================
# 시트명 변환
# =============================================================================

def sector_sheet_to_label(sheet_name: str) -> str:
    """'SECTOR_AAA은행채' → '은행채(AAA)'"""
    body = sheet_name.removeprefix("SECTOR_")
    m = re.match(r'^([A-Z]+[+\-]?)([가-힣]+)$', body)
    if not m:
        raise ValueError(f"시트명 파싱 불가: {sheet_name!r}")
    return f"{m.group(2)}({m.group(1)})"


def sheet_to_key(sheet_name: str) -> str | None:
    if sheet_name.startswith("SECTOR_"):
        return f"S_{sector_sheet_to_label(sheet_name)}"
    return {"ISSUER(BOND)": "I_BOND", "ISSUER(CD)": "I_CD", "기준금리": "기준금리"}.get(sheet_name)


# =============================================================================
# S-type 파싱
# =============================================================================

def _parse_sector(raw: pd.DataFrame) -> pd.DataFrame:
    """SECTOR_XXX → date | maturity | yield"""
    header_idx    = _find_header_row(raw, '날짜')
    header        = raw.iloc[header_idx]
    data          = raw.iloc[header_idx + 1:].copy()
    date_col_idxs = [i for i, v in enumerate(header) if v == '날짜']

    is_date = data.iloc[:, date_col_idxs[0]].apply(lambda v: isinstance(v, datetime))
    data    = data[is_date]

    blocks = []
    for idx, dcol in enumerate(date_col_idxs):
        next_dcol  = date_col_idxs[idx + 1] if idx + 1 < len(date_col_idxs) else raw.shape[1]
        mat_idxs   = list(range(dcol + 1, next_dcol))
        maturities = [header.iloc[c] for c in mat_idxs]
        block      = data.iloc[:, [dcol] + mat_idxs].copy()
        block.columns = ['date'] + maturities
        blocks.append(block.melt(id_vars='date', var_name='maturity', value_name='yield'))

    result = pd.concat(blocks, ignore_index=True)
    result['date']     = pd.to_datetime(result['date']).dt.normalize()
    result['maturity'] = result['maturity'].apply(_mat_to_years)
    result['yield']    = pd.to_numeric(result['yield'], errors='coerce')
    return (result.dropna(subset=['maturity', 'yield'])
                  .sort_values(['date', 'maturity'])
                  .reset_index(drop=True))


# =============================================================================
# I-type 파싱: ISSUER(BOND)
# =============================================================================

def _extract_rating(category: str) -> str:
    s = str(category)
    if '정부보증' in s:
        return 'AAA'
    m = re.search(r'(AAA|AA[+\-]?|A[+\-]?|BBB[+\-]?)', s)
    return m.group(1) if m else ''


def _parse_issuer_bond(raw: pd.DataFrame) -> pd.DataFrame:
    """ISSUER(BOND) → issuer | category | rating | maturity | yield"""
    header_idx = _find_header_row(raw, '회사코드')
    df = raw.iloc[header_idx:].copy()
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)

    # '분류', '분류 ▼' 등 컬럼명 무관하게 처리
    분류_col = next((c for c in df.columns if '분류' in str(c)), list(df.columns)[3])
    df = df.rename(columns={분류_col: '분류'})

    mat_cols = [c for c in df.columns if _mat_to_years(c) is not None]

    long = (df[['회사명', '분류'] + mat_cols]
            .rename(columns={'회사명': 'issuer', '분류': 'category'})
            .copy())
    long['rating'] = long['category'].apply(_extract_rating)
    long = long.melt(id_vars=['issuer', 'category', 'rating'],
                     value_vars=mat_cols, var_name='_mat', value_name='yield')
    long['maturity'] = long['_mat'].apply(_mat_to_years)
    long['yield']    = pd.to_numeric(long['yield'], errors='coerce')
    return (long.drop(columns='_mat')
               [['issuer', 'category', 'rating', 'maturity', 'yield']]
               .dropna(subset=['yield'])
               .reset_index(drop=True))


# =============================================================================
# I-type 파싱: ISSUER(CD)
# =============================================================================

def _parse_issuer_cd(raw: pd.DataFrame) -> pd.DataFrame:
    """ISSUER(CD) → issuer | maturity | yield"""
    header_idx = _find_header_row(raw, '기관코드')
    df = raw.iloc[header_idx:].copy()
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)

    mat_cols = [c for c in df.columns if _mat_to_years(c) is not None]
    long = (df[['회사명'] + mat_cols]
            .rename(columns={'회사명': 'issuer'})
            .melt(id_vars=['issuer'], value_vars=mat_cols, var_name='_mat', value_name='yield'))
    long['maturity'] = long['_mat'].apply(_mat_to_years)
    long['yield']    = pd.to_numeric(long['yield'], errors='coerce')
    return (long.drop(columns='_mat')
               [['issuer', 'maturity', 'yield']]
               .dropna(subset=['yield'])
               .reset_index(drop=True))


# =============================================================================
# 기준금리
# =============================================================================

def _parse_base_rate(raw: pd.DataFrame) -> pd.DataFrame:
    """기준금리 → date | rate (소수)"""
    header_idx = _find_header_row(raw, '일자')
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = ['date', 'rate']
    df['date'] = pd.to_datetime(df['date'], errors='coerce', format='mixed').dt.normalize()
    df['rate'] = pd.to_numeric(df['rate'], errors='coerce') / 100
    return (df.dropna().sort_values('date', ascending=False).reset_index(drop=True))


# =============================================================================
# 메인
# =============================================================================

_PARSERS = {
    'SECTOR':       _parse_sector,
    'ISSUER(BOND)': _parse_issuer_bond,
    'ISSUER(CD)':   _parse_issuer_cd,
    '기준금리':      _parse_base_rate,
}


def load_excel(path: str | Path) -> dict[str, pd.DataFrame]:
    xl = pd.ExcelFile(path)
    result: dict[str, pd.DataFrame] = {}
    for sheet in xl.sheet_names:
        key = sheet_to_key(sheet)
        if key is None:
            continue
        raw    = xl.parse(sheet, header=None)
        parser = _PARSERS['SECTOR'] if sheet.startswith('SECTOR_') else _PARSERS.get(sheet)
        if parser is None:
            continue
        try:
            result[key] = parser(raw)
            print(f"  ✓ {sheet:30s} → {key:25s} ({len(result[key]):,} rows)")
        except Exception as e:
            print(f"  ✗ {sheet}: {e}")
    return result


def save_processed(data: dict[str, pd.DataFrame], out_dir: Path = PROCESSED_DIR) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for key, df in data.items():
        df.to_csv(out_dir / f"{key}.csv", index=False, encoding='utf-8-sig')
        print(f"  저장: {key}.csv ({len(df):,} rows)")


def load_processed(key: str, base_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    df = pd.read_csv(Path(base_dir) / f"{key}.csv", encoding='utf-8-sig')
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
    return df


if __name__ == "__main__":
    import sys
    _default = Path(__file__).parent.parent / "data" / "raw" / "raw.xlsx"
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default
    print(f"\n[ 로딩: {path} ]")
    data = load_excel(path)
    print(f"\n[ 저장: processed/ ]")
    save_processed(data)
