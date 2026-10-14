# =============================================================
#  텔레그램 매수·매도 시그널 알림 봇 (GitHub Actions에서 자동 실행)
#  - RSI 매수(30 상향 돌파) · 매도(70 하향 돌파) 감지 시 텔레그램 발송
#  - 대시보드(app.py)와 같은 시그널 규칙 사용
#  - 같은 시그널은 한 번만 발송 (last_alert.json에 기록)
#  - 아직 진행 중인(미완성) 마지막 봉은 제외 → 나중에 사라지는 가짜 신호 방지
# =============================================================

import json
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
INTERVAL = "5m"        # 감시할 봉 종류 (GitHub 스케줄 지연 때문에 1m는 의미 없음)
PERIOD = "2d"          # 조회 범위
WINDOW_MINUTES = 40    # 최근 몇 분 안에 완성된 신호까지 알릴지 (중복은 자동 차단됨)
STATE_FILE = "last_alert.json"
# ---------------------------------------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TEST_MODE = os.environ.get("TEST_MODE", "").lower() == "true"


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


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main() -> int:
    if not BOT_TOKEN or not CHAT_ID:
        print("오류: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 시크릿이 설정되지 않았습니다.")
        return 1

    if TEST_MODE:
        ok = send_telegram("✅ 테스트 메시지입니다. 텔레그램 연결이 정상입니다.\n"
                           "이후 매수/매도 시그널이 나오면 이 채팅으로 알려드릴게요.")
        print("테스트 메시지 발송:", "성공" if ok else "실패")
        return 0 if ok else 1

    state = load_state()
    now_utc = pd.Timestamp.now(tz="UTC")
    bar_len = pd.Timedelta(INTERVAL.replace("m", "min"))

    for symbol, name in TICKERS.items():
        df = fetch(symbol)
        if df.empty or len(df) < 30:
            print(f"[{symbol}] 데이터 부족, 건너뜀")
            continue

        # 진행 중인 마지막 봉 제외 (아직 완성되지 않아 값이 바뀔 수 있음)
        last_open = df.index[-1]
        last_open = last_open.tz_convert("UTC") if last_open.tz is not None \
            else last_open.tz_localize("UTC")
        if now_utc < last_open + bar_len:
            df = df.iloc[:-1]

        rsi = compute_rsi(df["Close"])
        prev = rsi.shift(1)
        signals = {
            "매수": ((prev < 30) & (rsi >= 30), "🔴", "RSI 30 상향 돌파"),
            "매도": ((prev > 70) & (rsi <= 70), "🔵", "RSI 70 하향 돌파"),
        }

        found = False
        for kind, (cond, icon, rule) in signals.items():
            sig_times = df.index[cond]
            if len(sig_times) == 0:
                continue

            last_sig = sig_times[-1]
            sig_utc = last_sig.tz_convert("UTC") if last_sig.tz is not None \
                else last_sig.tz_localize("UTC")
            age_min = (now_utc - sig_utc).total_seconds() / 60
            if age_min > WINDOW_MINUTES:
                continue

            key = f"{symbol}|{kind}"
            if state.get(key) == sig_utc.isoformat():
                print(f"[{symbol}] {kind} 신호는 이미 알림 발송됨 → 생략")
                continue

            found = True
            sig_kst = sig_utc.tz_convert("Asia/Seoul")
            price = float(df.loc[last_sig, "Close"])
            rsi_now = float(rsi.iloc[-1])

            msg = (
                f"{icon} {kind} 시그널 발생\n"
                f"종목: {name}\n"
                f"시각: {sig_kst.strftime('%m/%d %H:%M')} (KST, {INTERVAL} 봉)\n"
                f"가격: {price:,.2f}\n"
                f"현재 RSI: {rsi_now:.1f} ({rule})\n"
                "※ 참고용 신호입니다. 지연 시세 기반이며 판단은 실시간 시세로 하세요."
            )
            if send_telegram(msg):
                state[key] = sig_utc.isoformat()
                print(f"[{symbol}] {kind} 알림 발송 완료: {sig_kst}")

        if not found:
            print(f"[{symbol}] 새 시그널 없음 (현재 RSI {float(rsi.iloc[-1]):.1f})")

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
