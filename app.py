# =============================================================
#  FX 환율 웹 대시보드 v3
#  - 차트: TradingView lightweight-charts (업비트 스타일 조작감)
#  - 봉 종류: 1분/5분/15분/30분/1시간/일/주/월
# =============================================================

import json
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

import base64
import os

_icon = "icon.png" if os.path.exists("icon.png") else "💹"
st.set_page_config(page_title="토마곰 환율 지표", page_icon=_icon, layout="wide")

# 상단 빈 공간 줄이기 (기본 여백이 넓어서 축소)
st.markdown("""
<style>
.block-container { padding-top: 1.2rem; }
[data-testid="stSidebarContent"] { padding-top: 1.2rem; }
</style>
""", unsafe_allow_html=True)

# 제목: 곰 아이콘 + 짧은 제목
if os.path.exists("icon.png"):
    _b64 = base64.b64encode(open("icon.png", "rb").read()).decode()
    st.markdown(
        f'<h1 style="display:flex; align-items:center; gap:14px; margin-bottom:0.2rem;">'
        f'<img src="data:image/png;base64,{_b64}" width="52" height="52" '
        f'style="border-radius:12px;"> 토마곰 환율 지표</h1>',
        unsafe_allow_html=True,
    )
else:
    st.title("💹 토마곰 환율 지표")
st.caption("데이터 출처: Yahoo Finance (yfinance) · 지연 시세이며 투자 조언이 아닙니다.")

# -------------------------------------------------------------
# 1. 사이드바
# -------------------------------------------------------------
# 원/달러 고정
pair_label = "원/달러 (USD/KRW)"
PAIRS = {pair_label: "USDKRW=X"}

# 봉 종류별 yfinance 허용 조회 기간 (분봉은 최근 데이터만 제공됨)
INTERVALS = {
    "1분봉":   {"interval": "1m",  "periods": {"1일": "1d", "5일": "5d", "7일": "7d"}, "default": "1일"},
    "5분봉":   {"interval": "5m",  "periods": {"1일": "1d", "5일": "5d", "1개월": "1mo"}, "default": "5일"},
    "15분봉":  {"interval": "15m", "periods": {"5일": "5d", "1개월": "1mo", "2개월": "60d"}, "default": "5일"},
    "30분봉":  {"interval": "30m", "periods": {"5일": "5d", "1개월": "1mo", "2개월": "60d"}, "default": "1개월"},
    "1시간봉": {"interval": "1h",  "periods": {"1개월": "1mo", "3개월": "3mo", "6개월": "6mo", "1년": "1y"}, "default": "3개월"},
    "일봉":    {"interval": "1d",  "periods": {"3개월": "3mo", "6개월": "6mo", "1년": "1y", "3년": "3y", "5년": "5y"}, "default": "6개월"},
    "주봉":    {"interval": "1wk", "periods": {"1년": "1y", "3년": "3y", "5년": "5y", "10년": "10y"}, "default": "3년"},
    "월봉":    {"interval": "1mo", "periods": {"5년": "5y", "10년": "10y", "최대": "max"}, "default": "10년"},
}

with st.sidebar:
    st.header("⚙️ 설정")

    st.subheader("🕐 타임프레임")
    iv_label = st.selectbox("봉 종류", list(INTERVALS.keys()), index=0)
    iv_conf = INTERVALS[iv_label]
    period_options = list(iv_conf["periods"].keys())
    period_label = st.selectbox(
        "조회 기간", period_options,
        index=period_options.index(iv_conf["default"]),
        help="분봉은 야후 파이낸스 정책상 최근 데이터만 조회 가능해 선택지가 자동 제한됩니다. (1분봉: 최대 7일)",
    )

    st.divider()
    st.subheader("🔔 매수/매도 시그널 알림")
    alert_on = st.toggle(
        "알림 켜기 (자동 새로고침)",
        help="이 탭을 열어둔 동안 주기적으로 데이터를 다시 확인해, 새 매수/매도 시그널이 나오면 알림을 띄웁니다.",
    )
    refresh_sec = 60
    if alert_on:
        refresh_sec = st.selectbox(
            "확인 주기", [30, 60, 120], index=1,
            format_func=lambda s: f"{s}초마다",
        )
        st.caption("브라우저 알림을 받으려면 아래 버튼을 한 번 눌러 권한을 허용해주세요.")
        components.html("""
<button onclick="
  if ('Notification' in window) {
    Notification.requestPermission().then(p => {
      if (p === 'granted') {
        new Notification('알림 설정 완료', { body: '매수 시그널이 발생하면 이렇게 알려드릴게요.' });
      }
    });
  }
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const o = ctx.createOscillator(); const g = ctx.createGain();
  o.connect(g); g.connect(ctx.destination);
  o.frequency.value = 880; g.gain.value = 0.15;
  o.start(); o.stop(ctx.currentTime + 0.15);
" style="width:100%; padding:8px; font-size:13px; cursor:pointer;
         border:1px solid #ccc; border-radius:6px; background:#fff;">
  🔔 알림 권한 허용 + 소리 테스트
</button>""", height=45)

ticker = PAIRS[pair_label]
tf = {"period": iv_conf["periods"][period_label], "interval": iv_conf["interval"]}
tf_label = f"{period_label} · {iv_label}"
is_intraday = tf["interval"] in ("1m", "5m", "15m", "30m", "1h")

