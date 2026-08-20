"""
내일주식 - 급등 전조(pre-surge) 스크리닝 백테스트

목적:
    "아직 크게 오르지 않았지만 거래량/거래대금이 늘고 고점에 근접한" 조건이
    실제로 이후 주가 상승을 예고하는지 과거 데이터로 검증한다.

주의:
    daily_screener.py의 급등 전조 조건 중 "수급 개선"은 종목별 과거 데이터를
    대량으로 가져와야 해서(400종목 x 1년치) 이 백테스트에서는 제외했다.
    나머지 4개 조건(가격상승 낮음/거래량 증가/거래대금 증가/변동성 중간/고점 근접)만 검증한다.

설치:
    pip install finance-datareader pandas

실행:
    python backtest_presurge.py

결과:
    - 콘솔에 신호 그룹 vs 베이스라인 비교 출력
    - presurge_signals.csv: 상세 신호 내역
    - docs/backtest_results.json: 대시보드 ⑥번 패널이 읽는 요약 결과
      (이 파일을 커밋하면 대시보드에 백테스트 결과가 표시됨)
"""

import json
import math
import os
import time

import pandas as pd
import FinanceDataReader as fdr

from universe import get_top_n_by_marketcap


# ----------------------------
# 설정값 (daily_screener.py의 급등 전조 조건과 동일, 수급 제외)
# ----------------------------
PRICE_CHANGE_MAX = 3.0
VOLUME_RATIO_MIN = 1.5
VALUE_RATIO_MIN = 1.5
VOLATILITY_RATIO_RANGE = (1.2, 3.0)
HIGH_PROXIMITY_MAX = 10.0

BACKTEST_DAYS = 365
N_PER_MARKET = 100          # 백테스트는 시간이 오래 걸려서 기본값을 daily_screener보다 적게 설정
FORWARD_DAYS = [1, 3, 5]

OUTPUT_JSON = os.path.join("docs", "backtest_results.json")


def get_daily_price(code: str, days: int) -> pd.DataFrame:
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=days * 2)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)
    return df.tail(days)


def find_presurge_signals(code: str, name: str, df: pd.DataFrame) -> list[dict]:
    signals = []
    vol_lo, vol_hi = VOLATILITY_RATIO_RANGE

    for i in range(60, len(df) - max(FORWARD_DAYS)):
        window = df.iloc[: i + 1]

        avg_volume_20 = window["Volume"].iloc[-21:-1].mean()
        today_volume = window["Volume"].iloc[-1]
        volume_ratio = today_volume / avg_volume_20 if avg_volume_20 > 0 else 0

        prev_close = window["Close"].iloc[-2]
        today_close = window["Close"].iloc[-1]
        price_change_pct = (today_close - prev_close) / prev_close * 100

        today_value = today_close * today_volume
        avg_value_20 = (window["Close"] * window["Volume"]).iloc[-21:-1].mean()
        value_ratio = today_value / avg_value_20 if avg_value_20 > 0 else 0

        daily_range = (window["High"] - window["Low"]) / window["Close"]
        avg_range_20 = daily_range.iloc[-21:-1].mean()
        today_range = daily_range.iloc[-1]
        volatility_ratio = today_range / avg_range_20 if avg_range_20 > 0 else 0

        recent_high = window["High"].iloc[-60:].max()
        pct_from_high = (recent_high - today_close) / recent_high * 100 if recent_high > 0 else 100

        is_presurge = (
            0 <= price_change_pct < PRICE_CHANGE_MAX and
            volume_ratio >= VOLUME_RATIO_MIN and
            value_ratio >= VALUE_RATIO_MIN and
            vol_lo <= volatility_ratio <= vol_hi and
            pct_from_high <= HIGH_PROXIMITY_MAX
        )

        if is_presurge:
            row = {
                "code": code,
                "name": name,
                "signal_date": df.index[i].strftime("%Y-%m-%d"),
                "volume_ratio": round(volume_ratio, 2),
                "pct_from_high": round(pct_from_high, 2),
            }
            for fwd in FORWARD_DAYS:
                future_close = df["Close"].iloc[i + fwd]
                fwd_return = (future_close - today_close) / today_close * 100
                row[f"return_{fwd}d_pct"] = round(fwd_return, 2)
            signals.append(row)

    return signals


