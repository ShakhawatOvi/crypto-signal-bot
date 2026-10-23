"""
Backtest: how would the BUY/SELL rules have done on past data?

What it can test: the technical pillar + the market-sentiment pillar
(Fear & Greed history and the BTC trend). Free historical news and
fundamental snapshots don't exist, so those pillars are left out here.
The same scoring code as the live bot is used, with no peeking ahead:
each decision only sees candles that had already closed.

Usage:
  python backtest.py                   top 20 coins, ~1000 days
  python backtest.py --coins 50 --days 1000 --buy-score 40
"""
import argparse
import os
from datetime import timezone

import pandas as pd

import config
import data_sources as ds
import indicators
import analysis

FEE = 0.001  # 0.1% per side (Binance spot standard fee)
ALLOC = 0.10  # equity curve assumes 10% of capital per signal


def fng_history():
    try:
        rows = ds.fear_greed(limit=0)
        return {t.date(): (v, label) for t, v, label in rows}
    except Exception as e:
        print("Fear & Greed history unavailable:", e)
        return {}


def score_series(daily, fng_by_date, btc_trend_by_date, is_btc):
    """Daily score for every closed candle (only using data up to that candle)."""
    out = {}
    dates = list(daily.index)
    for i in range(60, len(daily)):
        window = daily.iloc[: i + 1]
        tech, _, info = analysis.technical_score(window)
        if tech is None:
            continue
        day = dates[i].date()
        fng_rows = []
        for k in range(8):  # newest first, like the live API
            v = fng_by_date.get((dates[i] - pd.Timedelta(days=k)).date())
            if v:
                fng_rows.append((None, v[0], v[1]))
        m_part, b_part, _ = analysis.market_sentiment(fng_rows if len(fng_rows) == 8 else fng_rows[:1],
                                                      btc_trend_by_date.get(day))
        sent = analysis.sentiment_score(m_part, b_part, 0.0, is_btc=is_btc)
        out[dates[i]] = (analysis.combine(tech, None, sent, None), tech, info)
    return out


def simulate(sym, daily, scores, buy_score):
    trades, pos = [], None
    dates = list(daily.index)
    for i in range(len(dates) - 1):
        d, nxt = dates[i], dates[i + 1]
        bar_next = daily.loc[nxt]
        if pos is None:
            s = scores.get(d)
            if s and s[0] >= buy_score and s[1] >= config.MIN_TECH_FOR_BUY and pos is None:
                if pos is None and (not trades or nxt >= trades[-1]["cooldown_until"]):
                    entry = float(bar_next.open)
                    lv = analysis.risk_levels(entry, s[2]["atr"], s[2].get("low20"))
                    pos = {"entry": entry, "stop": lv["stop"], "t1": lv["t1"], "t2": lv["t2"],
                           "t1_hit": False, "open": nxt, "score": s[0]}
            continue
        # manage open position on the bar of day d (entered at open of pos['open'])
        bar = daily.loc[d]
        if d < pos["open"]:
            continue
        exit_px, reason = None, None
        if bar.low <= pos["stop"]:
            exit_px, reason = min(pos["stop"], float(bar.open)), ("break-even stop" if pos["t1_hit"] else "stop-loss")
        elif bar.high >= pos["t2"]:
            exit_px, reason = pos["t2"], "target 2"
        else:
            if bar.high >= pos["t1"] and not pos["t1_hit"]:
                pos["t1_hit"], pos["stop"] = True, pos["entry"]
            s = scores.get(d)
            if s and s[0] <= config.EXIT_SCORE:
                exit_px, reason = float(bar_next.open), "score exit"
            elif (d - pos["open"]).days >= config.MAX_HOLD_DAYS:
                exit_px, reason = float(bar_next.open), "time stop"
        if exit_px is not None:
            gross = (exit_px - pos["entry"]) / pos["entry"]
            if pos["t1_hit"]:
                gross = 0.5 * (pos["t1"] - pos["entry"]) / pos["entry"] + 0.5 * gross
            net = gross - 2 * FEE
            trades.append({"symbol": sym, "opened": pos["open"].date(), "closed": d.date(),
                           "entry": pos["entry"], "exit": exit_px, "pct": round(100 * net, 2),
                           "reason": reason, "score": pos["score"],
                           "cooldown_until": d + pd.Timedelta(days=config.REBUY_COOLDOWN_DAYS)})
            pos = None
    return trades


def summarise(trades):
    if not trades:
        return {"trades": 0}
    r = pd.Series([t["pct"] for t in trades])
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {
        "trades": len(r), "win_rate": round(100 * (r > 0).mean(), 1),
        "avg_pct": round(r.mean(), 2), "median_pct": round(r.median(), 2),
        "best": round(r.max(), 1), "worst": round(r.min(), 1),
        "profit_factor": round(gains / losses, 2) if losses > 0 else float("inf"),
    }


