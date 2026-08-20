"""
내일주식 - 일일 스크리닝 서비스 (GitHub Actions 자동 실행용, v0.6)

네 가지 스크리닝을 함께 수행한다.
1. 모멘텀 스크리닝: 거래량 급증 + 당일 급등 종목 (이미 급등한 종목)
2. 밸류 스크리닝: 최근 거래량이 늘었지만 PER이 낮은(저평가 가능성) 종목
3. 급락 경고 스크리닝: 거래량 급증 + 당일 급락 종목 (보유 종목 매도 판단 참고용)
4. 급등 전조(pre-surge) 스크리닝: "아직 크게 오르지 않았지만" 거래량/거래대금/수급이
   개선되며 고점에 근접해가는 종목 — 이미 오른 종목이 아니라 "오르기 시작하는" 종목을 찾는다.
   아래 5개 하위 뷰로 나눠서 보여준다.
   ① 오늘의 급등 후보 (종합 점수 상위)
   ② 급등 전조 종목 (수급 개선 상위)
   ③ 거래량 폭발 종목 (거래량+거래대금 증가 상위)
   ④ 돌파 임박 종목 (고점 근접도 상위)
   ⑤ AI 추천 종목 (정규화 가중합 스코어 — 실제 ML모델이 아닌 규칙 기반 스코어링)

대상: 코스피/코스닥 시가총액 상위 200개씩, 총 최대 400개 종목

결과는 docs/results.json에 저장되어 GitHub Pages 대시보드가 읽는다.

실행 (로컬/Actions 공통):
    python daily_screener.py
"""

import json
import math
import time
import os
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime

import pandas as pd

from universe import get_combined_universe
from fundamentals import get_latest_fundamental
from sectors import get_sector_map
from supply_demand import get_supply_demand_improvement_map


# ----------------------------
# 설정값
# ----------------------------
N_PER_MARKET = 200
LOOKBACK_DAYS = 60

# 모멘텀 스크리닝 조건
VOLUME_SURGE_RATIO = 2.0
PRICE_CHANGE_MIN = 3.0

# 밸류 스크리닝 조건 (거래량 증가 + 저PER, 업종별 상위 N개)
VALUE_VOLUME_RATIO_MIN = 1.5   # 최근 거래량이 자기 20일 평균 대비 이 배수 이상이면 "거래량 증가"로 간주
PER_MAX_DEFAULT = 10.0         # 일반 업종 PER 상한
PER_MAX_GROWTH = 20.0          # 성장주 업종 PER 상한 (더 완화된 기준)
VALUE_TOP_N_PER_SECTOR = 2     # 업종(테마)당 뽑을 종목 수

# "성장주"로 간주할 업종 키워드 (KRX 공식 분류가 아닌 임의 기준, 필요시 조정)
GROWTH_SECTOR_KEYWORDS = [
    "반도체", "전자", "IT", "소프트웨어", "인터넷", "게임",
    "바이오", "제약", "생물", "2차전지", "전지", "로봇", "통신장비", "우주항공",
]

# 급락 경고 스크리닝 조건 (거래량 급증 + 급락)
CRASH_VOLUME_SURGE_RATIO = 2.0  # 거래량 급증 기준 (모멘텀과 동일 기준 사용)
PRICE_DROP_MIN = 3.0            # 당일 하락률(%) 최소 기준. 예: 3.0 => -3% 이하 하락 시 포착

# 급등 전조(pre-surge) 스크리닝 조건
# 핵심: "이미 급등한 종목"이 아니라 "지금 막 오르기 시작하는 종목"을 찾는다.
PRESURGE_PRICE_CHANGE_MAX = PRICE_CHANGE_MIN   # 상승률: 이 값 미만이어야 함(=아직 안 터짐). 음수도 제외.
PRESURGE_VOLUME_RATIO_MIN = 1.5                # 거래량: 20일 평균 대비 이 배수 이상 (높음)
PRESURGE_VALUE_RATIO_MIN = 1.5                 # 거래대금: 20일 평균 대비 이 배수 이상 (높음)
PRESURGE_VOLATILITY_RATIO_RANGE = (1.2, 3.0)   # 변동성: 20일 평균 대비 이 범위 안이어야 함 (중간 정도 증가)
PRESURGE_HIGH_PROXIMITY_MAX = 10.0             # 고점 거리: 최근 60일 고점 대비 이 %(하락폭) 이내면 "근접"
PRESURGE_TOP_N = 20                            # 각 뷰(리스트)마다 상위 몇 개를 보여줄지

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
    sector: str
    is_growth: bool
    volume_ratio: float
    per: float
    pbr: float
    score: float


