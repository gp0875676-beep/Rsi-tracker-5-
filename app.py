import os
import requests
import pandas as pd
import numpy as np
from flask import Flask, request, jsonify
from datetime import datetime
import threading
import time

# ── ENV VARIABLES (Render pe set karo) ──
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID", "")   # Auto-alerts ke liye
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")         # Render URL

app = Flask(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
RSI_PERIOD = 14
MFI_PERIOD = 14

# Alert levels
RSI_OVERSOLD   = 30
RSI_OVERBOUGHT = 70
MFI_OVERSOLD   = 20
MFI_OVERBOUGHT = 80

# Auto-alert check har 5 min (in-memory tracking)
sent_alerts = {}

KNOWN_COINS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
    "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
    "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
    "LTC": "litecoin", "ATOM": "cosmos", "NEAR": "near",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
    "SUI": "sui", "PEPE": "pepe", "WIF": "dogwifcoin",
    "SHIB": "shiba-inu", "TON": "the-open-network", "TRX": "tron",
    "AAVE": "aave", "INJ": "injective-protocol", "HBAR": "hedera-hashgraph",
    "BONK": "bonk", "WLD": "worldcoin-wld", "RENDER": "render-token",
    "FIL": "filecoin", "VET": "vechain", "SEI": "sei-network",
}

TIMEFRAMES = {
    "15m": {"days": 2,  "label": "15 Min"},
    "1H":  {"days": 30, "label": "1 Hour"},
    "4H":  {"days": 90, "label": "4 Hour"},
}


# ══════════════════════════════════════════
#  TELEGRAM SEND
# ══════════════════════════════════════════
def send_message(chat_id, text, parse_mode="HTML"):
    if not BOT_TOKEN:
        print("BOT_TOKEN missing!")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10
        )
    except Exception as e:
        print(f"Send error: {e}")


# ══════════════════════════════════════════
#  DATA & INDICATORS
# ══════════════════════════════════════════
def get_coin_id(ticker):
    ticker = ticker.upper().strip()
    if ticker in KNOWN_COINS:
        return KNOWN_COINS[ticker]
    try:
        r = requests.get(f"{COINGECKO_BASE}/search", params={"query": ticker}, timeout=10)
        coins = r.json().get("coins", [])
        for c in coins:
            if c["symbol"].upper() == ticker:
                return c["id"]
        if coins:
            return coins[0]["id"]
    except Exception:
        pass
    return ticker.lower()


def fetch_ohlc(coin_id, days):
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": str(days)},
            timeout=15
        )
        if r.status_code == 429:
            return None, "rate_limit"
        if r.status_code == 404:
            return None, "not_found"
        if r.status_code != 200:
            return None, "error"
        data = r.json()
        if not data:
            return None, "empty"
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close"])
        for c in ["open","high","low","close"]:
            df[c] = df[c].astype(float)
        df["volume"] = 1.0
        return df, None
    except Exception as e:
        return None, str(e)


def fetch_volume(coin_id, days):
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": str(days)},
            timeout=15
        )
        if r.status_code != 200:
            return None
        vols = r.json().get("total_volumes", [])
        if not vols:
            return None
        return pd.DataFrame(vols, columns=["ts", "volume"])["volume"]
    except Exception:
        return None


def calc_rsi(close):
    if len(close) < RSI_PERIOD + 1:
        return float("nan")
    d = close.diff()
    gain = d.clip(lower=0)
    loss = (-d).clip(lower=0)
    ag = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    al = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    val = float((100 - 100/(1+rs)).iloc[-1])
    return round(val, 2) if not np.isnan(val) else float("nan")


def calc_mfi(df):
    if len(df) < MFI_PERIOD + 1:
        return float("nan")
    tp = (df["high"] + df["low"] + df["close"]) / 3
    rmf = tp * df["volume"]
    diff = tp.shift(1)
    pos = rmf.where(tp > diff, 0).rolling(MFI_PERIOD).sum()
    neg = rmf.where(tp < diff, 0).rolling(MFI_PERIOD).sum()
    val = float((100 - 100/(1 + pos/neg.replace(0, np.nan))).iloc[-1])
    return round(val, 2) if not np.isnan(val) else float("nan")


def get_price(coin_id):
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=8
        )
        return float(r.json().get(coin_id, {}).get("usd", 0))
    except Exception:
        return 0.0


def rsi_emoji(v):
    if np.isnan(v):   return "❓"
    if v >= 70:       return "🔴"
    if v <= 30:       return "🟢"
    if v >= 55:       return "🟡"
    if v <= 45:       return "🟠"
    return                   "⚪"

def mfi_emoji(v):
    if np.isnan(v):   return "❓"
    if v >= 80:       return "🔴"
    if v <= 20:       return "🟢"
    if v >= 60:       return "🟡"
    if v <= 40:       return "🟠"
    return                   "⚪"

