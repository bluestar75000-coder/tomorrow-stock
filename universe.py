"""
내일주식 - 종목 유니버스(시가총액 상위 N개) 조회 공통 모듈

여러 스크립트(screener, backtest 등)에서 공통으로 사용하는
"코스피/코스닥 시가총액 상위 N개 종목 리스트" 조회 기능을 모아둔 파일.
"""

import pandas as pd
import FinanceDataReader as fdr


def get_top_n_by_marketcap(market: str = "KOSPI", n: int = 200) -> pd.DataFrame:
    """시가총액 상위 N개 종목을 반환한다.

    FinanceDataReader 버전에 따라 시가총액 컬럼명이 'Marcap'이 아닐 수 있어서,
    없으면 거래대금(Amount) 기준으로 대신 정렬한다(대략적인 대형주 필터 효과).
    """
    listing = fdr.StockListing(market)

    if "Marcap" in listing.columns:
        sort_col = "Marcap"
    elif "MarketCap" in listing.columns:
        sort_col = "MarketCap"
    elif "Amount" in listing.columns:
        print(f"[안내] {market} 리스트에 시가총액 컬럼이 없어 거래대금(Amount) 기준으로 정렬합니다.")
        sort_col = "Amount"
    else:
        print(f"[경고] {market} 리스트에 정렬 기준 컬럼을 찾지 못해 원래 순서 상위 {n}개를 사용합니다.")
        return listing.head(n)

    listing = listing.dropna(subset=[sort_col])
    listing = listing.sort_values(by=sort_col, ascending=False)
    return listing.head(n).reset_index(drop=True)


def get_combined_universe(n_per_market: int = 200) -> pd.DataFrame:
    """코스피 + 코스닥 시가총액 상위 N개씩을 합쳐서 반환한다. 'Market' 컬럼 추가."""
    frames = []
    for market in ["KOSPI", "KOSDAQ"]:
        df = get_top_n_by_marketcap(market, n_per_market)
        df = df.copy()
        df["Market"] = market
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    # 단독 실행 시 확인용 출력
    combined = get_combined_universe(200)
    print(f"총 {len(combined)}개 종목 (코스피+코스닥 각 상위 200개)")
    print(combined[["Code", "Name", "Market"]].head(10))
