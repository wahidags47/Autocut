import os
import time
import requests
import pandas as pd
import numpy as np

# =============== KONFIG ENV ===============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["LTCUSDT", "BTCUSDT", "ETHUSDT"]  # Pair yang dimonitor
INTERVAL = "15m"  # Timeframe Binance: 1m,3m,5m,15m,1h,4h,1d
NEAR_TOL = 0.003  # 0.3% jarak toleransi
LIMIT = 300       # jumlah candle yang diambil

# =============== FUNGSI ===============
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    requests.post(url, data=data)

def pct_diff(a, b):
    return abs(a - b) / b if b != 0 else 0

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

# =============== MAIN LOOP ===============
if __name__ == "__main__":
    send_telegram("📢 Bot Alert S/R aktif (Binance API).")

    last_alerts = set()

    while True:
        try:
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
                                send_telegram(f"🟢 BUY ALERT: {symbol} {price:.2f} dekat support {sup:.2f} (Tren naik)")
                            else:
                                send_telegram(f"⚪ SUPPORT TEST: {symbol} {price:.2f} dekat support {sup:.2f}")

                # Cek resistance
                for res in all_resists:
                    if pct_diff(price, res) <= NEAR_TOL:
                        alert_id = f"{symbol}-RES-{res:.2f}"
                        if alert_id not in last_alerts:
                            last_alerts.add(alert_id)
                            if trend_down:
                                send_telegram(f"🔴 SELL ALERT: {symbol} {price:.2f} dekat resistance {res:.2f} (Tren turun)")
                            else:
                                send_telegram(f"⚪ RESISTANCE TEST: {symbol} {price:.2f} dekat resistance {res:.2f}")

            time.sleep(300)  # cek tiap 5 menit
 # Kirim live price setelah loop semua simbol
            send_telegram(live_prices_msg)

            time.sleep(300)  # cek tiap 5 menit
        except Exception as e:
            send_telegram(f"⚠️ Error: {e}")
            time.sleep(30)
