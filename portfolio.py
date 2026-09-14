"""
내일주식 - 개인 보유 종목 시장 지도(트리맵) 데이터 생성

핀비즈 스타일의 트리맵을 만들기 위해, 보유 종목/코인의 현재가·등락률·평가금액을
계산해서 docs/portfolio.json 으로 저장한다. 박스 크기는 실제 보유 평가금액
비중으로 정해진다 (시가총액 기준이 아님 — 코인 시가총액이 압도적으로 커서
개인 포트폴리오 뷰에는 부적합하기 때문).

설치:
    pip install finance-datareader pandas

실행 (단독 실행도 가능, daily_screener.py에서도 자동 호출됨):
    python portfolio.py
"""

import json
import math
import urllib.request
from datetime import datetime

import pandas as pd

# 보유 종목 (이름, 수량) - 코스피/코스닥 종목은 이름으로 자동 코드 조회
HOLDINGS_KR = [
    ("KODEX 코리아밸류업", 35),
    ("삼성물산", 2),
    ("SK바이오팜", 23),
    ("NAVER", 8),
    ("현대차", 2),
    ("한화오션", 10),
    ("산일전기", 7),
]

# 암호화폐 (CoinGecko id, 표시이름, 심볼, 수량)
HOLDINGS_CRYPTO = [
    ("bitcoin", "비트코인", "BTC", 0.0059),
    ("ethereum", "이더리움", "ETH", 0.11439),
]

OUTPUT_PATH = "docs/portfolio.json"


def sanitize_for_json(obj):
    """NaN/Infinity 값을 null로 바꿔서 표준 JSON으로 안전하게 저장되도록 정리한다."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def resolve_kr_holdings(holdings: list) -> list:
    """종목명으로 종목코드를 자동 조회한다 (코드를 직접 하드코딩하면 실수 위험이 있어서)."""
    import FinanceDataReader as fdr

    try:
        listing = fdr.StockListing("KRX")
    except Exception as e:
        print(f"[경고] 종목 리스트 조회 실패: {e}")
        return []

    name_col = "Name" if "Name" in listing.columns else None
    if name_col is None:
        print("[경고] 종목 리스트에서 이름 컬럼을 찾지 못했습니다.")
        return []

    results = []
    for name, qty in holdings:
        normalized = name.replace(" ", "").upper()
        listing_names_normalized = listing[name_col].str.replace(" ", "").str.upper()

        match = listing[listing_names_normalized == normalized]
        if match.empty:
            match = listing[listing_names_normalized.str.contains(normalized, na=False)]

        if match.empty:
            print(f"[경고] '{name}' 종목을 찾지 못했습니다. 건너뜁니다. (종목명을 확인해주세요)")
            continue

        row = match.iloc[0]
        results.append({"code": row["Code"], "name": row["Name"], "qty": qty})

    return results


def get_kr_price_and_change(code: str):
    import FinanceDataReader as fdr
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)
    if len(df) < 2:
        return None, None
    today_close = df["Close"].iloc[-1]
    prev_close = df["Close"].iloc[-2]
    change_pct = (today_close - prev_close) / prev_close * 100
    return float(today_close), float(change_pct)


def get_crypto_data(crypto_holdings: list) -> dict:
    ids_param = ",".join(c[0] for c in crypto_holdings)
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids_param}&vs_currencies=krw&include_24hr_change=true"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[경고] 암호화폐 데이터 조회 실패: {e}")
        return {}


def build_portfolio() -> dict:
    items = []

    resolved_kr = resolve_kr_holdings(HOLDINGS_KR)
    for h in resolved_kr:
        try:
            price, change_pct = get_kr_price_and_change(h["code"])
        except Exception as e:
            print(f"  [skip] {h['name']}({h['code']}): {e}")
            continue
        if price is None:
            continue
        value = price * h["qty"]
        items.append({
            "symbol": h["code"],
            "name": h["name"],
            "type": "stock",
            "price": round(price, 0),
            "change_pct": round(change_pct, 2),
            "qty": h["qty"],
            "value": round(value, 0),
        })

    crypto_data = get_crypto_data(HOLDINGS_CRYPTO)
    for coingecko_id, name, symbol, qty in HOLDINGS_CRYPTO:
        info = crypto_data.get(coingecko_id)
        if not info:
            print(f"[경고] {name} 데이터를 가져오지 못했습니다.")
            continue
        price = info.get("krw", 0)
        change_pct = info.get("krw_24h_change", 0)
        value = price * qty
        items.append({
            "symbol": symbol,
            "name": name,
            "type": "crypto",
            "price": round(price, 0),
            "change_pct": round(change_pct, 2),
            "qty": qty,
            "value": round(value, 0),
        })

    total_value = sum(i["value"] for i in items)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
        "total_value": round(total_value, 0),
    }


def main():
    output = build_portfolio()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(output), f, ensure_ascii=False, indent=2)
    print(f"포트폴리오 데이터를 {OUTPUT_PATH} 에 저장했습니다. "
          f"총 평가금액: {output['total_value']:,.0f}원 ({len(output['items'])}개 종목)")


if __name__ == "__main__":
    main()
