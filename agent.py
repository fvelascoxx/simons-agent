import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz

# ─────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "6962946701")
NEWS_API_KEY   = os.environ.get("NEWS_API_KEY", "")
CLT            = pytz.timezone("America/Santiago")

ACTIVOS = {
    "GOLD":     {"ticker": "GC=F",    "emoji": "🥇", "nombre": "Gold XAU/USD",   "pip_val": 0.1,    "spread": 0.9},
    "SILVER":   {"ticker": "SI=F",    "emoji": "🥈", "nombre": "Silver XAG/USD", "pip_val": 0.05,   "spread": 0.148},
    "OIL":      {"ticker": "CL=F",    "emoji": "🛢️", "nombre": "Oil WTI",        "pip_val": 0.01,   "spread": 0.03},
    "COPPER":   {"ticker": "HG=F",    "emoji": "🟠", "nombre": "Copper",         "pip_val": 0.0001, "spread": 0.002},
    "PLATINUM": {"ticker": "PL=F",    "emoji": "💎", "nombre": "Platinum",       "pip_val": 0.1,    "spread": 0.5},
    "DXY":      {"ticker": "DX-Y.NYB","emoji": "💵", "nombre": "DXY Index",      "pip_val": 0.001,  "spread": 0.01},
}

KEYWORDS = {
    "GOLD": {
        "bull": ["war","attack","sanctions","inflation","rate cut","geopolit","iran","hormuz","conflict","crisis","fed cut","recession","safe haven","gold rally","uncertainty"],
        "bear": ["ceasefire","peace","deal","rate hike","strong dollar","agreement","truce","gold drop","risk on"]
    },
    "SILVER": {
        "bull": ["industrial demand","solar","ev","green energy","ai","chip","rate cut","silver rally","manufacturing","china demand"],
        "bear": ["recession","demand drop","strong dollar","rate hike","silver drop","industrial slowdown"]
    },
    "OIL": {
        "bull": ["opec cut","supply cut","hormuz","blockade","attack","iran","escalat","oil rally","production cut","hurricane","refinery"],
        "bear": ["ceasefire","hormuz open","supply surge","opec increase","deal","truce","negotiat","oil drop","recession","demand drop"]
    },
    "COPPER": {
        "bull": ["china demand","infrastructure","green energy","ev","construction boom","copper rally","manufacturing"],
        "bear": ["china slowdown","recession","demand drop","copper drop","oversupply","trade war"]
    },
    "PLATINUM": {
        "bull": ["auto demand","hydrogen","fuel cell","supply cut","platinum rally","palladium shortage"],
        "bear": ["ev adoption","auto slowdown","platinum drop","oversupply","recession"]
    },
    "DXY": {
        "bull": ["rate hike","fed hawkish","strong economy","dollar rally","risk off","nfp beat"],
        "bear": ["rate cut","fed dovish","dollar drop","deficit","debt ceiling","nfp miss"]
    }
}

SESIONES = {
    "ASIA":     {"open_utc": 0,  "close_utc": 8,  "emoji": "🌏"},
    "EUROPA":   {"open_utc": 7,  "close_utc": 16, "emoji": "🌍"},
    "NEW_YORK": {"open_utc": 13, "close_utc": 21, "emoji": "🌎"},
}

EVENTOS_MACRO = {
    "EIA_INVENTORY": {"weekday": 2, "hour_utc": 14, "min_utc": 30, "nombre": "EIA Inventory Report", "emoji": "🛢️", "activos": ["OIL"]},
    "NFP":           {"weekday": 4, "hour_utc": 13, "min_utc": 30, "nombre": "NFP Employment",        "emoji": "💼", "activos": ["GOLD","SILVER","DXY"]},
}

estado = {
    "alertas_enviadas": {},
    "scores_anteriores": {},
    "pre_evento_enviado": {},
    "ultimo_ciclo": None,
}

