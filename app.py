# =============================================================
#  FX 환율 웹 대시보드 (Streamlit + yfinance + Plotly)
#  - 설치 없이 Streamlit Community Cloud에서 바로 작동
#  - 기술적 지표는 pandas로 직접 계산 (외부 지표 라이브러리 불필요)
# =============================================================

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import yfinance as yf

# -------------------------------------------------------------
# 0. 기본 페이지 설정
# -------------------------------------------------------------
st.set_page_config(
    page_title="FX 환율 대시보드",
    page_icon="💱",
    layout="wide",
)

st.title("💱 FX 환율 기술적 분석 대시보드")
st.caption("데이터 출처: Yahoo Finance (yfinance) · 지연 시세이며 투자 조언이 아닙니다.")

# -------------------------------------------------------------
# 1. 사이드바 (환율 / 타임프레임 / 백테스팅 파라미터)
# -------------------------------------------------------------
PAIRS = {
    "원/달러 (USD/KRW)": "USDKRW=X",
    "엔/달러 (USD/JPY)": "USDJPY=X",
    "유로/달러 (EUR/USD)": "EURUSD=X",
}

# 봉 종류별로 yfinance가 허용하는 조회 기간이 다름 (분봉은 최근 데이터만 제공)
INTERVALS = {
    "5분봉":  {"interval": "5m",  "periods": {"1일": "1d", "5일": "5d", "1개월": "1mo"},          "default": "5일"},
    "15분봉": {"interval": "15m", "periods": {"5일": "5d", "1개월": "1mo", "2개월": "60d"},        "default": "5일"},
    "1시간봉": {"interval": "1h",  "periods": {"1개월": "1mo", "3개월": "3mo", "6개월": "6mo", "1년": "1y"}, "default": "3개월"},
    "일봉":   {"interval": "1d",  "periods": {"3개월": "3mo", "6개월": "6mo", "1년": "1y", "3년": "3y", "5년": "5y"}, "default": "6개월"},
    "주봉":   {"interval": "1wk", "periods": {"1년": "1y", "3년": "3y", "5년": "5y", "10년": "10y"}, "default": "3년"},
}

with st.sidebar:
    st.header("⚙️ 설정")

    pair_label = st.selectbox("환율 종류", list(PAIRS.keys()))

    st.subheader("🕐 타임프레임")
    iv_label = st.selectbox("봉 종류", list(INTERVALS.keys()), index=3)
    iv_conf = INTERVALS[iv_label]
    period_options = list(iv_conf["periods"].keys())
    period_label = st.selectbox(
        "조회 기간", period_options,
        index=period_options.index(iv_conf["default"]),
        help="분봉/시간봉은 야후 파이낸스 정책상 최근 데이터만 조회할 수 있어 기간 선택지가 자동으로 제한됩니다.",
    )

    st.divider()
    st.subheader("🧪 백테스팅 파라미터")
    initial_capital = st.number_input(
        "초기 자본금", min_value=1000, value=10_000_000, step=1_000_000,
        help="원화 기준 등 원하는 통화 단위로 입력하세요."
    )
    stop_loss_pct = st.slider("손절 기준 (%)", 0.5, 10.0, 2.0, 0.5)
    take_profit_pct = st.slider("익절 기준 (%)", 0.5, 20.0, 4.0, 0.5)

ticker = PAIRS[pair_label]
tf = {"period": iv_conf["periods"][period_label], "interval": iv_conf["interval"]}
tf_label = f"{period_label} · {iv_label}"
is_intraday = tf["interval"] in ("5m", "15m", "1h")