# -------------------------------------------------------------
# 2. 데이터 수집
# -------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_data(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """3회 재시도 → 실패 시 예비 방식(Ticker.history)까지 시도.
    실패하면 예외를 발생시켜 '빈 결과'가 캐시에 저장되지 않게 함."""

    def clean(d):
        if d is None or d.empty:
            return None
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        d = d.dropna(subset=["Close"]) if "Close" in d.columns else pd.DataFrame()
        return d if not d.empty else None

    last_err = None
    for _ in range(3):
        try:
            out = clean(yf.download(symbol, period=period, interval=interval,
                                    auto_adjust=True, progress=False))
            if out is not None:
                return out
        except Exception as e:
            last_err = e
        time.sleep(1.5)

    # 예비 방식: 같은 데이터를 다른 경로로 요청
    try:
        out = clean(yf.Ticker(symbol).history(period=period, interval=interval,
                                              auto_adjust=True))
        if out is not None:
            return out
    except Exception as e:
        last_err = e

    raise RuntimeError(f"야후 파이낸스 응답 없음 ({symbol}): {last_err}")


def safe_load(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """보조 데이터(거시지표)용: 실패해도 앱이 멈추지 않게 빈 DataFrame 반환."""
    try:
        return load_data(symbol, period, interval)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=120, show_spinner=False)
def load_bithumb_usdt(interval_code: str) -> pd.DataFrame:
    """빗썸 공개 API에서 USDT/KRW 캔들 조회 (1m/5m/30m/1h)."""
    url = f"https://api.bithumb.com/public/candlestick/USDT_KRW/{interval_code}"
    r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    js = r.json()
    if js.get("status") != "0000":
        raise RuntimeError(f"빗썸 API 오류: {js.get('status')}")
    df = pd.DataFrame(js["data"],
                      columns=["ts", "open", "close", "high", "low", "vol"])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df["close"] = df["close"].astype(float)
    return df.set_index("ts").sort_index()[["close"]]


@st.cache_data(ttl=1800, show_spinner=False)
def load_news() -> list:
    """국내 언론사 경제 뉴스 RSS에서 한국어 헤드라인 수집.
    환율/달러/금리 관련 기사를 앞쪽에 우선 배치."""
    import xml.etree.ElementTree as ET

    feeds = [
        ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
        ("매일경제", "https://www.mk.co.kr/rss/30100041/"),
        ("한국경제", "https://www.hankyung.com/feed/economy"),
    ]
    keywords = ["환율", "달러", "원화", "외환", "연준", "Fed", "금리",
                "FOMC", "한은", "한국은행", "엔화", "위안"]

    items = []
    for src, url in feeds:
        try:
            r = requests.get(url, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                if title:
                    items.append({"title": title, "link": link, "src": src})
        except Exception:
            continue

    # 중복 제거
    seen, unique = set(), []
    for it in items:
        if it["title"] not in seen:
            seen.add(it["title"])
            unique.append(it)

    # 환율 관련 기사 우선, 나머지 일반 경제 뉴스는 뒤에
    fx_news = [it for it in unique if any(k in it["title"] for k in keywords)]
    etc_news = [it for it in unique if it not in fx_news]
    return (fx_news + etc_news)[:12], len(fx_news)


try:
    with st.spinner("환율 데이터를 불러오는 중..."):
        df = load_data(ticker, tf["period"], tf["interval"])
except Exception:
    st.error(
        "야후 파이낸스에서 데이터를 받지 못했습니다. "
        "무료 데이터 특성상 일시적으로 요청이 거부될 때가 있습니다 (보통 몇 분 내 회복). "
        "아래 버튼을 누르거나 잠시 후 다시 접속해주세요."
    )
    if st.button("🔄 다시 시도"):
        load_data.clear()
        st.rerun()
    st.stop()

if df.empty:
    st.error("데이터를 불러오지 못했습니다. 잠시 후 새로고침(F5) 해주세요.")
    st.stop()

# -------------------------------------------------------------
# 3. 기술적 지표 + 시그널
# -------------------------------------------------------------
def add_indicators(data: pd.DataFrame) -> pd.DataFrame:
    d = data.copy()
    close = d["Close"]
    d["SMA20"] = close.rolling(20).mean()
    d["SMA50"] = close.rolling(50).mean()
    std20 = close.rolling(20).std()
    d["BB_UP"] = d["SMA20"] + 2 * std20
    d["BB_DN"] = d["SMA20"] - 2 * std20
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    d["RSI"] = 100 - (100 / (1 + rs))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    d["MACD"] = ema12 - ema26
    d["MACD_SIG"] = d["MACD"].ewm(span=9, adjust=False).mean()
    d["MACD_HIST"] = d["MACD"] - d["MACD_SIG"]
    return d


def add_signals(data: pd.DataFrame) -> pd.DataFrame:
    d = data.copy()
    rsi, prev_rsi = d["RSI"], d["RSI"].shift(1)
    d["BUY"] = (prev_rsi < 30) & (rsi >= 30)
    d["SELL"] = (prev_rsi > 70) & (rsi <= 70)
    return d


df = add_signals(add_indicators(df))

# -------------------------------------------------------------
# 3.5 매수/매도 시그널 알림 (자동 새로고침 + 화면/소리/브라우저 알림)
# -------------------------------------------------------------
if alert_on:
    if st_autorefresh is not None:
        st_autorefresh(interval=refresh_sec * 1000, key="signal_refresh")
    else:
        st.warning("자동 새로고침 패키지가 없습니다. requirements.txt에 "
                   "`streamlit-autorefresh`를 추가했는지 확인해주세요.")

    now_kst = pd.Timestamp.now(tz="Asia/Seoul").strftime("%H:%M:%S")

    def _kst_str(t):
        if getattr(t, "tz", None) is not None:
            t = t.tz_convert("Asia/Seoul")
        return t.strftime("%m/%d %H:%M")

    # 매수/매도 중 가장 최근 시그널 하나를 찾음
    latest_sig, latest_kind = None, None
    buy_times = df.index[df["BUY"]]
    sell_times = df.index[df["SELL"]]
    if len(buy_times) > 0:
        latest_sig, latest_kind = buy_times[-1], "매수"
    if len(sell_times) > 0 and (latest_sig is None or sell_times[-1] > latest_sig):
        latest_sig, latest_kind = sell_times[-1], "매도"

    if latest_sig is not None:
        # 최근 3개 봉 안에서 나온 시그널만 '새 시그널'로 간주
        is_recent = latest_sig >= df.index[max(0, len(df) - 3)]
        sig_key = f"{ticker}|{tf['interval']}|{latest_kind}|{latest_sig}"

        if is_recent and st.session_state.get("last_alerted") != sig_key:
            st.session_state["last_alerted"] = sig_key
            sig_str = _kst_str(latest_sig)
            price_at_sig = float(df.loc[latest_sig, "Close"])

            icon = "🔴" if latest_kind == "매수" else "🔵"
            st.toast(f"{icon} {latest_kind} 시그널 발생! {pair_label} · "
                     f"{sig_str} · {price_at_sig:,.2f}", icon="🔔")
            msg_body = f"{pair_label} {iv_label} / {sig_str} / 가격 {price_at_sig:,.2f}"
            # 매수: 올라가는 음(880→1100Hz) / 매도: 내려가는 음(1100→880Hz)
            tones = "beep(0, 880); beep(0.2, 1100);" if latest_kind == "매수" \
                else "beep(0, 1100); beep(0.2, 880);"
            components.html("""
