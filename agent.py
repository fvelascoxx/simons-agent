import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8656521664:AAEbz01F8_DfHojmrWJSKJxTu0f9xR6zPAk")
CHAT_ID        = os.environ.get("CHAT_ID", "6962946701")
NEWS_API_KEY   = os.environ.get("NEWS_API_KEY", "")
CLT            = pytz.timezone("America/Santiago")

ACTIVOS = {
    "GOLD":     {"ticker": "GC=F",  "emoji": "🥇", "nombre": "Gold XAU/USD",   "pip_val": 0.1},
    "SILVER":   {"ticker": "SI=F",  "emoji": "🥈", "nombre": "Silver XAG/USD", "pip_val": 0.05},
    "OIL":      {"ticker": "CL=F",  "emoji": "🛢️", "nombre": "Oil WTI",        "pip_val": 0.01},
    "COPPER":   {"ticker": "HG=F",  "emoji": "🟠", "nombre": "Copper",         "pip_val": 0.0001},
    "PLATINUM": {"ticker": "PL=F",  "emoji": "💎", "nombre": "Platinum",       "pip_val": 0.1},
    "DXY":      {"ticker": "DX-Y.NYB","emoji": "💵","nombre": "DXY Index",     "pip_val": 0.001},
}

KEYWORDS_BULLISH_GOLD  = ["war","attack","sanctions","inflation","rate cut","geopolit","iran","hormuz","conflict","crisis","fed cut"]
KEYWORDS_BEARISH_GOLD  = ["ceasefire","peace","deal","rate hike","strong dollar","agreement","truce"]
KEYWORDS_BULLISH_OIL   = ["opec cut","supply cut","hormuz","blockade","attack","iran","escalat"]
KEYWORDS_BEARISH_OIL   = ["ceasefire","hormuz open","supply surge","opec increase","deal","truce","negotiat"]
KEYWORDS_BULLISH_SILVER= ["industrial demand","solar","ev","green energy","ai","chip","rate cut"]
KEYWORDS_BEARISH_SILVER= ["recession","demand drop","strong dollar","rate hike"]

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[Telegram error] {e}")
        return False

def get_price_data(ticker: str, period: str = "10d", interval: str = "1h"):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df.empty:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df
    except Exception as e:
        print(f"[Price error] {ticker}: {e}")
        return None

