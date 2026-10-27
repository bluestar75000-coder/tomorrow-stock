"""
내일주식 - 시장지도(트리맵) 데이터 생성

두 가지 시장지도 데이터를 만든다.
1. 국내주식(코스피+코스닥) 시가총액 상위 10개
2. 시가총액 상위 200개 종목 중 외국인 보유비중(지분율) 상위 10개

박스 크기는 시가총액, 색상은 전일 대비 등락률로 표현한다 (핀비즈 방식).
외국인 보유비중은 pykrx(KRX 로그인 필요)에서 가져오며, 조회 실패 시
두 번째 지도는 비워두고 안내 문구만 남긴다 (첫 번째 지도는 정상 동작).

결과는 docs/market_map.json 으로 저장된다.
"""

import pandas as pd

TOP_N_MARKETCAP = 10
TOP_N_FOREIGN = 10
FOREIGN_UNIVERSE_SIZE = 200


def _find_foreign_ratio_column(df: pd.DataFrame):
    """pykrx 버전에 따라 컬럼명이 달라서 '지분율'이 들어간 컬럼을 찾는다 (한도 관련 컬럼은 제외)."""
    for col in df.columns:
        name = str(col)
        if "지분율" in name and "한도" not in name:
            return col
    return None


def get_foreign_ratio_map() -> dict:
    """{종목코드: 외국인 보유비중(%)} 딕셔너리. 조회 실패 시 빈 딕셔너리."""
    try:
        from pykrx import stock
    except Exception as e:
        print(f"[경고] pykrx 임포트 실패로 외국인 보유비중을 건너뜁니다: {e}")
        return {}

    result = {}
    for market in ["KOSPI", "KOSDAQ"]:
        df = None
        for days_back in range(0, 7):  # 휴장일 대비: 최근 영업일 탐색
            target = (pd.Timestamp.today() - pd.Timedelta(days=days_back)).strftime("%Y%m%d")
            try:
                tmp = stock.get_exhaustion_rates_of_foreign_investment_by_ticker(target, market=market)
                if tmp is not None and not tmp.empty:
                    df = tmp
                    break
            except Exception:
                continue

        if df is None:
            print(f"[경고] {market} 외국인 보유비중 조회 실패")
            continue

        col = _find_foreign_ratio_column(df)
        if col is None:
            print(f"[경고] {market} 외국인 지분율 컬럼을 찾지 못했습니다. 컬럼: {list(df.columns)}")
            continue

        for code, value in df[col].items():
            try:
                result[str(code)] = float(value)
            except (TypeError, ValueError):
                continue

    return result


def get_price_and_change(code: str):
    import FinanceDataReader as fdr
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)
    if len(df) < 2:
        return None, None
    today_close = float(df["Close"].iloc[-1])
    prev_close = float(df["Close"].iloc[-2])
    return today_close, (today_close - prev_close) / prev_close * 100


def _to_item(row, extra: dict = None):
    code = str(row["Code"])
    try:
        price, change_pct = get_price_and_change(code)
    except Exception as e:
        print(f"  [skip] {row['Name']}({code}): {e}")
        return None
    if price is None:
        return None
    item = {
        "code": code,
        "name": row["Name"],
        "market": row.get("Market", ""),
        "price": round(price),
        "change_pct": round(change_pct, 2),
        "marcap": float(row["Marcap"]),
    }
    if extra:
        item.update(extra)
    return item


def build_market_maps(universe: pd.DataFrame) -> dict:
    """universe: Code, Name, Market, Marcap 컬럼을 가진 코스피+코스닥 종목 리스트."""
    ranked = universe.dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False)
    ranked = ranked.drop_duplicates(subset=["Code"]).reset_index(drop=True)

    # 1) 시가총액 상위 10
    top_marcap = []
    for _, row in ranked.head(TOP_N_MARKETCAP).iterrows():
        item = _to_item(row)
        if item:
            top_marcap.append(item)

    # 2) 시총 상위 200 중 외국인 보유비중 상위 10
    top_foreign = []
    foreign_note = ""
    foreign_map = get_foreign_ratio_map()
    if not foreign_map:
        foreign_note = "외국인 보유비중 데이터를 가져오지 못했습니다 (KRX 로그인 정보 확인 필요)."
    else:
        pool = ranked.head(FOREIGN_UNIVERSE_SIZE).copy()
        pool["foreign_ratio"] = pool["Code"].astype(str).map(foreign_map)
        pool = pool.dropna(subset=["foreign_ratio"]).sort_values("foreign_ratio", ascending=False)
        for _, row in pool.head(TOP_N_FOREIGN).iterrows():
            item = _to_item(row, {"foreign_ratio": round(float(row["foreign_ratio"]), 2)})
            if item:
                top_foreign.append(item)
        if not top_foreign:
            foreign_note = "외국인 보유비중 상위 종목을 계산하지 못했습니다."

    return {
        "top_marcap": top_marcap,
        "top_foreign": top_foreign,
        "foreign_note": foreign_note,
        "foreign_universe_size": FOREIGN_UNIVERSE_SIZE,
    }