<script>
if ('Notification' in window && Notification.permission === 'granted') {
  new Notification('__ICON__ __KIND__ 시그널 발생', { body: '__BODY__' });
}
try {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const beep = (t, f) => {
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = f; g.gain.value = 0.15;
    o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.15);
  };
  __TONES__
} catch (e) {}
</script>"""
                .replace("__BODY__", msg_body)
                .replace("__KIND__", latest_kind)
                .replace("__ICON__", icon)
                .replace("__TONES__", tones), height=0)

    last_buy_str = _kst_str(buy_times[-1]) if len(buy_times) > 0 else "-"
    last_sell_str = _kst_str(sell_times[-1]) if len(sell_times) > 0 else "-"
    st.caption(f"🔄 알림 작동 중 · 마지막 확인 {now_kst} (KST) · "
               f"마지막 매수 시그널: {last_buy_str} · 마지막 매도 시그널: {last_sell_str} · "
               f"⚠️ 이 탭을 열어둔 동안에만 알림이 옵니다")

last_close = float(df["Close"].iloc[-1])
prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else last_close
change = last_close - prev_close
change_pct = (change / prev_close * 100) if prev_close else 0.0

c1, c2, c3 = st.columns(3)
c1.metric(f"{pair_label} 현재가", f"{last_close:,.2f}", f"{change:+,.2f} ({change_pct:+.2f}%)")
c2.metric("RSI(14)", f"{df['RSI'].iloc[-1]:.1f}" if pd.notna(df['RSI'].iloc[-1]) else "-")
sma20_last = df["SMA20"].iloc[-1]
c3.metric("SMA 20", f"{sma20_last:,.2f}" if pd.notna(sma20_last) else "-")

# -------------------------------------------------------------
# 4.5 지금 상태 자동 해석 + 쉬운 지표 설명
# -------------------------------------------------------------
rsi_val = float(df["RSI"].iloc[-1]) if pd.notna(df["RSI"].iloc[-1]) else None

if rsi_val is not None and pd.notna(sma20_last):
    diff = last_close - float(sma20_last)
    near_avg = abs(diff) / last_close < 0.001  # 0.1% 이내면 평균에 붙은 것으로 간주

    # RSI 항목: (짧은 판정, 설명)
    if rsi_val >= 70:
        rsi_short, rsi_desc = "과매수 ⚠️", f"{rsi_val:.1f} → 70 이상, 단기 과열 상태"
    elif rsi_val <= 30:
        rsi_short, rsi_desc = "과매도 ⚠️", f"{rsi_val:.1f} → 30 이하, 단기 과락 상태"
    elif rsi_val >= 55:
        rsi_short, rsi_desc = "상승 우위", f"{rsi_val:.1f} → 오르는 힘이 조금 우세"
    elif rsi_val <= 45:
        rsi_short, rsi_desc = "하락 우위", f"{rsi_val:.1f} → 내리는 힘이 조금 우세"
    else:
        rsi_short, rsi_desc = "중립", f"{rsi_val:.1f} → 50 부근, 힘의 균형 상태"

    # 이동평균 항목
    if near_avg:
        sma_short, sma_desc = "방향 미정", f"현재가 ≈ 20평균 ({diff:+.2f}원 차이)"
    elif diff > 0:
        sma_short, sma_desc = "상승 흐름", f"현재가가 20평균보다 {diff:+.2f}원 위"
    else:
        sma_short, sma_desc = "하락 흐름", f"현재가가 20평균보다 {diff:+.2f}원 아래"

    # 종합 판정 (두 지표를 합쳐 한 마디로)
    if rsi_val >= 70:
        verdict, v_color = "🔴 과열 주의", "#fdecea"
    elif rsi_val <= 30:
        verdict, v_color = "🔵 과락 — 반등 주시", "#e8f0fe"
    elif not near_avg and diff > 0 and rsi_val >= 55:
        verdict, v_color = "🔴 상승 우위", "#fdecea"
    elif not near_avg and diff < 0 and rsi_val <= 45:
        verdict, v_color = "🔵 하락 우위", "#e8f0fe"
    else:
        verdict, v_color = "🟡 관망 구간 — 뚜렷한 신호 없음", "#fff8e1"

    st.markdown(f"""
