"""
News: free RSS feeds -> per-coin event detection and headline tone.

"Events" are discrete happenings that move prices (hacks, delistings,
exchange listings, ETF decisions, upgrades, token unlocks...).
"Tone" is the general bullish/bearish wording of headlines.
Both are simple keyword rules: transparent, free, and easy to edit below.
"""
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import config

log = logging.getLogger("news")

# (regex, weight, label). Weight sign = direction. |weight| >= 0.5 = "critical":
# a critical negative event blocks BUY signals and triggers warnings.
EVENT_RULES = [
    (r"\bhack(ed|s|er|ers)?\b|\bexploit(ed|s)?\b|\bdrained\b|\bstolen\b", -0.7, "hack/exploit"),
    (r"\bdelist(s|ed|ing)?\b|\bwill (remove|delist)\b", -0.7, "delisting"),
    (r"\brug ?pull\b|\bexit scam\b", -0.8, "rug pull"),
    (r"\binsolven|\bbankrupt|\bhalt(s|ed)? withdrawals?\b|\bpaus(es|ed) withdrawals?\b", -0.7, "insolvency/withdrawal halt"),
    (r"\bdepeg", -0.6, "depeg"),
    (r"\bsec (sues|charges|lawsuit)\b|\bsued\b|\blawsuit\b|\bindict", -0.4, "lawsuit/regulator action"),
    (r"\bfraud\b|\bponzi\b|\bscam\b", -0.4, "fraud allegation"),
    (r"\boutage\b|\bnetwork (halt|halted|down)\b|\bchain halt", -0.35, "network outage"),
    (r"\btoken unlocks?\b|\bunlock(s|ing)? \$?\d", -0.3, "token unlock (new supply)"),
    (r"\bban(s|ned)?\b", -0.25, "ban"),
    (r"\betf\b.*\b(approv|launch|debut|inflow)|\b(approv|launch)\w*\b.*\betf\b", 0.45, "ETF approval/launch/inflows"),
    (r"\b(lists?|listed|listing|will list)\b.*\b(binance|coinbase|upbit|robinhood|bithumb|okx)\b", 0.4, "major exchange listing"),
    (r"\bmainnet\b|\bhard ?fork\b|\bnetwork upgrade\b|\bupgrade (goes|went) live\b", 0.25, "mainnet/upgrade"),
    (r"\bpartner(s|ship)?\b|\bteams up\b|\bintegrat(es|ion)\b", 0.2, "partnership/integration"),
    (r"\bacqui(res|red|sition)\b", 0.2, "acquisition"),
    (r"\bbuy ?back\b|\btoken burn\b|\bburns\b", 0.25, "buyback/burn"),
    (r"\btreasury\b.*\b(buys|adds|purchase)|\b(buys|adds|purchases)\b.*\btreasury\b", 0.25, "treasury buying"),
    (r"\bapprov(al|es|ed)\b|\bgreen ?light", 0.15, "approval"),
]

TONE_POS = r"\b(surge[sd]?|soar(s|ed)?|rall(y|ies|ied)|jump(s|ed)?|gain(s|ed)?|bullish|record high|all-time high|ath|breaks? out|breakout|rebound(s|ed)?|recover(s|ed|y)|inflows?|outperform\w*|climb(s|ed)?|spike[sd]?)\b"
TONE_NEG = r"\b(plunge[sd]?|crash(es|ed)?|tumble[sd]?|slump(s|ed)?|drop(s|ped)?|fall(s|en)?|fell|sink(s)?|sank|bearish|sell-?off|dump(s|ed)?|outflows?|liquidat\w*|slide[sd]?|decline[sd]?|lows?|fear)\b"

# Names that are ordinary English words: only match these coins by ticker.
COMMON_WORD_NAMES = {
    "story", "render", "stacks", "flow", "gas", "pump", "sonic", "core", "maker", "sky",
    "usual", "virtual", "ai", "pi", "official trump", "near", "one", "mantra", "jupiter",
    "theta", "the graph", "plasma", "aster", "sei", "world liberty financial", "pengu",
}
# Extra ways headlines refer to big coins
ALIASES = {
    "BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "XRP": ["ripple", "xrp"],
    "BNB": ["bnb", "bnb chain"], "SOL": ["solana"], "DOGE": ["dogecoin"], "ADA": ["cardano"],
    "AVAX": ["avalanche"], "DOT": ["polkadot"], "LINK": ["chainlink"], "TRX": ["tron"],
    "TON": ["toncoin", "telegram open network"], "SHIB": ["shiba inu"], "LTC": ["litecoin"],
    "BCH": ["bitcoin cash"], "NEAR": ["near protocol"], "POL": ["polygon"], "UNI": ["uniswap"],
    "XLM": ["stellar"], "HBAR": ["hedera"], "SUI": ["sui"], "APT": ["aptos"], "ARB": ["arbitrum"],
    "OP": ["optimism"], "ATOM": ["cosmos"], "FIL": ["filecoin"], "ETC": ["ethereum classic"],
    "HYPE": ["hyperliquid"], "ENA": ["ethena"], "WLD": ["worldcoin"], "AAVE": ["aave"],
    "ONDO": ["ondo"], "TAO": ["bittensor"], "ICP": ["internet computer"], "XMR": ["monero"],
}

