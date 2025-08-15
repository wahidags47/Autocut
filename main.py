# main.py
import os
import io
import time
import threading
import traceback
import requests
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf
import matplotlib.pyplot as plt
from flask import Flask, request

# ------------- CONFIG -------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_URL")  # e.g. https://autocut-production.up.railway.app
PORT = int(os.environ.get("PORT", 5000))

if not TELEGRAM_TOKEN or not RAILWAY_URL:
    raise RuntimeError("Please set TELEGRAM_TOKEN and RAILWAY_URL environment variables")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# limits / defaults
MAX_LIMIT = 1000
DEFAULT_LIMIT = 500
VALID_TFS = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"}

app = Flask(__name__)

# ------------- UTIL TELEGRAM -------------
def tg_send_text(chat_id, text):
    try:
        r = requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)
        print("tg_send_text:", r.status_code, r.text)
    except Exception as e:
        print("tg_send_text error:", e)

def tg_send_photo_bytes(chat_id, png_bytes, caption=None):
    try:
        files = {"photo": ("chart.png", png_bytes)}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        r = requests.post(f"{TELEGRAM_API}/sendPhoto", data=data, files=files, timeout=60)
        print("tg_send_photo:", r.status_code, r.text)
    except Exception as e:
        print("tg_send_photo error:", e)

# ------------- BINANCE DATA (REST) -------------
def binance_get_klines(symbol="BTCUSDT", interval="4h", limit=500):
    symbol = symbol.upper()
    interval = interval.lower()
    if interval not in VALID_TFS:
        raise ValueError(f"Invalid timeframe: {interval}")
    limit = min(int(limit), MAX_LIMIT)
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(BINANCE_KLINES_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","num_trades","tb_base","tb_quote","ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df[["open","high","low","close","volume"]]

def get_price_simple(symbol="BTCUSDT"):
    r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

# ------------- S/R and Fibonacci -------------
def find_swings(highs, lows, window=5):
    highs_idx = []
    lows_idx = []
    n = len(highs)
    for i in range(window, n-window):
        if highs[i] == max(highs[i-window:i+window+1]):
            highs_idx.append(i)
        if lows[i] == min(lows[i-window:i+window+1]):
            lows_idx.append(i)
    highs_vals = [highs[i] for i in highs_idx]
    lows_vals = [lows[i] for i in lows_idx]
    return sorted(set(highs_vals)), sorted(set(lows_vals))

def pick_sr_from_swings(highs_vals, lows_vals):
    support = min(lows_vals) if lows_vals else None
    resistance = max(highs_vals) if highs_vals else None
    return support, resistance

def fib_levels(support, resistance):
    low = support
    high = resistance
    diff = high - low
    retr = {
        "0.0": high,
        "0.236": high - diff * 0.236,
        "0.382": high - diff * 0.382,
        "0.5": high - diff * 0.5,
        "0.618": high - diff * 0.618,
        "1.0": low
    }
    ext = {
        "1.272": high + diff * 0.272,
        "1.618": high + diff * 0.618
    }
    return retr, ext

# ------------- Chart builder -------------
def make_chart_png_bytes(df, title=None, sma_fast=50, sma_slow=200, swing_win=5):
    """
    returns: PNG bytes
    """
    try:
        plot_df = df.copy().rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        close = plot_df["Close"]
        ma_fast = close.rolling(sma_fast).mean()
        ma_slow = close.rolling(sma_slow).mean()

        # RSI (standard)
        delta = close.diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        ma_up = up.ewm(alpha=1/14, adjust=False).mean()
        ma_down = down.ewm(alpha=1/14, adjust=False).mean()
        rs = ma_up / ma_down.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))

        # MACD
        macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        macd_sig = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - macd_sig

        highs = plot_df["High"].to_numpy()
        lows = plot_df["Low"].to_numpy()
        highs_sw, lows_sw = find_swings(highs, lows, window=swing_win)
        support, resistance = pick_sr_from_swings(highs_sw, lows_sw)

        retr, ext = {}, {}
        if support is not None and resistance is not None and support < resistance:
            retr, ext = fib_levels(support, resistance)

        addplots = [
            mpf.make_addplot(ma_fast, color='tab:blue'),
            mpf.make_addplot(ma_slow, color='tab:red'),
            mpf.make_addplot(rsi_series, panel=1, ylabel='RSI'),
            mpf.make_addplot(macd_line, panel=2, color='fuchsia'),
            mpf.make_addplot(macd_sig, panel=2, color='green'),
            mpf.make_addplot(macd_hist, type='bar', panel=2, color='dimgray', width=0.7)
        ]

        fig, axes = mpf.plot(plot_df, type='candle', style='binance',
                             addplot=addplots, volume=False, returnfig=True,
                             figsize=(12,9), tight_layout=True, panel_ratios=(6,2,2))

        ax_main = axes[0]

        # draw S/R lines
        if support is not None:
            ax_main.hlines(support, plot_df.index[0], plot_df.index[-1], colors='green', linestyles='--', linewidth=1.2, alpha=0.9)
            ax_main.text(plot_df.index[-1], support, f"  S {support:.6f}", color='green', fontsize=8, va='bottom')
        if resistance is not None:
            ax_main.hlines(resistance, plot_df.index[0], plot_df.index[-1], colors='red', linestyles='--', linewidth=1.2, alpha=0.9)
            ax_main.text(plot_df.index[-1], resistance, f"  R {resistance:.6f}", color='red', fontsize=8, va='bottom')

        # draw fib retracement lines
        if retr:
            colors = {'0.236':'#cc9900','0.382':'#cc6600','0.5':'#888888','0.618':'#009900'}
            for k,v in retr.items():
                ax_main.hlines(v, plot_df.index[0], plot_df.index[-1], colors=colors.get(k,'#999999'), linestyles=':', linewidth=1)
                ax_main.text(plot_df.index[-1], v, f" {k} {v:.6f}", color=colors.get(k,'#999999'), fontsize=7, va='bottom')

        # fib extension (sell zone)
        if ext and "1.618" in ext:
            ax_main.hlines(ext["1.618"], plot_df.index[0], plot_df.index[-1], colors='purple', linestyles='-.', linewidth=1.2)
            ax_main.text(plot_df.index[-1], ext["1.618"], f"  EXT 1.618 {ext['1.618']:.6f}", color='purple', fontsize=8, va='bottom')

        if title:
            ax_main.set_title(title)

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150)
        buf.seek(0)
        try:
            import matplotlib.pyplot as plt
            plt.close(fig)
        except:
            pass
        return buf.read()
    except Exception:
        traceback.print_exc()
        raise

