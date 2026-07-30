# =============================================================
#  FX 환율 웹 대시보드 v3
#  - 차트: TradingView lightweight-charts (업비트 스타일 조작감)
#  - 봉 종류: 1분/5분/15분/30분/1시간/일/주/월
#  - 백테스팅: 환선물(달러선물) 계약 수 기반 손익 계산
# =============================================================

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

st.set_page_config(page_title="FX 환율 대시보드", page_icon="💱", layout="wide")

st.title("💱 FX 환율 기술적 분석 대시보드")
st.caption("데이터 출처: Yahoo Finance (yfinance) · 지연 시세이며 투자 조언이 아닙니다.")

# -------------------------------------------------------------
# 1. 사이드바
# -------------------------------------------------------------
PAIRS = {
    "원/달러 (USD/KRW)": "USDKRW=X",
    "엔/달러 (USD/JPY)": "USDJPY=X",
    "유로/달러 (EUR/USD)": "EURUSD=X",
}

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

    pair_label = st.selectbox("환율 종류", list(PAIRS.keys()))

    st.subheader("🕐 타임프레임")
    iv_label = st.selectbox("봉 종류", list(INTERVALS.keys()), index=5)
    iv_conf = INTERVALS[iv_label]
    period_options = list(iv_conf["periods"].keys())
    period_label = st.selectbox(
        "조회 기간", period_options,
        index=period_options.index(iv_conf["default"]),
        help="분봉은 야후 파이낸스 정책상 최근 데이터만 조회 가능해 선택지가 자동 제한됩니다. (1분봉: 최대 7일)",
    )

    st.divider()
    st.subheader("🧪 백테스팅 파라미터")
    st.caption("환선물(예: KRX 미국달러선물) 기준")

    initial_capital = st.number_input(
        "초기 자본금 (원)", min_value=1_000_000, value=100_000_000, step=10_000_000,
        help="백테스팅 시작 시점의 계좌 잔고. 수익률(%)과 자산 곡선의 기준이 됩니다.",
    )
    contracts = st.number_input(
        "계약 수", min_value=1, value=200, step=10,
        help="한 번 진입할 때 매수하는 선물 계약 수.",
    )
    multiplier = st.number_input(
        "계약 승수 (1포인트당 원)", min_value=1, value=10_000, step=1_000,
        help="KRX 미국달러선물은 계약당 US$10,000 → 환율 1원 변동 시 계약당 10,000원 손익.",
    )
    stop_loss_pct = st.slider(
        "손절 기준 (%)", 0.1, 10.0, 1.0, 0.1,
        help="진입가 대비 이 비율만큼 하락하면 자동 청산 (손실 확정).",
    )
    take_profit_pct = st.slider(
        "익절 기준 (%)", 0.1, 20.0, 2.0, 0.1,
        help="진입가 대비 이 비율만큼 상승하면 자동 청산 (이익 확정).",
    )

ticker = PAIRS[pair_label]
tf = {"period": iv_conf["periods"][period_label], "interval": iv_conf["interval"]}
tf_label = f"{period_label} · {iv_label}"
is_intraday = tf["interval"] in ("1m", "5m", "15m", "30m", "1h")

# -------------------------------------------------------------
# 2. 데이터 수집
# -------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_data(symbol: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval=interval,
                     auto_adjust=True, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])


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


with st.spinner("환율 데이터를 불러오는 중..."):
    df = load_data(ticker, tf["period"], tf["interval"])

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

last_close = float(df["Close"].iloc[-1])
prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else last_close
change = last_close - prev_close
change_pct = (change / prev_close * 100) if prev_close else 0.0

c1, c2, c3, c4 = st.columns(4)
c1.metric(f"{pair_label} 현재가", f"{last_close:,.2f}", f"{change:+,.2f} ({change_pct:+.2f}%)")
c2.metric("RSI(14)", f"{df['RSI'].iloc[-1]:.1f}" if pd.notna(df['RSI'].iloc[-1]) else "-")
sma20_last = df["SMA20"].iloc[-1]
c3.metric("SMA 20", f"{sma20_last:,.2f}" if pd.notna(sma20_last) else "-")
c4.metric("1원 변동 손익", f"{multiplier * contracts:+,.0f}원",
          f"{contracts}계약 × 승수 {multiplier:,}")

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
tab1, tab2, tab3 = st.tabs(["📈 FX 기술적 분석 차트", "🌍 거시경제 & 뉴스", "🧪 백테스팅 리포트"])

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
        "매도(▼): RSI가 70을 하향 돌파(과매수 이탈). 탭 3의 백테스팅에 동일하게 사용됩니다."
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

    dxy = load_data("DX-Y.NYB", m_period, m_interval)
    tnx = load_data("^TNX", m_period, m_interval)

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

