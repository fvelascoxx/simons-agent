import os
import json
import time
import requests
import logging
from datetime import datetime, timedelta
import pytz

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID        = os.environ.get("CHAT_ID")
FINNHUB_KEY    = os.environ.get("FINNHUB_API_KEY")

CLT = pytz.timezone("America/Santiago")

ASSETS = {
    "GOLD":     {"symbol": "OANDA:XAU_USD", "pip": 0.10, "name": "🥇 GOLD"},
    "SILVER":   {"symbol": "OANDA:XAG_USD", "pip": 0.01, "name": "🥈 SILVER"},
    "OIL":      {"symbol": "OANDA:BCO_USD", "pip": 0.01, "name": "🛢️ OIL"},
    "COPPER":   {"symbol": "OANDA:XCU_USD", "pip": 0.001,"name": "🟠 COPPER"},
    "PLATINUM": {"symbol": "OANDA:XPT_USD", "pip": 0.10, "name": "💎 PLATINUM"},
    "DXY":      {"symbol": "OANDA:US_Dollar_Basket","pip": 0.001,"name": "💵 DXY"},
}

# Macro keywords para scoring de noticias
BULLISH_GOLD  = ["war","conflict","inflation","fed cut","rate cut","sanctions","geopolit","iran","hormuz","refuge","haven","china tension"]
BEARISH_GOLD  = ["ceasefire","peace","deal","rate hike","strong dollar","hawkish","taper"]
BULLISH_OIL   = ["hormuz","iran","opec cut","supply cut","storm","attack","pipeline","escalat"]
BEARISH_OIL   = ["ceasefire","deal","opec increase","supply rise","recession","demand drop","truce","negotiat"]
BULLISH_SILVER= ["solar","ev","ai","industrial demand","green energy","deficit","manufact"]
BEARISH_SILVER= ["recession","demand drop","china slow","surplus"]

MACRO_CONTEXT_FILE = "/tmp/contexto_macro.json"
ALERT_HISTORY_FILE = "/tmp/alert_history.json"
REVERSAL_WATCH_FILE= "/tmp/reversal_watch.json"

ALERT_THRESHOLD        = 6.0   # Score mínimo para alerta normal
HIGH_ATTENTION_THRESHOLD = 5.5  # Score mínimo en ventanas de alta atención
MIN_ALERT_INTERVAL_MIN = 45    # Anti-spam por activo

# ─── HORARIOS DE ALTA ATENCIÓN (CLT) ──────────────────────────────────────────
HIGH_ATTENTION_DAYS  = [0, 1, 2, 3]  # Lun=0, Mar=1, Mié=2, Jue=3
HIGH_ATTENTION_HOURS = [
    (8, 0,  9, 30),   # 08:00–09:30
    (9, 30, 11, 0),   # 09:30–11:00
    (13, 30, 16, 0),  # 13:30–16:00
    (19, 30, 20, 0),  # 19:30 window
]

# Eventos del calendario económico
ECONOMIC_CALENDAR = {
    2: {"10:30": {"event": "EIA Crude Oil Inventories", "impact": "OIL", "direction": "bearish_if_build"}},
    4: {"10:30": {"event": "NFP Employment",             "impact": "GOLD",  "direction": "bearish_if_strong"}},
}

# ─── UTILIDADES ───────────────────────────────────────────────────────────────
def now_clt():
    return datetime.now(CLT)

def is_high_attention():
    n = now_clt()
    if n.weekday() not in HIGH_ATTENTION_DAYS:
        return False
    for sh, sm, eh, em in HIGH_ATTENTION_HOURS:
        start = n.replace(hour=sh, minute=sm, second=0)
        end   = n.replace(hour=eh, minute=em, second=0)
        if start <= n <= end:
            return True
    return False

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram no configurado")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# ─── CONTEXTO MACRO (MEMORIA PERSISTENTE) ─────────────────────────────────────
def load_macro_context():
    try:
        if os.path.exists(MACRO_CONTEXT_FILE):
            with open(MACRO_CONTEXT_FILE) as f:
                return json.load(f)
    except:
        pass
    return {
        "last_update": "",
        "dominant_theme": "",
        "iran_hormuz": "unknown",
        "fed_stance": "neutral",
        "opec_stance": "neutral",
        "dxy_trend": "neutral",
        "recent_headlines": [],
        "news_score_history": {}
    }