def equity_curve(trades):
    eq, peak, mdd, curve = 1.0, 1.0, 0.0, []
    for t in sorted(trades, key=lambda t: t["closed"]):
        eq *= 1 + ALLOC * t["pct"] / 100
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        curve.append((t["closed"], eq))
    return eq, mdd, curve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", type=int, default=20)
    ap.add_argument("--days", type=int, default=1000)
    ap.add_argument("--buy-score", type=float, default=config.BUY_SCORE)
    args = ap.parse_args()

    print("Loading coin list and Fear & Greed history...")
    markets = ds.coingecko_markets(pages=1, per_page=150)
    syms = []
    for c in markets:
        s = c["symbol"].upper()
        n = c["name"].lower()
        if s in config.EXCLUDE_SYMBOLS or any(w in n for w in config.EXCLUDE_NAME_WORDS) or s in syms:
            continue
        price, p30 = c.get("current_price") or 0, c.get("price_change_percentage_30d_in_currency") or 0
        if 0.97 <= price <= 1.03 and abs(p30) < 2:
            continue
        syms.append(s)
    syms = syms[: args.coins]
    fng = fng_history()

    print("Loading BTC history...")
    btc_raw, src = ds.get_long_history("BTC", "1d", args.days)
    btc = indicators.add_indicators(ds.drop_incomplete(btc_raw, "1d"))
    btc_trend = {}
    for i in range(60, len(btc)):
        _, _, info = analysis.technical_score(btc.iloc[: i + 1])
        if info:
            btc_trend[btc.index[i].date()] = info["trend"]

    all_trades, per_coin, holds = [], [], []
    for sym in syms:
        try:
            raw, src = ds.get_long_history(sym, "1d", args.days)
        except Exception as e:
            print(f"  {sym}: skipped ({e})")
            continue
        daily = indicators.add_indicators(ds.drop_incomplete(raw, "1d"))
        scores = score_series(daily, fng, btc_trend, sym == "BTC")
        trades = simulate(sym, daily, scores, args.buy_score)
        stats = summarise(trades)
        first = daily.index[60]
        hold = 100 * (daily["close"].iloc[-1] / daily.loc[first, "close"] - 1)
        holds.append(hold)
        per_coin.append({"symbol": sym, "days": len(daily) - 60, "source": src, "buy_hold_pct": round(hold, 1), **stats})
        all_trades += trades
        print(f"  {sym:>6}: {stats.get('trades', 0):>3} trades, win {stats.get('win_rate', '-')}%, "
              f"avg {stats.get('avg_pct', '-')}%  (buy & hold {hold:+.0f}%)  [{src}]")

    total = summarise(all_trades)
    final_eq, mdd, _ = equity_curve(all_trades)
    btc_hold = 100 * (btc["close"].iloc[-1] / btc["close"].iloc[60] - 1)

    os.makedirs("reports", exist_ok=True)
    pd.DataFrame(all_trades).drop(columns=["cooldown_until"], errors="ignore").to_csv("reports/backtest_trades.csv", index=False)
    lines = [
        "# Backtest results", "",
        f"Period: about {args.days} days of daily candles, {len(per_coin)} coins, BUY score >= {args.buy_score:g}.",
        "Pillars tested: technical + market sentiment (Fear & Greed, BTC trend). "
        "News/event and fundamental pillars are live-only (no free history).",
        f"Fees: {FEE * 100:.1f}% per side included. Coins are today's top list, so dead coins are missing "
        "(this flatters results a little).", "",
        "## Overall",
        f"- Signals (closed trades): **{total.get('trades', 0)}**",
        f"- Win rate: **{total.get('win_rate', '-')}%**",
        f"- Average trade: **{total.get('avg_pct', '-')}%** (median {total.get('median_pct', '-')}%)",
        f"- Profit factor (gains / losses): **{total.get('profit_factor', '-')}**",
        f"- Best / worst trade: {total.get('best', '-')}% / {total.get('worst', '-')}%",
        f"- Equity if 10% of capital went into each signal: **{(final_eq - 1) * 100:+.1f}%**, "
        f"max drawdown {mdd * 100:.1f}%",
        f"- For comparison, simply holding BTC: {btc_hold:+.1f}%", "",
        "## Per coin", "",
        "| Coin | Trades | Win % | Avg % | Profit factor | Buy & hold % |",
        "|------|-------:|------:|------:|--------------:|-------------:|",
    ]
    for p in sorted(per_coin, key=lambda p: p.get("avg_pct", -999), reverse=True):
        lines.append(f"| {p['symbol']} | {p.get('trades', 0)} | {p.get('win_rate', '-')} | {p.get('avg_pct', '-')} | "
                     f"{p.get('profit_factor', '-')} | {p['buy_hold_pct']} |")
    reasons = pd.Series([t["reason"] for t in all_trades]).value_counts() if all_trades else {}
    lines += ["", "## How trades ended", ""] + [f"- {k}: {v}" for k, v in dict(reasons).items()]
    with open("reports/backtest_results.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[6:14]))
    print("\nFull report: reports/backtest_results.md, trades: reports/backtest_trades.csv")


if __name__ == "__main__":
    main()