<div style="border:1px solid #dbe4f0; background:#f7faff; border-radius:10px;
            padding:12px 16px; margin:4px 0 8px 0; line-height:1.7; color:#1f2937;">
  <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:6px;">
    <span style="font-weight:600; color:#1f2937;">💡 지금 읽기</span>
    <span style="background:{v_color}; padding:2px 12px; border-radius:12px;
                 font-size:14px; font-weight:600; color:#1f2937;">{verdict}</span>
  </div>
  <div style="font-size:14px; color:#1f2937;">
    <b>이동평균</b> · <b>{sma_short}</b> — {sma_desc}<br>
    <b>RSI</b> · <b>{rsi_short}</b> — {rsi_desc}
  </div>
</div>
""", unsafe_allow_html=True)

with st.expander("📖 지표 쉽게 이해하기 (처음이라면 눌러보세요)"):
    st.markdown("""
**현재가** — 지금 1달러를 사는 데 필요한 원화 금액입니다. 아래 빨간/초록 숫자는 직전 봉 대비 변화폭이에요.

**SMA 20 (20기간 이동평균)** — 최근 20개 봉의 환율을 평균 낸 값으로, 단위는 현재가와 같은 '원'입니다.
가격의 잔잔한 흔들림을 걸러낸 **요즘 환율의 평균 자리**라고 보면 됩니다.
현재가가 이 값보다 확실히 위면 단기 상승 흐름, 아래면 하락 흐름으로 읽습니다.
차트의 주황 선이 SMA 20, 보라 선은 더 긴 SMA 50입니다.

**RSI(14)** — 최근 14개 봉 동안 오른 힘과 내린 힘의 비율을 0~100으로 나타낸 지표입니다.
**70 이상**이면 너무 가파르게 올라 쉬어갈 수 있는 '과매수', **30 이하**면 너무 빠져 반등이 나올 수 있는 '과매도', 50 부근이면 중립입니다.

**시그널 화살표** — 🔴 매수: RSI가 30을 다시 뚫고 올라오는 순간(과락 회복 시작) · 🔵 매도: RSI가 70 밑으로 내려오는 순간(과열 진정 시작)

⚠️ 이 지표들은 모두 과거 가격의 요약이며 미래를 보장하지 않습니다.
특히 강한 추세장에서는 RSI가 70 위에 오래 머물며 계속 오르기도 하니, 참고 도구로만 활용하세요.
""")

# -------------------------------------------------------------
# 4. 차트 데이터 → lightweight-charts용 JSON 변환
# -------------------------------------------------------------
def to_epoch(idx: pd.DatetimeIndex) -> list:
    """한국시간 기준으로 표시되도록 타임스탬프 변환."""
    if idx.tz is not None:
        idx = idx.tz_convert("Asia/Seoul").tz_localize(None)
    return ((idx - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")).tolist()


def series_json(times, values):
    return [
        {"time": t, "value": round(float(v), 4)}
        for t, v in zip(times, values) if pd.notna(v)
    ]


times = to_epoch(df.index)

candles = [
    {"time": t, "open": round(float(o), 4), "high": round(float(h), 4),
     "low": round(float(l), 4), "close": round(float(c), 4)}
    for t, o, h, l, c in zip(times, df["Open"], df["High"], df["Low"], df["Close"])
    if pd.notna(o) and pd.notna(h) and pd.notna(l) and pd.notna(c)
]

hist_json = [
    {"time": t, "value": round(float(v), 5),
     "color": "rgba(210,79,69,0.55)" if v >= 0 else "rgba(18,97,196,0.55)"}
    for t, v in zip(times, df["MACD_HIST"]) if pd.notna(v)
]

markers = []
for t, is_buy, is_sell in zip(times, df["BUY"], df["SELL"]):
    if is_buy:
        markers.append({"time": t, "position": "belowBar", "color": "#d24f45",
                        "shape": "arrowUp", "text": "매수"})
    elif is_sell:
        markers.append({"time": t, "position": "aboveBar", "color": "#1261c4",
                        "shape": "arrowDown", "text": "매도"})

payload = json.dumps({
    "candles": candles,
    "sma20": series_json(times, df["SMA20"]),
    "sma50": series_json(times, df["SMA50"]),
    "bbUp": series_json(times, df["BB_UP"]),
    "bbDn": series_json(times, df["BB_DN"]),
    "rsi": series_json(times, df["RSI"]),
    "macd": series_json(times, df["MACD"]),
    "macdSig": series_json(times, df["MACD_SIG"]),
    "hist": hist_json,
    "markers": markers,
    "lastClose": round(last_close, 4),
    "intraday": is_intraday,
    "title": f"{pair_label} · {tf_label}",
})

CHART_HTML = """
<style>
@media (max-width: 640px) {
  #wrap { padding: 0 12px; }
}
</style>
<div id="wrap" style="position:relative; width:100%; height:700px; font-family:sans-serif; box-sizing:border-box;">
  <div id="legend" style="position:absolute; top:8px; left:12px; z-index:10;
       font-size:13px; color:#333; background:rgba(255,255,255,0.85);
       padding:4px 8px; border-radius:6px; line-height:1.6;"></div>
  <div id="chart" style="width:100%; height:100%;"></div>
