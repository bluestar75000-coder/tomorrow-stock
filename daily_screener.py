"""
내일주식 - 일일 스크리닝 서비스 (GitHub Actions 자동 실행용, v0.4)

두 가지 스크리닝을 함께 수행한다.
1. 모멘텀 스크리닝: 거래량 급증 + 당일 급등 종목
2. 밸류 스크리닝: 최근 거래량이 늘었지만 PER이 낮은(저평가 가능성) 종목

대상: 코스피/코스닥 시가총액 상위 200개씩, 총 최대 400개 종목

결과는 docs/results.json에 저장되어 GitHub Pages 대시보드가 읽는다.

실행 (로컬/Actions 공통):
    python daily_screener.py
"""

import json
import time
import os
from dataclasses import dataclass, asdict
from datetime import datetime

import pandas as pd

from universe import get_combined_universe
from fundamentals import get_latest_fundamental


# ----------------------------
# 설정값
# ----------------------------
N_PER_MARKET = 200
LOOKBACK_DAYS = 60

# 모멘텀 스크리닝 조건
VOLUME_SURGE_RATIO = 2.0
PRICE_CHANGE_MIN = 3.0

# 밸류 스크리닝 조건 (거래량 증가 + 저PER)
VALUE_VOLUME_RATIO_MIN = 1.5   # 최근 거래량이 자기 20일 평균 대비 이 배수 이상이면 "거래량 증가"로 간주
PER_MAX_THRESHOLD = 10.0       # 이 값 이하의 PER만 "저PER"로 간주 (0 이하 = 적자기업은 제외)

OUTPUT_DIR = "docs"
OUTPUT_JSON = os.path.join(OUTPUT_DIR, "results.json")


@dataclass
class MomentumResult:
    code: str
    name: str
    market: str
    volume_ratio: float
    price_change_pct: float
    score: float


@dataclass
class ValueResult:
    code: str
    name: str
    market: str
    volume_ratio: float
    per: float
    pbr: float
    score: float


def get_daily_price(code: str, days: int = LOOKBACK_DAYS):
    import FinanceDataReader as fdr
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=days * 2)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)
    return df.tail(days)


def analyze_stock(code: str, name: str, market: str, fundamental_row):
    """한 종목에 대해 시세를 한 번만 조회해서 모멘텀/밸류 두 스크리닝을 동시에 계산한다."""
    momentum = None
    value = None

    try:
        df = get_daily_price(code)
        if len(df) < 21:
            return None, None

        avg_volume_20 = df["Volume"].iloc[-21:-1].mean()
        today_volume = df["Volume"].iloc[-1]
        volume_ratio = today_volume / avg_volume_20 if avg_volume_20 > 0 else 0

        prev_close = df["Close"].iloc[-2]
        today_close = df["Close"].iloc[-1]
        price_change_pct = (today_close - prev_close) / prev_close * 100

        # 1) 모멘텀 조건
        if volume_ratio >= VOLUME_SURGE_RATIO and price_change_pct >= PRICE_CHANGE_MIN:
            m_score = volume_ratio * 1.0 + price_change_pct * 0.5
            momentum = MomentumResult(
                code, name, market,
                round(volume_ratio, 2), round(price_change_pct, 2), round(m_score, 2)
            )

        # 2) 밸류 조건 (거래량 증가 + 저PER)
        if fundamental_row is not None:
            per = fundamental_row.get("PER", 0)
            pbr = fundamental_row.get("PBR", 0)
            if per and per > 0 and per <= PER_MAX_THRESHOLD and volume_ratio >= VALUE_VOLUME_RATIO_MIN:
                v_score = round(volume_ratio * (10 / per), 2)
                value = ValueResult(
                    code, name, market,
                    round(volume_ratio, 2), round(per, 2), round(pbr, 2), v_score
                )

    except Exception as e:
        print(f"  [skip] {code} {name}: {e}")

    return momentum, value


def main():
    print(f"코스피/코스닥 시가총액 상위 {N_PER_MARKET}개씩 유니버스 조회 중...")
    universe = get_combined_universe(N_PER_MARKET)
    print(f"총 {len(universe)}개 종목 스캔 시작...\n")

    print("재무 데이터(PER/PBR) 조회 중...")
    fundamentals = {
        "KOSPI": get_latest_fundamental("KOSPI"),
        "KOSDAQ": get_latest_fundamental("KOSDAQ"),
    }

    momentum_results = []
    value_results = []

    for i, row in universe.iterrows():
        code, name, market = row["Code"], row["Name"], row["Market"]

        fundamental_row = None
        fdf = fundamentals.get(market)
        if fdf is not None and code in fdf.index:
            fundamental_row = fdf.loc[code]

        m, v = analyze_stock(code, name, market, fundamental_row)
        if m:
            momentum_results.append(m)
        if v:
            value_results.append(v)

        if (i + 1) % 40 == 0:
            print(f"  진행: {i + 1}/{len(universe)}")

        time.sleep(0.05)

    momentum_results.sort(key=lambda r: r.score, reverse=True)
    value_results.sort(key=lambda r: r.score, reverse=True)

    print(f"\n=== 모멘텀 스크리닝 결과: {len(momentum_results)}건 ===")
    for r in momentum_results:
        print(f"[{r.market}] {r.name}({r.code}) | 거래량 {r.volume_ratio}배 | 등락률 {r.price_change_pct}% | 점수 {r.score}")

    print(f"\n=== 밸류(저PER+거래량증가) 스크리닝 결과: {len(value_results)}건 ===")
    for r in value_results:
        print(f"[{r.market}] {r.name}({r.code}) | 거래량 {r.volume_ratio}배 | PER {r.per} | PBR {r.pbr} | 점수 {r.score}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scanned_count": len(universe),
        "momentum_conditions": {
            "volume_surge_ratio": VOLUME_SURGE_RATIO,
            "price_change_min_pct": PRICE_CHANGE_MIN,
        },
        "value_conditions": {
            "volume_ratio_min": VALUE_VOLUME_RATIO_MIN,
            "per_max": PER_MAX_THRESHOLD,
        },
        "momentum_results": [asdict(r) for r in momentum_results],
        "value_results": [asdict(r) for r in value_results],
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n결과를 {OUTPUT_JSON} 에 저장했습니다.")

    today_str = datetime.today().strftime("%Y%m%d")
    os.makedirs("history", exist_ok=True)
    if momentum_results:
        pd.DataFrame([asdict(r) for r in momentum_results]).to_csv(
            f"history/momentum_{today_str}.csv", index=False, encoding="utf-8-sig"
        )
    if value_results:
        pd.DataFrame([asdict(r) for r in value_results]).to_csv(
            f"history/value_{today_str}.csv", index=False, encoding="utf-8-sig"
        )


if __name__ == "__main__":
    main()
