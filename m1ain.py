import os
import io
import time
import requests
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # penting untuk server tanpa display
import mplfinance as mpf
from flask import Flask, request

# ===== CONFIG =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # contoh: https://autocut-production.up.railway.app

SYMBOLS = ["LTCUSDT", "BTCUSDT", "ETHUSDT"]  # dipakai untuk live price + S/R
PAIR = "LTCUSDT"            # pasangan utama untuk S/R & crossover alert
TIMEFRAME = "5m"            # 1m,3m,5m,15m,1h,4h,1d
ALERT_INTERVAL = 300        # 5 menit
NEAR_TOL = 0.003            # 0.3% dari level S/R
SMA_FAST = 50
SMA_SLOW = 200
KLIMIT = 300                # jumlah candle diambil (cukup untuk MA200 di 5m)

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
def get_binance_price(symbol="LTCUSDT"):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return float(r.json()["price"])

def get_klines(symbol="LTCUSDT", interval="5m", limit=500):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000)}
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

# ===== S/R =====
def pivot_levels(h, l, c):
    H1, L1, C1 = h[-2], l[-2], c[-2]
    pp = (H1 + L1 + C1)/3
    r1 = 2*pp - L1
    s1 = 2*pp - H1
    r2 = pp + (H1 - L1)
    s2 = pp - (H1 - L1)
    return [s1, s2], [r1, r2]

def swing_levels(h, l, window=5):
    highs, lows = [], []
    for i in range(window, len(h)-window):
        if h[i] == max(h[i-window:i+window+1]): highs.append(h[i])
        if l[i] == min(l[i-window:i+window+1]): lows.append(l[i])
    return sorted(set(highs)), sorted(set(lows))

def pct_diff(a, b):
    return abs(a - b) / b if b != 0 else 0

# ===== CHARTING =====
def make_chart_png(df, title="", mav=(SMA_FAST, SMA_SLOW)):
    """Return PNG bytes of a candlestick chart with MAs."""
    # mpf butuh kolom: Open, High, Low, Close, Volume (capitalized)
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
    try:
        import matplotlib.pyplot as plt
        plt.close(fig)
    except:
        pass
    return buf.read()

# ===== STATE UNTUK CROSSOVER =====
last_cross_state = None  # "bull", "bear", atau None