def signal_text(v, ob, os_):
    if np.isnan(v):  return "N/A"
    if v >= ob:      return "OVERBOUGHT"
    if v <= os_:     return "OVERSOLD"
    if v >= 55:      return "Bullish"
    if v <= 45:      return "Bearish"
    return                  "Neutral"

def make_bar(v, width=10):
    if np.isnan(v): return "░" * width
    f = max(0, min(width, int(v/100*width)))
    return "█"*f + "░"*(width-f)


# ══════════════════════════════════════════
#  MAIN INDICATOR FETCH
# ══════════════════════════════════════════
def get_indicators(ticker):
    """Fetch all timeframes for a coin, return formatted message"""
    coin_id = get_coin_id(ticker)
    price   = get_price(coin_id)

    if price > 100:
        price_str = f"${price:,.2f}"
    elif price > 1:
        price_str = f"${price:.4f}"
    else:
        price_str = f"${price:.8f}"

    now = datetime.utcnow().strftime("%d %b %Y %H:%M UTC")

    lines = [
        f"📊 <b>{ticker.upper()}/USDT</b>",
        f"💰 Price: <b>{price_str}</b>",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    any_data = False
    for tf, cfg in TIMEFRAMES.items():
        df, err = fetch_ohlc(coin_id, cfg["days"])

        if err == "not_found":
            return f"❌ <b>{ticker.upper()}</b> nahi mila CoinGecko pe.\nCheck karo ticker sahi hai ya nahi."
        elif err == "rate_limit":
            return "⏳ CoinGecko rate limit. 1 minute baad dobara try karo."
        elif df is None or len(df) < RSI_PERIOD + 5:
            lines.append(f"\n<b>{cfg['label']} ({tf})</b>\n⚠️ Data nahi mila")
            continue

        vol = fetch_volume(coin_id, cfg["days"])
        if vol is not None and len(vol) >= len(df):
            df["volume"] = vol.values[:len(df)]

        rsi = calc_rsi(df["close"])
        mfi = calc_mfi(df)
        any_data = True

        rsi_str = f"{rsi:.2f}" if not np.isnan(rsi) else "N/A"
        mfi_str = f"{mfi:.2f}" if not np.isnan(mfi) else "N/A"

        lines += [
            f"\n<b>⏱ {cfg['label']} ({tf})</b>",
            f"{rsi_emoji(rsi)} RSI(14): <b>{rsi_str}</b>  [{make_bar(rsi)}]  {signal_text(rsi,70,30)}",
            f"{mfi_emoji(mfi)} MFI(14): <b>{mfi_str}</b>  [{make_bar(mfi)}]  {signal_text(mfi,80,20)}",
        ]

    if not any_data:
        return f"❌ {ticker.upper()} ka data nahi mila. Sahi ticker daalo."

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "🔴 ≥70/80  🟢 ≤30/20  ⚪ Neutral",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════
#  AUTO ALERT (background)
# ══════════════════════════════════════════
def check_alerts_for(coin_id, ticker):
    """Check extreme RSI/MFI and alert once per zone"""
    for tf, cfg in TIMEFRAMES.items():
        df, err = fetch_ohlc(coin_id, cfg["days"])
        if err or df is None or len(df) < RSI_PERIOD + 5:
            continue

        vol = fetch_volume(coin_id, cfg["days"])
        if vol is not None and len(vol) >= len(df):
            df["volume"] = vol.values[:len(df)]

        rsi = calc_rsi(df["close"])
        mfi = calc_mfi(df)

        key = f"{coin_id}_{tf}"
        prev = sent_alerts.get(key, {})
        alerts = []

        if not np.isnan(rsi):
            if rsi <= RSI_OVERSOLD and not prev.get("rsi_os"):
                alerts.append(f"🟢 <b>RSI OVERSOLD</b>\nRSI = <b>{rsi:.2f}</b> (≤{RSI_OVERSOLD})")
                prev["rsi_os"] = True; prev["rsi_ob"] = False
            elif rsi >= RSI_OVERBOUGHT and not prev.get("rsi_ob"):
                alerts.append(f"🔴 <b>RSI OVERBOUGHT</b>\nRSI = <b>{rsi:.2f}</b> (≥{RSI_OVERBOUGHT})")
                prev["rsi_ob"] = True; prev["rsi_os"] = False
            elif RSI_OVERSOLD < rsi < RSI_OVERBOUGHT:
                prev["rsi_os"] = False; prev["rsi_ob"] = False

        if not np.isnan(mfi):
            if mfi <= MFI_OVERSOLD and not prev.get("mfi_os"):
                alerts.append(f"🟢 <b>MFI OVERSOLD</b>\nMFI = <b>{mfi:.2f}</b> (≤{MFI_OVERSOLD})")
                prev["mfi_os"] = True; prev["mfi_ob"] = False
            elif mfi >= MFI_OVERBOUGHT and not prev.get("mfi_ob"):
                alerts.append(f"🔴 <b>MFI OVERBOUGHT</b>\nMFI = <b>{mfi:.2f}</b> (≥{MFI_OVERBOUGHT})")
                prev["mfi_ob"] = True; prev["mfi_os"] = False
            elif MFI_OVERSOLD < mfi < MFI_OVERBOUGHT:
                prev["mfi_os"] = False; prev["mfi_ob"] = False

        sent_alerts[key] = prev

        for alert in alerts:
            price = get_price(coin_id)
            p_str = f"${price:,.2f}" if price > 100 else f"${price:.4f}"
            msg = (
                f"🚨 <b>CRYPTO ALERT</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🪙 <b>{ticker.upper()}/USDT</b> | {cfg['label']} ({tf})\n"
                f"💰 Price: {p_str}\n"
                f"{alert}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🕐 {datetime.utcnow().strftime('%d %b %H:%M UTC')}"
            )
            send_message(CHAT_ID, msg)


def alert_loop():
    """Har 5 min mein major coins check karo"""
    time.sleep(20)
    alert_coins = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"
    }
    while True:
        if CHAT_ID:
            for ticker, coin_id in alert_coins.items():
                try:
                    check_alerts_for(coin_id, ticker)
                    time.sleep(4)
                except Exception as e:
                    print(f"Alert error {ticker}: {e}")
        time.sleep(300)