# -------------------------------------------------------------
# 2. 데이터 수집 (캐싱 포함)
# -------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def load_data(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """yfinance에서 OHLCV 데이터를 받아 정리해서 반환."""
    df = yf.download(
        symbol, period=period, interval=interval,
        auto_adjust=True, progress=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    # 최신 yfinance는 컬럼이 MultiIndex로 올 수 있음 → 1단계로 평탄화
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna(subset=["Close"])
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def load_news(symbols: list) -> list:
    """여러 티커의 뉴스 헤드라인을 모아 반환 (신/구 yfinance 포맷 모두 대응)."""
    items = []
    for sym in symbols:
        try:
            raw = yf.Ticker(sym).news or []
        except Exception:
            raw = []
        for n in raw:
            # 신버전: n["content"]["title"], 구버전: n["title"]
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
            items.append({"title": title, "link": link, "publisher": publisher, "src": sym})
    # 제목 기준 중복 제거
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
# 3. 기술적 지표 계산 (pandas 직접 계산)
# -------------------------------------------------------------
def add_indicators(data: pd.DataFrame) -> pd.DataFrame:
    d = data.copy()
    close = d["Close"]

    # 이동평균선
    d["SMA20"] = close.rolling(20).mean()
    d["SMA50"] = close.rolling(50).mean()

    # 볼린저밴드 (20, 2σ)
    std20 = close.rolling(20).std()
    d["BB_UP"] = d["SMA20"] + 2 * std20
    d["BB_DN"] = d["SMA20"] - 2 * std20

    # RSI (14, Wilder 방식)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    d["RSI"] = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    d["MACD"] = ema12 - ema26
    d["MACD_SIG"] = d["MACD"].ewm(span=9, adjust=False).mean()
    d["MACD_HIST"] = d["MACD"] - d["MACD_SIG"]

    return d


def add_signals(data: pd.DataFrame) -> pd.DataFrame:
    """매수: RSI가 30을 상향 돌파 & 종가가 SMA20 아래(과매도 반등)
       매도: RSI가 70을 하향 돌파"""
    d = data.copy()
    rsi, prev_rsi = d["RSI"], d["RSI"].shift(1)

    d["BUY"] = (prev_rsi < 30) & (rsi >= 30)
    d["SELL"] = (prev_rsi > 70) & (rsi <= 70)
    return d


df = add_signals(add_indicators(df))

# -------------------------------------------------------------
# 4. 현재가 요약 지표
# -------------------------------------------------------------
last_close = float(df["Close"].iloc[-1])
prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else last_close
change = last_close - prev_close
change_pct = (change / prev_close * 100) if prev_close else 0.0

c1, c2, c3 = st.columns(3)
c1.metric(f"{pair_label} 현재가", f"{last_close:,.2f}", f"{change:+,.2f} ({change_pct:+.2f}%)")
c2.metric("RSI(14)", f"{df['RSI'].iloc[-1]:.1f}" if pd.notna(df['RSI'].iloc[-1]) else "-")
sma20_last = df["SMA20"].iloc[-1]
c3.metric("20기간 이평선", f"{sma20_last:,.2f}" if pd.notna(sma20_last) else "-")

# -------------------------------------------------------------
# 5. 메인 탭 구성
# -------------------------------------------------------------
tab1, tab2, tab3 = st.tabs(["📈 FX 기술적 분석 차트", "🌍 거시경제 & 뉴스", "🧪 백테스팅 리포트"])

# =============================================================
# [탭 1] 기술적 분석 차트
# =============================================================
with tab1:
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03,
        subplot_titles=(f"{pair_label} · {tf_label}", "RSI (14)", "MACD (12, 26, 9)"),
    )

    # --- (1) 캔들스틱 + 이평선 + 볼린저밴드 ---
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
        name="가격", increasing_line_color="#e35b5b", decreasing_line_color="#4a7bd4",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df["SMA20"], name="SMA 20",
                             line=dict(color="orange", width=1.3)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["SMA50"], name="SMA 50",
                             line=dict(color="purple", width=1.3)), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df["BB_UP"], name="볼린저 상단",
                             line=dict(color="gray", width=0.8, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["BB_DN"], name="볼린저 하단",
                             line=dict(color="gray", width=0.8, dash="dot"),
                             fill="tonexty", fillcolor="rgba(128,128,128,0.08)"), row=1, col=1)

    # --- 매수/매도 시그널 마커 ---
    buys = df[df["BUY"]]
    sells = df[df["SELL"]]
    fig.add_trace(go.Scatter(
        x=buys.index, y=buys["Low"] * 0.999, mode="markers+text",
        marker=dict(symbol="triangle-up", size=12, color="green"),
        text=["BUY"] * len(buys), textposition="bottom center",
        textfont=dict(size=9, color="green"), name="매수 시그널",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=sells.index, y=sells["High"] * 1.001, mode="markers+text",
        marker=dict(symbol="triangle-down", size=12, color="red"),
        text=["SELL"] * len(sells), textposition="top center",
        textfont=dict(size=9, color="red"), name="매도 시그널",
    ), row=1, col=1)

    # --- (2) RSI ---
    fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], name="RSI",
                             line=dict(color="#2c7fb8", width=1.5)), row=2, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.5, row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.5, row=2, col=1)

    # --- (3) MACD ---
    hist_colors = np.where(df["MACD_HIST"] >= 0, "rgba(227,91,91,0.6)", "rgba(74,123,212,0.6)")
    fig.add_trace(go.Bar(x=df.index, y=df["MACD_HIST"], name="MACD 히스토그램",
                         marker_color=hist_colors), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], name="MACD",
                             line=dict(color="#e35b5b", width=1.2)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["MACD_SIG"], name="Signal",
                             line=dict(color="#4a7bd4", width=1.2)), row=3, col=1)

    # --- 현재가 수평 점선 + 우측 가격 라벨 ---
    fig.add_hline(
        y=last_close, line_dash="dot", line_color="#e35b5b", line_width=1,
        annotation_text=f" {last_close:,.2f}", annotation_position="right",
        annotation_font=dict(color="#e35b5b", size=12), row=1, col=1,
    )

    # --- 가독성 설정: 십자선 + 통합 툴팁 + 축 포맷 ---
    fig.update_layout(
        height=800, xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.06),
        margin=dict(l=10, r=70, t=60, b=10),
        hovermode="x unified",              # 세로선 하나에 모든 값이 한 번에 표시
        hoverlabel=dict(font_size=12),
        spikedistance=-1,
    )

    # 마우스를 따라다니는 십자선 (시간축: 전체 관통, 가격축: 라벨 표시)
    fig.update_xaxes(
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikedash="dot", spikethickness=1, spikecolor="#888",
    )
    fig.update_yaxes(
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikedash="dot", spikethickness=1, spikecolor="#888",
        tickformat=",.2f", row=1, col=1,
    )
    fig.update_yaxes(title_text="가격", tickformat=",.2f", row=1, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    fig.update_yaxes(title_text="MACD", row=3, col=1)

    # 시간축 라벨: 분/시간봉은 '날짜+시각', 일/주봉은 '날짜'로 표시
    if is_intraday:
        fig.update_xaxes(tickformat="%m/%d<br>%H:%M", row=3, col=1)
        # 장 마감/주말 빈 구간 제거 (FX는 주말 휴장)
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    else:
        fig.update_xaxes(tickformat="%Y-%m-%d", row=3, col=1)
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])

    # 캔들 툴팁에 시가/고가/저가/종가가 명확히 보이도록 포맷 지정
    fig.update_traces(
        selector=dict(type="candlestick"),
        hoverinfo="x+y",
    )

    st.plotly_chart(fig, use_container_width=True)

    st.info(
        "📌 **시그널 규칙** · 매수(BUY): RSI가 30을 상향 돌파(과매도 탈출) · "
        "매도(SELL): RSI가 70을 하향 돌파(과매수 이탈). "
        "이 시그널이 탭 3의 백테스팅에 그대로 사용됩니다."
    )

