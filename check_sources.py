"""
Setup check: which free data sources work from this machine, and does
Telegram deliver? Run this first (GitHub: Actions -> Run workflow -> mode "check").
"""
import requests

import config
import data_sources as ds
import news
import notifier


def main():
    lines = ["🔧 Crypto bot setup check"]

    for name, fetch in ds.CANDLE_SOURCES:
        try:
            df = fetch("BTC", "1d", 50)
            lines.append(f"✅ {name}: BTC close {df['close'].iloc[-1]:,.0f}")
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            lines.append(f"❌ {name}: HTTP {code}" + (" (blocked from this server's location)" if code in (403, 451) else ""))
        except Exception as e:
            lines.append(f"❌ {name}: {type(e).__name__}")

    pairs = ds.binance_usdt_pairs()
    lines.append(f"✅ Binance pair list: {len(pairs)} USDT pairs" if pairs else
                 "⚠️ Binance pair list unreachable (bot will scan without that filter)")

    try:
        m = ds.coingecko_markets(per_page=10)
        lines.append(f"✅ CoinGecko: top coin {m[0]['name']}" + (" (with your key)" if config.COINGECKO_API_KEY else " (no key)"))
    except Exception as e:
        lines.append(f"❌ CoinGecko: {type(e).__name__}")

    try:
        f = ds.fear_greed(1)
        lines.append(f"✅ Fear & Greed: {f[0][1]} ({f[0][2]})")
    except Exception as e:
        lines.append(f"❌ Fear & Greed: {type(e).__name__}")

    try:
        items = news.fetch_news()
        lines.append(f"{'✅' if items else '⚠️'} News: {len(items)} headlines from the last {config.NEWS_LOOKBACK_HOURS}h")
    except Exception as e:
        lines.append(f"❌ News: {type(e).__name__}")

    ok_candles = any(l.startswith("✅") for l in lines[1:1 + len(ds.CANDLE_SOURCES)])
    lines.append("\n" + ("👍 Ready: at least one price source works." if ok_candles else
                         "🚫 No price source works from here. Run the bot on your own PC instead (see README)."))
    if not notifier.enabled():
        lines.append("ℹ️ Telegram secrets not set, so this was only printed.")
    notifier.send(notifier.esc("\n".join(lines)))


if __name__ == "__main__":
    main()