# ─────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────
def send_telegram(msg: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[Telegram error] {e}")
        return False

# ─────────────────────────────────────────
# DATOS DE PRECIO
# ─────────────────────────────────────────
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
        df = yf.download(ticker, period="60d", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df
    except Exception as e:
        print(f"[Daily error] {ticker}: {e}")
        return None

# ─────────────────────────────────────────
# INDICADORES TÉCNICOS
# ─────────────────────────────────────────
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

def detectar_divergencia_rsi(df_h1) -> dict:
    if df_h1 is None or len(df_h1) < 30:
        return {"tipo": "none", "score": 0}
    close = df_h1["Close"]
    rsi_series = pd.Series([calc_rsi(close.iloc[:i+1]) for i in range(len(close))])
    if len(rsi_series) < 10:
        return {"tipo": "none", "score": 0}
    precio_reciente  = float(close.iloc[-1])
    precio_anterior  = float(close.iloc[-10])
    rsi_reciente     = float(rsi_series.iloc[-1])
    rsi_anterior     = float(rsi_series.iloc[-10])
    if precio_reciente < precio_anterior and rsi_reciente > rsi_anterior and rsi_reciente < 45:
        return {"tipo": "alcista", "score": 3}
    if precio_reciente > precio_anterior and rsi_reciente < rsi_anterior and rsi_reciente > 55:
        return {"tipo": "bajista", "score": 3}
    return {"tipo": "none", "score": 0}

def calc_fibonacci_signal(df_daily) -> dict:
    if df_daily is None or len(df_daily) < 5:
        return {"signal": "neutral", "pullback_pct": 0}
    high  = float(df_daily["High"].tail(20).max())
    low   = float(df_daily["Low"].tail(20).min())
    close = float(df_daily["Close"].iloc[-1])
    rng   = high - low
    if rng == 0:
        return {"signal": "neutral", "pullback_pct": 0}
    fib_382      = high - 0.382 * rng
    fib_618      = high - 0.618 * rng
    pullback_pct = (high - close) / high * 100
    signal = "long" if fib_618 <= close <= fib_382 else ("short_watch" if close >= high * 0.998 else "neutral")
    return {"signal": signal, "pullback_pct": round(pullback_pct, 2)}

def detectar_order_block(df_daily) -> dict:
    if df_daily is None or len(df_daily) < 10:
        return {"zona": "none", "score": 0, "nivel": 0}
    close  = float(df_daily["Close"].iloc[-1])
    highs  = df_daily["High"].tail(10).values
    lows   = df_daily["Low"].tail(10).values
    closes = df_daily["Close"].tail(10).values
    for i in range(len(closes) - 2):
        if closes[i] < closes[i-1] and closes[i+1] > closes[i] * 1.003:
            nivel = lows[i]
            if abs(close - nivel) / nivel < 0.005:
                return {"zona": "bullish_ob", "score": 2, "nivel": round(nivel, 4)}
        if closes[i] > closes[i-1] and closes[i+1] < closes[i] * 0.997:
            nivel = highs[i]
            if abs(close - nivel) / nivel < 0.005:
                return {"zona": "bearish_ob", "score": 2, "nivel": round(nivel, 4)}
    return {"zona": "none", "score": 0, "nivel": 0}

def detectar_fair_value_gap(df_h1) -> dict:
    if df_h1 is None or len(df_h1) < 5:
        return {"tipo": "none", "score": 0}
    for i in range(len(df_h1) - 3, max(len(df_h1) - 15, 0), -1):
        high_1 = float(df_h1["High"].iloc[i-1])
        low_1  = float(df_h1["Low"].iloc[i-1])
        high_3 = float(df_h1["High"].iloc[i+1])
        low_3  = float(df_h1["Low"].iloc[i+1])
        if low_3 > high_1:
            return {"tipo": "alcista", "score": 2, "zona_alta": low_3, "zona_baja": high_1}
        if high_3 < low_1:
            return {"tipo": "bajista", "score": 2, "zona_alta": low_1, "zona_baja": high_3}
    return {"tipo": "none", "score": 0}

# ─────────────────────────────────────────
# SISTEMAS DE SCORING
# ─────────────────────────────────────────
def simons_score(df_h1, df_daily) -> dict:
    if df_h1 is None or len(df_h1) < 20:
        return {"score": 0, "direction": "neutral", "rsi": 50, "stoch": 50, "pullback_pct": 0, "divergencia": "none"}
    rsi   = calc_rsi(df_h1["Close"])
    stoch = calc_stochastic(df_h1)
    fib   = calc_fibonacci_signal(df_daily)
    div   = detectar_divergencia_rsi(df_h1)
    score = 0
    direction = "neutral"

    if rsi < 30:    score += 3; direction = "long"
    elif rsi < 40:  score += 2; direction = "long"
    elif rsi > 70:  score += 3; direction = "short"
    elif rsi > 60:  score += 2; direction = "short"

    if stoch < 20:
        score += 2
        if direction == "neutral": direction = "long"
    elif stoch < 30:
        score += 1
        if direction == "neutral": direction = "long"
    elif stoch > 80:
        score += 2
        if direction == "neutral": direction = "short"
    elif stoch > 70:
        score += 1
        if direction == "neutral": direction = "short"

    if fib["signal"] == "long" and fib["pullback_pct"] >= 1:
        score += 2
    elif fib["pullback_pct"] >= 1:
        score += 1

    if div["tipo"] == "alcista":
        score += div["score"]
        if direction == "neutral": direction = "long"
    elif div["tipo"] == "bajista":
        score += div["score"]
        if direction == "neutral": direction = "short"

    if df_daily is not None and len(df_daily) >= 4:
        closes = df_daily["Close"].tail(4).values
        if all(closes[i] > closes[i-1] for i in range(1, 4)):
            score += 1
            if direction == "neutral": direction = "long"
        elif all(closes[i] < closes[i-1] for i in range(1, 4)):
            score += 1
            if direction == "neutral": direction = "short"

    return {"score": min(score, 10), "direction": direction,
            "rsi": round(rsi, 1), "stoch": round(stoch, 1),
            "pullback_pct": fib["pullback_pct"], "divergencia": div["tipo"]}

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
    score = 0
    direction = "neutral"

    if macd_val > 0 and macd_prev <= 0:    score += 3; direction = "long"
    elif macd_val < 0 and macd_prev >= 0:  score += 3; direction = "short"
    elif macd_val > 0:                     score += 1; direction = "long"
    elif macd_val < 0:                     score += 1; direction = "short"

    if price <= float(lower.iloc[-1]) * 1.002:
        score += 3
        if direction == "neutral": direction = "long"
    elif price >= float(upper.iloc[-1]) * 0.998:
        score += 3
        if direction == "neutral": direction = "short"

    bb_squeeze = float((upper - lower).iloc[-1]) / float(sma20.iloc[-1]) * 100
    if bb_squeeze < 1.5: score += 2
    elif bb_squeeze < 2.0: score += 1

    return {"score": min(score, 8), "direction": direction,
            "macd_hist": round(macd_val, 4), "bb_squeeze": round(bb_squeeze, 2)}

def get_news_sentiment(activo: str) -> dict:
    if not NEWS_API_KEY:
        return {"score": 0, "direction": "neutral", "headline": "Sin API key", "urgencia": 0}
    queries = {
        "GOLD":     "gold price OR XAU OR gold market OR gold rally",
        "SILVER":   "silver price OR XAG OR silver market OR silver rally",
        "OIL":      "oil price OR WTI OR crude oil OR OPEC OR Hormuz OR Iran oil",
        "COPPER":   "copper price OR copper demand OR China copper OR copper market",
        "PLATINUM": "platinum price OR platinum market OR platinum demand",
        "DXY":      "US dollar index OR DXY OR Fed rate OR dollar strength",
    }
    url = (f"https://newsapi.org/v2/everything"
           f"?q={queries.get(activo, activo)}"
           f"&sortBy=publishedAt&pageSize=15&language=en&apiKey={NEWS_API_KEY}")
    try:
        data = requests.get(url, timeout=10).json()
        if data.get("status") != "ok":
            return {"score": 0, "direction": "neutral", "headline": "Error API", "urgencia": 0}
        articles = data.get("articles", [])
        if not articles:
            return {"score": 0, "direction": "neutral", "headline": "Sin noticias", "urgencia": 0}

        bull_kw = KEYWORDS.get(activo, {}).get("bull", [])
        bear_kw = KEYWORDS.get(activo, {}).get("bear", [])
        bull_count = bear_count = 0
        urgencia = 0

        now = datetime.utcnow()
        for a in articles:
            text = (a.get("title", "") + " " + a.get("description", "")).lower()
            pub_at = a.get("publishedAt", "")
            try:
                pub_dt = datetime.strptime(pub_at[:19], "%Y-%m-%dT%H:%M:%S")
                horas_atras = (now - pub_dt).total_seconds() / 3600
                peso = 3 if horas_atras < 1 else (2 if horas_atras < 3 else 1)
            except:
                peso = 1

            b_hits = sum(1 for k in bull_kw if k in text)
            br_hits = sum(1 for k in bear_kw if k in text)
            bull_count += b_hits * peso
            bear_count += br_hits * peso
            if b_hits > 0 or br_hits > 0:
                urgencia = max(urgencia, peso)

        score = 0
        direction = "neutral"
        if bull_count > bear_count + 1:
            score = min(int(bull_count / 2), 5)
            direction = "long"
        elif bear_count > bull_count + 1:
            score = min(int(bear_count / 2), 5)
            direction = "short"

        return {"score": score, "direction": direction,
                "headline": articles[0].get("title", "")[:100],
                "urgencia": urgencia}
    except Exception as e:
        return {"score": 0, "direction": "neutral", "headline": f"Error: {str(e)[:40]}", "urgencia": 0}

def sr_smc_score(df_daily) -> dict:
    if df_daily is None or len(df_daily) < 10:
        return {"score": 0, "direction": "neutral", "support": 0, "resist": 0, "pos_pct": 0}
    close   = float(df_daily["Close"].iloc[-1])
    resist  = float(df_daily["High"].tail(20).max())
    support = float(df_daily["Low"].tail(20).min())
    rng     = resist - support
    if rng == 0:
        return {"score": 0, "direction": "neutral", "support": support, "resist": resist, "pos_pct": 0}

    pos   = (close - support) / rng
    score = 0
    direction = "neutral"

    if pos < 0.15:    score += 4; direction = "long"
    elif pos < 0.30:  score += 2; direction = "long"
    elif pos > 0.85:  score += 4; direction = "short"
    elif pos > 0.70:  score += 2; direction = "short"

    prev_high = float(df_daily["High"].iloc[-6:-1].max())
    prev_low  = float(df_daily["Low"].iloc[-6:-1].min())
    if close > prev_high * 1.003:    score += 2; direction = "long"
    elif close < prev_low * 0.997:   score += 2; direction = "short"

    ob = detectar_order_block(df_daily)
    if ob["zona"] == "bullish_ob":   score += ob["score"]; direction = "long"
    elif ob["zona"] == "bearish_ob": score += ob["score"]; direction = "short"

    return {"score": min(score, 8), "direction": direction,
            "support": round(support, 3), "resist": round(resist, 3),
            "pos_pct": round(pos * 100, 1)}

def macro_correlation_score(activo: str) -> dict:
    try:
        dxy = yf.download("DX-Y.NYB", period="5d", interval="1d", progress=False, auto_adjust=True)
        vix = yf.download("^VIX",     period="5d", interval="1d", progress=False, auto_adjust=True)
        if dxy.empty or vix.empty:
            return {"score": 0, "direction": "neutral", "dxy_trend": 0, "vix": 0}
        dxy.columns = [c[0] if isinstance(c, tuple) else c for c in dxy.columns]
        vix.columns = [c[0] if isinstance(c, tuple) else c for c in vix.columns]
        dxy_trend = float(dxy["Close"].iloc[-1]) - float(dxy["Close"].iloc[-3])
        vix_level = float(vix["Close"].iloc[-1])
        score = 0
        direction = "neutral"

        if activo in ["GOLD", "SILVER", "PLATINUM"]:
            if dxy_trend < -0.5:    score += 3; direction = "long"
            elif dxy_trend < -0.3:  score += 2; direction = "long"
            elif dxy_trend > 0.5:   score += 3; direction = "short"
            elif dxy_trend > 0.3:   score += 2; direction = "short"
            if vix_level > 30:      score += 3; direction = "long" if direction != "short" else direction
            elif vix_level > 25:    score += 2
            elif vix_level < 15:    score += 1

        elif activo == "OIL":
            if dxy_trend < -0.3:   score += 1; direction = "long"
            if vix_level > 30:     score += 2
            elif vix_level > 25:   score += 1

        elif activo == "COPPER":
            if dxy_trend < -0.3:   score += 2; direction = "long"
            elif dxy_trend > 0.3:  score += 2; direction = "short"

        elif activo == "DXY":
            if vix_level > 25:     score += 2; direction = "long"
            elif vix_level < 15:   score += 2; direction = "short"

        return {"score": min(score, 5), "direction": direction,
                "dxy_trend": round(dxy_trend, 3), "vix": round(vix_level, 1)}
    except Exception as e:
        return {"score": 0, "direction": "neutral", "dxy_trend": 0, "vix": 0}

# ─────────────────────────────────────────
# ✅ FIX 1: VALIDACIÓN DIRECCIÓN COHERENTE
# RSI/Stoch oversold = NUNCA recomendar SHORT
# RSI/Stoch overbought = NUNCA recomendar LONG
# ─────────────────────────────────────────
def validar_coherencia_direccion(direction: str, s1: dict) -> tuple[bool, str]:
    """
    Retorna (es_valido, motivo_rechazo)
    Bloquea señales donde técnica y dirección son contradictorias.
    """
    rsi   = s1.get("rsi", 50)
    stoch = s1.get("stoch", 50)

    # Bug original: recomendar SHORT cuando RSI/Stoch oversold
    if direction == "short" and rsi < 35:
        return False, f"BLOQUEADO: SHORT inválido con RSI={rsi} (oversold). Señal contradictoria."

    if direction == "short" and stoch < 25:
        return False, f"BLOQUEADO: SHORT inválido con Stoch={stoch} (oversold). Señal contradictoria."

    # También bloquear LONG cuando RSI/Stoch overbought extremo
    if direction == "long" and rsi > 75:
        return False, f"BLOQUEADO: LONG inválido con RSI={rsi} (overbought). Señal contradictoria."

    if direction == "long" and stoch > 85:
        return False, f"BLOQUEADO: LONG inválido con Stoch={stoch} (overbought). Señal contradictoria."

    return True, ""

# ─────────────────────────────────────────
# ✅ FIX 2: HARD BLOCK SI NOTICIAS = 0
# Sin catalizador macro = sin alerta
# ─────────────────────────────────────────
def validar_noticias(s3: dict) -> tuple[bool, str]:
    """
    Retorna (es_valido, motivo_rechazo)
    Bloquea cualquier señal sin catalizador de noticias confirmado.
    """
    news_score = s3.get("score", 0)
    if news_score == 0:
        return False, "BLOQUEADO: Noticias 0/5. Sin catalizador macro confirmado. Regla Soros: sin noticia = sin trade."
    return True, ""

# ─────────────────────────────────────────
# SCORE TOTAL — con validaciones integradas
# ─────────────────────────────────────────
def calcular_score_total(activo: str) -> dict:
    cfg    = ACTIVOS[activo]
    df_h1  = get_price_data(cfg["ticker"], period="10d", interval="1h")
    df_day = get_daily_data(cfg["ticker"])

    s1 = simons_score(df_h1, df_day)
    s2 = macd_bb_score(df_h1)
    s3 = get_news_sentiment(activo)
    s4 = sr_smc_score(df_day)
    s5 = macro_correlation_score(activo)

    fvg = detectar_fair_value_gap(df_h1)

    pesos = [0.28, 0.22, 0.25, 0.15, 0.10]
    maxs  = [10,   8,    5,    8,    5]
    scores = [s1["score"], s2["score"], s3["score"], s4["score"], s5["score"]]
    score_norm = sum(scores[i] / maxs[i] * pesos[i] * 10 for i in range(5))

    if fvg["tipo"] != "none":
        score_norm = min(score_norm + 0.5, 10.0)

    dirs    = [s1["direction"], s2["direction"], s3["direction"], s4["direction"], s5["direction"]]
    long_w  = sum(pesos[i] for i, d in enumerate(dirs) if d == "long")
    short_w = sum(pesos[i] for i, d in enumerate(dirs) if d == "short")
    direction = "long" if long_w > short_w else ("short" if short_w > long_w else "neutral")

    # ─────────────────────────────────────────
    # ✅ APLICAR VALIDACIONES ANTES DE ALERTAR
    # ─────────────────────────────────────────
    bloqueo_activo = False
    motivo_bloqueo = ""

    # Fix 2: Bloqueo por noticias = 0
    noticias_ok, motivo_noticias = validar_noticias(s3)
    if not noticias_ok:
        bloqueo_activo = True
        motivo_bloqueo = motivo_noticias
        print(f"  [{activo}] {motivo_noticias}")

    # Fix 1: Bloqueo por incoherencia técnica
    if not bloqueo_activo and direction != "neutral":
        coherencia_ok, motivo_coherencia = validar_coherencia_direccion(direction, s1)
        if not coherencia_ok:
            bloqueo_activo = True
            motivo_bloqueo = motivo_coherencia
            print(f"  [{activo}] {motivo_coherencia}")

    # Si hay bloqueo, forzar score a 0 para que no genere alerta
    if bloqueo_activo:
        score_norm = 0.0
        direction = "neutral"

    price = float(df_day["Close"].iloc[-1]) if df_day is not None else 0.0
    atr   = float((df_day["High"].tail(14) - df_day["Low"].tail(14)).mean()) if df_day is not None and len(df_day) >= 14 else price * 0.008

    sl_dist = atr * 1.2
    tp_dist = sl_dist * 3.5
    sl = round((price - sl_dist) if direction == "long" else (price + sl_dist), 4)
    tp = round((price + tp_dist) if direction == "long" else (price - tp_dist), 4)

    capital_usd = 1500
    riesgo_usd  = capital_usd * 0.01
    pip_val     = cfg["pip_val"]
    sl_pips     = sl_dist / pip_val if pip_val > 0 else 100
    lotes       = round(riesgo_usd / (sl_pips * 5), 2) if sl_pips > 0 else 0.02
    lotes       = max(0.01, min(lotes, 0.04))

    return {
        "activo": activo, "emoji": cfg["emoji"], "nombre": cfg["nombre"],
        "score": round(score_norm, 1), "direction": direction,
        "price": round(price, 4), "sl": sl, "tp": tp, "rr": 3.5, "lotes": lotes,
        "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5, "fvg": fvg,
        "headline": s3.get("headline", ""), "urgencia_noticias": s3.get("urgencia", 0),
        "vix": s5.get("vix", 0), "dxy_trend": s5.get("dxy_trend", 0),
        "bloqueado": bloqueo_activo, "motivo_bloqueo": motivo_bloqueo,
    }

# ─────────────────────────────────────────
# MENSAJES
# ─────────────────────────────────────────
def generar_alerta_trade(r: dict, tipo: str = "SEÑAL") -> str:
    dir_txt = "LONG 🟢 COMPRA" if r["direction"] == "long" else "SHORT 🔴 VENDE"
    hora    = datetime.now(CLT).strftime("%H:%M CLT")

    if tipo == "SEÑAL":
        urgencia_txt = "⚡ TIENES 10 MINUTOS PARA ENTRAR" if r["score"] >= 7.0 else "⚠️ SETUP CONFIRMÁNDOSE — PREPÁRATE"
        return f"""🚨 <b>AHORA ES EL MOMENTO</b> — {urgencia_txt}
━━━━━━━━━━━━━━━━━━━━━━
{r['emoji']} <b>{r['nombre']}</b> | Score: <b>{r['score']}/10</b>

🎯 DIRECCIÓN: <b>{dir_txt}</b>
💰 Entrada ahora: <b>${r['price']}</b>
🛡 Stop Loss:     <b>${r['sl']}</b>
🎯 Take Profit:   <b>${r['tp']}</b>
📐 R/R: <b>{r['rr']}:1</b> | Lotes: <b>{r['lotes']}</b>

📊 Sistemas:
• SIMONS RSI {r['s1']['rsi']} | Stoch {r['s1']['stoch']} | Div: {r['s1']['divergencia']}
• MACD/BB: {r['s2']['score']}/8 | Squeeze: {r['s2']['bb_squeeze']}%
• Noticias: {r['s3']['score']}/5 {'🔥' if r['urgencia_noticias'] >= 2 else ''}
• S/R+SMC: {r['s4']['score']}/8
• Macro DXY {r['dxy_trend']:+.2f} | VIX {r['vix']}

📰 {r['headline'][:80] if r['headline'] else 'Sin noticias'}

⚠️ SL obligatorio. Max 1% capital. No promediar.
🕐 {hora}
━━━━━━━━━━━━━━━━━━━━━━""".strip()

    elif tipo == "FORMACION":
        return f"""👁 <b>SETUP EN FORMACIÓN — MONITOREA</b>
━━━━━━━━━━━━━━━━━━━━━━
{r['emoji']} <b>{r['nombre']}</b> | Score: <b>{r['score']}/10</b>
🎯 Dirección probable: <b>{'LONG 🟢' if r['direction'] == 'long' else 'SHORT 🔴'}</b>
💰 Precio actual: <b>${r['price']}</b>
📊 Falta confluencia — espera score ≥ 6.0
🕐 {hora}
━━━━━━━━━━━━━━━━━━━━━━""".strip()

def generar_alerta_macro(evento: str, minutos_restantes: int) -> str:
    ev = EVENTOS_MACRO.get(evento, {})
    hora = datetime.now(CLT).strftime("%H:%M CLT")
    activos_txt = " | ".join(ev.get("activos", []))
    return f"""⏰ <b>DATO MACRO EN {minutos_restantes} MINUTOS</b>
━━━━━━━━━━━━━━━━━━━━━━
{ev.get('emoji','📊')} <b>{ev.get('nombre', evento)}</b>
⚡ Activos afectados: <b>{activos_txt}</b>
⚠️ ALTA VOLATILIDAD ESPERADA
💡 No abras posiciones nuevas ahora.
   Espera 5 min post-dato para confirmar dirección.
🕐 {hora}
━━━━━━━━━━━━━━━━━━━━━━""".strip()

def generar_alerta_sesion(sesion: str) -> str:
    hora = datetime.now(CLT).strftime("%H:%M CLT")
    msgs = {
        "ASIA":     "🌏 <b>APERTURA ASIA</b> — Mercados japoneses y australianos abren.\nMonitoreando Gold y Platinum por demanda oriental.",
        "EUROPA":   "🌍 <b>APERTURA EUROPA</b> — Londres activa. Mayor volumen en metales.\nSetups técnicos más confiables desde aquí.",
        "NEW_YORK": "🌎 <b>APERTURA NEW YORK</b> — Máxima liquidez. Momento de mayor oportunidad.\nTodos los sistemas en alerta máxima.",
    }
    return f"""{msgs.get(sesion, f'Apertura {sesion}')}
🕐 {hora} | Analizando ahora...
━━━━━━━━━━━━━━━━━━━━━━""".strip()

# ─────────────────────────────────────────
# HORARIOS Y SESIONES
# ─────────────────────────────────────────
def get_sesion_actual() -> str | None:
    now_utc = datetime.utcnow().hour
    if 0 <= now_utc < 8:   return "ASIA"
    if 7 <= now_utc < 16:  return "EUROPA"
    if 13 <= now_utc < 21: return "NEW_YORK"
    return None

def es_apertura_sesion(sesion: str) -> bool:
    now_utc = datetime.utcnow()
    hora    = SESIONES[sesion]["open_utc"]
    return now_utc.hour == hora and now_utc.minute < 15

def get_intervalo_ciclo() -> int:
    now_utc = datetime.utcnow().hour
    if 13 <= now_utc <= 16:  return 900
    if 7  <= now_utc <= 21:  return 1800
    return 3600

def check_eventos_macro() -> None:
    now = datetime.utcnow()
    for key, ev in EVENTOS_MACRO.items():
        if now.weekday() != ev["weekday"]:
            continue
        minutos_para_evento = (ev["hour_utc"] * 60 + ev["min_utc"]) - (now.hour * 60 + now.minute)
        if 8 <= minutos_para_evento <= 12:
            key_estado = f"{key}_{now.date()}"
            if not estado["pre_evento_enviado"].get(key_estado):
                send_telegram(generar_alerta_macro(key, minutos_para_evento))
                estado["pre_evento_enviado"][key_estado] = True
                print(f"  → ALERTA MACRO: {ev['nombre']}")

def puede_enviar_alerta(activo: str, score: float) -> bool:
    ultima = estado["alertas_enviadas"].get(activo)
    if ultima is None:
        return True
    minutos = (time.time() - ultima) / 60
    score_anterior = estado["scores_anteriores"].get(activo, 0)
    if score >= 8.0 and score - score_anterior >= 1.5:
        return minutos >= 20
    return minutos >= 45

# ─────────────────────────────────────────
# CICLO PRINCIPAL
# ─────────────────────────────────────────
def analizar_todos() -> None:
    now_clt = datetime.now(CLT)
    print(f"\n[{now_clt.strftime('%H:%M')}] Analizando mercados...")

    for sesion in SESIONES:
        if es_apertura_sesion(sesion):
            key = f"apertura_{sesion}_{now_clt.date()}"
            if not estado["pre_evento_enviado"].get(key):
                send_telegram(generar_alerta_sesion(sesion))
                estado["pre_evento_enviado"][key] = True
                print(f"  → ALERTA SESIÓN: {sesion} abre")

    check_eventos_macro()

    for activo in ACTIVOS:
        try:
            r = calcular_score_total(activo)
            score = r["score"]
            dir_  = r["direction"]

            # Si fue bloqueado, loguear y continuar sin alertar
            if r.get("bloqueado"):
                print(f"  {activo}: BLOQUEADO — {r['motivo_bloqueo'][:60]}")
                time.sleep(3)
                continue

            print(f"  {activo}: score={score} dir={dir_}")

            if dir_ == "neutral":
                estado["scores_anteriores"][activo] = score
                time.sleep(3)
                continue

            if score >= 6.0 and puede_enviar_alerta(activo, score):
                ok = send_telegram(generar_alerta_trade(r, "SEÑAL"))
                if ok:
                    estado["alertas_enviadas"][activo]   = time.time()
                    estado["scores_anteriores"][activo]  = score
                    print(f"  → SEÑAL ENVIADA ✅ ({dir_.upper()} | score {score})")
                else:
                    print(f"  → Error enviando señal ❌")

            elif 5.5 <= score < 6.0 and puede_enviar_alerta(activo, score):
                ok = send_telegram(generar_alerta_trade(r, "FORMACION"))
                if ok:
                    estado["alertas_enviadas"][activo]  = time.time()
                    estado["scores_anteriores"][activo] = score
                    print(f"  → ALERTA TEMPRANA ENVIADA ⚠️")

            estado["scores_anteriores"][activo] = score
            time.sleep(4)

        except Exception as e:
            print(f"  [Error {activo}] {e}")

def es_horario_activo() -> bool:
    now = datetime.now(CLT)
    return now.weekday() < 5 and 7 <= now.hour <= 23

def run():
    print(f"[SIMONS AGENT v3.2] Iniciando... {datetime.now(CLT).strftime('%d/%m/%Y %H:%M CLT')}")
    send_telegram(
        "🤖 <b>Agente SIMONS XTB v3.2 — ACTIVO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Analizando: Gold, Silver, Oil, Copper, Platinum, DXY\n"
        "✅ Alertas de sesión: Asia, Europa, New York\n"
        "✅ Alertas macro: EIA (miér), NFP (vier)\n"
        "✅ Umbral señal: score ≥ 6.0/10\n"
        "✅ Alerta temprana: score ≥ 5.5/10\n"
        "✅ Ciclo: 15 min (peak) / 30 min (normal)\n"
        "🔒 NUEVO: Hard block si noticias = 0/5\n"
        "🔒 NUEVO: Block SHORT si RSI/Stoch oversold\n"
        "🔒 NUEVO: Block LONG si RSI/Stoch overbought\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Solo te aviso cuando hay confluencia real. 🎯"
    )

    while True:
        if es_horario_activo():
            analizar_todos()
        else:
            print(f"[{datetime.now(CLT).strftime('%H:%M')}] Fuera de horario. Próximo ciclo en 60 min.")

        intervalo = get_intervalo_ciclo()
        print(f"  Próximo ciclo en {intervalo//60} min.")
        time.sleep(intervalo)

if __name__ == "__main__":
    run()
