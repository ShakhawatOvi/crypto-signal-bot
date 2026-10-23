@echo off
REM Run the crypto signal bot on your own Windows PC (use this if GitHub's
REM servers are blocked by every exchange). Schedule it every 2 hours with
REM Windows Task Scheduler. Fill in your own values below first.

cd /d "%~dp0"

set TELEGRAM_BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
set TELEGRAM_CHAT_ID=PASTE_YOUR_CHAT_ID_HERE
set COINGECKO_API_KEY=
set MY_HOLDINGS=

if not exist .venv (
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
)

.venv\Scripts\python bot.py %*
