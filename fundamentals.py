"""
내일주식 - 재무 데이터(PER 등) 조회 공통 모듈

pykrx를 이용해 시장 전체 종목의 PER/PBR 등 재무 데이터를 가져온다.
장이 쉬는 날(주말/공휴일)에 실행될 경우를 대비해, 최근 영업일을 최대 5일
역순으로 탐색하며 데이터가 있는 날짜를 찾는다.
"""

import pandas as pd


def get_latest_fundamental(market: str = "KOSPI") -> pd.DataFrame:
    """가장 최근 영업일 기준 종목별 PER/PBR/EPS 등을 반환한다.
    인덱스는 종목코드(Code)이며, 컬럼: BPS, PER, PBR, EPS, DIV, DPS
    """
    from pykrx import stock

    for days_back in range(0, 6):
        target_date = (pd.Timestamp.today() - pd.Timedelta(days=days_back)).strftime("%Y%m%d")
        try:
            df = stock.get_market_fundamental_by_ticker(target_date, market=market)
            if df is not None and not df.empty:
                df = df.copy()
                df.index.name = "Code"
                return df
        except Exception:
            continue

    print(f"[경고] {market} 재무 데이터를 가져오지 못했습니다. 빈 데이터로 진행합니다.")
    return pd.DataFrame(columns=["BPS", "PER", "PBR", "EPS", "DIV", "DPS"])