</div>
<script src="https://unpkg.com/lightweight-charts@5.0.8/dist/lightweight-charts.standalone.production.js"></script>
<script>
const D = __PAYLOAD__;
const LWC = LightweightCharts;

const chart = LWC.createChart(document.getElementById('chart'), {
  autoSize: true,
  layout: {
    background: { color: '#ffffff' }, textColor: '#333',
    panes: { separatorColor: '#e6e6e6', enableResize: true },
  },
  grid: { vertLines: { color: '#f2f3f5' }, horzLines: { color: '#f2f3f5' } },
  crosshair: {
    mode: LWC.CrosshairMode.Normal,
    vertLine: { color: '#9aa0a6', style: 3, labelBackgroundColor: '#4c525e' },
    horzLine: { color: '#9aa0a6', style: 3, labelBackgroundColor: '#4c525e' },
  },
  rightPriceScale: { borderColor: '#d9d9d9' },
  timeScale: {
    borderColor: '#d9d9d9',
    timeVisible: D.intraday, secondsVisible: false,
    rightOffset: 6, barSpacing: 8, minBarSpacing: 1,
  },
  handleScroll: {
    mouseWheel: true, pressedMouseMove: true,
    horzTouchDrag: true,
    vertTouchDrag: false,
  },
  localization: { locale: 'ko-KR' },
});

// ---- 메인: 캔들 (업비트 색상: 상승 빨강 / 하락 파랑) ----
const candle = chart.addSeries(LWC.CandlestickSeries, {
  upColor: '#d24f45', downColor: '#1261c4',
  borderUpColor: '#d24f45', borderDownColor: '#1261c4',
  wickUpColor: '#d24f45', wickDownColor: '#1261c4',
});
candle.setData(D.candles);

const mkLine = (data, color, width, pane, dashed) => {
  const s = chart.addSeries(LWC.LineSeries, {
    color: color, lineWidth: width,
    lineStyle: dashed ? 2 : 0,
    priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false,
  }, pane || 0);
  s.setData(data);
  return s;
};

mkLine(D.sma20, '#f0a12c', 2, 0, false);
mkLine(D.sma50, '#8153d7', 2, 0, false);
mkLine(D.bbUp, '#a8b0ba', 1, 0, true);
mkLine(D.bbDn, '#a8b0ba', 1, 0, true);

// 현재가 점선 + 우측 가격 라벨
candle.createPriceLine({
  price: D.lastClose, color: '#d24f45', lineWidth: 1, lineStyle: 2,
  axisLabelVisible: true, title: '현재가',
});

// 매수/매도 화살표 마커
if (LWC.createSeriesMarkers) { LWC.createSeriesMarkers(candle, D.markers); }
else if (candle.setMarkers) { candle.setMarkers(D.markers); }

// ---- 보조지표 1: RSI ----
const rsi = chart.addSeries(LWC.LineSeries, {
  color: '#2c7fb8', lineWidth: 2, priceLineVisible: false,
}, 1);
rsi.setData(D.rsi);
rsi.createPriceLine({ price: 70, color: '#d24f45', lineWidth: 1, lineStyle: 2, title: '70' });
rsi.createPriceLine({ price: 30, color: '#1261c4', lineWidth: 1, lineStyle: 2, title: '30' });

// ---- 보조지표 2: MACD ----
const hist = chart.addSeries(LWC.HistogramSeries, { priceLineVisible: false }, 2);
hist.setData(D.hist);
mkLine(D.macd, '#d24f45', 1, 2, false);
mkLine(D.macdSig, '#1261c4', 1, 2, false);

// 보조지표 창 높이 조절
try {
  const panes = chart.panes();
  if (panes[1]) panes[1].setHeight(120);
  if (panes[2]) panes[2].setHeight(120);
} catch (e) {}