def save_macro_context(ctx):
    try:
        with open(MACRO_CONTEXT_FILE, "w") as f:
            json.dump(ctx, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving macro context: {e}")

def update_macro_context(ctx, headlines, scores):
    ctx["last_update"] = now_clt().isoformat()
    ctx["recent_headlines"] = headlines[-10:]  # Últimas 10
    for asset, score in scores.items():
        if asset not in ctx["news_score_history"]:
            ctx["news_score_history"][asset] = []
        ctx["news_score_history"][asset].append({"time": now_clt().isoformat(), "score": score})
        ctx["news_score_history"][asset] = ctx["news_score_history"][asset][-20:]
    # Detectar tema dominante
    all_text = " ".join(headlines).lower()
    if "iran" in all_text or "hormuz" in all_text:
        ctx["iran_hormuz"] = "active"
    if "ceasefire" in all_text or "peace" in all_text or "deal" in all_text:
        ctx["iran_hormuz"] = "de-escalating"
    if "hawkish" in all_text or "rate hike" in all_text:
        ctx["fed_stance"] = "hawkish"
    elif "cut" in all_text or "dovish" in all_text:
        ctx["fed_stance"] = "dovish"
    save_macro_context(ctx)
    return ctx

# ─── HISTORIAL DE ALERTAS (ANTI-SPAM) ─────────────────────────────────────────
def load_alert_history():
    try:
        if os.path.exists(ALERT_HISTORY_FILE):
            with open(ALERT_HISTORY_FILE) as f:
                return json.load(f)
    except:
        pass
    return {}

def save_alert_history(h):
    try:
        with open(ALERT_HISTORY_FILE, "w") as f:
            json.dump(h, f)
    except:
        pass

def can_send_alert(asset, history):
    last = history.get(asset)
    if not last:
        return True
    elapsed = (now_clt() - datetime.fromisoformat(last)).total_seconds() / 60
    return elapsed >= MIN_ALERT_INTERVAL_MIN

def register_alert(asset, history):
    history[asset] = now_clt().isoformat()
    save_alert_history(history)

# ─── WATCH DE REVERSIÓN ───────────────────────────────────────────────────────
def load_reversal_watch():
    try:
        if os.path.exists(REVERSAL_WATCH_FILE):
            with open(REVERSAL_WATCH_FILE) as f:
                return json.load(f)
    except:
        pass
    return {}

def save_reversal_watch(w):
    try:
        with open(REVERSAL_WATCH_FILE, "w") as f:
            json.dump(w, f)
    except:
        pass

def add_reversal_watch(asset, direction, entry_price):
    w = load_reversal_watch()
    w[asset] = {
        "direction": direction,
        "entry_price": entry_price,
        "alert_time": now_clt().isoformat(),
        "checks": 0
    }
    save_reversal_watch(w)

def check_reversals(prices):
    """Detecta si algún activo monitoreado se está revirtiendo. Llama cada 5 min."""
    w = load_reversal_watch()
    if not w:
        return
    to_remove = []
    for asset, data in w.items():
        alert_time = datetime.fromisoformat(data["alert_time"])
        elapsed_min = (now_clt() - alert_time).total_seconds() / 60
        if elapsed_min > 30:
            to_remove.append(asset)
            continue
        current_price = prices.get(asset)
        if not current_price:
            continue
        entry = data["entry_price"]
        direction = data["direction"]
        data["checks"] = data.get("checks", 0) + 1
        # Detectar reversión: precio se movió ≥0.15% contra la dirección
        if direction == "LONG":
            move_pct = (current_price - entry) / entry * 100
            if move_pct <= -0.15:
                send_telegram(
                    f"⚠️ <b>ALERTA REVERSIÓN — {asset}</b>\n\n"
                    f"Entraste LONG @ {entry:.2f}\n"
                    f"Precio actual: {current_price:.2f} ({move_pct:+.2f}%)\n\n"
                    f"⚡ <b>EL MERCADO SE ESTÁ DANDO VUELTA</b>\n"
                    f"Considera: CIERRA el LONG → evalúa SHORT\n"
                    f"Tiempo desde alerta: {elapsed_min:.0f} min"
                )
                to_remove.append(asset)
        elif direction == "SHORT":
            move_pct = (current_price - entry) / entry * 100
            if move_pct >= 0.15:
                send_telegram(
                    f"⚠️ <b>ALERTA REVERSIÓN — {asset}</b>\n\n"
                    f"Entraste SHORT @ {entry:.2f}\n"
                    f"Precio actual: {current_price:.2f} ({move_pct:+.2f}%)\n\n"
                    f"⚡ <b>EL MERCADO SE ESTÁ DANDO VUELTA</b>\n"
                    f"Considera: CIERRA el SHORT → evalúa LONG\n"
                    f"Tiempo desde alerta: {elapsed_min:.0f} min"
                )
                to_remove.append(asset)
    for asset in to_remove:
        w.pop(asset, None)
    save_reversal_watch(w)

# ─── FINNHUB: PRECIO ──────────────────────────────────────────────────────────
def get_price_finnhub(symbol):
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_KEY}"
        r = requests.get(url, timeout=8)
        d = r.json()
        return d.get("c")  # current price
    except Exception as e:
        logger.error(f"Price error {symbol}: {e}")
        return None

