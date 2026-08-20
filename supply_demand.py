"""
내일주식 - 수급(기관/외국인 순매수) 데이터 조회 모듈

"급등 전조" 판단에 사용할 수급 개선 여부를 계산한다.
최근 N일과 그 이전 N일의 기관+외국인 순매수거래대금을 비교해서
"최근 들어 매수세가 강해지고 있는지"를 종목별로 계산한다.

주의:
- pykrx의 get_market_net_purchases_of_equity_by_ticker 함수를 사용하는데,
  버전에 따라 반환 컬럼명이 다를 수 있어 여러 후보 컬럼명을 시도한다.
- 시장 전체를 한 번에 조회하는 방식이라(종목별 개별 호출 아님) 400종목이어도
  호출 횟수는 시장당 4회(최근/이전 x 기관/외국인)로 적다.
"""

import pandas as pd


def _pick_value_column(df: pd.DataFrame) -> str:
    for col in ["순매수거래대금", "순매수 거래대금"]:
        if col in df.columns:
            return col
    # 컬럼명을 못 찾으면 마지막 컬럼을 사용 (pykrx 표는 보통 순매수거래대금이 마지막 부근)
    return df.columns[-1]


def get_supply_demand_improvement_map(market: str = "KOSPI", recent_days: int = 5, prior_days: int = 5) -> dict:
    """{종목코드: 순매수 개선폭(억원)} 딕셔너리를 반환한다.
    양수면 최근 매수세가 이전보다 강해졌다는 뜻. 조회 실패 시 빈 딕셔너리 반환(중립 처리).
    """
    try:
        from pykrx import stock
    except Exception as e:
        print(f"[경고] pykrx 임포트 실패로 수급 데이터를 건너뜁니다: {e}")
        return {}

    today = pd.Timestamp.today()
    recent_start = today - pd.Timedelta(days=recent_days * 2)
    prior_start = today - pd.Timedelta(days=(recent_days + prior_days) * 2)

    fmt = lambda d: d.strftime("%Y%m%d")

    try:
        recent_inst = stock.get_market_net_purchases_of_equity_by_ticker(
            fmt(recent_start), fmt(today), market, "기관합계"
        )
        recent_foreign = stock.get_market_net_purchases_of_equity_by_ticker(
            fmt(recent_start), fmt(today), market, "외국인"
        )
        prior_inst = stock.get_market_net_purchases_of_equity_by_ticker(
            fmt(prior_start), fmt(recent_start), market, "기관합계"
        )
        prior_foreign = stock.get_market_net_purchases_of_equity_by_ticker(
            fmt(prior_start), fmt(recent_start), market, "외국인"
        )
    except Exception as e:
        print(f"[경고] {market} 수급 데이터 조회 실패, 중립(0)으로 처리합니다: {e}")
        return {}

    try:
        r_col = _pick_value_column(recent_inst)
        p_col = _pick_value_column(prior_inst)

        recent_combined = recent_inst[r_col].add(recent_foreign[_pick_value_column(recent_foreign)], fill_value=0)
        prior_combined = prior_inst[p_col].add(prior_foreign[_pick_value_column(prior_foreign)], fill_value=0)

        diff = (recent_combined - prior_combined) / 1e8  # 억원 단위로 변환
        return diff.to_dict()
    except Exception as e:
        print(f"[경고] {market} 수급 데이터 계산 실패, 중립(0)으로 처리합니다: {e}")
        return {}