# ------------- Background worker (process heavy commands) -------------
def process_update_async(update):
    try:
        if not update or "message" not in update:
            return
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text","").strip()
        if not text:
            tg_send_text(chat_id, "No text command.")
            return

        parts = text.split()
        cmd = parts[0].lower()

        if cmd == "/price":
            if len(parts) >= 2:
                coin = parts[1].upper()
                symbol = coin if coin.endswith("USDT") else f"{coin}USDT"
                try:
                    price = get_price_simple(symbol)
                    tg_send_text(chat_id, f"💰 {symbol} = {price:.6f} USDT")
                except Exception as e:
                    tg_send_text(chat_id, f"❌ Failed to fetch price: {e}")
            else:
                tg_send_text(chat_id, "Usage: /price BTC")

        elif cmd == "/chart":
            if len(parts) >= 3:
                coin = parts[1].upper()
                tf = parts[2].lower()
                if tf not in VALID_TFS:
                    tg_send_text(chat_id, "Timeframe invalid. Examples: 15m, 1h, 4h, 1d")
                    return
                symbol = coin if coin.endswith("USDT") else f"{coin}USDT"
                try:
                    tg_send_text(chat_id, f"🔎 Generating {symbol} {tf} chart...")
                    df = binance_get_klines(symbol, tf, limit=300)
                    png = make_chart_png_bytes(df.tail(300), title=f"{symbol} {tf.upper()}")
                    tg_send_photo_bytes(chat_id, png, caption=f"📈 {symbol} {tf.upper()} (MA50/200 + RSI + MACD + S/R + Fib)")
                except Exception as e:
                    tg_send_text(chat_id, f"❌ Chart error: {e}")
                    traceback.print_exc()
            else:
                tg_send_text(chat_id, "Usage: /chart BTC 4h")

        else:
            tg_send_text(chat_id, "Commands:\n/price <coin>\n/chart <coin> <timeframe>\nExample: /chart BNB 4h")
    except Exception:
        traceback.print_exc()

# ------------- Webhook route (fast response) -------------
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json(force=True)
        print("Received update:", update)
        # spawn background worker
        t = threading.Thread(target=process_update_async, args=(update,), daemon=True)
        t.start()
    except Exception as e:
        print("Webhook handler exception:", e)
        traceback.print_exc()
    # return OK immediately so Telegram won't mark webhook failed
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return "Bot (SR + Fib) running", 200

# ------------- Ensure webhook is set -------------
def ensure_set_webhook():
    try:
        requests.get(f"{TELEGRAM_API}/deleteWebhook", timeout=10)
    except Exception:
        pass
    expected = f"{RAILWAY_URL.rstrip('/')}/{TELEGRAM_TOKEN}"
    try:
        r = requests.post(f"{TELEGRAM_API}/setWebhook", data={"url": expected}, timeout=20)
        print("setWebhook resp:", r.status_code, r.text)
    except Exception as e:
        print("ensure_set_webhook error:", e)
        traceback.print_exc()

# ------------- Start -------------
if __name__ == "__main__":
    print("Starting app, ensuring webhook...")
    ensure_set_webhook()
    app.run(host="0.0.0.0", port=PORT)