# ─── FINNHUB: NOTICIAS ────────────────────────────────────────────────────────
def get_finnhub_news():
    """Trae noticias generales de commodities/forex/macro desde Finnhub."""
    headlines = []
    categories = ["general", "forex"]
    for cat in categories:
        try:
            url = f"https://finnhub.io/api/v1/news?category={cat}&token={FINNHUB_KEY}"
            r = requests.get(url, timeout=10)
            news = r.json()
            for item in news[:15]:
                h = item.get("headline", "")
                if h:
                    headlines.append(h.lower())
        except Exception as e:
            logger.error(f"News error ({cat}): {e}")
    return headlines

def score_news(headlines, bullish_kw, bearish_kw):
    """Retorna score entre -5 y +5. Positivo = bullish, negativo = bearish."""
    if not headlines:
        return 0
    bull = sum(1 for h in headlines for kw in bullish_kw if kw in h)
    bear = sum(1 for h in headlines for kw in bearish_kw if kw in h)
    raw = bull - bear
    return max(-5, min(5, raw))

def get_news_scores(headlines):
    return {
        "GOLD":     score_news(headlines, BULLISH_GOLD,   BEARISH_GOLD),
        "SILVER":   score_news(headlines, BULLISH_SILVER, BEARISH_SILVER),
        "OIL":      score_news(headlines, BULLISH_OIL,    BEARISH_OIL),
        "COPPER":   score_news(headlines, BULLISH_SILVER, BEARISH_SILVER),  # proxy industrial
        "PLATINUM": score_news(headlines, BULLISH_SILVER, BEARISH_SILVER),
        "DXY":      score_news(headlines, BEARISH_GOLD,   BULLISH_GOLD),    # inverso a gold
    }

# ─── RSI SIMULADO (basado en precio + contexto) ───────────────────────────────
def estimate_rsi(asset, ctx):
    """Estimación simplificada de dirección basada en historial de scores."""
    history = ctx.get("news_score_history", {}).get(asset, [])
    if len(history) < 3:
        return 50  # neutral
    recent = [h["score"] for h in history[-5:]]
    avg = sum(recent) / len(recent)
    if avg > 2:
        return 70   # overbought
    elif avg < -2:
        return 30   # oversold
    return 50

# ─── ANÁLISIS TÉCNICO BÁSICO ──────────────────────────────────────────────────
def get_candles_finnhub(symbol, resolution="5", count=20):
    """Trae velas desde Finnhub para análisis técnico básico."""
    try:
        to_ts   = int(time.time())
        from_ts = to_ts - count * 60 * int(resolution)
        url = (f"https://finnhub.io/api/v1/forex/candle"
               f"?symbol={symbol}&resolution={resolution}"
               f"&from={from_ts}&to={to_ts}&token={FINNHUB_KEY}")
        r = requests.get(url, timeout=10)
        d = r.json()
        if d.get("s") == "ok":
            return d.get("c", [])  # closing prices
    except Exception as e:
        logger.error(f"Candles error {symbol}: {e}")
    return []