# ===== LOOP OTOMATIS =====
def auto_loop():
    global last_cross_state
    send_text(TELEGRAM_CHAT_ID, "🤖 Bot aktif: Live Price + S/R tiap 5 menit + MA50/200 crossover alert dengan chart.")

    last_alerts = set()  # anti-spam untuk S/R
    while True:
        try:
            # ---------- Live price agregat ----------
            live_msg = ["💹 *Live Price Update*"]
            for sym in SYMBOLS:
                df = get_klines(sym, TIMEFRAME, KLIMIT)
                c = df["close"].to_numpy()
                price = c[-1]
                ma50  = pd.Series(c).rolling(SMA_FAST).mean().iloc[-1]
                ma200 = pd.Series(c).rolling(SMA_SLOW).mean().iloc[-1]
                t_icon = "📈" if ma50 > ma200 else ("📉" if ma50 < ma200 else "⚪")
                live_msg.append(f"{t_icon} {sym}: {price:.2f} | MA{SMA_FAST}:{ma50:.2f} | MA{SMA_SLOW}:{ma200:.2f}")
            send_text(TELEGRAM_CHAT_ID, "\n".join(live_msg))

            # ---------- S/R + Crossover khusus PAIR ----------
            dfp = get_klines(PAIR, TIMEFRAME, KLIMIT)
            cp = dfp["close"].to_numpy()
            hp = dfp["high"].to_numpy()
            lp = dfp["low"].to_numpy()
            price_p = cp[-1]

            # S/R
            sup_piv, res_piv = pivot_levels(hp, lp, cp)
            swing_res, swing_sup = swing_levels(hp, lp)
            supports = sorted(set(sup_piv + swing_sup))
            resistances = sorted(set(res_piv + swing_res))

            # Tren
            ma50_p  = pd.Series(cp).rolling(SMA_FAST).mean()
            ma200_p = pd.Series(cp).rolling(SMA_SLOW).mean()
            trend_up = ma50_p.iloc[-1] > ma200_p.iloc[-1]
            trend_down = ma50_p.iloc[-1] < ma200_p.iloc[-1]

            # Alert S/R (tanpa gambar agar tidak spam)
            for s in supports:
                if pct_diff(price_p, s) <= NEAR_TOL:
                    aid = f"{PAIR}-SUP-{s:.2f}"
                    if aid not in last_alerts:
                        last_alerts.add(aid)
                        send_text(TELEGRAM_CHAT_ID,
                                  f"🟢 SUPPORT TEST {PAIR}: {price_p:.2f} dekat {s:.2f} "
                                  f"({'tren naik' if trend_up else 'netral/bear'})")
            for r in resistances:
                if pct_diff(price_p, r) <= NEAR_TOL:
                    aid = f"{PAIR}-RES-{r:.2f}"
                    if aid not in last_alerts:
                        last_alerts.add(aid)
                        send_text(TELEGRAM_CHAT_ID,
                                  f"🔴 RESISTANCE TEST {PAIR}: {price_p:.2f} dekat {r:.2f} "
                                  f"({'tren turun' if trend_down else 'netral/bull'})")

            # Crossover MA50/200 (dengan chart)
            if len(ma200_p.dropna()) > 2:
                prev_fast = ma50_p.iloc[-2]
                prev_slow = ma200_p.iloc[-2]
                curr_fast = ma50_p.iloc[-1]
                curr_slow = ma200_p.iloc[-1]

                cross_up = prev_fast <= prev_slow and curr_fast > curr_slow  # Golden Cross
                cross_dn = prev_fast >= prev_slow and curr_fast < curr_slow  # Death Cross

                if cross_up and last_cross_state != "bull":
                    last_cross_state = "bull"
                    png = make_chart_png(dfp.tail(200), title=f"{PAIR} {TIMEFRAME} — Golden Cross")
                    caption = (f"🟢 *GOLDEN CROSS* {PAIR}\n"
                               f"MA{SMA_FAST} potong MA{SMA_SLOW} naik\n"
                               f"Harga: {price_p:.2f}")
                    send_photo(TELEGRAM_CHAT_ID, png, caption)

                if cross_dn and last_cross_state != "bear":
                    last_cross_state = "bear"
                    png = make_chart_png(dfp.tail(200), title=f"{PAIR} {TIMEFRAME} — Death Cross")
                    caption = (f"🔴 *DEATH CROSS* {PAIR}\n"
                               f"MA{SMA_FAST} potong MA{SMA_SLOW} turun\n"
                               f"Harga: {price_p:.2f}")
                    send_photo(TELEGRAM_CHAT_ID, png, caption)

            time.sleep(ALERT_INTERVAL)

        except Exception as e:
            print("auto_loop error:", e)
            # kirim error ringan ke chat agar tahu bot masih hidup
            try:
                send_text(TELEGRAM_CHAT_ID, f"⚠️ Bot error singkat: {e}")
            except:
                pass
            time.sleep(30)

# ===== TELEGRAM COMMANDS =====
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json()
        if not update or "message" not in update:
            return "ok", 200

        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "").strip().lower()
        print(f"Incoming from {chat_id}: {text}")  # bantu cari chat_id di logs

        # /price <coin>  -> contoh: /price eth
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

        # /chart <coin>  -> kirim chart candle + MA50/200
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

        else:
            send_text(chat_id, "Perintah tersedia:\n/price <coin>\n/chart <coin>", parse=None)

        return "ok", 200
    except Exception as e:
        print("webhook error:", e)
        return "ok", 200

# ===== SET WEBHOOK =====
def set_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    data = {"url": f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"}
    r = requests.post(url, data=data, timeout=20)
    print(r.json())

# ===== START =====
def start_threads():
    import threading
    t = threading.Thread(target=auto_loop, daemon=True)
    t.start()

if __name__ == "__main__":
    set_webhook()
    start_threads()
    app.run(host="0.0.0.0", port=5000)