def get_daily_data(ticker: str):
    try:
        df = yf.download(ticker, period="30d", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df
    except Exception as e:
        print(f"[Daily error] {ticker}: {e}")
        return None

def calc_rsi(series, period: int = 14) -> float:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0

def calc_stochastic(df, k: int = 14) -> float:
    low_min  = df["Low"].rolling(k).min()
    high_max = df["High"].rolling(k).max()
    stoch    = 100 * (df["Close"] - low_min) / (high_max - low_min)
    return float(stoch.iloc[-1]) if not stoch.empty else 50.0

def calc_fibonacci_signal(df_daily) -> dict:
    if df_daily is None or len(df_daily) < 5:
        return {"signal": "neutral", "pullback_pct": 0}
    high  = float(df_daily["High"].tail(20).max())
    low   = float(df_daily["Low"].tail(20).min())
    close = float(df_daily["Close"].iloc[-1])
    rng   = high - low
    if rng == 0:
        return {"signal": "neutral", "pullback_pct": 0}
    fib_382 = high - 0.382 * rng
    fib_618 = high - 0.618 * rng
    pullback_pct = (high - close) / high * 100
    signal = "long" if fib_618 <= close <= fib_382 else ("short_watch" if close >= high * 0.998 else "neutral")
    return {"signal": signal, "pullback_pct": round(pullback_pct, 2)}

def simons_score(df_h1, df_daily) -> dict:
    if df_h1 is None or len(df_h1) < 20:
        return {"score": 0, "direction": "neutral", "rsi": 50, "stoch": 50, "pullback_pct": 0}
    rsi   = calc_rsi(df_h1["Close"])
    stoch = calc_stochastic(df_h1)
    fib   = calc_fibonacci_signal(df_daily)
    score = 0; direction = "neutral"
    if rsi < 35:   score += 2; direction = "long"
    elif rsi > 65: score += 2; direction = "short"
    if stoch < 25:
        score += 2
        if direction == "neutral": direction = "long"
    elif stoch > 75:
        score += 2
        if direction == "neutral": direction = "short"
    if fib["signal"] == "long" and fib["pullback_pct"] >= 1: score += 2
    if fib["pullback_pct"] >= 1: score += 1
    if df_daily is not None and len(df_daily) >= 4:
        closes = df_daily["Close"].tail(4).values
        if all(closes[i] > closes[i-1] for i in range(1, 4)):
            score += 1; direction = "long" if direction == "neutral" else direction
        elif all(closes[i] < closes[i-1] for i in range(1, 4)):
            score += 1; direction = "short" if direction == "neutral" else direction
    return {"score": min(score, 8), "direction": direction,
            "rsi": round(rsi, 1), "stoch": round(stoch, 1), "pullback_pct": fib["pullback_pct"]}

def macd_bb_score(df_h1) -> dict:
    if df_h1 is None or len(df_h1) < 30:
        return {"score": 0, "direction": "neutral", "macd_hist": 0, "bb_squeeze": 0}
    close  = df_h1["Close"]
    macd   = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    hist   = macd - macd.ewm(span=9).mean()
    sma20  = close.rolling(20).mean()
    std20  = close.rolling(20).std()
    upper  = sma20 + 2 * std20
    lower  = sma20 - 2 * std20
    price  = float(close.iloc[-1])
    macd_val  = float(hist.iloc[-1])
    macd_prev = float(hist.iloc[-2])
    score = 0; direction = "neutral"
    if macd_val > 0 and macd_prev <= 0:   score += 3; direction = "long"
    elif macd_val < 0 and macd_prev >= 0: score += 3; direction = "short"
    elif macd_val > 0: score += 1; direction = "long"
    elif macd_val < 0: score += 1; direction = "short"
    if price <= float(lower.iloc[-1]) * 1.002:
        score += 2
        if direction == "neutral": direction = "long"
    elif price >= float(upper.iloc[-1]) * 0.998:
        score += 2
        if direction == "neutral": direction = "short"
    bb_squeeze = float((upper - lower).iloc[-1]) / float(sma20.iloc[-1]) * 100
    if bb_squeeze < 2.0: score += 1
    return {"score": min(score, 6), "direction": direction,
            "macd_hist": round(macd_val, 4), "bb_squeeze": round(bb_squeeze, 2)}

def get_news_sentiment(activo: str) -> dict:
    if not NEWS_API_KEY:
        return {"score": 0, "direction": "neutral", "headline": "Sin API key de noticias"}
    queries = {
        "GOLD":     "gold price OR XAU OR gold market",
        "SILVER":   "silver price OR XAG OR silver market",
        "OIL":      "oil price OR WTI OR crude oil OR OPEC OR Hormuz",
        "COPPER":   "copper price OR copper demand OR China copper",
        "PLATINUM": "platinum price OR platinum market",
        "DXY":      "US dollar index OR DXY OR Fed rate",
    }
    url = f"https://newsapi.org/v2/everything?q={queries.get(activo,activo)}&sortBy=publishedAt&pageSize=10&language=en&apiKey={NEWS_API_KEY}"
    try:
        data = requests.get(url, timeout=10).json()
        if data.get("status") != "ok": return {"score": 0, "direction": "neutral", "headline": "Error API"}
        articles = data.get("articles", [])
        if not articles: return {"score": 0, "direction": "neutral", "headline": "Sin noticias"}
        bull_kw = {"GOLD": KEYWORDS_BULLISH_GOLD, "SILVER": KEYWORDS_BULLISH_SILVER, "OIL": KEYWORDS_BULLISH_OIL}.get(activo, [])
        bear_kw = {"GOLD": KEYWORDS_BEARISH_GOLD, "SILVER": KEYWORDS_BEARISH_SILVER, "OIL": KEYWORDS_BEARISH_OIL}.get(activo, [])
        bull_count = bear_count = 0
        for a in articles:
            text = (a.get("title","") + " " + a.get("description","")).lower()
            bull_count += sum(1 for k in bull_kw if k in text)
            bear_count += sum(1 for k in bear_kw if k in text)
        score = 0; direction = "neutral"
        if bull_count > bear_count + 1:   score = min(bull_count, 4); direction = "long"
        elif bear_count > bull_count + 1: score = min(bear_count, 4); direction = "short"
        return {"score": score, "direction": direction, "headline": articles[0].get("title","")[:80]}
    except Exception as e:
        return {"score": 0, "direction": "neutral", "headline": f"Error: {str(e)[:40]}"}

def sr_smc_score(df_daily) -> dict:
    if df_daily is None or len(df_daily) < 10:
        return {"score": 0, "direction": "neutral", "support": 0, "resist": 0, "pos_pct": 0}
    close   = float(df_daily["Close"].iloc[-1])
    resist  = float(df_daily["High"].tail(20).max())
    support = float(df_daily["Low"].tail(20).min())
    rng     = resist - support
    if rng == 0: return {"score": 0, "direction": "neutral", "support": support, "resist": resist, "pos_pct": 0}
    pos = (close - support) / rng
    score = 0; direction = "neutral"
    if pos < 0.20:   score += 3; direction = "long"
    elif pos < 0.35: score += 2; direction = "long"
    elif pos > 0.80: score += 3; direction = "short"
    elif pos > 0.65: score += 2; direction = "short"
    prev_high = float(df_daily["High"].iloc[-6:-1].max())
    prev_low  = float(df_daily["Low"].iloc[-6:-1].min())
    if close > prev_high * 1.002:   score += 2; direction = "long"
    elif close < prev_low * 0.998:  score += 2; direction = "short"
    return {"score": min(score, 5), "direction": direction,
            "support": round(support, 3), "resist": round(resist, 3), "pos_pct": round(pos*100, 1)}

def macro_correlation_score(activo: str) -> dict:
    try:
        dxy = yf.download("DX-Y.NYB", period="5d", interval="1d", progress=False, auto_adjust=True)
        vix = yf.download("^VIX",     period="5d", interval="1d", progress=False, auto_adjust=True)
        if dxy.empty or vix.empty: return {"score": 0, "direction": "neutral", "dxy_trend": 0, "vix": 0}
        dxy_trend = float(dxy["Close"].iloc[-1]) - float(dxy["Close"].iloc[-3])
        vix_level = float(vix["Close"].iloc[-1])
        score = 0; direction = "neutral"
        if activo in ["GOLD", "SILVER", "PLATINUM"]:
            if dxy_trend < -0.3:   score += 2; direction = "long"
            elif dxy_trend > 0.3:  score += 2; direction = "short"
            if vix_level > 25:     score += 2; direction = "long" if direction != "short" else direction
            elif vix_level < 15:   score += 1
        elif activo == "OIL":
            if dxy_trend < -0.3: score += 1; direction = "long"
            if vix_level > 30:   score += 1
        elif activo == "COPPER":
            if dxy_trend < -0.3:  score += 2; direction = "long"
            elif dxy_trend > 0.3: score += 2; direction = "short"
        return {"score": min(score, 4), "direction": direction,
                "dxy_trend": round(dxy_trend, 3), "vix": round(vix_level, 1)}
    except Exception as e:
        return {"score": 0, "direction": "neutral", "dxy_trend": 0, "vix": 0}

def calcular_score_total(activo: str) -> dict:
    cfg    = ACTIVOS[activo]
    df_h1  = get_price_data(cfg["ticker"], period="10d", interval="1h")
    df_day = get_daily_data(cfg["ticker"])
    s1 = simons_score(df_h1, df_day)
    s2 = macd_bb_score(df_h1)
    s3 = get_news_sentiment(activo)
    s4 = sr_smc_score(df_day)
    s5 = macro_correlation_score(activo)
    pesos  = [0.30, 0.20, 0.25, 0.15, 0.10]
    maxs   = [8, 6, 4, 5, 4]
    scores = [s1["score"], s2["score"], s3["score"], s4["score"], s5["score"]]
    score_norm = sum(scores[i]/maxs[i] * pesos[i] * 10 for i in range(5))
    dirs    = [s1["direction"], s2["direction"], s3["direction"], s4["direction"], s5["direction"]]
    long_w  = sum(pesos[i] for i, d in enumerate(dirs) if d == "long")
    short_w = sum(pesos[i] for i, d in enumerate(dirs) if d == "short")
    direction = "long" if long_w > short_w else ("short" if short_w > long_w else "neutral")
    price = float(df_day["Close"].iloc[-1]) if df_day is not None else 0.0
    atr   = float((df_day["High"].tail(14) - df_day["Low"].tail(14)).mean()) if df_day is not None and len(df_day) >= 14 else price * 0.008
    sl_dist = atr * 1.5
    tp_dist = sl_dist * 3.5
    sl = (price - sl_dist) if direction == "long" else (price + sl_dist)
    tp = (price + tp_dist) if direction == "long" else (price - tp_dist)
    return {"activo": activo, "emoji": cfg["emoji"], "nombre": cfg["nombre"],
            "score": round(score_norm, 1), "direction": direction,
            "price": round(price, 4), "sl": round(sl, 4), "tp": round(tp, 4), "rr": 3.5,
            "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5,
            "headline": s3.get("headline", "")}

def generar_mensaje(r: dict) -> str:
    dir_txt = "🟢 LONG — COMPRAR" if r["direction"] == "long" else "🔴 SHORT — VENDER"
    nivel   = "🔥 ALTA" if r["score"] >= 7.5 else ("⚡ MEDIA" if r["score"] >= 6.0 else "⚠️ BAJA")
    hora    = datetime.now(CLT).strftime("%d/%m/%Y %H:%M CLT")
    return f"""🤖 <b>AGENTE SIMONS XTB — SEÑAL DETECTADA</b>
━━━━━━━━━━━━━━━━━━━━━━
{r['emoji']} <b>{r['nombre']}</b>
📊 Score: <b>{r['score']}/10</b> | Convicción: {nivel}
🎯 Dirección: <b>{dir_txt}</b>

💰 Entrada:   <b>${r['price']}</b>
🛡 Stop Loss:  <b>${r['sl']}</b>
🎯 Take Profit: <b>${r['tp']}</b>
📐 R/R: <b>{r['rr']}:1</b>

📋 <b>DESGLOSE SISTEMAS:</b>
- SIMONS (RSI/Stoch/Fib): {r['s1']['score']}/8 → RSI {r['s1']['rsi']} | Stoch {r['s1']['stoch']}
- MACD + Bollinger: {r['s2']['score']}/6
- Sentimiento noticias: {r['s3']['score']}/4
- S/R + Smart Money: {r['s4']['score']}/5
- Correlación macro: {r['s5']['score']}/4

📰 <b>Noticia clave:</b>
{r['headline'][:100] if r['headline'] else 'Sin datos de noticias'}

⚠️ SL obligatorio. Max 1% capital. No promediar pérdidas.
🕐 {hora}
━━━━━━━━━━━━━━━━━━━━━━""".strip()

def es_horario_activo() -> bool:
    now = datetime.now(CLT)
    return now.weekday() < 5 and 8 <= now.hour <= 22

def run():
    print(f"[SIMONS AGENT] Iniciando... {datetime.now(CLT).strftime('%d/%m/%Y %H:%M CLT')}")
    send_telegram("🤖 <b>Agente SIMONS XTB activo</b>\nAnalizando Gold, Silver, Oil, Copper, Platinum, DXY cada 30 min.\nSolo aviso cuando score ≥ 7/10. ✅")
    while True:
        if es_horario_activo():
            print(f"\n[{datetime.now(CLT).strftime('%H:%M')}] Analizando mercados...")
            for activo in ACTIVOS:
                try:
                    r = calcular_score_total(activo)
                    print(f"  {activo}: score={r['score']} dir={r['direction']}")
                    if r["score"] >= 7.0 and r["direction"] != "neutral":
                        ok = send_telegram(generar_mensaje(r))
                        print(f"  → SEÑAL ENVIADA {'✅' if ok else '❌'}")
                    time.sleep(3)
                except Exception as e:
                    print(f"  [Error {activo}] {e}")
        else:
            print(f"[{datetime.now(CLT).strftime('%H:%M')}] Fuera de horario.")
        time.sleep(1800)

if __name__ == "__main__":
    run()
