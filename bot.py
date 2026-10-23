"""
Crypto swing-signal bot  -  analysis + Telegram alerts only. It never trades.

Each run:
  1. reads your Telegram commands (/hold, /unhold, /status, /stats, /help)
  2. builds the coin list (top coins by market cap that trade as XXX/USDT on Binance)
  3. scores every coin on 4 pillars: technical, fundamental, sentiment, events
  4. follows up on open signals (stop-loss, targets, exit) and on your holdings
  5. sends new BUY alerts (best few only) and, once a day, a market summary

Usage:
  python bot.py              normal run
  python bot.py --dry-run    analyse and print, but send nothing and save nothing
  python bot.py --summary    also force the daily summary now
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config
import data_sources as ds
import indicators
import analysis
import news
import notifier
from notifier import esc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

NOW = datetime.now(timezone.utc)
LOCAL_TZ = ZoneInfo(config.TIMEZONE)
DISCLAIMER = "<i>Signal, not financial advice. Check the chart and size your position before trading.</i>"


# ------------------------------------------------------------------ helpers ---
def fmt_price(p):
    if p is None:
        return "-"
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:,.3f}"
    if p >= 0.01:
        return f"${p:.5f}"
    return f"${p:.8f}"


def pct(a, b):
    return 100 * (a - b) / b


def binance_link(sym):
    return f'<a href="https://www.binance.com/en/trade/{sym}_{config.QUOTE}?type=spot">Open {sym}/{config.QUOTE} on Binance</a>'


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s):
    return datetime.fromisoformat(s) if s else None


def load_state():
    try:
        with open(config.STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    for k, v in {"active": {}, "holdings": {}, "cooldown": {}, "holding_alerts": {},
                 "history": [], "last_update_id": 0, "last_summary_date": ""}.items():
        st.setdefault(k, v)
    # MY_HOLDINGS (GitHub variable) is added once; after that /unhold can remove it
    st.setdefault("env_holdings_added", [])
    for sym in config.MY_HOLDINGS:
        if sym not in st["env_holdings_added"]:
            st["holdings"].setdefault(sym, {"entry": None, "since": iso(NOW)})
            st["env_holdings_added"].append(sym)
    return st


def save_state(st):
    os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
    st["history"] = st["history"][-300:]
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, default=str)


# --------------------------------------------------------- Telegram commands ---
HELP = (
    "<b>Commands</b> (answered on the bot's next scheduled run)\n"
    "/hold BTC 65000 - tell me you bought BTC at 65000 (price optional)\n"
    "/unhold BTC - you sold it\n"
    "/status - open signals and your holdings\n"
    "/stats - track record of the bot's signals\n"
    "/help - this list"
)


def _clean_symbol(s):
    s = re.sub(r"[^A-Z0-9]", "", s.upper())
    return s[: -len(config.QUOTE)] if s.endswith(config.QUOTE) and len(s) > len(config.QUOTE) else s


def handle_commands(st):
    texts, st["last_update_id"] = notifier.get_commands(st.get("last_update_id", 0))
    replies = []
    for t in texts:
        parts = t.split()
        if not parts:
            continue
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]
        if cmd == "/hold" and args:
            sym = _clean_symbol(args[0])
            entry = None
            if len(args) > 1:
                try:
                    entry = float(args[1].replace(",", "").replace("$", ""))
                except ValueError:
                    pass
            st["holdings"][sym] = {"entry": entry, "since": iso(NOW)}
            replies.append(f"👍 Watching your <b>{esc(sym)}</b>" + (f" (bought at {fmt_price(entry)})" if entry else "")
                           + ". I'll alert you if it turns bearish.")
        elif cmd == "/unhold" and args:
            sym = _clean_symbol(args[0])
            st["holdings"].pop(sym, None)
            st["holding_alerts"].pop(sym, None)
            replies.append(f"👍 Removed <b>{esc(sym)}</b> from your holdings.")
        elif cmd in ("/status", "/list"):
            replies.append("__STATUS__")
        elif cmd == "/stats":
            replies.append(stats_text(st))
        elif cmd in ("/help", "/start"):
            replies.append(HELP)
        else:
            replies.append(f"🤔 Unknown command: {esc(t[:40])}\n\n{HELP}")
    return replies


def stats_text(st):
    h = st["history"]
    if not h:
        return "📊 No closed signals yet. Results appear here after the first signals finish."
    wins = [x for x in h if x["pct"] > 0]
    avg = sum(x["pct"] for x in h) / len(h)
    last = "\n".join(f"• {x['symbol']}: {x['pct']:+.1f}% ({esc(x['reason'])})" for x in h[-8:])
    return (f"📊 <b>Bot signal track record</b> (paper results, before fees)\n"
            f"Closed signals: {len(h)}\nWin rate: {100 * len(wins) / len(h):.0f}%\n"
            f"Average result: {avg:+.2f}% per signal\n\nLast ones:\n{last}")


# ------------------------------------------------------------- the universe ---
def build_universe(st):
    markets = ds.coingecko_markets(pages=1, per_page=150)
    pairs = ds.binance_usdt_pairs()
    if pairs is None:
        log.warning("Binance pair list unreachable: not filtering by Binance listing")
    chosen, seen = [], set()
    for c in markets:
        sym = (c.get("symbol") or "").upper()
        name = (c.get("name") or "").lower()
        if not sym or sym in seen or sym in config.EXCLUDE_SYMBOLS:
            continue
        if any(w in name for w in config.EXCLUDE_NAME_WORDS):
            continue
        price = c.get("current_price") or 0
        p30 = c.get("price_change_percentage_30d_in_currency") or 0
        if 0.97 <= price <= 1.03 and abs(p30) < 2:   # looks like a stablecoin
            continue
        if pairs is not None and sym not in pairs:
            continue
        seen.add(sym)
        chosen.append(c)
    by_sym = {c["symbol"].upper(): c for c in chosen}
    universe = chosen[: config.TOP_N_COINS]
    # always include open signals and your holdings, even outside the top N
    for sym in list(st["active"]) + list(st["holdings"]):
        if sym not in {c["symbol"].upper() for c in universe}:
            universe.append(by_sym.get(sym, {"symbol": sym, "name": sym}))
    log.info("Universe: %d coins", len(universe))
    return universe, markets


# --------------------------------------------------------------- analysing ---
def analyse_all(universe, markets):
    fng = []
    try:
        fng = ds.fear_greed(10)
    except Exception as e:
        log.warning("Fear & Greed unavailable: %s", e)

    btc = next((c for c in markets if c.get("symbol", "").lower() == "btc"), {})
    btc_trend = None
    try:
        bd, _ = ds.get_candles("BTC", "1d", config.DAILY_CANDLES)
        bdi = indicators.add_indicators(ds.drop_incomplete(bd, "1d"))
        _, _, binfo = analysis.technical_score(bdi)
        btc_trend = binfo.get("trend")
    except Exception as e:
        log.warning("BTC regime unavailable: %s", e)
    market_part, btc_part, market_reasons = analysis.market_sentiment(fng, btc_trend)

    news_items = []
    try:
        news_items = news.fetch_news()
    except Exception as e:
        log.warning("News unavailable: %s", e)
    matchers = news.build_matchers(universe)

    results, sources, failures = {}, {}, []
    for coin in universe:
        sym = coin["symbol"].upper()
        try:
            draw, src = ds.get_candles(sym, "1d", config.DAILY_CANDLES)
            hraw, _ = ds.get_candles(sym, "4h", config.H4_CANDLES)
            sources[src] = sources.get(src, 0) + 1
        except Exception as e:
            failures.append(sym)
            log.warning("%s skipped: %s", sym, e)
            continue
        daily = indicators.add_indicators(ds.drop_incomplete(draw, "1d"))
        h4 = indicators.add_indicators(ds.drop_incomplete(hraw, "4h"))
        tech, tech_reasons, info = analysis.technical_score(daily, h4)
        if tech is None:
            continue
        fund, fund_reasons = (None, [])
        if coin.get("market_cap"):
            fund, fund_reasons = analysis.fundamental_score(
                coin, btc.get("price_change_percentage_30d_in_currency"),
                btc.get("price_change_percentage_7d_in_currency"))
        nw = news.analyse_coin_news(sym, news_items, matchers)
        sent = analysis.sentiment_score(market_part, btc_part, nw["tone_score"], is_btc=(sym == "BTC"))
        events = nw["events_score"] if nw["n_headlines"] else 0.0
        score = analysis.combine(tech, fund, sent, events)
        results[sym] = {
            "symbol": sym, "name": coin.get("name", sym), "rank": coin.get("market_cap_rank"),
            "score": score, "tech": tech, "fund": fund, "sent": sent, "events": events,
            "critical_event": nw["critical_negative"], "info": info,
            "live_price": float(hraw["close"].iloc[-1]), "h4_raw": hraw,
            "reasons": tech_reasons + fund_reasons + nw["event_reasons"],
            "event_reasons": nw["event_reasons"],
            "headlines": nw["headlines"], "tone": nw["tone_counts"],
        }
        time.sleep(0.15)  # be polite to free APIs
    ctx = {"fng": fng, "btc_trend": btc_trend, "market_reasons": market_reasons,
           "sources": sources, "failures": failures, "n_news": len(news_items)}
    return results, ctx


def pillar_line(r):
    f = "n/a" if r["fund"] is None else f"{r['fund']:+.2f}"
    return f"Tech {r['tech']:+.2f} | Fund {f} | Sent {r['sent']:+.2f} | Events {r['events']:+.2f}"


# ------------------------------------------------------------ open signals ---
def close_trade(st, sym, price, reason):
    t = st["active"].pop(sym)
    res = pct(price, t["entry"])
    if t.get("t1_hit"):   # half was taken at target 1
        res = 0.5 * pct(t["t1"], t["entry"]) + 0.5 * res
    st["history"].append({"symbol": sym, "entry": t["entry"], "exit": price, "pct": round(res, 2),
                          "opened": t["opened"], "closed": iso(NOW), "reason": reason})
    st["cooldown"][sym] = iso(NOW + timedelta(days=config.REBUY_COOLDOWN_DAYS))
    return res


def follow_up_signals(st, results):
    alerts = []
    for sym in list(st["active"]):
        t = st["active"][sym]
        r = results.get(sym)
        if r is None:
            continue
        price = r["live_price"]
        since = parse_iso(t.get("last_check") or t["opened"]) - timedelta(hours=4)
        path = r["h4_raw"][r["h4_raw"].index >= since]
        outcome = None
        for _, c in path.iterrows():          # stop is checked first (conservative)
            if c.low <= t["stop"]:
                outcome = ("stop", t["stop"])
                break
            if c.high >= t["t2"]:
                outcome = ("t2", t["t2"])
                break
            if c.high >= t["t1"] and not t.get("t1_hit"):
                t["t1_hit"] = True
                t["stop"] = t["entry"]       # move stop to break-even
                alerts.append(
                    f"🎯 <b>{sym}: Target 1 reached</b> ({fmt_price(t['t1'])}, {pct(t['t1'], t['entry']):+.1f}%)\n"
                    f"Suggested: sell about half, move your stop-loss up to your entry ({fmt_price(t['entry'])}).\n"
                    f"Next target: {fmt_price(t['t2'])}\n{binance_link(sym)}")
        t["last_check"] = iso(NOW)
        held_days = (NOW - parse_iso(t["opened"])).days

        if outcome and outcome[0] == "stop":
            res = close_trade(st, sym, outcome[1], "stop-loss" if not t.get("t1_hit") else "break-even stop")
            alerts.append(f"🛑 <b>SELL {sym}: stop-loss hit</b> at {fmt_price(outcome[1])}\n"
                          f"Signal result: {res:+.1f}%. Exit if you still hold it.\n{binance_link(sym)}")
        elif outcome and outcome[0] == "t2":
            res = close_trade(st, sym, outcome[1], "target 2")
            alerts.append(f"🏁 <b>SELL {sym}: Target 2 reached</b> at {fmt_price(outcome[1])}\n"
                          f"Signal result: {res:+.1f}%. Consider taking the rest of the profit.\n{binance_link(sym)}")
        elif r["critical_event"]:
            res = close_trade(st, sym, price, "critical negative news")
            alerts.append(f"🚨 <b>SELL {sym}: critical negative news</b>\n"
                          + "\n".join(f"• {esc(x)}" for x in r["event_reasons"] if x.startswith("⚠️"))
                          + f"\nPrice {fmt_price(price)} ({res:+.1f}% from signal). Consider exiting now.\n{binance_link(sym)}")
        elif r["score"] <= config.EXIT_SCORE:
            res = close_trade(st, sym, price, f"score fell to {r['score']:+.0f}")
            why = r["event_reasons"] + [x for x in r["reasons"] if x not in r["event_reasons"]]
            alerts.append(f"🔴 <b>SELL {sym}: setup has turned bearish</b> (score {r['score']:+.0f})\n"
                          f"{pillar_line(r)}\n" + "\n".join(f"• {esc(x)}" for x in why[:5])
                          + f"\nPrice {fmt_price(price)} ({res:+.1f}% from signal).\n{binance_link(sym)}")
        elif held_days >= config.MAX_HOLD_DAYS:
            res = close_trade(st, sym, price, "time stop")
            alerts.append(f"⌛ <b>{sym}: {held_days} days without reaching target</b> - consider closing.\n"
                          f"Price {fmt_price(price)} ({res:+.1f}% from signal).\n{binance_link(sym)}")
    return alerts


def follow_up_holdings(st, results):
    alerts = []
    for sym, h in st["holdings"].items():
        if sym in st["active"]:
            continue  # already followed as a bot signal
        r = results.get(sym)
        if r is None:
            continue
        kind = None
        if r["critical_event"]:
            kind = "CRITICAL"
        elif r["score"] <= config.STRONG_SELL_SCORE:
            kind = "SELL"
        prev = st["holding_alerts"].get(sym)
        if kind is None:
            if prev and r["score"] > config.EXIT_SCORE:
                st["holding_alerts"].pop(sym, None)   # recovered: allow future warnings
            continue
        if prev and prev["type"] == kind and NOW - parse_iso(prev["time"]) < timedelta(hours=24):
            continue  # don't repeat the same warning within 24h
        st["holding_alerts"][sym] = {"type": kind, "time": iso(NOW)}
        pl = f" ({pct(r['live_price'], h['entry']):+.1f}% vs your buy)" if h.get("entry") else ""
        head = "🚨 <b>Your {0}: critical negative news</b>" if kind == "CRITICAL" else "🔴 <b>SELL warning on your {0}</b>"
        why = r["event_reasons"] + [x for x in r["reasons"] if x not in r["event_reasons"]]
        alerts.append(head.format(sym) + f" - score {r['score']:+.0f}\n"
                      f"Price {fmt_price(r['live_price'])}{pl}\n{pillar_line(r)}\n"
                      + "\n".join(f"• {esc(x)}" for x in why[:6])
                      + f"\n{binance_link(sym)}\n{DISCLAIMER}")
    return alerts


# --------------------------------------------------------------- new BUYs ---
def new_buy_signals(st, results):
    cands = []
    for sym, r in results.items():
        if sym in st["active"] or r["critical_event"]:
            continue
        cd = st["cooldown"].get(sym)
        if cd and parse_iso(cd) > NOW:
            continue
        if r["score"] >= config.BUY_SCORE and r["tech"] >= config.MIN_TECH_FOR_BUY:
            cands.append(r)
    cands.sort(key=lambda r: r["score"], reverse=True)
    alerts = []
    for r in cands[: config.MAX_NEW_BUYS_PER_RUN]:
        sym, info = r["symbol"], r["info"]
        price = r["live_price"]
        lv = analysis.risk_levels(price, info["atr"], info.get("low20"))
        st["active"][sym] = {"entry": price, "stop": lv["stop"], "t1": lv["t1"], "t2": lv["t2"],
                             "t1_hit": False, "opened": iso(NOW), "last_check": iso(NOW), "score": r["score"]}
        size = min(100.0, config.RISK_PER_TRADE_PCT / lv["risk_pct"] * 100)
        why = "\n".join(f"• {esc(x)}" for x in r["reasons"][:7])
        heads = "\n".join(f'  - <a href="{esc(l)}">{esc(t[:80])}</a>' for t, l in r["headlines"][:2])
        alerts.append(
            f"🟢 <b>BUY signal: {sym}/{config.QUOTE}</b>  (#{r['rank'] or '?'} {esc(r['name'])})\n"
            f"Score <b>{r['score']:+.0f}</b>/100  ·  {pillar_line(r)}\n\n"
            f"💵 Price now: <b>{fmt_price(price)}</b>\n"
            f"🛑 Stop-loss: {fmt_price(lv['stop'])} (-{lv['risk_pct']:.1f}%)\n"
            f"🎯 Target 1: {fmt_price(lv['t1'])} ({pct(lv['t1'], price):+.1f}%) - sell half, stop to entry\n"
            f"🎯 Target 2: {fmt_price(lv['t2'])} ({pct(lv['t2'], price):+.1f}%)\n\n"
            f"<b>Why</b>\n{why}\n" + (f"📰 Recent news:\n{heads}\n" if heads else "")
            + f"\n📏 Sizing: to risk {config.RISK_PER_TRADE_PCT:g}% of your capital, use about "
              f"<b>{size:.0f}%</b> of capital on this trade.\n"
            f"{binance_link(sym)}\n{DISCLAIMER}")
    return alerts


# ---------------------------------------------------------------- summary ---
def status_text(st, results):
    lines = ["📋 <b>Open bot signals</b>"]
    if not st["active"]:
        lines.append("none")
    for sym, t in st["active"].items():
        p = results.get(sym, {}).get("live_price")
        lines.append(f"• {sym}: entry {fmt_price(t['entry'])}, now {fmt_price(p)}"
                     + (f" ({pct(p, t['entry']):+.1f}%)" if p else "")
                     + f", stop {fmt_price(t['stop'])}" + (" · T1 hit" if t.get("t1_hit") else ""))
    lines.append("\n💼 <b>Your holdings</b>")
    if not st["holdings"]:
        lines.append("none - add with /hold BTC 65000")
    for sym, h in st["holdings"].items():
        r = results.get(sym)
        if r:
            pl = f" ({pct(r['live_price'], h['entry']):+.1f}%)" if h.get("entry") else ""
            lines.append(f"• {sym}: {fmt_price(r['live_price'])}{pl}, score {r['score']:+.0f}")
        else:
            lines.append(f"• {sym}: no data this run")
    return "\n".join(lines)


def summary_text(st, results, ctx):
    ranked = sorted(results.values(), key=lambda r: r["score"], reverse=True)
    local = NOW.astimezone(LOCAL_TZ)
    lines = [f"☀️ <b>Daily crypto brief</b> - {local:%a %d %b, %H:%M}"]
    lines += [esc(x) for x in ctx["market_reasons"]]
    lines.append(f"Coins analysed: {len(results)} · headlines read: {ctx['n_news']}")
    lines.append("\n🏆 <b>Strongest setups</b>")
    for r in ranked[:5]:
        mark = " ← BUY zone" if r["score"] >= config.BUY_SCORE else ""
        lines.append(f"{r['symbol']}: {r['score']:+.0f} @ {fmt_price(r['live_price'])}{mark}")
    near = [r for r in ranked if config.BUY_SCORE - 10 <= r["score"] < config.BUY_SCORE]
    if near:
        lines.append("\n👀 <b>Close to a BUY</b>: " + ", ".join(f"{r['symbol']} ({r['score']:+.0f})" for r in near[:6]))
    lines.append("\n🥶 <b>Weakest</b>")
    for r in ranked[-3:][::-1]:
        lines.append(f"{r['symbol']}: {r['score']:+.0f}")
    lines.append("\n" + status_text(st, results))
    if ctx["failures"]:
        lines.append(f"\n⚠️ No data for: {', '.join(ctx['failures'][:10])}")
    lines.append("\nData: Binance/OKX/Gate/KuCoin, CoinGecko, Fear &amp; Greed by alternative.me, news RSS.")
    return "\n".join(lines)


def write_report(results, ctx):
    ranked = sorted(results.values(), key=lambda r: r["score"], reverse=True)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "latest_scan.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    local = NOW.astimezone(LOCAL_TZ)
    out = [f"# Latest scan - {local:%Y-%m-%d %H:%M} ({config.TIMEZONE})", ""]
    out += [f"- {x}" for x in ctx["market_reasons"]]
    out += [f"- Candle sources used: {ctx['sources']}", f"- Headlines read: {ctx['n_news']}", "",
            "| # | Coin | Score | Tech | Fund | Sent | Events | Price | RSI |",
            "|---|------|------:|-----:|-----:|-----:|-------:|------:|----:|"]
    for i, r in enumerate(ranked, 1):
        f = "n/a" if r["fund"] is None else f"{r['fund']:+.2f}"
        out.append(f"| {i} | {r['symbol']} | {r['score']:+.0f} | {r['tech']:+.2f} | {f} | {r['sent']:+.2f} | "
                   f"{r['events']:+.2f} | {fmt_price(r['live_price'])} | {r['info']['rsi']:.0f} |")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        config.TELEGRAM_BOT_TOKEN = ""  # print only

    st = load_state()
    replies = handle_commands(st)

    try:
        universe, markets = build_universe(st)
    except Exception as e:
        log.error("Could not build coin list (CoinGecko): %s", e)
        notifier.send(f"⚠️ Crypto bot: could not load the coin list this run ({esc(e)}). Will retry next run.")
        if not args.dry_run:
            save_state(st)
        return 1

    results, ctx = analyse_all(universe, markets)
    log.info("Analysed %d coins. Sources: %s", len(results), ctx["sources"])
    if not results:
        notifier.send("⚠️ Crypto bot: no price data from any exchange this run. "
                      "All sources may be blocking this server - see README 'Troubleshooting'.")
        if not args.dry_run:
            save_state(st)
        return 1

    alerts = follow_up_signals(st, results) + follow_up_holdings(st, results) + new_buy_signals(st, results)
    for msg in alerts:
        notifier.send(msg)

    for rep in replies:
        notifier.send(status_text(st, results) if rep == "__STATUS__" else rep)

    local = NOW.astimezone(LOCAL_TZ)
    if args.summary or (local.hour >= config.DAILY_SUMMARY_HOUR and st["last_summary_date"] != local.date().isoformat()):
        notifier.send(summary_text(st, results, ctx))
        st["last_summary_date"] = local.date().isoformat()

    st["last_run"] = iso(NOW)
    if not args.dry_run:
        save_state(st)
        write_report(results, ctx)
    log.info("Done: %d alerts sent", len(alerts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