# =============================================================
# [탭 2] 거시경제 & 뉴스
# =============================================================
with tab2:
    st.subheader("달러 인덱스(DXY) vs 미 10년물 국채금리(TNX)")

    # 거시 지표는 항상 일봉/주봉으로 조회 (분봉·시간봉 선택 시에는 최근 3개월 일봉으로 표시)
    if is_intraday:
        m_period, m_interval = "3mo", "1d"
    elif tf["interval"] == "1wk":
        m_period, m_interval = tf["period"], "1wk"
    else:
        m_period, m_interval = tf["period"], "1d"

    dxy = load_data("DX-Y.NYB", m_period, m_interval)
    tnx = load_data("^TNX", m_period, m_interval)

    if dxy.empty or tnx.empty:
        st.warning("거시경제 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
    else:
        mfig = make_subplots(specs=[[{"secondary_y": True}]])
        mfig.add_trace(go.Scatter(
            x=dxy.index, y=dxy["Close"], name="달러 인덱스 (DXY)",
            line=dict(color="#1f77b4", width=2),
        ), secondary_y=False)
        mfig.add_trace(go.Scatter(
            x=tnx.index, y=tnx["Close"], name="미 10년물 금리 (%)",
            line=dict(color="#d62728", width=2, dash="dash"),
        ), secondary_y=True)

        mfig.update_yaxes(title_text="달러 인덱스", secondary_y=False)
        mfig.update_yaxes(title_text="10년물 금리 (%)", secondary_y=True)
        mfig.update_layout(
            height=450, legend=dict(orientation="h", y=1.1),
            margin=dict(l=10, r=10, t=40, b=10),
            hovermode="x unified", hoverlabel=dict(font_size=12),
        )
        mfig.update_xaxes(
            rangebreaks=[dict(bounds=["sat", "mon"])],
            showspikes=True, spikemode="across", spikesnap="cursor",
            spikedash="dot", spikethickness=1, spikecolor="#888",
            tickformat="%Y-%m-%d",
        )
        st.plotly_chart(mfig, use_container_width=True)

        mc1, mc2 = st.columns(2)
        mc1.metric("달러 인덱스 (DXY)", f"{float(dxy['Close'].iloc[-1]):.2f}")
        mc2.metric("미 10년물 금리", f"{float(tnx['Close'].iloc[-1]):.3f}%")

    st.divider()
    st.subheader("📰 관련 뉴스 헤드라인")
    news = load_news([ticker, "DX-Y.NYB", "^TNX"])
    if not news:
        st.caption("현재 표시할 뉴스가 없습니다. (Yahoo Finance 뉴스가 비어있을 수 있어요)")
    else:
        for n in news:
            pub = f" — *{n['publisher']}*" if n["publisher"] else ""
            if n["link"]:
                st.markdown(f"- [{n['title']}]({n['link']}){pub}")
            else:
                st.markdown(f"- {n['title']}{pub}")

# =============================================================
# [탭 3] 백테스팅 리포트
# =============================================================
with tab3:
    st.subheader("🧪 RSI 전략 백테스팅")
    st.caption(
        f"전략: BUY 시그널에서 전액 매수 → SELL 시그널 / 손절 -{stop_loss_pct}% / "
        f"익절 +{take_profit_pct}% 중 먼저 도달하는 조건에서 청산 (롱 온리, 수수료 미반영)"
    )

    def run_backtest(data: pd.DataFrame, capital: float, sl: float, tp: float):
        cash = capital
        units = 0.0
        entry_price = None
        equity_curve = []
        trades = []  # 각 거래의 수익률(%) 기록

        for _, row in data.iterrows():
            price = row["Close"]
            if pd.isna(price):
                equity_curve.append(cash + units * (price if not pd.isna(price) else 0))
                continue

            if units == 0:
                # 진입
                if bool(row["BUY"]):
                    units = cash / price
                    cash = 0.0
                    entry_price = price
            else:
                ret = (price - entry_price) / entry_price * 100
                exit_now = bool(row["SELL"]) or ret <= -sl or ret >= tp
                if exit_now:
                    cash = units * price
                    units = 0.0
                    trades.append(ret)
                    entry_price = None

            equity_curve.append(cash + units * price)

        # 마지막까지 보유 중이면 종가로 청산 처리
        if units > 0:
            final_price = data["Close"].iloc[-1]
            trades.append((final_price - entry_price) / entry_price * 100)
            cash = units * final_price
            units = 0.0

        eq = pd.Series(equity_curve, index=data.index, name="Equity")
        return eq, trades

    equity, trades = run_backtest(df, initial_capital, stop_loss_pct, take_profit_pct)

    if len(trades) == 0:
        st.warning("선택한 기간/타임프레임에서 발생한 거래가 없습니다. 타임프레임을 바꿔보세요.")
    else:
        final_equity = float(equity.iloc[-1])
        total_return = (final_equity / initial_capital - 1) * 100
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100

        # MDD (최대 낙폭)
        running_max = equity.cummax()
        drawdown = (equity - running_max) / running_max * 100
        mdd = float(drawdown.min())

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("총 수익률", f"{total_return:+.2f}%")
        k2.metric("승률", f"{win_rate:.1f}%")
        k3.metric("MDD (최대 낙폭)", f"{mdd:.2f}%")
        k4.metric("총 거래 횟수", f"{len(trades)}회")

        # Equity Curve
        efig = go.Figure()
        efig.add_trace(go.Scatter(
            x=equity.index, y=equity.values, name="누적 자산",
            line=dict(color="#2ca02c", width=2),
            fill="tozeroy", fillcolor="rgba(44,160,44,0.08)",
        ))
        efig.add_hline(y=initial_capital, line_dash="dash", line_color="gray",
                       annotation_text="초기 자본금")
        efig.update_layout(
            title="누적 자산 변화 (Equity Curve)",
            height=420, margin=dict(l=10, r=10, t=50, b=10),
            yaxis_title="자산",
        )
        st.plotly_chart(efig, use_container_width=True)

        with st.expander("개별 거래 수익률 보기"):
            trade_df = pd.DataFrame({
                "거래 번호": range(1, len(trades) + 1),
                "수익률 (%)": [round(t, 2) for t in trades],
                "결과": ["✅ 익절/이익" if t > 0 else "❌ 손절/손실" for t in trades],
            })
            st.dataframe(trade_df, use_container_width=True, hide_index=True)

st.divider()
st.caption("⚠️ 본 대시보드는 학습·참고용이며 투자 손실에 대한 책임은 이용자 본인에게 있습니다.")
