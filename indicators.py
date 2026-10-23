"""Technical indicators, written with pandas only (no extra libraries)."""
import numpy as np
import pandas as pd


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close, n=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    out = out.where(loss != 0, 100.0)
    return out.where(gain.notna())


def atr(df, n=14):
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df, n=14):
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    a = atr(df, n)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def add_indicators(df):
    """Return a copy of an OHLCV frame with all indicator columns added."""
    d = df.copy()
    c = d["close"]
    d["ema20"] = ema(c, 20)
    d["ema50"] = ema(c, 50)
    d["ema200"] = ema(c, 200)
    d["rsi"] = rsi(c)
    macd = ema(c, 12) - ema(c, 26)
    d["macd"] = macd
    d["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
    d["macd_hist"] = d["macd"] - d["macd_signal"]
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    d["bb_upper"] = mid + 2 * sd
    d["bb_lower"] = mid - 2 * sd
    d["bb_pctb"] = (c - d["bb_lower"]) / (d["bb_upper"] - d["bb_lower"]).replace(0, np.nan)
    d["atr"] = atr(d)
    d["adx"] = adx(d)
    d["vol_ratio"] = d["volume"] / d["volume"].rolling(20).mean().shift(1)
    d["high20"] = d["high"].rolling(20).max().shift(1)   # previous 20-candle high
    d["low20"] = d["low"].rolling(20).min().shift(1)     # previous 20-candle low
    return d