@dataclass
class CrashResult:
    code: str
    name: str
    market: str
    volume_ratio: float
    price_change_pct: float  # 음수 값 (하락률)
    score: float


@dataclass
class PresurgeResult:
    code: str
    name: str
    market: str
    sector: str
    price_change_pct: float
    volume_ratio: float
    value_ratio: float
    volatility_ratio: float
    pct_from_high: float
    supply_demand_score: float  # 억원 단위, 최근5일-이전5일 순매수 증가분
    composite_score: float
    ai_score: float = 0.0


def get_daily_price(code: str, days: int = LOOKBACK_DAYS):
    import FinanceDataReader as fdr
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=days * 2)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)
    return df.tail(days)


def analyze_stock(code: str, name: str, market: str, fundamental_row, sector: str, supply_demand_map: dict):
    """한 종목에 대해 시세를 한 번만 조회해서 모멘텀/밸류/급락/급등전조 네 스크리닝을 동시에 계산한다."""
    momentum = None
    value = None
    crash = None
    presurge = None

    try:
        df = get_daily_price(code)
        if len(df) < 21:
            return None, None, None, None

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

        # 2) 밸류 조건 (거래량 증가 + 저PER, 업종별 PER 기준 차등 적용)
        if fundamental_row is not None:
            per = fundamental_row.get("PER", 0)
            pbr = fundamental_row.get("PBR", 0)
            sector_name = sector or "기타"
            is_growth = any(kw in sector_name for kw in GROWTH_SECTOR_KEYWORDS)
            per_threshold = PER_MAX_GROWTH if is_growth else PER_MAX_DEFAULT

            if per and per > 0 and per <= per_threshold and volume_ratio >= VALUE_VOLUME_RATIO_MIN:
                v_score = round(volume_ratio * (10 / per), 2)
                value = ValueResult(
                    code, name, market, sector_name, is_growth,
                    round(volume_ratio, 2), round(per, 2), round(pbr, 2), v_score
                )

        # 3) 급락 경고 조건
        if volume_ratio >= CRASH_VOLUME_SURGE_RATIO and price_change_pct <= -PRICE_DROP_MIN:
            c_score = volume_ratio * 1.0 + abs(price_change_pct) * 0.5
            crash = CrashResult(
                code, name, market,
                round(volume_ratio, 2), round(price_change_pct, 2), round(c_score, 2)
            )

        # 4) 급등 전조 조건 ("아직 안 올랐지만" 거래량/거래대금/변동성/고점근접이 함께 개선)
        today_value = today_close * today_volume
        avg_value_20 = (df["Close"] * df["Volume"]).iloc[-21:-1].mean()
        value_ratio = today_value / avg_value_20 if avg_value_20 > 0 else 0

        daily_range = (df["High"] - df["Low"]) / df["Close"]
        avg_range_20 = daily_range.iloc[-21:-1].mean()
        today_range = daily_range.iloc[-1]
        volatility_ratio = today_range / avg_range_20 if avg_range_20 > 0 else 0

        recent_high = df["High"].iloc[-60:].max()
        pct_from_high = (recent_high - today_close) / recent_high * 100 if recent_high > 0 else 100

        vol_lo, vol_hi = PRESURGE_VOLATILITY_RATIO_RANGE
        is_presurge = (
            0 <= price_change_pct < PRESURGE_PRICE_CHANGE_MAX and
            volume_ratio >= PRESURGE_VOLUME_RATIO_MIN and
            value_ratio >= PRESURGE_VALUE_RATIO_MIN and
            vol_lo <= volatility_ratio <= vol_hi and
            pct_from_high <= PRESURGE_HIGH_PROXIMITY_MAX
        )

        if is_presurge:
            supply_demand_score = supply_demand_map.get(code, 0.0)
            composite_score = round(
                volume_ratio * 2.0 +
                value_ratio * 2.0 +
                max(0.0, 3.0 - abs(volatility_ratio - 1.8)) * 1.0 +
                max(0.0, PRESURGE_HIGH_PROXIMITY_MAX - pct_from_high) * 1.0 +
                max(0.0, supply_demand_score) * 0.5,
                2
            )
            presurge = PresurgeResult(
                code, name, market, sector or "기타",
                round(price_change_pct, 2), round(volume_ratio, 2), round(value_ratio, 2),
                round(volatility_ratio, 2), round(pct_from_high, 2), round(supply_demand_score, 2),
                composite_score
            )

    except Exception as e:
        print(f"  [skip] {code} {name}: {e}")

    return momentum, value, crash, presurge