// ---- 좌상단 실시간 시세 레전드 (업비트 스타일) ----
const legend = document.getElementById('legend');
const fmt = (v) => v == null ? '-' : v.toLocaleString('ko-KR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
function renderLegend(bar) {
  if (!bar) { legend.innerHTML = '<b>' + D.title + '</b>'; return; }
  const up = bar.close >= bar.open;
  const col = up ? '#d24f45' : '#1261c4';
  const pct = ((bar.close - bar.open) / bar.open * 100).toFixed(2);
  legend.innerHTML = '<b>' + D.title + '</b><br>'
    + '시 <span style="color:' + col + '">' + fmt(bar.open) + '</span> '
    + '고 <span style="color:#d24f45">' + fmt(bar.high) + '</span> '
    + '저 <span style="color:#1261c4">' + fmt(bar.low) + '</span> '
    + '종 <span style="color:' + col + ';font-weight:600">' + fmt(bar.close) + '</span> '
    + '<span style="color:' + col + '">(' + (pct >= 0 ? '+' : '') + pct + '%)</span>';
}
renderLegend(D.candles[D.candles.length - 1]);
chart.subscribeCrosshairMove((param) => {
  const bar = param && param.seriesData ? param.seriesData.get(candle) : null;
  renderLegend(bar || D.candles[D.candles.length - 1]);
});

// 최근 봉 위주로 시작 (드래그/휠로 자유 탐색)
const n = D.candles.length;
if (n > 150) {
  chart.timeScale().setVisibleLogicalRange({ from: n - 150, to: n + 6 });
} else {
  chart.timeScale().fitContent();
}
</script>
"""

# -------------------------------------------------------------
# 4.7 테더-환율 갭 차트 템플릿 (0선 기준 위 빨강 / 아래 파랑)
# -------------------------------------------------------------
GAP_HTML = """
<style>
@media (max-width: 640px) {
  #gwrap { padding: 0 12px; }
}
</style>
<div id="gwrap" style="position:relative; width:100%; height:520px; font-family:sans-serif; box-sizing:border-box;">
  <div id="glegend" style="position:absolute; top:8px; left:12px; z-index:10;
       font-size:13px; color:#333; background:rgba(255,255,255,0.85);
       padding:4px 8px; border-radius:6px; line-height:1.6;"></div>
  <div id="gchart" style="width:100%; height:100%;"></div>
</div>
<script src="https://unpkg.com/lightweight-charts@5.0.8/dist/lightweight-charts.standalone.production.js"></script>
<script>
const D = __PAYLOAD__;
const LWC = LightweightCharts;

const chart = LWC.createChart(document.getElementById('gchart'), {
  autoSize: true,
  layout: {
    background: { color: '#ffffff' }, textColor: '#333',
    panes: { separatorColor: '#e6e6e6', enableResize: true },
  },
  grid: { vertLines: { color: '#f2f3f5' }, horzLines: { color: '#f2f3f5' } },
  crosshair: {
    mode: LWC.CrosshairMode.Normal,
    vertLine: { color: '#9aa0a6', style: 3, labelBackgroundColor: '#4c525e' },
    horzLine: { color: '#9aa0a6', style: 3, labelBackgroundColor: '#4c525e' },
  },
  rightPriceScale: { borderColor: '#d9d9d9' },
  timeScale: {
    borderColor: '#d9d9d9', timeVisible: true, secondsVisible: false,
    rightOffset: 6, barSpacing: 8, minBarSpacing: 1,
  },
  handleScroll: {
    mouseWheel: true, pressedMouseMove: true,
    horzTouchDrag: true, vertTouchDrag: false,
  },
  localization: { locale: 'ko-KR' },
});

const baseOpts = (fmt, extras) => ({
  baseValue: { type: 'price', price: 0 },
  topLineColor: '#d24f45',
  topFillColor1: 'rgba(210,79,69,0.35)',
  topFillColor2: 'rgba(210,79,69,0.06)',
  bottomLineColor: '#1261c4',
  bottomFillColor1: 'rgba(18,97,196,0.06)',
  bottomFillColor2: 'rgba(18,97,196,0.35)',
  lineWidth: 3,
  priceFormat: fmt,
  priceLineVisible: false,
  autoscaleInfoProvider: (original) => {
    const res = original();
    if (res && res.priceRange) {
      let mn = Math.min(res.priceRange.minValue, 0);
      let mx = Math.max(res.priceRange.maxValue, 0);
      (extras || []).forEach((v) => {
        mn = Math.min(mn, v); mx = Math.max(mx, v);
      });
      const span = mx - mn;
      res.priceRange.minValue = mn - span * 0.08;
      res.priceRange.maxValue = mx + span * 0.08;
    }
    return res;
  },
});

// 위: 갭 (원) / 아래: 갭 (%)
const gapS = chart.addSeries(LWC.BaselineSeries,
  baseOpts({ type: 'price', precision: 2, minMove: 0.01 }, D.levels), 0);
gapS.setData(D.gap);
gapS.createPriceLine({ price: 0, color: '#5f5e5a', lineWidth: 2,
  lineStyle: 0, axisLabelVisible: true, title: '기준 0' });

const pctS = chart.addSeries(LWC.BaselineSeries,
  baseOpts({ type: 'price', precision: 3, minMove: 0.001 }, D.pctLevels), 1);
pctS.setData(D.pct);
pctS.createPriceLine({ price: 0, color: '#5f5e5a', lineWidth: 2,
  lineStyle: 0, axisLabelVisible: true, title: '기준 0' });

// 사용자 지정 기준선 (트레이딩뷰 노란 수평선 스타일)
(D.levels || []).forEach((v) => {
  gapS.createPriceLine({ price: v, color: '#e0a800', lineWidth: 2,
    lineStyle: 0, axisLabelVisible: true, title: '기준선' });
});
(D.pctLevels || []).forEach((v) => {
  pctS.createPriceLine({ price: v, color: '#e0a800', lineWidth: 2,
    lineStyle: 0, axisLabelVisible: true, title: '기준선' });
});

try {
  const panes = chart.panes();
  if (panes[1]) panes[1].setHeight(170);
} catch (e) {}

const legend = document.getElementById('glegend');
function renderLegend(g, p) {
  const col = (v) => v >= 0 ? '#d24f45' : '#1261c4';
  const gs = (g == null) ? '-' : (g >= 0 ? '+' : '') + g.toFixed(2) + '원';
  const ps = (p == null) ? '-' : (p >= 0 ? '+' : '') + p.toFixed(3) + '%';
  legend.innerHTML = '<b>테더 − 환율 갭</b><br>'
    + '갭 <span style="color:' + col(g ?? 0) + ';font-weight:600">' + gs + '</span> · '
    + '<span style="color:' + col(p ?? 0) + ';font-weight:600">' + ps + '</span>';
}
renderLegend(D.lastGap, D.lastPct);
chart.subscribeCrosshairMove((param) => {
  let g = null, p = null;
  if (param && param.seriesData) {
    const gd = param.seriesData.get(gapS);
    const pd = param.seriesData.get(pctS);
    if (gd) g = gd.value; if (pd) p = pd.value;
  }
  if (g == null && p == null) renderLegend(D.lastGap, D.lastPct);
  else renderLegend(g, p);
});

const n = D.gap.length;
if (n > 180) {
  chart.timeScale().setVisibleLogicalRange({ from: n - 180, to: n + 6 });
} else {
  chart.timeScale().fitContent();
}
</script>
"""

# -------------------------------------------------------------
# 5. 메인 탭
# -------------------------------------------------------------
tab_gap, tab1, tab2 = st.tabs(
    ["🪙 테더-환율 갭", "📈 환율 분석 차트", "🌍 거시경제 & 뉴스"])

# ===================== [탭 1] 차트 =====================
with tab1:
    components.html(CHART_HTML.replace("__PAYLOAD__", payload), height=710)
    st.caption(
        "🖱️ **조작법** · 차트 드래그: 좌우 이동 · 마우스 휠: 확대/축소 · "
        "하단 시간축을 잡고 좌우로 드래그: 봉 간격 늘리기/줄이기 · "
        "우측 가격축 드래그: 가격 범위 조절 · 시간축 더블클릭: 전체 보기"
    )
    st.info(
        "📌 **시그널 규칙** · 매수(▲): RSI가 30을 상향 돌파(과매도 탈출) · "
        "매도(▼): RSI가 70을 하향 돌파(과매수 이탈)"
    )

# ===================== [탭: 테더-환율 갭] =====================
with tab_gap:
    st.subheader("🪙 테더(USDT/KRW) − 환율(USD/KRW) 갭")

    GAP_CONF = {
        "1분봉": ("1m", "1m", "5d", 720),
        "5분봉": ("5m", "5m", "5d", 864),
        "30분봉": ("30m", "30m", "1mo", 700),
        "1시간봉": ("1h", "1h", "1mo", 700),
    }
    g_label = st.radio("봉 종류", list(GAP_CONF.keys()), horizontal=True,
                       key="gap_interval")
    bit_code, g_yf_iv, g_yf_period, g_tail = GAP_CONF[g_label]

    level_input = st.text_input(
        "📏 기준선 (원 단위 · 콤마로 여러 개 입력 가능)",
        value="", placeholder="예: -15, -20", key="gap_levels",
        help="입력한 값 위치에 노란 수평선이 그려집니다. 아래 % 차트에도 같은 위치가 자동 환산되어 표시돼요.",
    )
    user_levels = []
    for tok in level_input.replace(" ", "").split(","):
        if tok:
            try:
                user_levels.append(float(tok))
            except ValueError:
                pass

    try:
        usdt = load_bithumb_usdt(bit_code)
    except Exception:
        usdt = pd.DataFrame()
    fx_g = safe_load("USDKRW=X", g_yf_period, g_yf_iv)

    if usdt.empty:
        st.warning("빗썸 테더 시세를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
    elif fx_g.empty:
        st.warning("환율 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
    else:
        # 테더(24시간 거래)와 환율(평일만 거래)을 시간 기준으로 맞춤
        # 환율이 비는 시간(밤/주말)은 마지막 환율을 그대로 사용
        merged = pd.concat(
            [usdt["close"].rename("usdt"), fx_g["Close"].rename("fx")],
            axis=1).sort_index()
        merged["fx"] = merged["fx"].ffill()
        merged = merged.dropna()
        merged = merged[merged.index.isin(usdt.index)].tail(g_tail)

        if len(merged) < 5:
            st.warning("두 데이터의 겹치는 구간이 부족합니다. 다른 봉 종류를 선택해보세요.")
        else:
            merged["gap"] = merged["usdt"] - merged["fx"]              # 수식 1: 원
            merged["pct"] = merged["gap"] / merged["fx"] * 100         # 수식 2: %

            last_usdt = float(merged["usdt"].iloc[-1])
            last_fx = float(merged["fx"].iloc[-1])
            last_gap = float(merged["gap"].iloc[-1])
            last_pct = float(merged["pct"].iloc[-1])

            g1, g2, g3, g4 = st.columns(4)
            g1.metric("테더 (빗썸)", f"{last_usdt:,.2f}원")
            g2.metric("환율 (USD/KRW)", f"{last_fx:,.2f}원")
            g3.metric("갭 (원)", f"{last_gap:+,.2f}원")
            g4.metric("갭 (%)", f"{last_pct:+.3f}%")

            g_times = to_epoch(merged.index)
            g_payload = json.dumps({
                "gap": series_json(g_times, merged["gap"]),
                "pct": series_json(g_times, merged["pct"]),
                "lastGap": round(last_gap, 2),
                "lastPct": round(last_pct, 3),
                "levels": [round(v, 2) for v in user_levels],
                "pctLevels": [round(v / last_fx * 100, 3) for v in user_levels],
            })
            components.html(GAP_HTML.replace("__PAYLOAD__", g_payload), height=530)

            st.caption(
                "🖱️ 위 차트: 갭(원) = 빗썸 테더가 − 환율 · 아래 차트: 갭(%) = 갭 ÷ 환율 × 100 · "
                "0선 위(빨강)는 테더가 환율보다 비싼 프리미엄, 아래(파랑)는 디스카운트"
            )
            st.info(
                "📌 환율은 평일 장중에만 움직이므로, 밤·주말에는 마지막 환율을 기준으로 갭을 계산합니다. "
                "테더 시세는 빗썸 실시간, 환율은 야후 지연 시세라 트레이딩뷰 수치와 소수점 수준의 차이가 있을 수 있어요."
            )

# ===================== [탭 2] 거시경제 & 뉴스 =====================
with tab2:
    st.subheader("달러 인덱스(DXY) vs 미 10년물 국채금리(TNX)")

    if is_intraday:
        m_period, m_interval = "3mo", "1d"
    elif tf["interval"] in ("1wk", "1mo"):
        m_period, m_interval = ("3y", "1wk")
    else:
        m_period, m_interval = tf["period"], "1d"

    dxy = safe_load("DX-Y.NYB", m_period, m_interval)
    tnx = safe_load("^TNX", m_period, m_interval)

    if dxy.empty or tnx.empty:
        st.warning("거시경제 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
    else:
        mfig = make_subplots(specs=[[{"secondary_y": True}]])
        mfig.add_trace(go.Scatter(x=dxy.index, y=dxy["Close"], name="달러 인덱스 (DXY)",
                                  line=dict(color="#1261c4", width=2)), secondary_y=False)
        mfig.add_trace(go.Scatter(x=tnx.index, y=tnx["Close"], name="미 10년물 금리 (%)",
                                  line=dict(color="#d24f45", width=2, dash="dash")), secondary_y=True)
        mfig.update_yaxes(title_text="달러 인덱스", secondary_y=False)
        mfig.update_yaxes(title_text="10년물 금리 (%)", secondary_y=True)
        mfig.update_layout(height=450, legend=dict(orientation="h", y=1.1),
                           margin=dict(l=10, r=10, t=40, b=10),
                           hovermode="x unified", hoverlabel=dict(font_size=12))
        mfig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])],
                          showspikes=True, spikemode="across", spikesnap="cursor",
                          spikedash="dot", spikethickness=1, spikecolor="#888",
                          tickformat="%Y-%m-%d")
        st.plotly_chart(mfig, use_container_width=True)

        mc1, mc2 = st.columns(2)
        mc1.metric("달러 인덱스 (DXY)", f"{float(dxy['Close'].iloc[-1]):.2f}")
        mc2.metric("미 10년물 금리", f"{float(tnx['Close'].iloc[-1]):.3f}%")

    st.divider()
    st.subheader("📰 국내 경제 뉴스 헤드라인")
    st.caption("연합뉴스·매일경제·한국경제 경제 섹션 · 환율/금리 관련 기사가 위쪽에 표시됩니다")
    try:
        news, fx_count = load_news()
    except Exception:
        news, fx_count = [], 0
    if not news:
        st.caption("현재 표시할 뉴스가 없습니다. 잠시 후 다시 확인해주세요.")
    else:
        for i, n in enumerate(news):
            tag = "💱 " if i < fx_count else ""
            if n["link"]:
                st.markdown(f"- {tag}[{n['title']}]({n['link']}) — *{n['src']}*")
            else:
                st.markdown(f"- {tag}{n['title']} — *{n['src']}*")

st.divider()
st.caption("⚠️ 본 대시보드는 학습·참고용이며 투자 손실에 대한 책임은 이용자 본인에게 있습니다.")
