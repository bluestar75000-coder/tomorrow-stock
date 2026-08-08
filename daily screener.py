"""
내일주식 - 일일 스크리닝 서비스 (GitHub Actions 자동 실행용, v0.3)

기존 daily_screener.py와 로직은 동일하지만, 결과를 GitHub Pages 대시보드가
읽을 수 있도록 docs/results.json 형태로도 저장한다.

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


# ----------------------------
# 설정값
# ----------------------------
VOLUME_SURGE_RATIO = 2.0
PRICE_CHANGE_MIN = 3.0
LOOKBACK_DAYS = 60
N_PER_MARKET = 100
OUTPUT_DIR = "docs"
OUTPUT_JSON = os.path.join(OUTPUT_DIR, "results.json")


@dataclass
class ScreenResult:
    code: str
    name: str
    market: str
    volume_ratio: float
    price_change_pct: float
    score: float


def get_daily_price(code: str, days: int = LOOKBACK_DAYS):
    import FinanceDataReader as fdr
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=days * 2)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)
    return df.tail(days)


def screen_stock(code: str, name: str, market: str):
    try:
        df = get_daily_price(code)
        if len(df) < 21:
            return None

        avg_volume_20 = df["Volume"].iloc[-21:-1].mean()
        today_volume = df["Volume"].iloc[-1]
        volume_ratio = today_volume / avg_volume_20 if avg_volume_20 > 0 else 0

        prev_close = df["Close"].iloc[-2]
        today_close = df["Close"].iloc[-1]
        price_change_pct = (today_close - prev_close) / prev_close * 100

        if volume_ratio >= VOLUME_SURGE_RATIO and price_change_pct >= PRICE_CHANGE_MIN:
            score = volume_ratio * 1.0 + price_change_pct * 0.5
            return ScreenResult(
                code, name, market,
                round(volume_ratio, 2), round(price_change_pct, 2), round(score, 2)
            )
    except Exception as e:
        print(f"  [skip] {code} {name}: {e}")

    return None


def main():
    print(f"코스피/코스닥 시가총액 상위 {N_PER_MARKET}개씩 유니버스 조회 중...")
    universe = get_combined_universe(N_PER_MARKET)
    print(f"총 {len(universe)}개 종목 스캔 시작...\n")

    results = []
    for i, row in universe.iterrows():
        code, name, market = row["Code"], row["Name"], row["Market"]
        result = screen_stock(code, name, market)
        if result:
            results.append(result)

        if (i + 1) % 20 == 0:
            print(f"  진행: {i + 1}/{len(universe)}")

        time.sleep(0.05)

    results.sort(key=lambda r: r.score, reverse=True)

    print("\n=== 관심 종목 스크리닝 결과 ===")
    if not results:
        print("조건을 만족하는 종목이 없습니다.")
    else:
        for r in results:
            print(
                f"[{r.market}] {r.name}({r.code}) | 거래량 {r.volume_ratio}배 | "
                f"등락률 {r.price_change_pct}% | 점수 {r.score}"
            )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scanned_count": len(universe),
        "conditions": {
            "volume_surge_ratio": VOLUME_SURGE_RATIO,
            "price_change_min_pct": PRICE_CHANGE_MIN,
        },
        "results": [asdict(r) for r in results],
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n결과를 {OUTPUT_JSON} 에 저장했습니다.")

    if results:
        today_str = datetime.today().strftime("%Y%m%d")
        os.makedirs("history", exist_ok=True)
        pd.DataFrame([asdict(r) for r in results]).to_csv(
            f"history/results_{today_str}.csv", index=False, encoding="utf-8-sig"
        )


if __name__ == "__main__":
    main()