def compute_ai_scores(candidates: list) -> list:
    """급등 전조 후보군에 대해 정규화된 가중합 점수를 계산한다 (규칙 기반, ML 모델 아님).
    각 지표를 0~1로 min-max 정규화한 뒤 가중합해서 0~100 스케일로 변환한다."""
    if not candidates:
        return candidates

    def normalize(values: list) -> list:
        lo, hi = min(values), max(values)
        if hi - lo < 1e-9:
            return [0.0] * len(values)
        return [(v - lo) / (hi - lo) for v in values]

    volume_n = normalize([c.volume_ratio for c in candidates])
    value_n = normalize([c.value_ratio for c in candidates])
    high_prox_n = normalize([-c.pct_from_high for c in candidates])       # 고점에 가까울수록 높은 점수
    supply_n = normalize([c.supply_demand_score for c in candidates])
    vol_ideal_n = normalize([-abs(c.volatility_ratio - 1.8) for c in candidates])  # 이상적 변동성(1.8배)에 가까울수록 높은 점수

    for i, c in enumerate(candidates):
        raw = (
            volume_n[i] * 0.25 +
            value_n[i] * 0.25 +
            high_prox_n[i] * 0.20 +
            supply_n[i] * 0.20 +
            vol_ideal_n[i] * 0.10
        )
        c.ai_score = round(raw * 100, 1)

    return candidates


def build_presurge_views(candidates: list, top_n: int = PRESURGE_TOP_N) -> dict:
    """급등 전조 후보군을 5개 관점으로 나눠서 반환한다."""
    candidates = compute_ai_scores(candidates)

    today_candidates = sorted(candidates, key=lambda r: r.composite_score, reverse=True)[:top_n]
    presage_signal = sorted(candidates, key=lambda r: r.supply_demand_score, reverse=True)[:top_n]
    volume_explosion = sorted(candidates, key=lambda r: (r.volume_ratio + r.value_ratio), reverse=True)[:top_n]
    breakout_imminent = sorted(candidates, key=lambda r: r.pct_from_high)[:top_n]
    ai_recommended = sorted(candidates, key=lambda r: r.ai_score, reverse=True)[:top_n]

    return {
        "today_candidates": today_candidates,
        "presage_signal": presage_signal,
        "volume_explosion": volume_explosion,
        "breakout_imminent": breakout_imminent,
        "ai_recommended": ai_recommended,
    }


def limit_top_n_per_sector(results: list, n: int = VALUE_TOP_N_PER_SECTOR) -> list:
    """업종(테마)별로 점수 상위 N개만 남긴다. 결과는 업종명 -> 점수 내림차순으로 정렬."""
    grouped = defaultdict(list)
    for r in results:
        grouped[r.sector].append(r)

    limited = []
    for sector_name, items in grouped.items():
        items.sort(key=lambda r: r.score, reverse=True)
        limited.extend(items[:n])

    limited.sort(key=lambda r: (r.sector, -r.score))
    return limited


