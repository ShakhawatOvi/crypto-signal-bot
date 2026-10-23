"""
Free, key-less (or free-key) public data sources.

Candles come from Binance's public market-data API when it is reachable.
Binance blocks some cloud servers (HTTP 451), so the bot automatically
falls back to OKX, Gate and KuCoin, whose USDT prices track Binance closely.
None of this touches your Binance account.
"""
import time
import logging
from datetime import datetime, timezone

import pandas as pd
import requests

import config

log = logging.getLogger("data")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "crypto-signal-bot/1.0 (personal, read-only)"})
TIMEOUT = 15

INTERVAL_SECONDS = {"1d": 86400, "4h": 14400}


def _get(url, params=None, headers=None, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if r.status_code == 429:  # rate-limited: wait and retry
                time.sleep(5 * (attempt + 1))
                last = RuntimeError("429 rate limited")
                continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            # 4xx other than 429 won't be fixed by retrying
            raise
        except Exception as e:  # network trouble: short retry
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def _frame(rows):
    """rows: list of (open_time_ms, open, high, low, close, volume)."""
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df.drop_duplicates("time").sort_values("time").set_index("time")


# ------------------------------------------------------------ candle feeds ---
class SymbolNotFound(Exception):
    pass


def _binance(host):
    def fetch(base, interval, limit, end_ms=None):
        params = {"symbol": f"{base}{config.QUOTE}", "interval": interval, "limit": min(limit, 1000)}
        if end_ms:
            params["endTime"] = end_ms
        try:
            data = _get(f"https://{host}/api/v3/klines", params)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                raise SymbolNotFound(base)
            raise
        return _frame([(k[0], k[1], k[2], k[3], k[4], k[5]) for k in data])
    return fetch


def _okx(base, interval, limit, end_ms=None):
    bar = {"1d": "1Dutc", "4h": "4H"}[interval]
    inst = f"{base}-{config.QUOTE}"
    rows, after = [], end_ms
    # /candles gives the latest 300; /history-candles pages further back (100 each)
    url = "https://www.okx.com/api/v5/market/candles"
    while len(rows) < limit:
        params = {"instId": inst, "bar": bar, "limit": 300 if url.endswith("/candles") else 100}
        if after:
            params["after"] = str(after)
        data = _get(url, params)
        if data.get("code") not in ("0", 0):
            raise SymbolNotFound(base)
        batch = data.get("data", [])
        if not batch:
            break
        rows += [(int(k[0]), k[1], k[2], k[3], k[4], k[5]) for k in batch]
        after = int(batch[-1][0])  # oldest in batch (OKX returns newest first)
        url = "https://www.okx.com/api/v5/market/history-candles"
    if not rows:
        raise SymbolNotFound(base)
    return _frame(rows).tail(limit)


def _gate(base, interval, limit, end_ms=None):
    params = {"currency_pair": f"{base}_{config.QUOTE}", "interval": interval}
    if end_ms:
        step = INTERVAL_SECONDS[interval]
        params["to"] = end_ms // 1000
        params["from"] = end_ms // 1000 - step * min(limit, 999)
    else:
        params["limit"] = min(limit, 1000)
    try:
        data = _get("https://api.gateio.ws/api/v4/spot/candlesticks", params)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            raise SymbolNotFound(base)
        raise
    # [t(sec), quote_vol, close, high, low, open, base_vol, closed]
    return _frame([(int(k[0]) * 1000, k[5], k[3], k[4], k[2], k[6]) for k in data])


def _kucoin(base, interval, limit, end_ms=None):
    typ = {"1d": "1day", "4h": "4hour"}[interval]
    params = {"symbol": f"{base}-{config.QUOTE}", "type": typ}
    end_s = (end_ms // 1000) if end_ms else int(time.time())
    params["endAt"] = end_s
    params["startAt"] = end_s - INTERVAL_SECONDS[interval] * min(limit, 1500)
    data = _get("https://api.kucoin.com/api/v1/market/candles", params)
    if data.get("code") != "200000" or not data.get("data"):
        raise SymbolNotFound(base)
    # [time(sec), open, close, high, low, volume, turnover] newest first
    return _frame([(int(k[0]) * 1000, k[1], k[3], k[4], k[2], k[5]) for k in data["data"]])


CANDLE_SOURCES = [
    ("Binance (data-api.binance.vision)", _binance("data-api.binance.vision")),
    ("Binance (api.binance.com)", _binance("api.binance.com")),
    ("OKX", _okx),
    ("Gate", _gate),
    ("KuCoin", _kucoin),
]
_dead_sources = set()   # sources blocked/unreachable during this run


def get_candles(base, interval, limit, end_ms=None):
    """Return (DataFrame, source_name). Tries each source in turn."""
    errors = []
    for name, fetch in CANDLE_SOURCES:
        if name in _dead_sources:
            continue
        try:
            df = fetch(base, interval, limit, end_ms)
            if len(df) >= 30:
                return df, name
            errors.append(f"{name}: only {len(df)} candles")
        except SymbolNotFound:
            errors.append(f"{name}: pair not listed")
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            errors.append(f"{name}: HTTP {code}")
            if code in (403, 451):        # geo-blocked: skip for the rest of the run
                _dead_sources.add(name)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
            _dead_sources.add(name)
    raise RuntimeError(f"No candle data for {base}: " + "; ".join(errors))


def get_long_history(base, interval="1d", total=1000):
    """Several pages of candles for backtesting."""
    df, src = get_candles(base, interval, min(total, 1000))
    while len(df) < total:
        end_ms = int(df.index[0].timestamp() * 1000) - 1
        try:
            more, _ = get_candles(base, interval, min(1000, total - len(df)), end_ms=end_ms)
        except RuntimeError:
            break
        more = more[more.index < df.index[0]]
        if more.empty:
            break
        df = pd.concat([more, df])
    return df.tail(total), src


def drop_incomplete(df, interval):
    """Remove the candle that is still forming, so indicators use closed candles."""
    if df.empty:
        return df
    last_open = df.index[-1]
    if (datetime.now(timezone.utc) - last_open.to_pydatetime()).total_seconds() < INTERVAL_SECONDS[interval]:
        return df.iloc[:-1]
    return df


# ---------------------------------------------------- Binance pair listing ---
def binance_usdt_pairs():
    """Set of base assets with a TRADING spot USDT pair on Binance, or None if unreachable."""
    for host in ("data-api.binance.vision", "api.binance.com"):
        try:
            data = _get(f"https://{host}/api/v3/exchangeInfo", {"permissions": "SPOT"}, retries=1)
            return {
                s["baseAsset"] for s in data.get("symbols", [])
                if s.get("quoteAsset") == config.QUOTE and s.get("status") == "TRADING"
            }
        except Exception as e:
            log.warning("Binance exchangeInfo via %s failed: %s", host, e)
    return None


# -------------------------------------------------------------- CoinGecko ---
def coingecko_markets(pages=1, per_page=150):
    headers = {"x-cg-demo-api-key": config.COINGECKO_API_KEY} if config.COINGECKO_API_KEY else None
    out = []
    for page in range(1, pages + 1):
        out += _get(
            "https://api.coingecko.com/api/v3/coins/markets",
            {
                "vs_currency": "usd", "order": "market_cap_desc", "per_page": per_page,
                "page": page, "price_change_percentage": "24h,7d,30d",
            },
            headers=headers,
        )
    return out


# ------------------------------------------------------- Fear & Greed index ---
def fear_greed(limit=10):
    """List of (datetime, value) newest first. Source: alternative.me (attribution required)."""
    data = _get("https://api.alternative.me/fng/", {"limit": limit})
    return [
        (datetime.fromtimestamp(int(d["timestamp"]), tz=timezone.utc), int(d["value"]), d["value_classification"])
        for d in data.get("data", [])
    ]
