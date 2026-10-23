"""
The four analysis pillars. Each returns a score from -1 to +1 plus
plain-English reasons, so every alert explains itself.
"""
import math

import config


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _ok(*vals):
    return all(v is not None and not (isinstance(v, float) and math.isnan(v)) for v in vals)


# ================================================================ TECHNICAL ===
def technical_score(daily, h4=None):
    """
    daily, h4: indicator frames from indicators.add_indicators (closed candles only).
    The daily chart decides the trend; the 4h chart fine-tunes entry timing.
    Returns (score, reasons, info) or (None, [why], {}) if there is too little data.
    """
    if daily is None or len(daily) < 60:
        return None, ["not enough price history"], {}
    d, p = daily.iloc[-1], daily.iloc[-2]
    if not _ok(d.ema20, d.ema50, d.rsi, d.macd_hist, d.atr):
        return None, ["indicators not ready"], {}
    reasons = []

    # --- 1. Trend (daily)
    t = 0.35 if d.close > d.ema50 else -0.35
    t += 0.25 if d.ema20 > d.ema50 else -0.25
    if _ok(d.ema200):
        t += 0.25 if d.close > d.ema200 else -0.25
    ema50_prev = daily["ema50"].iloc[-6]
    if _ok(ema50_prev):
        t += 0.15 if d.ema50 > ema50_prev else -0.15
    if _ok(d.adx):
        if d.adx > 25:
            t *= 1.2
        elif d.adx < 18:
            t *= 0.7
    t = _clip(t)
    if t >= 0.5:
        reasons.append("📈 Daily uptrend (price above rising 20/50" + ("/200" if _ok(d.ema200) else "") + " EMAs)")
    elif t <= -0.5:
        reasons.append("📉 Daily downtrend (price below falling EMAs)")
    else:
        reasons.append("↔️ Daily trend mixed/sideways")

    # --- 2. Momentum (daily)
    m = 0.3 if d.macd_hist > 0 else -0.3
    m += 0.2 if d.macd_hist > p.macd_hist else -0.2
    r = d.rsi
    if 50 <= r <= 68:
        m += 0.3
    elif 68 < r <= 75:
        m += 0.1
    elif r > 75:
        m -= 0.3
        reasons.append(f"🔥 RSI {r:.0f}: overbought, risk of pullback")
    elif 40 <= r < 50:
        m -= 0.1
    elif 30 <= r < 40:
        m -= 0.3
    else:
        m += 0.1
        reasons.append(f"🧊 RSI {r:.0f}: deeply oversold (bounce possible, trend still weak)")
    if p.macd_hist <= 0 < d.macd_hist:
        reasons.append("✅ Daily MACD just turned bullish")
    elif p.macd_hist >= 0 > d.macd_hist:
        reasons.append("❌ Daily MACD just turned bearish")
    m = _clip(m)

    # --- 3. Setup / timing
    s = 0.0
    vr = d.vol_ratio if _ok(d.vol_ratio) else 1.0
    if _ok(d.high20) and d.close > d.high20:
        s += 0.5 if vr >= 1.5 else 0.25
        reasons.append(f"🚀 Broke above 20-day high" + (f" on {vr:.1f}x normal volume" if vr >= 1.5 else ""))
    elif _ok(d.low20) and d.close < d.low20:
        s -= 0.5 if vr >= 1.5 else 0.25
        reasons.append(f"🔻 Broke below 20-day low" + (f" on {vr:.1f}x volume" if vr >= 1.5 else ""))
    if _ok(d.bb_pctb):
        if d.bb_pctb > 1.0:
            s -= 0.3
            reasons.append("⚠️ Stretched above upper Bollinger Band")
        elif d.bb_pctb < 0.2 and t > 0.3:
            s += 0.35
            reasons.append("🎯 Pullback to lower band inside an uptrend (dip-buy zone)")

    if h4 is not None and len(h4) >= 60:
        h, hp = h4.iloc[-1], h4.iloc[-2]
        if _ok(h.ema20, h.ema50, h.rsi, h.macd_hist, hp.macd_hist):
            s += 0.1 if h.ema20 > h.ema50 else -0.1
            if hp.macd_hist <= 0 < h.macd_hist:
                s += 0.2
                reasons.append("⏱️ 4h momentum just turned up (entry timing)")
            elif hp.macd_hist >= 0 > h.macd_hist:
                s -= 0.2
                reasons.append("⏱️ 4h momentum just turned down")
            if t > 0.3 and h.rsi < 40:
                s += 0.25
                reasons.append(f"🎯 4h RSI {h.rsi:.0f}: short-term dip in a daily uptrend")
            elif h.rsi > 78:
                s -= 0.15
    s = _clip(s)

    score = _clip(0.4 * t + 0.35 * m + 0.25 * s)
    info = {
        "price": float(d.close), "atr": float(d.atr), "rsi": float(r),
        "trend": round(t, 2), "momentum": round(m, 2), "setup": round(s, 2),
        "low20": float(d.low20) if _ok(d.low20) else None,
    }
    return score, reasons, info


