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


@st.cache_data(ttl=1800, show_spinner=False)
def load_news(symbols: list) -> list:
    items = []
    for sym in symbols:
        try:
            raw = yf.Ticker(sym).news or []
        except Exception:
            raw = []
        for n in raw:
            content = n.get("content", n)
            title = content.get("title")
            if not title:
                continue
            link = ""
            url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl")
            if isinstance(url_obj, dict):
                link = url_obj.get("url", "")
            elif isinstance(content.get("link"), str):
                link = content["link"]
            publisher = ""
            prov = content.get("provider")
            if isinstance(prov, dict):
                publisher = prov.get("displayName", "")
            elif isinstance(n.get("publisher"), str):
                publisher = n["publisher"]
            items.append({"title": title, "link": link, "publisher": publisher})
    seen, unique = set(), []
    for it in items:
        if it["title"] not in seen:
            seen.add(it["title"])
            unique.append(it)
    return unique[:15]


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
<div id="wrap" style="position:relative; width:100%; height:700px; font-family:sans-serif;">
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
# 5. 메인 탭
# -------------------------------------------------------------
tab1, tab2 = st.tabs(["📈 FX 기술적 분석 차트", "🌍 거시경제 & 뉴스"])

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
    st.subheader("📰 관련 뉴스 헤드라인")
    news = load_news([ticker, "DX-Y.NYB", "^TNX"])
    if not news:
        st.caption("현재 표시할 뉴스가 없습니다.")
    else:
        for n in news:
            pub = f" — *{n['publisher']}*" if n["publisher"] else ""
            if n["link"]:
                st.markdown(f"- [{n['title']}]({n['link']}){pub}")
            else:
                st.markdown(f"- {n['title']}{pub}")

st.divider()
st.caption("⚠️ 본 대시보드는 학습·참고용이며 투자 손실에 대한 책임은 이용자 본인에게 있습니다.")