threading.Thread(target=alert_loop, daemon=True).start()


# ══════════════════════════════════════════
#  WEBHOOK HANDLER
# ══════════════════════════════════════════
@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data:
        return "ok"

    msg = data.get("message") or data.get("edited_message")
    if not msg:
        return "ok"

    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()

    if not text:
        return "ok"

    # Commands
    cmd = text.split()[0].lower().lstrip("/").replace("@", "").split("@")[0]
    args = text.split()[1:] if len(text.split()) > 1 else []

    if cmd in ("start", "help"):
        reply = (
            "👋 <b>Crypto RSI + MFI Bot</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 <b>Commands:</b>\n\n"
            "/btc — Bitcoin RSI + MFI\n"
            "/eth — Ethereum RSI + MFI\n"
            "/sol — Solana RSI + MFI\n"
            "/check &lt;COIN&gt; — Koi bhi coin\n\n"
            "<b>Examples:</b>\n"
            "/check BNB\n"
            "/check PEPE\n"
            "/check DOGE\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⏱ Timeframes: 15m | 1H | 4H\n"
            "🔔 Auto-alerts: RSI≤30/≥70 · MFI≤20/≥80"
        )
        send_message(chat_id, reply)

    elif cmd == "btc":
        send_message(chat_id, "⏳ BTC data fetch ho raha hai...")
        send_message(chat_id, get_indicators("BTC"))

    elif cmd == "eth":
        send_message(chat_id, "⏳ ETH data fetch ho raha hai...")
        send_message(chat_id, get_indicators("ETH"))

    elif cmd == "sol":
        send_message(chat_id, "⏳ SOL data fetch ho raha hai...")
        send_message(chat_id, get_indicators("SOL"))

    elif cmd == "check":
        if not args:
            send_message(chat_id, "❌ Coin ka naam daalo!\nExample: /check BNB")
        else:
            ticker = args[0].upper()
            send_message(chat_id, f"⏳ {ticker} data fetch ho raha hai...")
            send_message(chat_id, get_indicators(ticker))

    else:
        # Agar seedha coin naam likha ho jaise "BTC" ya "SOL"
        if text.upper() in KNOWN_COINS or (len(text) <= 10 and text.isalpha()):
            send_message(chat_id, f"⏳ {text.upper()} data fetch ho raha hai...")
            send_message(chat_id, get_indicators(text.upper()))
        else:
            send_message(chat_id,
                "❓ Samjha nahi. Try karo:\n"
                "/check BTC\n/check ETH\n/help"
            )

    return "ok"


@app.route("/set_webhook")
def set_webhook():
    """Webhook set karo — ek baar call karo deploy ke baad"""
    if not BOT_TOKEN or not WEBHOOK_URL:
        return "BOT_TOKEN ya WEBHOOK_URL missing!", 400
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    r = requests.post(url, json={"url": f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}"})
    return jsonify(r.json())


@app.route("/")
def home():
    return "✅ Crypto RSI+MFI Telegram Bot is running!"


@app.route("/health")
def health():
    return jsonify({"status": "ok", "bot": bool(BOT_TOKEN), "chat_id": bool(CHAT_ID)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