def technical_score(closes):
    """
    Score técnico simple de -5 a +5.
    Basado en: momentum de velas, posición vs media, stoch aproximado.
    """
    if len(closes) < 5:
        return 0
    last   = closes[-1]
    prev5  = closes[-6:-1]
    ma5    = sum(prev5) / len(prev5)
    # Momentum: cuántas de las últimas 3 velas son alcistas
    momentum = sum(1 for i in range(-3, 0) if closes[i] > closes[i-1])
    # Posición vs MA
    above_ma = 1 if last > ma5 else -1
    # Stoch aproximado (posición dentro del rango últimas 14 velas)
    if len(closes) >= 14:
        high14 = max(closes[-14:])
        low14  = min(closes[-14:])
        rng = high14 - low14
        stoch = (last - low14) / rng * 100 if rng > 0 else 50
    else:
        stoch = 50
    # Score
    score = 0
    score += (momentum - 1.5)  # -1.5 a +1.5
    score += above_ma * 1.5
    if stoch < 25:
        score -= 1.5  # oversold → no short
    elif stoch > 75:
        score += 1.5  # overbought → no long
    return round(max(-5, min(5, score)), 2)

# ─── SEÑAL FINAL ──────────────────────────────────────────────────────────────
def compute_signal(asset, news_score, tech_score_val, ctx):
    """
    Combina news + técnica + contexto macro.
    Retorna (direction, composite_score, reason).
    REGLAS SAGRADAS:
    - news_score == 0 → NO ALERTA (sin catalizador)
    - SHORT solo si stoch/tech indica overbought
    - LONG solo si stoch/tech NO indica overbought extremo
    """
    rsi_est = estimate_rsi(asset, ctx)

    # HARD BLOCK 1: Sin catalizador de noticias = no alerta
    if news_score == 0:
        return None, 0, "Sin catalizador macro confirmado"

    # HARD BLOCK 2: No SHORT si oversold (RSI < 35)
    if rsi_est < 35 and news_score < 0:
        return None, 0, f"BLOQUEADO: RSI oversold ({rsi_est}) + señal SHORT contradictoria"

    # HARD BLOCK 3: No LONG si overbought (RSI > 70) sin noticia muy fuerte
    if rsi_est > 70 and news_score < 3:
        return None, 0, f"BLOQUEADO: RSI overbought ({rsi_est}) sin catalizador fuerte"

    # Score compuesto ponderado (news pesa más — protocolo Soros primero)
    composite = (news_score * 0.55) + (tech_score_val * 0.45)
    composite = round(composite * 2, 2)  # escalar a ~10

    if composite > 0:
        direction = "LONG"
    elif composite < 0:
        direction = "SHORT"
        composite = abs(composite)
    else:
        return None, 0, "Señal neutral"

    reason = f"News: {news_score:+.1f} | Tech: {tech_score_val:+.1f} | RSI est: {rsi_est}"
    return direction, composite, reason

