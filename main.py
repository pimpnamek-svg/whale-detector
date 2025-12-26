import time
from typing import List, Dict, Any

import ccxt
import numpy as np
from fastapi import FastAPI


# ==========================
# APP
# ==========================
app = FastAPI(title="OKX Liquidity Grab Scanner")
# ==========================
# COOLDOWN STATE (DISPLAY ONLY)
# ==========================
COOLDOWN_DURATION = 900  # seconds (15 minutes)
cooldown_start_time = time.time()
# ==========================
# MANUAL OVERRIDES (TESTING ONLY)
# ==========================
FORCE_RELEASE = True  # set to True to simulate RELEASE


@app.get("/whale-status")
def whale_status():

    # ==========================
    # MANUAL RELEASE OVERRIDE
    # ==========================
    if FORCE_RELEASE:
        return {
            "whale_state": "RELEASE",
            "entry_permission": "ALLOWED",
            "cooldown_seconds_remaining": None,
            "confidence_score": 82,
            "confidence_grade": "A+",
            "fail_state": None,
            "message": "🐋 RELEASE — ENTRY ALLOWED (Confidence: 82)"
        }

    # ==========================
    # NORMAL COOLDOWN LOGIC
    # ==========================
    elapsed = int(time.time() - cooldown_start_time)
    remaining = max(COOLDOWN_DURATION - elapsed, 0)

    is_locked = remaining > 0

    return {
        "whale_state": "POSITIONING" if is_locked else "TRANSITION",
        "entry_permission": "LOCKED",
        "cooldown_seconds_remaining": remaining if is_locked else None,
        "confidence_score": None,
        "confidence_grade": None,
        "fail_state": None,
        "message": (
            f"🐋 POSITIONING — ENTRY LOCKED (⏳ {remaining // 60}:{remaining % 60:02d})"
            if is_locked
            else "🐋 TRANSITION — ENTRY LOCKED (Await confirmation)"
        )
    }





# ==========================
# CONFIG
# ==========================
TIMEFRAME = "5m"
LIMIT = 20

VOLUME_SPIKE_MULT = 6.0
EXTREME_SPIKE_MULT = 8.0
MIN_MOVE_PCT = 2.0
WICK_THRESHOLD = 0.4  # 40% wick


# ==========================
# EXCHANGE
# ==========================
def create_okx_client():
    return ccxt.okx({"enableRateLimit": True})


def get_usdt_symbols(exchange):
    markets = exchange.load_markets()
    return [
        s for s, info in markets.items()
        if info.get("spot") and s.endswith("/USDT")
    ]


def fetch_candles(exchange, symbol):
    return exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LIMIT)


# ==========================
# CORE LOGIC
# ==========================
def analyze_liquidity_grab(candles):
    if len(candles) < 5:
        return None

    vols = [c[5] for c in candles[:-1]]
    last = candles[-1]

    avg_vol = np.mean(vols)
    vol_mult = last[5] / avg_vol if avg_vol > 0 else 0

    o, h, l, c = last[1], last[2], last[3], last[4]
    rng = h - l
    body = abs(c - o)
    wick = rng - body

    move_pct = abs(c - o) / o * 100 if o > 0 else 0

    # Hard filters
    if vol_mult < VOLUME_SPIKE_MULT or move_pct < MIN_MOVE_PCT:
        return None

    wick_ratio = wick / rng if rng > 0 else 0
    if wick_ratio < WICK_THRESHOLD:
        return None

    direction = "bull_trap" if c < o else "bear_trap"

    tier = 2
    if vol_mult >= EXTREME_SPIKE_MULT and move_pct >= MIN_MOVE_PCT * 1.5:
        tier = 3

    # Trade levels
    if direction == "bear_trap":
        entry = round((h + c) / 2, 4)
        stop = round(h * 1.002, 4)
        targets = [round((o + l) / 2, 4), round(l, 4)]
    else:
        entry = round((l + c) / 2, 4)
        stop = round(l * 0.998, 4)
        targets = [round((o + h) / 2, 4), round(h, 4)]

    return {
        "tier": tier,
        "direction": direction,
        "volume_mult": round(vol_mult, 2),
        "move_pct": round(move_pct, 2),
        "entry": entry,
        "stop": stop,
        "targets": targets,
        "alert": "cash_register" if tier == 3 else "bell",
    }


# ==========================
# SCANNER
# ==========================
def run_scan():
    exchange = create_okx_client()
    symbols = get_usdt_symbols(exchange)

    hits = []
    for symbol in symbols:
        try:
            candles = fetch_candles(exchange, symbol)
            signal = analyze_liquidity_grab(candles)
            if signal:
                hits.append({"symbol": symbol, **signal})
        except Exception:
            time.sleep(0.2)

    hits.sort(key=lambda x: (x["tier"], x["volume_mult"]), reverse=True)
    return hits


# ==========================
# ROUTES
# ==========================
@app.get("/")
def health_check():
    return {"status": "ok", "service": "Liquidity Grab Scanner"}


@app.get("/scan")
def scan():
    results = run_scan()
    return {
        "timeframe": TIMEFRAME,
        "signals": results,
        "count": len(results),
    }

