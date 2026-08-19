"""
내일주식 - 종목별 업종(테마) 조회 모듈

밸류 스크리닝에서 "업종(테마)별 상위 N개" 그룹핑을 위해 사용한다.
FinanceDataReader의 KRX-DESC(상장법인 개요) 리스트에서 업종 정보를 가져온다.
"""

import FinanceDataReader as fdr


def get_sector_map() -> dict:
    """{종목코드: 업종명} 딕셔너리를 반환한다.
    조회 실패 시 빈 딕셔너리를 반환하며, 이 경우 모든 종목은 '기타'로 처리된다.
    """
    try:
        desc = fdr.StockListing("KRX-DESC")
    except Exception as e:
        print(f"[경고] 업종 정보를 가져오지 못했습니다: {e}")
        return {}

    code_col = "Symbol" if "Symbol" in desc.columns else ("Code" if "Code" in desc.columns else None)
    sector_col = "Sector" if "Sector" in desc.columns else None

    if code_col is None or sector_col is None:
        print("[경고] KRX-DESC 리스트에서 업종 컬럼을 찾지 못했습니다. (컬럼 구성이 바뀌었을 수 있음)")
        return {}

    return dict(zip(desc[code_col], desc[sector_col]))
