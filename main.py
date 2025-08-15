import os
import logging
import requests
from flask import Flask, request
import pandas as pd
import matplotlib.pyplot as plt
import mplfinance as mpf
from binance.client import Client

# ===== SETUP LOGGING =====
logging.basicConfig(level=logging.INFO)

# ===== ENVIRONMENT VARIABLES =====
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RAILWAY_URL = os.environ.get("RAILWAY_URL")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

if not TOKEN or not RAILWAY_URL:
    raise ValueError("TELEGRAM_TOKEN dan RAILWAY_URL harus di-set di Railway Environment Variables!")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

# ===== FLASK APP =====
app = Flask(__name__)
binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# ===== WEBHOOK SETUP =====
def set_webhook():
    try:
        # Hapus webhook lama
        requests.get(f"{TELEGRAM_API}/deleteWebhook")
        # Set webhook baru
        resp = requests.get(f"{TELEGRAM_API}/setWebhook?url={RAILWAY_URL}/{TOKEN}").json()
        logging.info(f"Webhook set: {resp}")
    except Exception as e:
        logging.error(f"Gagal set webhook: {e}")

# ===== DATA HANDLER =====
def get_ohlcv(symbol="BTCUSDT", interval="4h", limit=200):
    klines = binance_client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df

def find_sr(df):
    support = df["low"].min()
    resistance = df["high"].max()
    return support, resistance

def plot_chart(symbol, interval):
    df = get_ohlcv(symbol, interval)
    support, resistance = find_sr(df)

    # Fibonacci retracement
    diff = resistance - support
    levels = {
        "0%": resistance,
        "23.6%": resistance - 0.236 * diff,
        "38.2%": resistance - 0.382 * diff,
        "50%": resistance - 0.5 * diff,
        "61.8%": resistance - 0.618 * diff,
        "100%": support
    }

    fig, ax = plt.subplots(figsize=(10, 6))
    mpf.plot(df.set_index("time"), type="candle", ax=ax, style="yahoo")

    # Plot S/R
    ax.axhline(support, color="green", linestyle="--", label=f"Support {support:.2f}")
    ax.axhline(resistance, color="red", linestyle="--", label=f"Resistance {resistance:.2f}")

    # Plot Fibonacci levels
    for lvl, price in levels.items():
        ax.axhline(price, linestyle=":", alpha=0.7)
        ax.text(df["time"].iloc[0], price, f"{lvl} - {price:.2f}", color="blue")

    ax.legend()
    chart_path = f"/tmp/{symbol}_{interval}.png"
    plt.savefig(chart_path)
    plt.close()
    return chart_path

# ===== TELEGRAM HANDLER =====
def send_message(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})

def send_photo(chat_id, photo_path):
    with open(photo_path, "rb") as photo:
        requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id": chat_id}, files={"photo": photo})

def process_update(update):
    if "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")

        if text.startswith("/chart"):
            try:
                parts = text.split()
                symbol = parts[1].upper() if len(parts) > 1 else "BTCUSDT"
                interval = parts[2] if len(parts) > 2 else "4h"
                send_message(chat_id, f"📊 Membuat chart {symbol} {interval} ...")
                chart_path = plot_chart(symbol, interval)
                send_photo(chat_id, chart_path)
            except Exception as e:
                send_message(chat_id, f"❌ Gagal membuat chart: {e}")

# ===== ROUTES =====
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json(force=True)
        logging.info(f"Update diterima: {update}")
        process_update(update)
    except Exception as e:
        logging.error(f"Error di webhook: {e}")
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return "Bot aktif 🚀", 200

# ===== STARTUP =====
if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
