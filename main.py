# main.py
import os
import io
import time
import traceback
import threading
import requests
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
from flask import Flask, request

# ----------------- CONFIG (env) -----------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_URL")  # e.g. https://autocut-production.up.railway.app

if not TOKEN or not RAILWAY_URL:
    raise RuntimeError("Set TELEGRAM_TOKEN and RAILWAY_URL in environment variables")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
VALID_TFS = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"}

# default alert monitored pair/timeframe (still optional)
ALERT_PAIR = os.getenv("ALERT_PAIR", "LTCUSDT")
ALERT_TF = os.getenv("ALERT_TF", "4h")
ALERT_INTERVAL = int(os.getenv("ALERT_INTERVAL", 300))  # seconds

SMA_FAST = int(os.getenv("SMA_FAST", 50))
SMA_SLOW = int(os.getenv("SMA_SLOW", 200))
KLIMIT = int(os.getenv("KLIMIT", 500))

app = Flask(__name__)

# ----------------- UTIL TELEGRAM -----------------
def tg_send_message(chat_id, text):
    try:
        url = f"{TELEGRAM_API}/sendMessage"
        r = requests.post(url, json={"chat_id": chat_id, "text": text})
        # print debug
        print("tg_send_message:", r.status_code, r.text)
    except Exception as e:
        print("tg_send_message error:", e)

def tg_send_photo(chat_id, png_bytes, caption=None):
    try:
        url = f"{TELEGRAM_API}/sendPhoto"
        files = {"photo": ("chart.png", png_bytes)}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        r = requests.post(url, data=data, files=files, timeout=60)
        print("tg_send_photo:", r.status_code, r.text)
    except Exception as e:
        print("tg_send_photo error:", e)

# ----------------- BINANCE DATA (public) -----------------
def get_binance_klines(symbol="BTCUSDT", interval="1h", limit=500):
    symbol = symbol.upper()
    interval = interval.lower()
    if interval not in VALID_TFS:
        raise ValueError("Invalid timeframe")
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, KLIMIT)}
    r = requests.get(url, params=params, timeout=20)
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

def get_binance_price(symbol="BTCUSDT"):
    r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol.upper()}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

# ----------------- INDICATORS -----------------
def sma(series, period):
    return series.rolling(period).mean()

def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ma_up / ma_down.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series.fillna(50)

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - sig
    return macd_line, sig, hist

# ----------------- S/R (swing based) & FIB -----------------
def find_swings(highs, lows, window=5):
    highs_idx, lows_idx = [], []
    n = len(highs)
    for i in range(window, n-window):
        if highs[i] == max(highs[i-window:i+window+1]): highs_idx.append(i)
        if lows[i] == min(lows[i-window:i+window+1]): lows_idx.append(i)
    highs_vals = [highs[i] for i in highs_idx]
    lows_vals = [lows[i] for i in lows_idx]
    return sorted(set(highs_vals)), sorted(set(lows_vals))

def pick_sr(highs_vals, lows_vals):
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

# ----------------- CHART DRAW (MA + S/R + Fib + RSI + MACD) -----------------
def make_chart_png(df, title="", sma_fast=SMA_FAST, sma_slow=SMA_SLOW, swing_win=5):
    try:
        data = df.copy()
        # rename for mplfinance
        plot_df = data.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        close = plot_df["Close"]
        ma_fast = sma(close, sma_fast)
        ma_slow = sma(close, sma_slow)
        rsi_v = rsi(close)
        macd_line, macd_sig, macd_hist = macd(close)

        highs = plot_df["High"].to_numpy()
        lows = plot_df["Low"].to_numpy()
        highs_sw, lows_sw = find_swings(highs, lows, window=swing_win)
        support, resistance = pick_sr(highs_sw, lows_sw)

        retr, ext = {}, {}
        if support is not None and resistance is not None and support < resistance:
            retr, ext = fib_levels(support, resistance)

        # addplots: MAs on main, RSI panel, MACD panel
        addplots = [
            mpf.make_addplot(ma_fast, color='tab:blue'),
            mpf.make_addplot(ma_slow, color='tab:red'),
            mpf.make_addplot(rsi_v, panel=1, ylabel='RSI'),
            mpf.make_addplot(macd_line, panel=2, color='fuchsia'),
            mpf.make_addplot(macd_sig, panel=2, color='green'),
            mpf.make_addplot(macd_hist, type='bar', panel=2, color='dimgray', width=0.7)
        ]

        fig, axes = mpf.plot(plot_df, type='candle', style='binance',
                             addplot=addplots, volume=False, returnfig=True,
                             figsize=(12,9), tight_layout=True, panel_ratios=(6,2,2))

        ax_main = axes[0]
        # draw supports/resistances
        if support is not None:
            ax_main.hlines(support, plot_df.index[0], plot_df.index[-1], colors='green', linestyles='--', linewidth=1.2, alpha=0.8)
            ax_main.text(plot_df.index[-1], support, f"  S {support:.6f}", color='green', fontsize=8, va='bottom')
        if resistance is not None:
            ax_main.hlines(resistance, plot_df.index[0], plot_df.index[-1], colors='red', linestyles='--', linewidth=1.2, alpha=0.8)
            ax_main.text(plot_df.index[-1], resistance, f"  R {resistance:.6f}", color='red', fontsize=8, va='bottom')

        # draw fib retracement lines
        if retr:
            colors = {'0.236':'#cc9900','0.382':'#cc6600','0.5':'#888888','0.618':'#009900'}
            for k,v in retr.items():
                ax_main.hlines(v, plot_df.index[0], plot_df.index[-1], colors=colors.get(k,'#999999'), linestyles=':', linewidth=1)
                ax_main.text(plot_df.index[-1], v, f" {k} {v:.6f}", color=colors.get(k,'#999999'), fontsize=7, va='bottom')

        # draw fib extension (sell zone)
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
        print("Chart created OK")
        return buf.read()
    except Exception as e:
        print("make_chart_png error:", e)
        traceback.print_exc()
        raise

