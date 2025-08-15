# main.py
import os
import io
import time
import threading
import requests
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf
from flask import Flask, request

# ===== CONFIG (env) =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

ALERT_PAIR = "LTCUSDT"
ALERT_TF = "4h"
ALERT_INTERVAL = 300

SMA_FAST = 50
SMA_SLOW = 200
KLIMIT = 500

VALID_TFS = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"}

app = Flask(__name__)

# ===== helpers: telegram =====
def send_text(chat_id, text, parse_mode="Markdown"):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": parse_mode}, timeout=15)
    except Exception as e:
        print("send_text error:", e)

def send_photo(chat_id, png_bytes, caption=None):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        files = {"photo": ("chart.png", png_bytes)}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "Markdown"
        requests.post(url, data=data, files=files, timeout=30)
    except Exception as e:
        print("send_photo error:", e)

# ===== Binance data =====
def get_binance_price(symbol="BTCUSDT"):
    r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol.upper()}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

def get_klines(symbol="BTCUSDT", interval="1h", limit=500):
    symbol = symbol.upper()
    if interval not in VALID_TFS:
        raise ValueError("Invalid timeframe")
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, KLIMIT)}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
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

# ===== indicators =====
def sma(series, period):
    return series.rolling(period).mean()

def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ma_up / (ma_down.replace(0, np.nan))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

# ===== S/R via swing detection =====
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

def pick_sr_from_swings(highs, lows):
    # take most recent meaningful swing low as support, swing high as resistance
    sup = min(lows) if len(lows)>0 else None
    res = max(highs) if len(highs)>0 else None
    return sup, res

# ===== Fibonacci helpers =====
def fib_levels(support, resistance):
    # assumes support < resistance
    low = support
    high = resistance
    diff = high - low
    retracements = {
        "0.0": high,
        "0.236": high - diff * 0.236,
        "0.382": high - diff * 0.382,
        "0.5": high - diff * 0.5,
        "0.618": high - diff * 0.618,
        "1.0": low
    }
    # extension above resistance (sell zone)
    extensions = {
        "1.272": high + diff * 0.272,
        "1.618": high + diff * 0.618
    }
    return retracements, extensions

# ===== charting with S/R & fib =====
def make_chart_with_sr_fib(df, title="", sma_fast=SMA_FAST, sma_slow=SMA_SLOW, swing_win=5):
    # df: open,high,low,close,volume
    plot_df = df.copy()
    plot_df.columns = ["Open","High","Low","Close","Volume"]
    close = plot_df["Close"]
    ma_fast = sma(close, sma_fast)
    ma_slow = sma(close, sma_slow)
    rsi_v = rsi(close)
    macd_line, macd_sig, macd_hist = macd(close)

    # find swing highs/lows
    highs = plot_df["High"].to_numpy()
    lows = plot_df["Low"].to_numpy()
    s_highs, s_lows = find_swings(highs, lows, window=swing_win)
    support, resistance = pick_sr_from_swings(s_highs, s_lows)

    # compute fib levels if both exist and support < resistance
    retr = {}
    ext = {}
    if support is not None and resistance is not None and support < resistance:
        retr, ext = fib_levels(support, resistance)

    # prepare mpf addplots
    addplots = [
        mpf.make_addplot(ma_fast, color='blue'),
        mpf.make_addplot(ma_slow, color='red'),
        mpf.make_addplot(rsi_v, panel=1, ylabel='RSI'),
        mpf.make_addplot(macd_line, panel=2, color='fuchsia'),
        mpf.make_addplot(macd_sig, panel=2, color='green'),
        mpf.make_addplot(macd_hist, type='bar', panel=2, color='dimgray', width=0.7)
    ]

    fig, axes = mpf.plot(
        plot_df,
        type='candle',
        style='binance',
        addplot=addplots,
        volume=False,
        returnfig=True,
        figsize=(12,9),
        tight_layout=True,
        panel_ratios=(6,2,2)
    )

    ax_main = axes[0]  # price axes

    # draw S/R lines
    if support is not None:
        ax_main.hlines(support, plot_df.index[0], plot_df.index[-1], colors='green', linestyles='--', linewidth=1.2, label='Support')
        ax_main.text(plot_df.index[-1], support, f"  S {support:.4f}", color='green', va='bottom', fontsize=8)
    if resistance is not None:
        ax_main.hlines(resistance, plot_df.index[0], plot_df.index[-1], colors='red', linestyles='--', linewidth=1.2, label='Resistance')
        ax_main.text(plot_df.index[-1], resistance, f"  R {resistance:.4f}", color='red', va='bottom', fontsize=8)

    # draw swing markers (optional - small markers)
    # convert indices to datetimes for markers
    # (we won't mark every swing to keep chart clean)

    # draw fibonacci retracement lines
    if retr:
        colors = {'0.236':'#cc9900','0.382':'#cc6600','0.5':'#888888','0.618':'#009900'}
        for key, lvl in retr.items():
            ax_main.hlines(lvl, plot_df.index[0], plot_df.index[-1], colors=colors.get(key,'#999999'), linestyles=':', linewidth=1)
            ax_main.text(plot_df.index[-1], lvl, f" {key} {lvl:.4f}", color=colors.get(key,'#999999'), va='bottom', fontsize=7)

    # draw extension (sell zone)
    if ext:
        ax_main.hlines(ext.get("1.618"), plot_df.index[0], plot_df.index[-1], colors='purple', linestyles='-.', linewidth=1.2)
        ax_main.text(plot_df.index[-1], ext.get("1.618"), f"  EXT 1.618 {ext.get('1.618'):.4f}", color='purple', va='bottom', fontsize=8)

    # title
    if title:
        ax_main.set_title(title)

    # save to buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=160)
    buf.seek(0)
    try:
        import matplotlib.pyplot as plt
        plt.close(fig)
    except:
        pass
    return buf.read()

