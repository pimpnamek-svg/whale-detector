import time
from typing import List, Dict, Any

import ccxt


# ==========================
# CONFIG
# ==========================
EXCHANGE_NAME = "okx"
TIMEFRAME = "5m"          # 5-minute candles
LIMIT = 20                # last 20 candles
VOLUME_SPIKE_MULT = 5.0   # last candle volume >= 5x average
TIER2_MULT = 3.0          # medium spike threshold
TIER3_MULT = 8.0          # extreme spike threshold
MIN_SPIKE_MOVE_PCT = 1.5  # % move in last candle for spike
ACCUM_LOOKBACK = 20       # candles to check for accumulation
ACCUM_MIN_PCT = 1.0       # total move at least this
ACCUM_MAX_PCT = 4.0       # total move at most this
ACCUM_LOW_VOL_FRACTION = 0.6  # fraction of bars with <= average volume


# ==========================
# EXCHANGE WRAPPER
# ==========================
def create_okx_client():
    exchange = ccxt.okx({
        "enableRateLimit": True,
    })
    return exchange


def get_usdt_symbols(exchange) -> List[str]:
    markets = exchange.load_markets()
    symbols = []
    for symbol, info in markets.items():
        # Spot USDT pairs only
        if info.get("spot") and symbol.endswith("/USDT"):
            symbols.append(symbol)
    return symbols


def fetch_candles(exchange, symbol: str, timeframe: str, limit: int):
    """Return list of OHLCV candles: [timestamp, open, high, low, close, volume]."""
    return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)


# ==========================
# ANALYSIS HELPERS
# ==========================
def calc_volume_spike(candles: List[List[float]]) -> Dict[str, Any]:
    if len(candles) < 2:
        return {
            "spike": False,
            "volume_mult": 0.0,
            "move_pct": 0.0
        }

    closes = [c[4] for c in candles]
    vols = [c[5] for c in candles]

    avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
    last_vol = vols[-1]
    last_open = candles[-1][1]
    last_close = candles[-1][4]

    volume_mult = last_vol / avg_vol if avg_vol > 0 else 0.0
    move_pct = abs(last_close - last_open) / last_open * 100 if last_open > 0 else 0.0

    spike = volume_mult >= VOLUME_SPIKE_MULT and move_pct >= MIN_SPIKE_MOVE_PCT

    return {
        "spike": spike,
        "volume_mult": round(volume_mult, 2),
        "move_pct": round(move_pct, 2),
    }


def detect_quiet_accumulation(candles: List[List[float]]) -> Dict[str, Any]:
    if len(candles) < ACCUM_LOOKBACK:
        return {
            "accumulation": False,
            "strength": 0.0,
            "total_move_pct": 0.0
        }

    closes = [c[4] for c in candles]
    vols = [c[5] for c in candles]

    start = closes[-ACCUM_LOOKBACK]
    end = closes[-1]
    total_move_pct = (end - start) / start * 100 if start > 0 else 0.0

    avg_vol = sum(vols[-ACCUM_LOOKBACK:]) / ACCUM_LOOKBACK
    low_vol_bars = sum(1 for v in vols[-ACCUM_LOOKBACK:] if v <= avg_vol)
    low_vol_fraction = low_vol_bars / ACCUM_LOOKBACK

    # Simple rules: uptrend but not mooning, and mostly average/below-average volume
    accumulation = (
        ACCUM_MIN_PCT <= total_move_pct <= ACCUM_MAX_PCT
        and low_vol_fraction >= ACCUM_LOW_VOL_FRACTION
    )

    strength = 0.0
    if accumulation:
        # scale strength by move and low-vol fraction
        strength = min(1.0, (total_move_pct / ACCUM_MAX_PCT) * low_vol_fraction)

    return {
        "accumulation": accumulation,
        "strength": round(strength, 2),
        "total_move_pct": round(total_move_pct, 2),
    }


def assign_tier(spike_info: Dict[str, Any], accum_info: Dict[str, Any]) -> Dict[str, Any]:
    volume_mult = spike_info["volume_mult"]
    move_pct = spike_info["move_pct"]
    accumulation = accum_info["accumulation"]
    strength = accum_info["strength"]

    tier = 0
    reasons = []

    # Tier by spike
    if volume_mult >= TIER2_MULT:
        tier = 1
        reasons.append(f"vol ≥ {TIER2_MULT}x avg")
    if volume_mult >= VOLUME_SPIKE_MULT and spike_info["spike"]:
        tier = 2
        reasons.append(f"whale spike {volume_mult}x, {move_pct}% move")
    if volume_mult >= TIER3_MULT and move_pct >= MIN_SPIKE_MOVE_PCT * 2:
        tier = 3
        reasons.append(f"extreme spike {volume_mult}x, {move_pct}% move")

    # Tier by accumulation
    if accumulation:
        if tier < 1:
            tier = 1
        if strength >= 0.5 and tier < 2:
            tier = 2
        if strength >= 0.8 and tier < 3:
            tier = 3
        reasons.append(f"quiet accumulation, strength {strength}")

    return {
        "tier": tier,
        "reasons": reasons,
    }


# ==========================
# SCAN LOOP
# ==========================
def scan_once() -> List[Dict[str, Any]]:
    exchange = create_okx_client()
    symbols = get_usdt_symbols(exchange)

    results = []
    for symbol in symbols:
        try:
            candles = fetch_candles(exchange, symbol, TIMEFRAME, LIMIT)
            if not candles:
                continue

            spike_info = calc_volume_spike(candles)
            accum_info = detect_quiet_accumulation(candles)
            tier_info = assign_tier(spike_info, accum_info)

            if tier_info["tier"] > 0:
                results.append({
                    "symbol": symbol,
                    "tier": tier_info["tier"],
                    "spike": spike_info["spike"],
                    "volume_mult": spike_info["volume_mult"],
                    "move_pct": spike_info["move_pct"],
                    "accumulation": accum_info["accumulation"],
                    "accum_strength": accum_info["strength"],
                    "total_move_pct": accum_info["total_move_pct"],
                    "reasons": tier_info["reasons"],
                })
        except Exception as e:
            # Ignore single-symbol errors for now, just continue
            print(f"Error scanning {symbol}: {e}")
            time.sleep(0.2)

    # Sort: highest tier first, then highest volume_mult
    results.sort(key=lambda x: (x["tier"], x["volume_mult"]), reverse=True)
    return results


def main():
    print(f"Scanning OKX {TIMEFRAME} USDT pairs (last {LIMIT} candles)...")
    results = scan_once()
    if not results:
        print("No whale or accumulation candidates found this scan.")
        return

    for r in results[:50]:  # show top 50 to avoid spam
        print("-" * 60)
        print(f"Symbol:         {r['symbol']}")
        print(f"Tier:           {r['tier']}")
        print(f"Whale spike:    {r['spike']}  (vol x{r['volume_mult']}, move {r['move_pct']}%)")
        print(f"Accumulation:   {r['accumulation']}  (strength {r['accum_strength']}, total {r['total_move_pct']}%)")
        print("Reasons:")
        for reason in r["reasons"]:
            print(f"  - {reason}")


if __name__ == "__main__":
    main()
