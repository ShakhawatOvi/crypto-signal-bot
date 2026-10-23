"""Telegram alerts (free). Falls back to printing when no token is set."""
import html
import logging

import requests

import config

log = logging.getLogger("telegram")
API = "https://api.telegram.org/bot{token}/{method}"


def esc(s):
    return html.escape(str(s), quote=False)


def enabled():
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send(text):
    """Send an HTML-formatted message (split if longer than Telegram's limit)."""
    print("\n" + "=" * 60 + "\n" + text + "\n" + "=" * 60)
    if not enabled():
        log.info("Telegram not configured: message printed only")
        return False
    ok = True
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for chunk in chunks:
        try:
            r = requests.post(
                API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage"),
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=15,
            )
            if not r.ok:
                log.error("Telegram error %s: %s", r.status_code, r.text[:300])
                ok = False
        except Exception as e:
            log.error("Telegram send failed: %s", e)
            ok = False
    return ok


def get_commands(last_update_id):
    """
    Read messages you sent to the bot since the last run.
    Only messages from YOUR chat id are accepted.
    Returns (list_of_texts, new_last_update_id).
    """
    if not enabled():
        return [], last_update_id
    try:
        r = requests.get(
            API.format(token=config.TELEGRAM_BOT_TOKEN, method="getUpdates"),
            params={"offset": (last_update_id or 0) + 1, "timeout": 0},
            timeout=15,
        )
        r.raise_for_status()
        updates = r.json().get("result", [])
    except Exception as e:
        log.warning("Could not read Telegram commands: %s", e)
        return [], last_update_id
    texts = []
    for u in updates:
        last_update_id = max(last_update_id or 0, u["update_id"])
        msg = u.get("message") or u.get("edited_message") or {}
        if str(msg.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
            continue  # ignore strangers
        if msg.get("text"):
            texts.append(msg["text"].strip())
    return texts, last_update_id
