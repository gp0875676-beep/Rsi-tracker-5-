from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import requests
import pandas as pd
import numpy as np
import os

app = Flask(__name__)
CORS(app)

RSI_PERIOD = 14
MFI_PERIOD = 14
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

KNOWN_COINS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
    "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
    "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
    "LTC": "litecoin", "ATOM": "cosmos", "NEAR": "near",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
    "SUI": "sui", "PEPE": "pepe", "WIF": "dogwifcoin",
    "SHIB": "shiba-inu", "TON": "the-open-network", "TRX": "tron",
    "FIL": "filecoin", "AAVE": "aave", "INJ": "injective-protocol",
    "SEI": "sei-network", "HBAR": "hedera-hashgraph", "VET": "vechain",
    "BONK": "bonk", "WLD": "worldcoin-wld", "RENDER": "render-token",
}

TIMEFRAME_CONFIG = {
    "15m": {"days": 2,  "label": "15 Min"},
    "1H":  {"days": 30, "label": "1 Hour"},
    "4H":  {"days": 90, "label": "4 Hour"},
}


def get_coin_id(ticker: str) -> str:
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


def fetch_ohlc(coin_id: str, days: int):
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": str(days)},
            timeout=15,
            headers={"Accept": "application/json"}
        )
        if r.status_code == 429:
            return None, "rate_limit"
        if r.status_code == 404:
            return None, "not_found"
        if r.status_code != 200:
            return None, f"error_{r.status_code}"

        data = r.json()
        if not data or not isinstance(data, list):
            return None, "empty"

        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = 1.0
        return df, None
    except Exception as e:
        return None, str(e)


def fetch_volume(coin_id: str, days: int):
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": str(days)},
            timeout=15,
            headers={"Accept": "application/json"}
        )
        if r.status_code != 200:
            return None
        vols = r.json().get("total_volumes", [])
        if not vols:
            return None
        vol_df = pd.DataFrame(vols, columns=["timestamp", "volume"])
        return vol_df["volume"]
    except Exception:
        return None


def calculate_rsi(close: pd.Series) -> float:
    if len(close) < RSI_PERIOD + 1:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0/RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    val = float(rsi.iloc[-1])
    return round(val, 2) if not np.isnan(val) else float("nan")


def calculate_mfi(df: pd.DataFrame) -> float:
    if len(df) < MFI_PERIOD + 1:
        return float("nan")
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    rmf = tp * df["volume"]
    tp_prev = tp.shift(1)
    pos_flow = rmf.where(tp > tp_prev, 0.0)
    neg_flow = rmf.where(tp < tp_prev, 0.0)
    pos_sum = pos_flow.rolling(window=MFI_PERIOD).sum()
    neg_sum = neg_flow.rolling(window=MFI_PERIOD).sum()
    mfr = pos_sum / neg_sum.replace(0, np.nan)
    mfi = 100.0 - (100.0 / (1.0 + mfr))
    val = float(mfi.iloc[-1])
    return round(val, 2) if not np.isnan(val) else float("nan")


def get_signal(val: float, overbought: int, oversold: int) -> dict:
    if np.isnan(val):
        return {"text": "N/A", "color": "neutral"}
    if val >= overbought:
        return {"text": "OVERBOUGHT", "color": "red"}
    if val <= oversold:
        return {"text": "OVERSOLD", "color": "green"}
    if val >= (50 + overbought) / 2 - 10:
        return {"text": "BULLISH", "color": "yellow"}
    if val <= (50 + oversold) / 2 + 10:
        return {"text": "BEARISH", "color": "orange"}
    return {"text": "NEUTRAL", "color": "neutral"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/indicators")
def get_indicators():
    ticker = request.args.get("coin", "BTC").upper().strip()
    coin_id = get_coin_id(ticker)

    # Get price
    price = 0.0
    try:
        r = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=8
        )
        price = float(r.json().get(coin_id, {}).get("usd", 0))
    except Exception:
        pass

    results = {}
    error_msg = None

    for tf, cfg in TIMEFRAME_CONFIG.items():
        df, err = fetch_ohlc(coin_id, cfg["days"])

        if err == "not_found":
            error_msg = f"Coin '{ticker}' nahi mila CoinGecko pe"
            break
        elif err == "rate_limit":
            error_msg = "CoinGecko rate limit — thoda wait karo"
            break
        elif df is None or len(df) < RSI_PERIOD + 5:
            results[tf] = {"rsi": None, "mfi": None, "error": "Insufficient data"}
            continue

        # Try to get volume
        vol = fetch_volume(coin_id, cfg["days"])
        if vol is not None and len(vol) >= len(df):
            df["volume"] = vol.values[:len(df)]

        rsi = calculate_rsi(df["close"])
        mfi = calculate_mfi(df)

        results[tf] = {
            "label": cfg["label"],
            "rsi": rsi if not np.isnan(rsi) else None,
            "mfi": mfi if not np.isnan(mfi) else None,
            "rsi_signal": get_signal(rsi, 70, 30),
            "mfi_signal": get_signal(mfi, 80, 20),
        }

    return jsonify({
        "ticker": ticker,
        "coin_id": coin_id,
        "price": price,
        "results": results,
        "error": error_msg
    })


@app.route("/api/search")
def search_coin():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        r = requests.get(f"{COINGECKO_BASE}/search", params={"query": q}, timeout=8)
        coins = r.json().get("coins", [])[:8]
        return jsonify([
            {"id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"]}
            for c in coins
        ])
    except Exception:
        return jsonify([])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
