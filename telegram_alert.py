# =============================================================
#  텔레그램 매수 시그널 알림 봇 (GitHub Actions에서 자동 실행)
#  - 15분봉 기준 RSI 30 상향 돌파(매수 시그널) 감지 시 텔레그램 발송
#  - 대시보드(app.py)와 동일한 시그널 규칙 사용
# =============================================================

import os
import sys

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ---------------- 설정 (원하면 수정) ----------------
TICKERS = {
    "USDKRW=X": "원/달러 (USD/KRW)",
}
INTERVAL = "15m"       # 감시할 봉 종류 (Actions 실행 간격상 15m 권장)
PERIOD = "2d"          # 조회 범위
WINDOW_MINUTES = 25    # 최근 몇 분 내에 나온 시그널만 알림 (실행 간격보다 약간 크게)
# ---------------------------------------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def compute_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def fetch(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol, period=PERIOD, interval=INTERVAL,
                     auto_adjust=True, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])


def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=15)
        ok = r.status_code == 200 and r.json().get("ok", False)
        if not ok:
            print("텔레그램 발송 실패:", r.text)
        return ok
    except Exception as e:
        print("텔레그램 발송 오류:", e)
        return False


def main() -> int:
    if not BOT_TOKEN or not CHAT_ID:
        print("오류: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 시크릿이 설정되지 않았습니다.")
        return 1

    now_utc = pd.Timestamp.now(tz="UTC")
    sent_any = False

    for symbol, name in TICKERS.items():
        df = fetch(symbol)
        if df.empty or len(df) < 20:
            print(f"[{symbol}] 데이터 부족, 건너뜀")
            continue

        rsi = compute_rsi(df["Close"])
        prev = rsi.shift(1)
        buy = (prev < 30) & (rsi >= 30)
        buy_times = df.index[buy]

        if len(buy_times) == 0:
            print(f"[{symbol}] 시그널 없음 (현재 RSI {float(rsi.iloc[-1]):.1f})")
            continue

        last_sig = buy_times[-1]
        sig_utc = last_sig.tz_convert("UTC") if last_sig.tz is not None \
            else last_sig.tz_localize("UTC")
        age_min = (now_utc - sig_utc).total_seconds() / 60

        if age_min > WINDOW_MINUTES:
            print(f"[{symbol}] 마지막 시그널이 {age_min:.0f}분 전 → 알림 생략")
            continue

        sig_kst = sig_utc.tz_convert("Asia/Seoul")
        price = float(df.loc[last_sig, "Close"])
        rsi_now = float(rsi.iloc[-1])

        msg = (
            "🔔 매수 시그널 발생\n"
            f"종목: {name}\n"
            f"시각: {sig_kst.strftime('%m/%d %H:%M')} (KST, {INTERVAL} 봉)\n"
            f"가격: {price:,.2f}\n"
            f"현재 RSI: {rsi_now:.1f} (30 상향 돌파)\n"
            "※ 참고용 신호입니다. 지연 시세 기반이며 투자 판단은 실시간 시세로 하세요."
        )
        if send_telegram(msg):
            print(f"[{symbol}] 알림 발송 완료: {sig_kst}")
            sent_any = True

    if not sent_any:
        print("이번 실행에서 발송된 알림 없음 (정상)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