def compute_baseline_returns(df: pd.DataFrame) -> dict:
    baseline = {}
    for fwd in FORWARD_DAYS:
        daily_returns = (df["Close"].shift(-fwd) - df["Close"]) / df["Close"] * 100
        baseline[fwd] = daily_returns.mean()
    return baseline


def sanitize_for_json(obj):
    """NaN/Infinity 값을 null로 바꿔서 표준 JSON으로 안전하게 저장되도록 정리한다."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def main():
    all_signals = []
    baseline_accum = {fwd: [] for fwd in FORWARD_DAYS}

    for market in ["KOSPI", "KOSDAQ"]:
        universe = get_top_n_by_marketcap(market, N_PER_MARKET)
        print(f"{market} 시가총액 상위 {len(universe)}개 종목 백테스트 중...")

        for _, row in universe.iterrows():
            code, name = row["Code"], row["Name"]
            try:
                df = get_daily_price(code, BACKTEST_DAYS)
                if len(df) < 70:
                    continue

                signals = find_presurge_signals(code, name, df)
                all_signals.extend(signals)

                baseline = compute_baseline_returns(df)
                for fwd in FORWARD_DAYS:
                    if pd.notna(baseline[fwd]):
                        baseline_accum[fwd].append(baseline[fwd])

            except Exception as e:
                print(f"  [skip] {code} {name}: {e}")

            time.sleep(0.05)

    if not all_signals:
        print("\n조건을 만족하는 신호가 하나도 없습니다. 조건을 완화해보세요.")
        return

    signals_df = pd.DataFrame(all_signals)
    signals_df.to_csv("presurge_signals.csv", index=False, encoding="utf-8-sig")

    print(f"\n=== 총 신호 발생 횟수: {len(signals_df)}건 ===\n")
    print("[신호 그룹 vs 전체 평균(베이스라인) 비교]")
    print(f"{'기간':<8}{'신호그룹 평균%':<16}{'신호그룹 승률%':<16}{'베이스라인 평균%':<18}")

    result_rows = []
    for fwd in FORWARD_DAYS:
        col = f"return_{fwd}d_pct"
        signal_mean = signals_df[col].mean()
        win_rate = (signals_df[col] > 0).mean() * 100
        baseline_mean = sum(baseline_accum[fwd]) / len(baseline_accum[fwd]) if baseline_accum[fwd] else float("nan")
        print(f"{fwd}일후    {signal_mean:>8.2f}%       {win_rate:>8.1f}%        {baseline_mean:>8.2f}%")
        result_rows.append({
            "horizon": f"{fwd}일 후",
            "avg_return": round(float(signal_mean), 2),
            "win_rate": round(float(win_rate), 1),
            "baseline": round(float(baseline_mean), 2),
        })

    os.makedirs("docs", exist_ok=True)
    output = {
        "generated_at": pd.Timestamp.today().strftime("%Y-%m-%d %H:%M:%S"),
        "period": f"최근 {BACKTEST_DAYS}일, 코스피/코스닥 상위 {N_PER_MARKET}개씩",
        "signal_count": len(signals_df),
        "rows": result_rows,
        "note": "수급(기관/외국인 순매수) 조건은 백테스트에서 제외됨 (계산량 문제)",
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(output), f, ensure_ascii=False, indent=2)

    print(f"\n요약 결과를 {OUTPUT_JSON} 에 저장했습니다. 이 파일을 커밋하면 대시보드에 표시됩니다.")


if __name__ == "__main__":
    main()
