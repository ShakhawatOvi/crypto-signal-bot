# Crypto Swing-Signal Bot (free, alerts only)

This bot scans the **top 50 coins that trade as XXX/USDT on Binance**. Every 2 hours it analyses them, then sends **BUY / SELL alerts to your phone on Telegram**. You place the trades yourself in the Binance app.

The bot **never logs in to Binance and never trades**. It only reads public market data, so it cannot touch your account or your money.

Everything runs free: GitHub Actions does the hosting, Telegram the alerts, and the data comes from free public APIs.

---

## What it analyses

Every coin gets a score from **-100 (very bearish) to +100 (very bullish)**. The score combines four pillars:

| Pillar | Weight | What it looks at |
|---|---|---|
| **Technical** | 50% | **Daily trend:** EMA 20/50/200 and ADX.<br>**Momentum:** RSI and MACD.<br>**Setups:** 20-day breakouts with volume, Bollinger Band dips.<br>**Entry timing:** the 4-hour chart. |
| **Fundamental** | 15% | **Trading activity:** volume vs market cap.<br>**Strength vs BTC:** 7 and 30 days.<br>**Dilution:** FDV vs market cap.<br>**Other:** distance from the all-time high, and rank. |
| **Sentiment** | 20% | **Fear & Greed Index:** used "contrarian", so fear counts as an opportunity and greed as a risk.<br>**BTC trend:** whether the market as a whole is healthy.<br>**Headline tone:** how the coin's recent headlines read. |
| **Events** | 15% | **Price-moving news from 5 crypto news feeds.**<br>Bearish: hacks, delistings, lawsuits, token unlocks, outages.<br>Bullish: ETF approvals, major exchange listings, upgrades, partnerships, buybacks. |

**Rules for the alerts:**
- 🟢 **BUY**: three conditions must all hold:
  - The score is **+40 or more**.
  - The chart itself is bullish.
  - There is no critical bad news.

  Each alert comes with a stop-loss, two targets and a position-size tip. You get at most 3 new BUYs per run.
- 🎯 **Target 1 hit**: sell about half and move your stop to your entry price.
- 🏁 **Target 2 hit**, 🛑 **stop-loss hit**, 🔴 **setup turned bearish** (score below -10), or ⌛ **30 days without progress**: exit.
- 🚨 **Critical news** (hack, delisting, exploit) on a coin you hold: an immediate warning.
- ☀️ **Daily brief**, around 08:17 Bangladesh time: market mood, the strongest and weakest coins, and your open positions.

Every alert says **why** in plain words, so you can check it against the chart before you act.

---

## Setup (about 20 minutes, once)

### Step 1: Create your Telegram bot
1. In Telegram, open **@BotFather** and send `/newbot`. Choose a name and a username ending in `bot`.
2. BotFather replies with a **token** like `7123456789:AAH...`. Save it. Anyone with this token can control your bot, so keep it private.
3. Open your new bot in Telegram and send it any message, for example `hi`.
4. In a browser, open this address, replacing `<TOKEN>` with your token:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find `"chat":{"id":123456789` in the page. That number is your **chat ID**.

### Step 2 (optional): Get a free CoinGecko key
The bot works without a key. A free "Demo" key makes it more reliable. To get one, sign up at coingecko.com → Developer Dashboard → create a Demo API key.

### Step 3: Put the bot on GitHub
1. Create a free account at **github.com**.
2. Click **New repository**:
   - Name it `crypto-signal-bot`.
   - Choose **Public**. Scheduled runs are free and reliable on public repos. Your secrets stay encrypted and hidden even in a public repo.
   - Click **Create repository**.
3. Unzip the download on a computer. Click **"uploading an existing file"**. Open the `crypto-signal-bot` folder and drag in **everything inside it**, including the **`.github`** folder. Don't drag the folder itself. Then click **Commit changes**.
   - If the `.github` folder didn't upload: click **Add file → Create new file**, type the name `.github/workflows/signal-bot.yml`, paste the contents of that file, and commit.

### Step 4: Add your secrets
In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | your chat ID number |
| `COINGECKO_API_KEY` | your CoinGecko key (optional) |

Optional: on the **Variables** tab, add `MY_HOLDINGS` with coins you already own, e.g. `BTC,ETH,SOL`. You can also do this later from Telegram.

Then go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**, and click **Save**. This lets the bot keep its memory between runs.

