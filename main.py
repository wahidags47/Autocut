import os
import time
import threading
import requests
import pandas as pd
from flask import Flask, request

# ===== KONFIG RAILWAY =====
TELEGRAM_TOKEN = os.getenv("8400411121:AAEndGuw6PGtv6y0hGcxeR7O3G1-QWJqGtk")
TELEGRAM_CHAT_ID = os.getenv("691664631")  # Default alert group
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Public URL Railway

SYMBOLS = ["LTCUSDT", "BTCUSDT", "ETHUSDT"]
INTERVAL = "15m"
NEAR_TOL = 0.003
LIMIT = 300

app = Flask(__name__)

# ====== FUNGSI UMUM ======
def send_telegram(chat_id, msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
    requests.post(url, data=data)

def get_binance_price(symbol="ETHUSDT"):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
    r = requests.get(url)
    return float(r.json()["price"])

def get_binance_klines(symbol="LTCUSDT", interval="15m", limit=500):
    url = f"https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params)
    data = r.json()

    df = pd.DataFrame(data, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_volume", "taker_buy_quote_volume", "ignore"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df[["open","high","low","close","volume"]] = df[["open","high","low","close","volume"]].astype(float)
    return df

def pivot_levels(h, l, c):
    H1, L1, C1 = h[-2], l[-2], c[-2]
    PP = (H1 + L1 + C1) / 3
    R1 = 2*PP - L1
    S1 = 2*PP - H1
    R2 = PP + (H1 - L1)
    S2 = PP - (H1 - L1)
    return [S1, S2], [R1, R2]

def swing_levels(h, l, window=5):
    highs, lows = [], []
    for i in range(window, len(h)-window):
        if h[i] == max(h[i-window:i+window+1]):
            highs.append(h[i])
        if l[i] == min(l[i-window:i+window+1]):
            lows.append(l[i])
    return sorted(set(highs)), sorted(set(lows))

def pct_diff(a, b):
    return abs(a - b) / b if b != 0 else 0

# ====== MODE OTOMATIS ======
def auto_alert():
    send_telegram(TELEGRAM_CHAT_ID, "🤖 Bot S/R + Live Price aktif (update tiap 5 menit).")
    last_alerts = set()

    while True:
        try:
            live_prices_msg = "💹 *Live Price Update*\n"
            for symbol in SYMBOLS:
                df = get_binance_klines(symbol, INTERVAL, LIMIT)
                c = df['close'].to_numpy()
                h = df['high'].to_numpy()
                l = df['low'].to_numpy()

                price = c[-1]
                ma50 = pd.Series(c).rolling(50).mean().iloc[-1]
                ma200 = pd.Series(c).rolling(200).mean().iloc[-1]
                trend_up = ma50 > ma200
                trend_down = ma50 < ma200
                trend_icon = "📈" if trend_up else ("📉" if trend_down else "⚪")
                live_prices_msg += f"{trend_icon} {symbol}: {price:.2f} | MA50: {ma50:.2f} | MA200: {ma200:.2f}\n"

                # Support & Resistance
                sup_piv, res_piv = pivot_levels(h, l, c)
                swing_res, swing_sup = swing_levels(h, l)
                all_supports = sorted(set(sup_piv + swing_sup))
                all_resists = sorted(set(res_piv + swing_res))

                # Cek support
                for sup in all_supports:
                    if pct_diff(price, sup) <= NEAR_TOL:
                        alert_id = f"{symbol}-SUP-{sup:.2f}"
                        if alert_id not in last_alerts:
                            last_alerts.add(alert_id)
                            if trend_up:
                                send_telegram(TELEGRAM_CHAT_ID, f"🟢 BUY ALERT: {symbol} {price:.2f} dekat support {sup:.2f} (Tren naik)")
                            else:
                                send_telegram(TELEGRAM_CHAT_ID, f"⚪ SUPPORT TEST: {symbol} {price:.2f} dekat support {sup:.2f}")

                # Cek resistance
                for res in all_resists:
                    if pct_diff(price, res) <= NEAR_TOL:
                        alert_id = f"{symbol}-RES-{res:.2f}"
                        if alert_id not in last_alerts:
                            last_alerts.add(alert_id)
                            if trend_down:
                                send_telegram(TELEGRAM_CHAT_ID, f"🔴 SELL ALERT: {symbol} {price:.2f} dekat resistance {res:.2f} (Tren turun)")
                            else:
                                send_telegram(TELEGRAM_CHAT_ID, f"⚪ RESISTANCE TEST: {symbol} {price:.2f} dekat resistance {res:.2f}")

            # Kirim Live Price
            send_telegram(TELEGRAM_CHAT_ID, live_prices_msg)
            time.sleep(300)  # 5 menit
        except Exception as e:
            send_telegram(TELEGRAM_CHAT_ID, f"⚠️ Error: {e}")
            time.sleep(30)

# ====== MODE MANUAL (Telegram Command) ======
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
                    send_telegram(chat_id, "❌ Gagal mengambil harga. Pastikan simbol benar.")
            else:
                send_telegram(chat_id, "⚠️ Format salah. Contoh: /price eth")
    return "ok", 200

# ====== SET WEBHOOK ======
def set_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    data = {"url": f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"}
    r = requests.post(url, data=data)
    print(r.json())

# ====== MAIN ======
if __name__ == "__main__":
    threading.Thread(target=auto_alert, daemon=True).start()
    set_webhook()
    app.run(host="0.0.0.0", port=5000)