# ─── FORMATEO DE ALERTA ───────────────────────────────────────────────────────
def format_alert(asset, direction, score, price, reason, ctx, high_attention):
    info = ASSETS[asset]
    pip  = info["pip"]
    name = info["name"]

    # SL/TP dinámicos según activo
    sl_pips = {"GOLD": 30, "SILVER": 25, "OIL": 40, "COPPER": 20, "PLATINUM": 35, "DXY": 15}
    sl_dist = sl_pips.get(asset, 30) * pip

    if direction == "LONG":
        sl = price - sl_dist
        tp = price + sl_dist * 3
    else:
        sl = price + sl_dist
        tp = price - sl_dist * 3

    rr = 3.0
    # ─── SISTEMA 3 NIVELES DE CONVICCIÓN (Kelly-inspired) ───────────────────
    if score >= 8.5:
        nivel = "🔴 MÁXIMA CONVICCIÓN"
        lotes = 0.10
        urgencia = "⚡⚡ APUESTA GRANDE — TODO ALINEADO"
    elif score >= 7.0:
        nivel = "🟠 ALTA CONVICCIÓN"
        lotes = 0.05
        urgencia = "⚡ APUESTA MEDIA — CONFLUENCIA FUERTE"
    else:
        nivel = "🟡 CONVICCIÓN NORMAL"
        lotes = 0.02
        urgencia = "Señal estándar — tamaño conservador"
    conviccion = nivel
    attention_tag = "⭐ VENTANA ALTA ATENCIÓN\n" if high_attention else ""

    # Contexto macro adicional
    macro_ctx = ""
    if ctx.get("iran_hormuz") == "active" and asset in ["OIL","GOLD"]:
        macro_ctx = "🌍 Iran/Hormuz activo — prima geopolítica activa\n"
    elif ctx.get("iran_hormuz") == "de-escalating" and asset == "OIL":
        macro_ctx = "🕊️ De-escalada en Hormuz — presión bajista en OIL\n"
    if ctx.get("fed_stance") == "hawkish" and asset == "GOLD":
        macro_ctx += "🏦 Fed hawkish — presión bajista en GOLD\n"
    elif ctx.get("fed_stance") == "dovish" and asset == "GOLD":
        macro_ctx += "🏦 Fed dovish — soporte alcista en GOLD\n"

    msg = (
        f"🚨 <b>SIMONS AGENT v3.3 — ALERTA</b>\n"
        f"{attention_tag}"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{name} → <b>{direction}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Entrada: <b>{price:.2f}</b>\n"
        f"🛑 SL:      <b>{sl:.2f}</b>\n"
        f"🎯 TP:      <b>{tp:.2f}</b>\n"
        f"📊 R/R:     <b>{rr}:1</b>\n"
        f"💪 Convicción: {conviccion} ({score:.1f}/10)\n\n"
        f"{macro_ctx}"
        f"📰 Señal: {reason}\n\n"
        f"⏰ {now_clt().strftime('%H:%M CLT')}\n"
        f"📦 Lotes sugeridos: <b>{lotes}</b>\n"
        f"💡 {urgencia}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ TIENES 10 MIN PARA ENTRAR\n"
        f"Monitoreo reversión activado (30 min)"
    )
    return msg, sl, tp

# ─── ALERTAS PRE-EVENTO ───────────────────────────────────────────────────────
def check_pre_event_alerts(prices):
    """Genera recomendaciones de limit orders antes de eventos macro clave."""
    n = now_clt()
    day_events = ECONOMIC_CALENDAR.get(n.weekday(), {})

    for event_time_str, event_data in day_events.items():
        eh, em = map(int, event_time_str.split(":"))
        event_dt = n.replace(hour=eh, minute=em, second=0)
        minutes_to_event = (event_dt - n).total_seconds() / 60

        # Alerta 45 min antes del evento
        if 40 <= minutes_to_event <= 50:
            asset  = event_data["impact"]
            price  = prices.get(asset)
            event  = event_data["event"]
            direct = event_data["direction"]

            if not price:
                continue

            pip = ASSETS[asset]["pip"]
            sl_dist = 40 * pip

            if "bearish_if_build" in direct:
                # EIA: si inventarios suben → oil baja
                limit_sell = price + 20 * pip
                sl  = limit_sell + sl_dist
                tp  = limit_sell - sl_dist * 3
                msg = (
                    f"📅 <b>PRE-EVENTO — {event}</b>\n"
                    f"En ~45 min | {event_time_str} CLT\n\n"
                    f"<b>{ASSETS[asset]['name']}</b>\n"
                    f"Escenario bearish (inventarios al alza):\n"
                    f"→ SELL LIMIT: <b>{limit_sell:.2f}</b>\n"
                    f"→ SL: {sl:.2f} | TP: {tp:.2f}\n\n"
                    f"⚠️ Coloca la orden AHORA antes del dato.\n"
                    f"Si inventarios bajan → cancela y evalúa LONG."
                )
            else:
                # NFP fuerte → gold baja (dólar sube)
                limit_sell = price + 15 * pip
                sl  = limit_sell + sl_dist
                tp  = limit_sell - sl_dist * 3
                msg = (
                    f"📅 <b>PRE-EVENTO — {event}</b>\n"
                    f"En ~45 min | {event_time_str} CLT\n\n"
                    f"<b>{ASSETS[asset]['name']}</b>\n"
                    f"Escenario bearish si NFP > expectativas:\n"
                    f"→ SELL LIMIT: <b>{limit_sell:.2f}</b>\n"
                    f"→ SL: {sl:.2f} | TP: {tp:.2f}\n\n"
                    f"⚠️ Coloca la orden AHORA antes del dato.\n"
                    f"Si NFP débil → LONG GOLD en su lugar."
                )
            send_telegram(msg)
            logger.info(f"Pre-event alert sent for {asset} — {event}")