def sanitize_for_json(obj):
    """NaN/Infinity 값을 null로 바꿔서 표준 JSON으로 안전하게 저장되도록 정리한다.
    (파이썬 json.dump는 NaN/Infinity를 그대로 써버리는데, 이건 표준 JSON이 아니라서
    브라우저의 JSON.parse가 파일 전체를 파싱 실패시킨다.)"""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def main():
    print(f"코스피/코스닥 시가총액 상위 {N_PER_MARKET}개씩 유니버스 조회 중...")
    universe = get_combined_universe(N_PER_MARKET)
    print(f"총 {len(universe)}개 종목 스캔 시작...\n")

    print("재무 데이터(PER/PBR) 조회 중...")
    fundamentals = {
        "KOSPI": get_latest_fundamental("KOSPI"),
        "KOSDAQ": get_latest_fundamental("KOSDAQ"),
    }

    print("업종(테마) 정보 조회 중...")
    sector_map = get_sector_map()

    print("수급(기관/외국인 순매수) 데이터 조회 중...")
    supply_demand_map = {}
    for market in ["KOSPI", "KOSDAQ"]:
        supply_demand_map.update(get_supply_demand_improvement_map(market))

    momentum_results = []
    value_candidates = []
    crash_results = []
    presurge_candidates = []

    for i, row in universe.iterrows():
        code, name, market = row["Code"], row["Name"], row["Market"]

        fundamental_row = None
        fdf = fundamentals.get(market)
        if fdf is not None and code in fdf.index:
            fundamental_row = fdf.loc[code]

        sector = sector_map.get(code, "기타")

        m, v, c, p = analyze_stock(code, name, market, fundamental_row, sector, supply_demand_map)
        if m:
            momentum_results.append(m)
        if v:
            value_candidates.append(v)
        if c:
            crash_results.append(c)
        if p:
            presurge_candidates.append(p)

        if (i + 1) % 40 == 0:
            print(f"  진행: {i + 1}/{len(universe)}")

        time.sleep(0.05)

    momentum_results.sort(key=lambda r: r.score, reverse=True)
    crash_results.sort(key=lambda r: r.score, reverse=True)
    value_results = limit_top_n_per_sector(value_candidates, VALUE_TOP_N_PER_SECTOR)
    presurge_views = build_presurge_views(presurge_candidates)

    print(f"\n=== 모멘텀 스크리닝 결과: {len(momentum_results)}건 ===")
    for r in momentum_results:
        print(f"[{r.market}] {r.name}({r.code}) | 거래량 {r.volume_ratio}배 | 등락률 {r.price_change_pct}% | 점수 {r.score}")

    print(f"\n=== 밸류(업종별 저PER+거래량증가 상위 {VALUE_TOP_N_PER_SECTOR}개) 스크리닝 결과: {len(value_results)}건 ===")
    for r in value_results:
        tag = "성장주" if r.is_growth else "일반"
        print(f"[{r.market}][{r.sector}/{tag}] {r.name}({r.code}) | 거래량 {r.volume_ratio}배 | PER {r.per} | PBR {r.pbr} | 점수 {r.score}")

    print(f"\n=== 급락 경고 스크리닝 결과: {len(crash_results)}건 ===")
    for r in crash_results:
        print(f"[{r.market}] {r.name}({r.code}) | 거래량 {r.volume_ratio}배 | 등락률 {r.price_change_pct}% | 점수 {r.score}")

    print(f"\n=== 급등 전조 후보군: {len(presurge_candidates)}건 (조건 통과 종목 전체) ===")
    print(f"  ① 오늘의 급등 후보: {len(presurge_views['today_candidates'])}건")
    print(f"  ② 급등 전조 종목: {len(presurge_views['presage_signal'])}건")
    print(f"  ③ 거래량 폭발 종목: {len(presurge_views['volume_explosion'])}건")
    print(f"  ④ 돌파 임박 종목: {len(presurge_views['breakout_imminent'])}건")
    print(f"  ⑤ AI 추천 종목: {len(presurge_views['ai_recommended'])}건")

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
            "per_max_default": PER_MAX_DEFAULT,
            "per_max_growth": PER_MAX_GROWTH,
            "top_n_per_sector": VALUE_TOP_N_PER_SECTOR,
            "growth_sector_keywords": GROWTH_SECTOR_KEYWORDS,
        },
        "crash_conditions": {
            "volume_surge_ratio": CRASH_VOLUME_SURGE_RATIO,
            "price_drop_min_pct": PRICE_DROP_MIN,
        },
        "presurge_conditions": {
            "price_change_max_pct": PRESURGE_PRICE_CHANGE_MAX,
            "volume_ratio_min": PRESURGE_VOLUME_RATIO_MIN,
            "value_ratio_min": PRESURGE_VALUE_RATIO_MIN,
            "volatility_ratio_range": list(PRESURGE_VOLATILITY_RATIO_RANGE),
            "high_proximity_max_pct": PRESURGE_HIGH_PROXIMITY_MAX,
            "note": "AI 추천 종목은 실제 ML 모델이 아니라 정규화된 지표의 가중합(규칙 기반) 점수입니다.",
        },
        "momentum_results": [asdict(r) for r in momentum_results],
        "value_results": [asdict(r) for r in value_results],
        "crash_results": [asdict(r) for r in crash_results],
        "presurge_today_candidates": [asdict(r) for r in presurge_views["today_candidates"]],
        "presurge_signal": [asdict(r) for r in presurge_views["presage_signal"]],
        "presurge_volume_explosion": [asdict(r) for r in presurge_views["volume_explosion"]],
        "presurge_breakout_imminent": [asdict(r) for r in presurge_views["breakout_imminent"]],
        "presurge_ai_recommended": [asdict(r) for r in presurge_views["ai_recommended"]],
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(output), f, ensure_ascii=False, indent=2)

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
    if crash_results:
        pd.DataFrame([asdict(r) for r in crash_results]).to_csv(
            f"history/crash_{today_str}.csv", index=False, encoding="utf-8-sig"
        )
    if presurge_candidates:
        pd.DataFrame([asdict(r) for r in presurge_candidates]).to_csv(
            f"history/presurge_{today_str}.csv", index=False, encoding="utf-8-sig"
        )


if __name__ == "__main__":
    main()
