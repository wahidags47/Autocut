import os
import time
import requests
import pandas as pd
from flask import Flask, request

# ===== CONFIG =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # contoh: https://autocut-production.up.railway.app

PAIR = "LTCUSDT"  # pasangan default untuk alert S/R
TIMEFRAME = "5m"  # time frame untuk data candle Binance
ALERT_INTERVAL = 300  # 5 menit

app = Flask(__name__)

# ===== FUNCTIONS =====
def send_telegram(chat_id, msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": msg}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print("Error send_telegram:", e)

def get_binance_price(symbol="LTCUSDT"):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
    r = requests.get(url)
    return float(r.json()["price"])

def get_candle_data(symbol="LTCUSDT", interval="5m", limit=50):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume",
        "_", "_", "_", "_", "_", "_"
    ])
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    return df

def check_support_resistance():
    df = get_candle_data(PAIR, TIMEFRAME, 50)
    support = df["low"].min()
    resistance = df["high"].max()
    price = get_binance_price(PAIR)

    msg = f"📊 {PAIR} Update\nHarga: {price:.2f} USDT\nSupport: {support:.2f}\nResistance: {resistance:.2f}"
    send_telegram(TELEGRAM_CHAT_ID, msg)

# ===== TELEGRAM HANDLER =====
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json()

    if "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "").strip().lower()

        if text.startswith("/price"):
            parts = text.split()
            if len(parts) == 2:
                coin = parts[1].upper()
                symbol = f"{coin}USDT"
                try:
                    price = get_binance_price(symbol)
                    send_telegram(chat_id, f"💰 Harga {symbol}: {price:.2f} USDT")
                except:
                    send_telegram(chat_id, "❌ Gagal mengambil harga.")
            else:
                send_telegram(chat_id, "⚠️ Format salah. Contoh: /price eth")

    return "ok", 200

# ===== SET WEBHOOK =====
def set_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    data = {"url": f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"}
    r = requests.post(url, data=data)
    print(r.json())

# ===== AUTO ALERT LOOP =====
@app.before_first_request
def activate_job():
    def run_job():
        while True:
            try:
                check_support_resistance()
            except Exception as e:
                print("Error in auto alert:", e)
            time.sleep(ALERT_INTERVAL)
    import threading
    thread = threading.Thread(target=run_job)
    thread.daemon = True
    thread.start()

if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=5000)
