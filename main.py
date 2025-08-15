import os
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import threading
from flask import Flask, request
from binance.client import Client
from binance.exceptions import BinanceAPIException
import mplfinance as mpf

# --- Load ENV Vars ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
RAILWAY_URL = os.environ.get("RAILWAY_URL")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

if not TOKEN or not RAILWAY_URL:
    raise ValueError("TELEGRAM_TOKEN dan RAILWAY_URL harus di-set di Railway Environment Variables!")

# --- Setup Binance Client ---
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# --- Flask App ---
app = Flask(__name__)

# --- Set Webhook otomatis ---
def set_webhook():
    url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
    data = {"url": f"{RAILWAY_URL}/{TOKEN}"}
    r = requests.post(url, data=data)
    print("SetWebhook response:", r.json())

def check_webhook():
    info = requests.get(f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo").json()
    if info.get("ok"):
        current = info["result"].get("url", "")
        expected = f"{RAILWAY_URL}/{TOKEN}"
        if current != expected:
            print("Webhook salah, setting ulang...")
            set_webhook()
        else:
            print("Webhook sudah benar.")
    else:
        print("Gagal cek webhook:", info)

# --- Trading Logic ---
def get_binance_klines(symbol, interval, limit=200):
    try:
        klines = client.get_klines(symbol=symbol.upper(), interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df[["open","high","low","close","volume"]] = df[["open","high","low","close","volume"]].astype(float)
        return df
    except BinanceAPIException as e:
        print("Binance API error:", e)
        return None

def find_support_resistance(df, window=5):
    supports, resistances = [], []
    for i in range(window, len(df)-window):
        low_range = df["low"][i-window:i+window]
        high_range = df["high"][i-window:i+window]
        if df["low"][i] == low_range.min():
            supports.append((df["timestamp"][i], df["low"][i]))
        if df["high"][i] == high_range.max():
            resistances.append((df["timestamp"][i], df["high"][i]))
    return supports, resistances

def plot_chart(symbol, interval):
    df = get_binance_klines(symbol, interval)
    if df is None:
        return None

    # Indicators
    df["MA50"] = df["close"].rolling(50).mean()
    df["MA200"] = df["close"].rolling(200).mean()
    supports, resistances = find_support_resistance(df)

    # Plot
    mc = mpf.make_marketcolors(up='g', down='r', wick='inherit', edge='inherit')
    s = mpf.make_mpf_style(marketcolors=mc)

    fig, axlist = mpf.plot(df.set_index("timestamp"), type='candle', style=s,
                           mav=(50,200), volume=True, returnfig=True, figsize=(12,8))

    ax = axlist[0]
    for ts, price in supports:
        ax.axhline(price, color='blue', linestyle='--', alpha=0.6)
    for ts, price in resistances:
        ax.axhline(price, color='orange', linestyle='--', alpha=0.6)

    filepath = f"/tmp/{symbol}_{interval}.png"
    plt.savefig(filepath)
    plt.close(fig)
    return filepath

# --- Telegram Send ---
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": text})

def send_photo(chat_id, filepath):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    with open(filepath, 'rb') as photo:
        requests.post(url, data={"chat_id": chat_id}, files={"photo": photo})

# --- Auto Alert Thread ---
def auto_alert():
    chat_id = None  # set manual kalau mau kirim ke chat tertentu
    while True:
        try:
            df = get_binance_klines("LTCUSDT", Client.KLINE_INTERVAL_4HOUR)
            if df is not None:
                latest_price = df["close"].iloc[-1]
                print(f"[AutoAlert] LTCUSDT 4H price: {latest_price}")
        except Exception as e:
            print("Auto alert error:", e)
        import time; time.sleep(300)  # tiap 5 menit

# --- Flask Routes ---
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json()
    if "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")

        if text.startswith("/chart"):
            parts = text.split()
            if len(parts) == 3:
                _, symbol, tf = parts
                filepath = plot_chart(symbol, tf)
                if filepath:
                    send_photo(chat_id, filepath)
                else:
                    send_message(chat_id, "Gagal ambil data chart.")
            else:
                send_message(chat_id, "Format salah. Contoh: /chart BTCUSDT 4h")
        else:
            send_message(chat_id, "Command tidak dikenal.")
    return "ok"

# --- Main ---
if __name__ == "__main__":
    check_webhook()
    threading.Thread(target=auto_alert, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