# ─── CICLO PRINCIPAL ──────────────────────────────────────────────────────────
def main_cycle():
    logger.info("=== SIMONS Agent v3.3 — Ciclo iniciado ===")

    ctx     = load_macro_context()
    history = load_alert_history()
    ha      = is_high_attention()

    threshold = HIGH_ATTENTION_THRESHOLD if ha else ALERT_THRESHOLD
    if ha:
        logger.info("⭐ VENTANA DE ALTA ATENCIÓN ACTIVA")

    # 1. Obtener noticias (Finnhub)
    headlines = get_finnhub_news()
    news_scores = get_news_scores(headlines)

    # 2. Actualizar memoria macro
    ctx = update_macro_context(ctx, headlines, news_scores)

    # 3. Precios actuales
    prices = {}
    for asset, info in ASSETS.items():
        p = get_price_finnhub(info["symbol"])
        if p:
            prices[asset] = p
        time.sleep(0.3)  # rate limit

    # 4. Chequear reversiones activas (cada ciclo de 5 min)
    check_reversals(prices)

    # 5. Alertas pre-evento
    check_pre_event_alerts(prices)

    # 6. Analizar cada activo
    for asset, info in ASSETS.items():
        if asset == "DXY":
            continue  # Solo monitoreo, no alertas directas

        price = prices.get(asset)
        if not price:
            logger.warning(f"Sin precio para {asset}")
            continue

        # Análisis técnico
        closes = get_candles_finnhub(info["symbol"])
        tech   = technical_score(closes)
        time.sleep(0.5)

        ns = news_scores.get(asset, 0)
        direction, composite, reason = compute_signal(asset, ns, tech, ctx)

        logger.info(f"{asset}: news={ns:+.1f} tech={tech:+.2f} composite={composite:.2f} dir={direction}")

        if direction is None:
            logger.info(f"  → SKIP: {reason}")
            continue

        if composite < threshold:
            logger.info(f"  → SKIP: score {composite:.2f} < threshold {threshold}")
            continue

        if not can_send_alert(asset, history):
            logger.info(f"  → SKIP: anti-spam (< {MIN_ALERT_INTERVAL_MIN} min)")
            continue

        # ¡Alerta!
        msg, sl, tp = format_alert(asset, direction, composite, price, reason, ctx, ha)
        send_telegram(msg)
        register_alert(asset, history)

        # Activar monitor de reversión
        add_reversal_watch(asset, direction, price)
        logger.info(f"  → ALERTA ENVIADA: {direction} {asset} @ {price:.2f}")

    logger.info(f"=== Ciclo completo. Próximo en {'5' if ha else '30'} min ===\n")

# ─── LOOP ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    send_telegram(
        "🤖 <b>SIMONS Agent v3.4 — ONLINE</b>\n\n"
        "✅ Finnhub activo (noticias reales)\n"
        "✅ Memoria macro persistente\n"
        "✅ Monitor de reversión (5 min)\n"
        "✅ Alta atención: Lun-Jue 08:00-11:00 / 13:30-16:00 / 19:30\n"
        "✅ Alertas pre-evento (EIA / NFP)\n"
        "✅ Hard blocks activados\n\n"
        "Listo para proteger y hacer crecer tu capital. 💪"
    )

    while True:
        try:
            ha = is_high_attention()
            main_cycle()
            # En alta atención: ciclo cada 5 min. Normal: cada 30 min.
            interval = 5 * 60 if ha else 30 * 60
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Agente detenido manualmente.")
            break
        except Exception as e:
            logger.error(f"Error en ciclo principal: {e}")
            time.sleep(60)
