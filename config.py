"""
All the knobs you can tune live here.

The bot NEVER trades. It only reads public market data and sends you
Telegram alerts. You place every order yourself on Binance.
"""
import os

# ---------------------------------------------------------------- secrets ---
# Set these as GitHub "Secrets" (or environment variables on your PC).
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()  # optional, free "Demo" key

# Coins you already hold, e.g. "BTC,ETH,SOL". The bot watches these for SELL
# alerts even if it never told you to buy them. You can also manage this list
# from Telegram with /hold and /unhold.
MY_HOLDINGS = [s.strip().upper() for s in os.getenv("MY_HOLDINGS", "").split(",") if s.strip()]

# --------------------------------------------------------------- universe ---
TOP_N_COINS = 50          # how many coins to scan (by market cap, after filters)
QUOTE = "USDT"            # Binance quote currency you trade against

# Stablecoins, wrapped / staked copies and other tokens that make no sense
# to swing-trade against USDT.
EXCLUDE_SYMBOLS = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD", "USD1", "USDD",
    "BUSD", "USDP", "GUSD", "FRAX", "LUSD", "RLUSD", "USDTB", "USDX", "USD0", "EURC",
    "XAUT", "PAXG", "BSC-USD", "SUSDE", "SUSDS", "USYC", "BUIDL", "USDF", "USDG",
    "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "RETH", "METH", "EZETH",
    "RSETH", "LBTC", "SOLVBTC", "CLBTC", "BTCB", "WBETH", "JITOSOL", "MSOL",
    "BNSOL", "WBNB", "WTRX", "WEETH", "LSETH", "OSETH", "TBTC", "FBTC", "UBTC",
}
EXCLUDE_NAME_WORDS = ("wrapped", "staked", "bridged", "restaked", "liquid staking")

# ------------------------------------------------------------- timeframes ---
# Swing trading: the daily chart sets direction, the 4h chart times entries.
DAILY_CANDLES = 300
H4_CANDLES = 200

# ---------------------------------------------------------------- scoring ---
# Each pillar gives a score from -1 (very bearish) to +1 (very bullish).
# The final score is the weighted sum x 100, so it runs from -100 to +100.
WEIGHTS = {
    "technical": 0.50,
    "fundamental": 0.15,
    "sentiment": 0.20,
    "events": 0.15,
}

BUY_SCORE = 40            # final score needed for a BUY alert
MIN_TECH_FOR_BUY = 0.25   # ...and the chart itself must be at least this bullish
EXIT_SCORE = -10          # an open BUY gets a SELL/exit alert below this
STRONG_SELL_SCORE = -40   # coins in MY_HOLDINGS get a warning below this

MAX_NEW_BUYS_PER_RUN = 3  # avoid alert floods: only the best few per run
REBUY_COOLDOWN_DAYS = 3   # don't re-signal a coin right after exiting it
MAX_HOLD_DAYS = 30        # swing trade "time stop"

# Risk levels suggested in each BUY alert (based on daily ATR = average daily range)
STOP_ATR_MULT = 2.0       # stop-loss = entry - 2 x ATR
TARGET1_R = 1.5           # first target = entry + 1.5 x risk
TARGET2_R = 3.0           # second target = entry + 3 x risk
RISK_PER_TRADE_PCT = 1.0  # shown as advice: risk at most 1% of capital per trade

# ------------------------------------------------------------------- news ---
NEWS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "The Block": "https://www.theblock.co/rss.xml",
}
NEWS_LOOKBACK_HOURS = 48

# --------------------------------------------------------------- schedule ---
TIMEZONE = "Asia/Dhaka"
DAILY_SUMMARY_HOUR = 8    # local hour after which the once-a-day summary is sent

# ------------------------------------------------------------------ files ---
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "state.json")