# ===== state for alerts =====
last_cross = None
last_rsi_state = None
last_macd_state = None

# ===== alert loop (monitor ALERT_PAIR ALERT_TF) =====
def alert_loop():
    global last_cross, last_rsi_state, last_macd_state
    send_text(TELEGRAM_CHAT_ID, f"🤖 Bot aktif: monitor {ALERT_PAIR} {ALERT_TF}")
    while True:
        try:
            df = get_klines(ALERT_PAIR, ALERT_TF, limit=KLIMIT)
            close = df["close"]
            ma_fast = sma(close, SMA_FAST)
            ma_slow = sma(close, SMA_SLOW)
            rsi_v = rsi(close)
            macd_line, macd_sig, macd_hist = macd(close)

            # crossover detection
            if len(ma_slow.dropna()) >= 2:
                prev_fast = ma_fast.iloc[-2]; prev_slow = ma_slow.iloc[-2]
                curr_fast = ma_fast.iloc[-1]; curr_slow = ma_slow.iloc[-1]
                cross_up = prev_fast <= prev_slow and curr_fast > curr_slow
                cross_dn = prev_fast >= prev_slow and curr_fast < curr_slow
                if cross_up and last_cross != "bull":
                    last_cross = "bull"
                    png = make_chart_with_sr_fib(df.tail(400), title=f"{ALERT_PAIR} {ALERT_TF} — GOLDEN CROSS")
                    send_photo(TELEGRAM_CHAT_ID, png, caption=f"🟢 GOLDEN CROSS {ALERT_PAIR} {ALERT_TF}\nPrice: {close.iloc[-1]:.6f}")
                if cross_dn and last_cross != "bear":
                    last_cross = "bear"
                    png = make_chart_with_sr_fib(df.tail(400), title=f"{ALERT_PAIR} {ALERT_TF} — DEATH CROSS")
                    send_photo(TELEGRAM_CHAT_ID, png, caption=f"🔴 DEATH CROSS {ALERT_PAIR} {ALERT_TF}\nPrice: {close.iloc[-1]:.6f}")

            # RSI alerts
            curr_rsi = rsi_v.iloc[-1]
            if curr_rsi > 70 and last_rsi_state != "over":
                last_rsi_state = "over"
                send_text(TELEGRAM_CHAT_ID, f"⚠️ RSI OVERBOUGHT {ALERT_PAIR} {ALERT_TF}: RSI={curr_rsi:.1f}")
            elif curr_rsi < 30 and last_rsi_state != "under":
                last_rsi_state = "under"
                send_text(TELEGRAM_CHAT_ID, f"✅ RSI OVERSOLD {ALERT_PAIR} {ALERT_TF}: RSI={curr_rsi:.1f}")

            # MACD cross
            if len(macd_line) >= 2:
                prev_m = macd_line.iloc[-2]; prev_s = macd_sig.iloc[-2]
                curr_m = macd_line.iloc[-1]; curr_s = macd_sig.iloc[-1]
                macd_up = prev_m <= prev_s and curr_m > curr_s
                macd_dn = prev_m >= prev_s and curr_m < curr_s
                if macd_up and last_macd_state != "bull":
                    last_macd_state = "bull"
                    send_text(TELEGRAM_CHAT_ID, f"🔔 MACD CROSS UP {ALERT_PAIR} {ALERT_TF}")
                if macd_dn and last_macd_state != "bear":
                    last_macd_state = "bear"
                    send_text(TELEGRAM_CHAT_ID, f"🔔 MACD CROSS DOWN {ALERT_PAIR} {ALERT_TF}")

        except Exception as e:
            print("alert_loop error:", e)
            try:
                send_text(TELEGRAM_CHAT_ID, f"⚠️ Bot error: {e}")
            except:
                pass
        time.sleep(ALERT_INTERVAL)