# ----------------- BACKGROUND ALERT (monitors ALERT_PAIR ALERT_TF) -----------------
bg_thread = None
bg_lock = threading.Lock()
running = False

last_cross_state = None
last_rsi_state = None
last_macd_state = None

def alert_worker():
    global last_cross_state, last_rsi_state, last_macd_state, running
    running = True
    print("Alert worker started")
    while True:
        try:
            df = get_binance_klines(ALERT_PAIR, ALERT_TF, limit=KLIMIT)
            close = df["close"]
            ma50 = sma(close, SMA_FAST) if False else sma(close, SMA_FAST)  # placeholder
        except Exception as e:
            print("alert_worker data error:", e)
            traceback.print_exc()
        time.sleep(ALERT_INTERVAL)

def start_background_thread():
    global bg_thread
    with bg_lock:
        if bg_thread is None or not bg_thread.is_alive():
            bg_thread = threading.Thread(target=alert_worker, daemon=True)
            bg_thread.start()
            print("Background thread started")
        else:
            print("Background already running")

# ----------------- TELEGRAM WEBHOOK HANDLER -----------------
@app.route("/", methods=["GET"])
def home():
    return "OK", 200

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        print("Received update:", update)
        # ensure background thread is running (safety)
        try:
            start_background_thread()
        except Exception as e:
            print("start background error:", e)
        if not update:
            return "ok", 200

        if "message" not in update:
            return "ok", 200
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text","").strip().lower()
        print("Message from", chat_id, ":", text)

        if text.startswith("/price"):
            parts = text.split()
            if len(parts) == 2:
                sym = parts[1].upper() + "USDT"
                try:
                    p = get_binance_price(sym)
                    tg_send_message(chat_id, f"💰 {sym}: {p:.6f} USDT")
                except Exception as e:
                    print("price error:", e)
                    tg_send_message(chat_id, f"❌ Gagal ambil harga: {e}")
            else:
                tg_send_message(chat_id, "Usage: /price BTC")

        elif text.startswith("/chart"):
            parts = text.split()
            if len(parts) == 3:
                sym = parts[1].upper() + "USDT"
                tf = parts[2].lower()
                if tf not in VALID_TFS:
                    tg_send_message(chat_id, "Timeframe invalid. Examples: 15m, 1h, 4h, 1d")
                else:
                    try:
                        tg_send_message(chat_id, f"🔎 Membuat chart {sym} {tf} ...")
                        df = get_binance_klines(sym, tf, limit=400)
                        png = make_chart_png(df.tail(300), title=f"{sym} {tf.upper()}")
                        tg_send_photo(chat_id, png, caption=f"📈 {sym} {tf.upper()} (MA{SMA_FAST}/{SMA_SLOW} + RSI + MACD + S/R + Fib)")
                    except Exception as e:
                        print("chart error:", e)
                        traceback.print_exc()
                        tg_send_message(chat_id, f"❌ Gagal buat chart: {e}")
            else:
                tg_send_message(chat_id, "Usage: /chart <symbol> <timeframe>. Example: /chart bnb 4h")

        else:
            tg_send_message(chat_id, "Commands:\n/price <coin>\n/chart <coin> <timeframe>")

        return "ok", 200
    except Exception as e:
        print("webhook handler error:", e)
        traceback.print_exc()
        return "ok", 200

# ----------------- set webhook and run -----------------
def ensure_webhook():
    try:
        info = requests.get(f"{TELEGRAM_API}/getWebhookInfo", timeout=10).json()
        current = info.get("result", {}).get("url","")
        expected = f"{RAILWAY_URL}/{TOKEN}"
        print("Webhook current:", current)
        if current != expected:
            r = requests.post(f"{TELEGRAM_API}/setWebhook", data={"url": expected}, timeout=10)
            print("setWebhook result:", r.status_code, r.text)
        else:
            print("Webhook already set to expected URL")
    except Exception as e:
        print("ensure_webhook error:", e)

if __name__ == "__main__":
    print("Starting app, ensuring webhook...")
    ensure_webhook()
    start_background_thread()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