### Step 5: Test it
1. Open the **Actions** tab. If GitHub asks, click **"I understand my workflows, go ahead and enable them"**.
2. Click **Crypto Signal Bot → Run workflow**, choose mode **check**, and click **Run workflow**.
3. Within 1–2 minutes you should get a Telegram message listing which data sources work.
   - You need ✅ on **at least one** price source.
   - Binance may show ❌ HTTP 451, because GitHub's servers are in the US. That's expected; OKX, Gate or KuCoin then supply the prices.
4. Run it again with mode **summary**. You'll get a first daily brief straight away.

After that, it runs by itself every 2 hours.

---

## Before you trust it with money: run the backtest
Go to **Actions → Run workflow → mode `backtest`**. It replays the BUY/SELL rules on about 1,000 days of history for 50 coins, including fees. When it finishes, open **`reports/backtest_results.md`** in your repo. It shows:
- win rate
- average trade
- profit factor (above 1.0 means the gains outweighed the losses)
- worst drawdown
- a comparison with simply holding BTC

Limits of the backtest:
- It can only test the technical and Fear & Greed parts. Free historical news doesn't exist.
- It uses today's top coins, which slightly flatters the results.
- Past results don't guarantee future ones.

If the results are poor, raise `BUY_SCORE` in `config.py` (e.g. 50) and run the backtest again.

---

## Telegram commands
Send these to your bot. It answers on its **next run**, within 2 hours.

| Command | What it does |
|---|---|
| `/hold SOL 172.5` | You bought SOL at 172.5. The bot will warn you if SOL turns bearish. The price is optional. |
| `/unhold SOL` | You sold it. |
| `/status` | Open signals and your holdings with profit/loss. |
| `/stats` | Track record of the bot's past signals. |
| `/help` | The command list. |

The bot only obeys messages from **your** chat ID.

---

## Tuning (`config.py`)
Edit the file directly on GitHub: click the file, then the ✏️ pencil icon, then commit.

- `BUY_SCORE`: raise it for fewer but stronger signals.
- `TOP_N_COINS`: how many coins to scan.
- `WEIGHTS`: how much each pillar counts.
- `STOP_ATR_MULT`, `TARGET1_R`, `TARGET2_R`: how far away the stop and the targets are.
- `RISK_PER_TRADE_PCT`: the risk used in the sizing tip.
- `DAILY_SUMMARY_HOUR`: the hour for the daily brief.
- How often it runs: the `cron` line in `.github/workflows/signal-bot.yml`. The time there is in UTC.

To change the news rules (which words count as a hack, a listing and so on), edit `EVENT_RULES` in `news.py`.

The latest full ranking of all coins is saved after every run in **`reports/latest_scan.md`**.

---

## Troubleshooting

**Every price source shows ❌ (HTTP 451 or 403).**
The exchanges are blocking GitHub's servers. Run the bot on your own Windows PC instead:
1. Install Python from python.org, and tick "Add to PATH" during setup.
2. Unzip the bot into a folder.
3. Edit `run_on_windows.bat` and paste in your token and chat ID.
4. Double-click the file to test it.
5. Open **Task Scheduler → Create Basic Task**. Set it to run `run_on_windows.bat` daily, then in the task's settings make it repeat every 2 hours.

The bot only runs while your PC is on.

**No Telegram messages arrive.**
- Check both secrets for typos and extra spaces.
- Make sure you sent your bot at least one message first.
- Open the run in the **Actions** tab and read the log.

**Scheduled runs aren't happening.**
- Scheduled runs can start 5–30 minutes late. That's normal on GitHub.
- GitHub pauses schedules in public repos after 60 days without activity. The bot's own saves normally count as activity. If GitHub emails you that it paused the workflow, open the Actions tab and re-enable it.

**"Could not load the coin list".**
CoinGecko rate-limited the free tier. Add the free `COINGECKO_API_KEY`.

---

## Honest limits
- These are **probabilistic signals, not predictions**. Many will be wrong. That's why every BUY comes with a stop-loss and a position-size tip.
- The news analysis uses keyword rules on headlines. It's fast and transparent, but it can misread sarcasm or unusual wording. Click through to the headline when an alert cites news.
- Free data can be late or missing. The daily brief lists any coins that had no data.
- This is not financial advice. Never trade money you can't afford to lose.

**Data sources:**
- Binance public market data, with OKX, Gate and KuCoin as fallbacks
- CoinGecko
- Fear & Greed Index by alternative.me
- RSS feeds from CoinDesk, Cointelegraph, Decrypt, CryptoSlate and The Block
