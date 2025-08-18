import os
import io
import requests
import pandas as pd
import mplfinance as mpf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, request
import time
import threading

# ===== CONFIG =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # contoh: https://yourapp-production.up.railway.app

TIMEFRAME = "1h"   # timeframe default
SMA_FAST = 50
SMA_SLOW = 200

app = Flask(__name__)

# ===== TELEGRAM =====
def send_text(chat_id, msg, parse="Markdown"):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": msg, "parse_mode": parse})
    except Exception as e:
        print("send_text error:", e)

def send_photo(chat_id, png_bytes, caption=None):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        files = {"photo": ("chart.png", png_bytes)}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        requests.post(url, data=data, files=files, timeout=30)
    except Exception as e:
        print("send_photo error:", e)

# ===== BINANCE DATA =====
def get_binance_price(symbol="BTCUSDT"):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return float(r.json()["price"])

def get_klines(symbol="BTCUSDT", interval="1h", limit=200):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=20)
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","trades","tbbav","tbqav","ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    return df[["open","high","low","close","volume"]]

# ===== CHART MA =====
def make_chart_png(df, title="", mav=(SMA_FAST, SMA_SLOW)):
    data = df.copy()
    data.columns = ["Open","High","Low","Close","Volume"]

    fig, ax = mpf.plot(
        data,
        type="candle",
        mav=mav,
        volume=False,
        style="binance",
        title=title,
        returnfig=True,
        figsize=(10, 5),
        tight_layout=True,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160)
    buf.seek(0)
    plt.close(fig)
    return buf.read()

# ===== FIBONACCI CHART =====
def fibonacci_levels(df):
    high = df["high"].max()
    low = df["low"].min()
    diff = high - low
    levels = {
        "0.236": high - diff * 0.236,
        "0.382": high - diff * 0.382,
        "0.5":   high - diff * 0.5,
        "0.618": high - diff * 0.618,
        "0.786": high - diff * 0.786,
    }
    return high, low, levels

def make_fibo_chart(symbol="BTCUSDT", interval="1h", limit=200):
    df = get_klines(symbol, interval, limit)
    high, low, levels = fibonacci_levels(df)

    mc = mpf.make_marketcolors(up="g", down="r", inherit=True)
    s  = mpf.make_mpf_style(marketcolors=mc)
    fig, ax = mpf.plot(
        df, type="candle", style=s, figsize=(10,5),
        returnfig=True, title=f"{symbol} {interval} — Fibonacci Retracement"
    )

    for label, lvl in levels.items():
        if lvl < (high + low)/2:  # support (buy)
            ax[0].axhline(lvl, color="green", linestyle="--", alpha=0.8)
            ax[0].text(df.index[-1], lvl, f" BUY {label}", color="green")
        else:  # resistance (sell)
            ax[0].axhline(lvl, color="red", linestyle="--", alpha=0.8)
            ax[0].text(df.index[-1], lvl, f" SELL {label}", color="red")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf.read()

# ===== TELEGRAM COMMANDS =====
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json()
        if not update or "message" not in update:
            return "ok", 200

        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "").strip().lower()

        if text.startswith("/price"):
            parts = text.split()
            if len(parts) == 2:
                coin = parts[1].upper()
                symbol = f"{coin}USDT"
                try:
                    p = get_binance_price(symbol)
                    send_text(chat_id, f"💰 Harga {symbol}: {p:.4f} USDT", parse=None)
                except Exception as e:
                    send_text(chat_id, f"❌ Gagal ambil harga: {e}", parse=None)
            else:
                send_text(chat_id, "⚠️ Format: /price eth", parse=None)

        elif text.startswith("/chart"):
            parts = text.split()
            if len(parts) == 2:
                coin = parts[1].upper()
                symbol = f"{coin}USDT"
                try:
                    df = get_klines(symbol, TIMEFRAME, 220)
                    png = make_chart_png(df.tail(200), title=f"{symbol} {TIMEFRAME}")
                    send_photo(chat_id, png, caption=f"📈 {symbol} {TIMEFRAME} (MA{SMA_FAST}/{SMA_SLOW})")
                except Exception as e:
                    send_text(chat_id, f"❌ Gagal buat chart: {e}", parse=None)
            else:
                send_text(chat_id, "⚠️ Format: /chart eth", parse=None)

        elif text.startswith("/now"):
            parts = text.split()
            if len(parts) == 2:
                coin = parts[1].upper()
                symbol = f"{coin}USDT"
                try:
                    png = make_fibo_chart(symbol, TIMEFRAME, 200)
                    send_photo(chat_id, png, caption=f"📊 {symbol} {TIMEFRAME}\nFibonacci Support/Resistance")
                except Exception as e:
                    send_text(chat_id, f"❌ Gagal buat chart: {e}", parse=None)
            else:
                send_text(chat_id, "⚠️ Format: /now eth", parse=None)

        else:
            send_text(chat_id, "Perintah:\n/price <coin>\n/chart <coin>\n/now <coin>", parse=None)

        return "ok", 200
    except Exception as e:
        print("webhook error:", e)
        return "ok", 200

# ===== AUTO LOOP LTCUSDT =====
def auto_loop():
    while True:
        try:
            png = make_fibo_chart("LTCUSDT", TIMEFRAME, 200)
            send_photo(
                TELEGRAM_CHAT_ID,
                png,
                caption="📊 LTCUSDT Auto Update (5 menit)\nFibonacci Support/Resistance"
            )
        except Exception as e:
            print("auto_loop error:", e)
            try:
                send_text(TELEGRAM_CHAT_ID, f"⚠️ AutoLoop error: {e}")
            except:
                pass
        time.sleep(300)  # 5 menit

# ===== SET WEBHOOK =====
def set_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    data = {"url": f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"}
    r = requests.post(url, data=data, timeout=20)
    print(r.json())

if __name__ == "__main__":
    set_webhook()
    t = threading.Thread(target=auto_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000)