# ============================================================== FUNDAMENTAL ===
def fundamental_score(coin, btc_30d=None, btc_7d=None):
    """coin: one row from CoinGecko /coins/markets."""
    f, reasons = 0.0, []
    mcap = coin.get("market_cap") or 0
    vol = coin.get("total_volume") or 0
    if mcap > 0:
        turnover = vol / mcap
        if turnover >= 0.15:
            f += 0.25
            reasons.append(f"💧 Very active trading ({turnover:.0%} of market cap/day)")
        elif turnover >= 0.05:
            f += 0.1
        elif turnover < 0.02:
            f -= 0.25
            reasons.append("🐢 Thin trading volume vs market cap")

    p30 = coin.get("price_change_percentage_30d_in_currency")
    p7 = coin.get("price_change_percentage_7d_in_currency")
    if _ok(p30, btc_30d):
        rs = p30 - btc_30d
        if rs > 15:
            f += 0.3
            reasons.append(f"💪 Beat BTC by {rs:.0f}% over 30 days")
        elif rs > 5:
            f += 0.15
        elif rs < -15:
            f -= 0.3
            reasons.append(f"🪫 Lagged BTC by {abs(rs):.0f}% over 30 days")
        elif rs < -5:
            f -= 0.15
    if _ok(p7, btc_7d):
        f += 0.1 if p7 > btc_7d else -0.1

    fdv = coin.get("fully_diluted_valuation")
    if fdv and mcap:
        ratio = fdv / mcap
        if ratio > 3:
            f -= 0.3
            reasons.append(f"🧮 Heavy future dilution (FDV {ratio:.1f}x market cap)")
        elif ratio > 1.8:
            f -= 0.15
        elif ratio <= 1.2:
            f += 0.1

    ath = coin.get("ath_change_percentage")
    if _ok(ath):
        if ath > -20:
            f += 0.1
        elif ath < -85:
            f -= 0.2
            reasons.append(f"🕳️ Still {abs(ath):.0f}% below all-time high")

    rank = coin.get("market_cap_rank") or 999
    if rank <= 10:
        f += 0.1
    return _clip(f), reasons


# ================================================================ SENTIMENT ===
def market_sentiment(fng, btc_trend):
    """
    Market-wide part, same for every coin.
    fng: list of (time, value, label) newest first. btc_trend: BTC daily trend score (-1..1) or None.
    For swing trades the Fear & Greed index is used contrarian-style:
    extreme fear has historically been a better place to buy than extreme greed.
    """
    s, reasons = 0.0, []
    if fng:
        v, label = fng[0][1], fng[0][2]
        if v <= 20:
            s += 0.4
        elif v <= 35:
            s += 0.2
        elif v >= 80:
            s -= 0.4
        elif v >= 65:
            s -= 0.15
        if len(fng) >= 8 and fng[0][1] - fng[7][1] >= 10 and v < 60:
            s += 0.1
        reasons.append(f"😨 Fear & Greed {v} ({label})")
    btc_part = 0.0
    if btc_trend is not None:
        if btc_trend <= -0.5:
            btc_part = -0.4
            reasons.append("🐻 BTC in a daily downtrend: altcoins usually struggle")
        elif btc_trend >= 0.5:
            btc_part = 0.2
            reasons.append("🐂 BTC in a daily uptrend: supportive backdrop")
    return s, btc_part, reasons


def sentiment_score(market_part, btc_part, news_tone, is_btc=False):
    s = market_part + (0 if is_btc else btc_part) + 0.5 * news_tone
    return _clip(s)


# ================================================================== COMBINE ===
def combine(tech, fund, sent, events):
    w = config.WEIGHTS
    parts = {"technical": tech, "fundamental": fund, "sentiment": sent, "events": events}
    total_w = sum(w[k] for k, v in parts.items() if v is not None)
    score = sum(w[k] * v for k, v in parts.items() if v is not None) / total_w
    return round(100 * score, 1)


def risk_levels(price, atr, low20=None):
    """Swing stop-loss and targets from the daily average true range."""
    stop = price - config.STOP_ATR_MULT * atr
    # if the recent 20-day low is a little below that, use it (natural support)
    if low20 and stop * 0.97 < low20 < stop:
        stop = low20 * 0.995
    risk = price - stop
    return {
        "entry": price,
        "stop": stop,
        "t1": price + config.TARGET1_R * risk,
        "t2": price + config.TARGET2_R * risk,
        "risk_pct": 100 * risk / price,
    }