# ===================== [탭 3] 백테스팅 =====================
with tab3:
    st.subheader("🧪 환선물 RSI 전략 백테스팅")

    notional = last_close * multiplier * contracts
    st.caption(
        f"전략: 매수 시그널에서 {contracts}계약 진입 → 매도 시그널 / 손절 -{stop_loss_pct}% / "
        f"익절 +{take_profit_pct}% 중 먼저 도달 시 청산 (롱 온리 · 수수료/증거금/슬리피지 미반영) · "
        f"현재가 기준 명목 계약금액: 약 {notional:,.0f}원"
    )

    def run_backtest(data, capital, n_contracts, mult, sl, tp):
        equity = float(capital)
        in_pos, entry = False, None
        curve, trades = [], []

        for _, row in data.iterrows():
            price = row["Close"]
            if pd.isna(price):
                curve.append(equity)
                continue

            if not in_pos:
                if bool(row["BUY"]):
                    in_pos, entry = True, price
            else:
                ret = (price - entry) / entry * 100
                if bool(row["SELL"]) or ret <= -sl or ret >= tp:
                    pnl = (price - entry) * mult * n_contracts
                    equity += pnl
                    trades.append({"ret": ret, "pnl": pnl,
                                   "entry": entry, "exit": price})
                    in_pos, entry = False, None

            open_pnl = (price - entry) * mult * n_contracts if in_pos else 0.0
            curve.append(equity + open_pnl)

        if in_pos:
            price = data["Close"].iloc[-1]
            pnl = (price - entry) * mult * n_contracts
            equity += pnl
            trades.append({"ret": (price - entry) / entry * 100, "pnl": pnl,
                           "entry": entry, "exit": price})

        eq = pd.Series(curve, index=data.index, name="Equity")
        return eq, trades

    equity, trades = run_backtest(df, initial_capital, contracts, multiplier,
                                  stop_loss_pct, take_profit_pct)

    if len(trades) == 0:
        st.warning("이 기간/봉 종류에서 발생한 거래가 없습니다. 타임프레임을 바꿔보세요.")
    else:
        final_equity = float(equity.iloc[-1])
        total_pnl = final_equity - initial_capital
        total_return = total_pnl / initial_capital * 100
        wins = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = wins / len(trades) * 100
        running_max = equity.cummax()
        mdd = float(((equity - running_max) / running_max * 100).min())

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("총 손익", f"{total_pnl:+,.0f}원")
        k2.metric("수익률", f"{total_return:+.2f}%")
        k3.metric("승률", f"{win_rate:.1f}%")
        k4.metric("MDD", f"{mdd:.2f}%")
        k5.metric("거래 횟수", f"{len(trades)}회")

        efig = go.Figure()
        efig.add_trace(go.Scatter(x=equity.index, y=equity.values, name="누적 자산",
                                  line=dict(color="#2ca02c", width=2),
                                  fill="tozeroy", fillcolor="rgba(44,160,44,0.08)"))
        efig.add_hline(y=initial_capital, line_dash="dash", line_color="gray",
                       annotation_text="초기 자본금")
        efig.update_layout(title="누적 자산 변화 (Equity Curve)", height=420,
                           margin=dict(l=10, r=10, t=50, b=10), yaxis_title="자산 (원)",
                           hovermode="x unified")
        st.plotly_chart(efig, use_container_width=True)

        with st.expander("개별 거래 내역 보기"):
            trade_df = pd.DataFrame({
                "거래": range(1, len(trades) + 1),
                "진입가": [round(t["entry"], 2) for t in trades],
                "청산가": [round(t["exit"], 2) for t in trades],
                "변동률 (%)": [round(t["ret"], 2) for t in trades],
                "손익 (원)": [round(t["pnl"]) for t in trades],
                "결과": ["✅ 이익" if t["pnl"] > 0 else "❌ 손실" for t in trades],
            })
            st.dataframe(trade_df, use_container_width=True, hide_index=True)

st.divider()
st.caption("⚠️ 본 대시보드는 학습·참고용이며 투자 손실에 대한 책임은 이용자 본인에게 있습니다.")