_TAG = re.compile(r"<[^>]+>")


def _clean(s):
    return html.unescape(_TAG.sub(" ", s or "")).strip()


def _parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fetch_news():
    """List of dicts {source, title, summary, link, time} from the last NEWS_LOOKBACK_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.NEWS_LOOKBACK_HOURS)
    items, seen = [], set()
    for source, url in config.NEWS_FEEDS.items():
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 crypto-signal-bot"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            log.warning("News feed %s failed: %s", source, e)
            continue
        atom = "{http://www.w3.org/2005/Atom}"
        entries = root.findall(".//item") or root.findall(f".//{atom}entry")
        for it in entries:
            def text(tag):
                el = it.find(tag)
                if el is None:
                    el = it.find(atom + tag)
                return el.text if el is not None and el.text else ""
            title = _clean(text("title"))
            if not title or title.lower() in seen:
                continue
            link = text("link")
            if not link:
                el = it.find(atom + "link")
                link = el.get("href", "") if el is not None else ""
            when = _parse_date(text("pubDate") or text("published") or text("updated"))
            if when is None or when < cutoff:
                continue
            seen.add(title.lower())
            items.append({
                "source": source, "title": title, "summary": _clean(text("description") or text("summary"))[:400],
                "link": link.strip(), "time": when,
            })
    log.info("Fetched %d recent headlines", len(items))
    return items


def build_matchers(coins):
    """coins: list of dicts with 'symbol' and 'name'. Returns {SYMBOL: [compiled regex, ...]}."""
    out = {}
    for c in coins:
        sym, name = c["symbol"].upper(), c["name"].lower()
        pats = []
        if len(sym) >= 3:  # tickers are matched case-sensitively: "LINK", not "link"
            pats.append(re.compile(rf"(?<![A-Za-z0-9$]){re.escape(sym)}(?![A-Za-z0-9])|\${re.escape(sym)}\b"))
        names = set(ALIASES.get(sym, []))
        if name not in COMMON_WORD_NAMES and len(name) >= 4:
            names.add(name)
        for n in names:
            pats.append(re.compile(rf"\b{re.escape(n)}\b", re.IGNORECASE))
        out[sym] = pats
    return out


def _recency_weight(when):
    hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    return 1.0 if hours <= 12 else 0.7 if hours <= 24 else 0.4


def analyse_coin_news(symbol, news_items, matchers):
    """
    Returns dict with:
      events_score (-1..1), tone_score (-1..1), critical_negative (bool),
      event_reasons [str], headlines [(title, link)], tone_counts (pos, neg)
    """
    pats = matchers.get(symbol, [])
    ev_total, pos, neg, critical = 0.0, 0.0, 0.0, False
    reasons, headlines = [], []
    for item in news_items:
        title = item["title"]
        if not any(p.search(title) for p in pats):
            continue
        w = _recency_weight(item["time"])
        low = title.lower()
        headlines.append((title, item["link"]))
        for rx, weight, label in EVENT_RULES:
            if re.search(rx, low):
                ev_total += weight * w
                if weight <= -0.5 and w >= 0.7:
                    critical = True
                reasons.append(f"{'⚠️' if weight < 0 else '✅'} {label}: \"{title[:90]}\"")
                break  # one event label per headline
        if re.search(TONE_POS, low):
            pos += w
        if re.search(TONE_NEG, low):
            neg += w
    events_score = max(-1.0, min(1.0, ev_total))
    tone_score = 0.0
    if pos + neg > 0:
        tone_score = max(-1.0, min(1.0, (pos - neg) / max(2.0, pos + neg)))
    return {
        "events_score": events_score,
        "tone_score": tone_score,
        "critical_negative": critical,
        "event_reasons": reasons[:4],
        "headlines": headlines[:5],
        "tone_counts": (round(pos, 1), round(neg, 1)),
        "n_headlines": len(headlines),
    }