# ===== webhook handler (commands) =====
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json()
        if not update or "message" not in update:
            return "ok", 200
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text","").strip().lower()
        print("incoming:", chat_id, text)

        if text.startswith("/price"):
            parts = text.split()
            if len(parts)==2:
                coin = parts[1].upper()
                sym = f"{coin}USDT"
                try:
                    p = get_binance_price(sym)
                    send_text(chat_id, f"💰 {sym}: {p:.6f} USDT", parse_mode=None)
                except Exception as e:
                    send_text(chat_id, f"❌ Gagal ambil harga: {e}", parse_mode=None)
            else:
                send_text(chat_id, "⚠️ Format: /price <coin>. Contoh: /price eth")

        elif text.startswith("/chart"):
            parts = text.split()
            if len(parts)==3:
                coin = parts[1].upper()
                tf = parts[2].lower()
                if tf not in VALID_TFS:
                    send_text(chat_id, "⚠️ Timeframe invalid. Contoh: 15m, 1h, 4h, 1d")
                else:
                    sym = f"{coin}USDT"
                    try:
                        df = get_klines(sym, tf, limit=400)
                        png = make_chart_with_sr_fib(df.tail(300), title=f"{sym} {tf.upper()}")
                        send_photo(chat_id, png, caption=f"📈 {sym} {tf.upper()} (MA{SMA_FAST}/{SMA_SLOW} + RSI + MACD + S/R + Fib)")
                    except Exception as e:
                        send_text(chat_id, f"❌ Gagal buat chart: {e}")
            else:
                send_text(chat_id, "⚠️ Format: /chart <coin> <timeframe>. Contoh: /chart bnb 4h")

        else:
            send_text(chat_id, "Perintah:\n/price <coin>\n/chart <coin> <timeframe>\nContoh: /chart bnb 4h")

        return "ok", 200
    except Exception as e:
        print("webhook error:", e)
        return "ok", 200

# ===== set webhook =====
def set_webhook():
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
        data = {"url": f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"}
        r = requests.post(url, data=data, timeout=20)
        print("setWebhook:", r.json())
    except Exception as e:
        print("set_webhook error:", e)

# ===== start background thread =====
def start_background():
    t = threading.Thread(target=alert_loop, daemon=True)
    t.start()

if __name__ == "__main__":
    missing = [k for k in ("TELEGRAM_TOKEN","TELEGRAM_CHAT_ID","WEBHOOK_URL") if not os.getenv(k)]
    if missing:
        print("Missing env vars:", missing)
    set_webhook()
    start_background()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
