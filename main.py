"""
OKX Liquidity Grab Scanner (DISPLAY / SAFETY MODE)
- FastAPI app exposing:
  - GET /               -> health/status
  - GET /whale-status   -> state, entry permission, timer, confidence, fail_state message
  - POST /admin/reset   -> resets state machine timer
  - POST /admin/force   -> optional manual overrides

IMPORTANT:
- This version is "display-only": it does NOT place trades, and it does NOT claim certainty.
- Designed to be used alongside your Trade Evaluator: you read this as a "permission + timing + confidence" layer.

Run locally:
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Railway start command:
  uvicorn main:app --host 0.0.0.0 --port $PORT
"""

# ==========================
# APP
# ==========================
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("static/index.html") as f:
        return f.read()


# ==========================
# STATE MACHINE CONFIG
# ==========================
# "Timer filter": lock entries during early phases. Only allow in RELEASE.
STATE_ORDER = ["POSITIONING", "TRANSITION", "DISTRIBUTION", "RELEASE"]

# You can tune these. Default: total cycle 15 minutes split across 3 lock phases + release.
PHASE_DURATIONS_SECONDS = {
    "POSITIONING": 6 * 60,    # 6 minutes locked
    "TRANSITION": 4 * 60,     # 4 minutes locked
    "DISTRIBUTION": 3 * 60,   # 3 minutes locked
    "RELEASE": 2 * 60,        # 2 minutes allowed (then cycle restarts)
}

# Safety defaults:
MIN_CONFIDENCE_TO_ALLOW = 70  # Only "allow" if in RELEASE AND confidence >= this
STALE_DATA_FAIL_AFTER = 6 * 60  # If your external data feed (later) hasn't updated, show fail_state.
# ==========================
# CONFIG
# ==========================
STALE_DATA_FAIL_AFTER = 6 * 60

OKX_TOKENS = [
    "ETH/USDT",
    "SOL/USDT",
    "AVAX/USDT",
    "BNB/USDT",
    "DOGE/USDT",
    "PEPE/USDT"
]


# ==========================
# MANUAL OVERRIDES (TESTING ONLY)
# ==========================
FORCE_RELEASE: bool = False     # True => force RELEASE
FORCE_LOCK: bool = False        # True => force LOCKED (even if RELEASE)
FORCE_STATE: Optional[str] = None  # e.g. "TRANSITION" to pin it (must be in STATE_ORDER)
FORCE_CONFIDENCE: Optional[int] = None  # e.g. 82 to simulate


# ==========================
# INTERNAL STATE
# ==========================
@dataclass
class EngineState:
    cycle_started_at: float
    last_data_update_at: float  # later: update this when you pull ccxt / orderflow / etc.


ENGINE = EngineState(
    cycle_started_at=time.time(),
    last_data_update_at=time.time(),
)


# ==========================
# HELPERS
# ==========================
def _now() -> float:
    return time.time()


def _mmss(seconds: int) -> str:
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"


def _grade_from_score(score: Optional[int]) -> Optional[str]:
    if score is None:
        return None
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _compute_phase_and_remaining(now_ts: float) -> Dict[str, Any]:
    """
    Returns:
      - phase: current phase in STATE_ORDER
      - phase_remaining: seconds remaining in current phase
      - cycle_elapsed: seconds elapsed since cycle start
      - cycle_total: total seconds in full cycle
    """
    elapsed = int(now_ts - ENGINE.cycle_started_at)
    cycle_total = sum(PHASE_DURATIONS_SECONDS[p] for p in STATE_ORDER)

    # wrap elapsed in a loop
    elapsed_in_cycle = elapsed % cycle_total

    cursor = 0
    for phase in STATE_ORDER:
        dur = PHASE_DURATIONS_SECONDS[phase]
        if elapsed_in_cycle < cursor + dur:
            phase_elapsed = elapsed_in_cycle - cursor
            phase_remaining = max(dur - phase_elapsed, 0)
            return {
                "phase": phase,
                "phase_remaining": int(phase_remaining),
                "cycle_elapsed": int(elapsed_in_cycle),
                "cycle_total": int(cycle_total),
            }
        cursor += dur

    # fallback (shouldn't happen)
    return {
        "phase": "RELEASE",
        "phase_remaining": 0,
        "cycle_elapsed": int(elapsed_in_cycle),
        "cycle_total": int(cycle_total),
    }


def _compute_confidence(phase: str) -> Optional[int]:
    """
    Confidence meter (display-only).
    Right now it's a simple deterministic model. Later you can replace with real signals.

    Idea:
      - During lock phases, confidence is intentionally hidden/null to prevent "false certainty".
      - During RELEASE, we show a score (can be overridden).
    """
    if FORCE_CONFIDENCE is not None:
        return int(max(0, min(100, FORCE_CONFIDENCE)))

    if phase != "RELEASE":
        return None

    # Simple baseline score in release:
    # (Replace this later with your real “whale + evaluator alignment” score)
    return 82


def _fail_state(now_ts: float) -> Optional[str]:
    """
    Fail-state alert = "do not trust this output right now."
    Examples:
      - stale data (your feed hasn't updated)
      - conflicting overrides
      - system clock / state corruption
    """
    # Conflicting overrides
    if FORCE_RELEASE and FORCE_LOCK:
        return "CONFIG_CONFLICT: FORCE_RELEASE and FORCE_LOCK are both True"

    # Stale data (when you later wire in real data updates, bump ENGINE.last_data_update_at)
    staleness = int(now_ts - ENGINE.last_data_update_at)
    if staleness >= STALE_DATA_FAIL_AFTER:
        return f"STALE_DATA: last update {staleness}s ago"

    # All good
    return None


def _resolve_effective_phase(phase: str) -> str:
    if FORCE_STATE is not None and FORCE_STATE in STATE_ORDER:
        return FORCE_STATE
    if FORCE_RELEASE:
        return "RELEASE"
    return phase


def _entry_permission(phase: str, confidence: Optional[int], fail_state: Optional[str]) -> str:
    """
    Entry permission logic:
      - If fail_state exists -> LOCKED
      - If FORCE_LOCK -> LOCKED
      - Only allow entries in RELEASE with confidence >= threshold
    """
    if fail_state is not None:
        return "LOCKED"
    if FORCE_LOCK:
        return "LOCKED"
    if phase != "RELEASE":
        return "LOCKED"
    if confidence is None:
        return "LOCKED"
    return "ALLOWED" if confidence >= MIN_CONFIDENCE_TO_ALLOW else "LOCKED"


def _message(phase: str, permission: str, phase_remaining: Optional[int], confidence: Optional[int], fail_state: Optional[str]) -> str:
    # If fail-state: make it loud and simple
    if fail_state is not None:
        return f"⚠️ FAIL STATE — ENTRY LOCKED ({fail_state})"

    if permission == "LOCKED":
        # show timer only if we have remaining seconds
        if phase_remaining is not None:
            return f"🐋 {phase} — ENTRY LOCKED (⏳ {_mmss(phase_remaining)})"
        return f"🐋 {phase} — ENTRY LOCKED"

    # allowed
    return f"🐋 RELEASE — ENTRY ALLOWED (Confidence: {confidence})"
# ==========================
# DATA HEARTBEAT (SAFE)
# ==========================
def market_heartbeat():
    """
    Fetches a minimal piece of real market data
    and marks data as fresh.
    """
    try:
        # TEMP: minimal heartbeat (no heavy calls yet)
        # Later this becomes whale detection logic
        ENGINE.last_data_update_at = time.time()
        return True
    except Exception:
        return False
# ==========================
# BTC VOLUME WHALE SIGNAL
# ==========================
def btc_volume_state():
    """
    Detects whale phase based on BTC volume behavior.
    SAFE: read-only, no orders, no side effects.
    """
    try:
        exchange = ccxt.okx()
        
        # 1-minute candles, last ~30 minutes
        candles = exchange.fetch_ohlcv(
            symbol="BTC/USDT",
            timeframe="1m",
            limit=30
        )

        volumes = [c[5] for c in candles]
        avg_volume = sum(volumes[:-1]) / (len(volumes) - 1)
        last_volume = volumes[-1]

        ratio = last_volume / avg_volume if avg_volume > 0 else 0

        # --- Heuristic thresholds (tunable later) ---
        if ratio < 1.2:
            state = "POSITIONING"
        elif 1.2 <= ratio < 1.8:
            state = "TRANSITION"
        elif 1.8 <= ratio < 2.5:
            state = "DISTRIBUTION"
        else:
            state = "RELEASE"

        # mark data as fresh
        ENGINE.last_data_update_at = time.time()

        return {
            "state": state,
            "volume_ratio": round(ratio, 2),
            "last_volume": round(last_volume, 2),
            "avg_volume": round(avg_volume, 2),
        }

    except Exception as e:
        return {
            "state": "UNKNOWN",
            "error": str(e)
        }
# ==========================
# WHALE STATE FROM BTC
# ==========================
def whale_state_from_btc():
    data = btc_volume_state()
    state = data.get("state")

    if state in {"POSITIONING", "TRANSITION", "DISTRIBUTION", "RELEASE"}:
        return state

    return "POSITIONING"

# ==========================
# TOKEN-LEVEL WHALE SCAN
# ==========================
def token_volume_whale_scan(symbol: str):
    try:
        exchange = ccxt.okx()

        candles = exchange.fetch_ohlcv(
            symbol=symbol,
            timeframe="1m",
            limit=30
        )

        volumes = [c[5] for c in candles]
        avg_volume = sum(volumes[:-1]) / (len(volumes) - 1)
        last_volume = volumes[-1]

        ratio = last_volume / avg_volume if avg_volume > 0 else 0

        if ratio < 1.2:
            state = "QUIET"
        elif ratio < 1.8:
            state = "WARMING"
        elif ratio < 2.5:
            state = "HOT"
        else:
            state = "EXPLOSIVE"

        ENGINE.last_data_update_at = time.time()

        return {
            "symbol": symbol,
            "state": state,
            "volume_ratio": round(ratio, 2),
            "last_volume": round(last_volume, 2),
            "avg_volume": round(avg_volume, 2),
        }

    except Exception as e:
        return {
            "symbol": symbol,
            "state": "ERROR",
            "error": str(e)
        }
def scan_okx_tokens():
    results = []

    for symbol in OKX_TOKENS:
        results.append(token_volume_whale_scan(symbol))

    results.sort(
        key=lambda x: x.get("volume_ratio", 0),
        reverse=True
    )

    return results

# ==========================
# BTC + TOKEN AGREEMENT
# ==========================
def whale_candidates():
    try:
        btc_state = whale_state_from_btc()

        if btc_state != "RELEASE":
            return {
                "btc_state": btc_state,
                "candidates": [],
                "reason": "BTC not in RELEASE"
            }

        tokens = scan_okx_tokens() or []

        candidates = [
            t for t in tokens
            if t.get("state") in {"HOT", "EXPLOSIVE"}
        ]

        return {
            "btc_state": btc_state,
            "candidates": candidates,
            "reason": "BTC RELEASE + token confirmation"
        }

    except Exception as e:
        return {
            "btc_state": "UNKNOWN",
            "candidates": [],
            "reason": f"ERROR: {str(e)}"
        }
# ==========================
# REAL TRADE EVALUATOR (MANUAL)
# ==========================
def evaluate_trade(symbol: str):
    try:
        exchange = ccxt.okx()

        # Fetch recent candles (15m for structure)
        candles = exchange.fetch_ohlcv(
            symbol=symbol,
            timeframe="15m",
            limit=50
        )

        highs = [c[2] for c in candles]
        lows = [c[3] for c in candles]
        closes = [c[4] for c in candles]

        current_price = closes[-1]

        # === ATR (14) ===
        trs = []
        for i in range(1, len(candles)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i - 1]
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            trs.append(tr)

        atr = sum(trs[-14:]) / 14

        # === Stop & Target ===
        stop = current_price - (1.5 * atr)
        target = current_price + (4.5 * atr)

        risk = current_price - stop
        reward = target - current_price
        rr = reward / risk if risk > 0 else 0

        # === Structure Check (simple) ===
        structure_ok = current_price > sum(closes[-20:]) / 20  # above 20-period mean

        # === Volatility Check ===
        volatility_ok = atr / current_price < 0.03  # <3% ATR

        # === Scoring ===
        score = 0
        if round(rr, 2) >= 3:
            score += 40
        if structure_ok:
            score += 30
        if volatility_ok:
            score += 30

        grade = (
            "A" if score >= 80 else
            "B" if score >= 65 else
            "C"
        )

        decision = "CONSIDER" if score >= 70 else "PASS"

        return {
            "symbol": symbol,
            "price": round(current_price, 4),
            "atr": round(atr, 4),
            "stop": round(stop, 4),
            "target": round(target, 4),
            "risk_reward": round(rr, 2),
            "structure_ok": structure_ok,
            "volatility_ok": volatility_ok,
            "confidence_score": score,
            "confidence_grade": grade,
            "decision": decision
        }

    except Exception as e:
        return {
            "symbol": symbol,
            "error": str(e),
            "decision": "ERROR"
        }

# ==========================
# API MODELS (ADMIN)
# ==========================
class ForceRequest(BaseModel):
    force_release: Optional[bool] = None
    force_lock: Optional[bool] = None
    force_state: Optional[str] = Field(default=None, description="One of POSITIONING/TRANSITION/DISTRIBUTION/RELEASE")
    force_confidence: Optional[int] = Field(default=None, ge=0, le=100)


# ==========================
# ROUTES
# ==========================
@app.get("/", response_class=HTMLResponse)
async def read_root():
    return HTMLResponse("""
<!DOCTYPE html>
<html>... YOUR FULL HTML CODE HERE ...</html>
    """.strip())

@app.get("/btc-volume")
def btc_volume():
    data = btc_volume_state()
    return data
@app.get("/token-whales")
def token_whales():
    data = scan_okx_tokens()
    return {
        "btc_state": whale_state_from_btc(),
        "tokens": data
    }


@app.get("/whale-status")
def whale_status(force: str | None = Query(default=None)):
    global FORCE_RELEASE, FORCE_STATE

    # ==========================
    # BROWSER-BASED OVERRIDES
    # ==========================
    if force:
        f = force.lower().strip()
        if f == "release":
            FORCE_RELEASE = True
            FORCE_STATE = None
        elif f in {"positioning", "transition", "distribution"}:
            FORCE_RELEASE = False
            FORCE_STATE = f.upper()
        elif f == "clear":
            FORCE_RELEASE = False
            FORCE_STATE = None

    now_ts = time.time()

    phase_info = _compute_phase_and_remaining(now_ts)
    phase = _resolve_effective_phase(phase_info["phase"])

    confidence = _compute_confidence(phase)
    confidence_grade = _grade_from_score(confidence)
    fail_state = _fail_state(now_ts)

    permission = _entry_permission(phase, confidence, fail_state)

    # timer only shown when locked
    cooldown = None
    if permission == "LOCKED":
        if phase == phase_info["phase"]:
            cooldown = phase_info["phase_remaining"]
        else:
            cooldown = PHASE_DURATIONS_SECONDS.get(phase, None)

    return {
        "whale_state": phase,
        "entry_permission": permission,
        "cooldown_seconds_remaining": cooldown,
        "confidence_score": confidence,
        "confidence_grade": confidence_grade,
        "fail_state": fail_state,
        "message": _message(phase, permission, cooldown, confidence, fail_state),
    }

@app.get("/whale-candidates")
def whale_candidates_route():
    return whale_candidates()

@app.get("/evaluate-trade")
def evaluate_trade_route(symbol: str):
    return evaluate_trade(symbol)


@app.post("/admin/reset")
def admin_reset() -> Dict[str, str]:
    """
    Resets the cycle timer back to POSITIONING start.
    Useful if the countdown gets out of sync or you want a fresh cycle.
    """
    ENGINE.cycle_started_at = _now()
    ENGINE.last_data_update_at = _now()
    return {"status": "ok", "message": "Cycle reset"}
  
@app.get("/reset")
def browser_reset():
    ENGINE.cycle_started_at = time.time()
    ENGINE.last_data_update_at = time.time()
    return {
        "status": "ok",
        "message": "Engine reset (browser-safe)"
    }
@app.get("/admin/reset")
def browser_admin_reset():
    ENGINE.cycle_started_at = time.time()
    ENGINE.last_data_update_at = time.time()
    return {
        "status": "ok",
        "message": "Engine reset (browser-safe)"
    }
@app.get("/dashboard")
def dashboard():
    return FileResponse("static/dashboard.html")

@app.get("/heartbeat")
def heartbeat():
    ok = market_heartbeat()
    return {
        "status": "ok" if ok else "error",
        "last_data_update_at": ENGINE.last_data_update_at
    }


@app.post("/admin/force")
def admin_force(payload: ForceRequest) -> Dict[str, Any]:
    """
    Set manual overrides without editing code.
    """
    global FORCE_RELEASE, FORCE_LOCK, FORCE_STATE, FORCE_CONFIDENCE

    if payload.force_release is not None:
        FORCE_RELEASE = bool(payload.force_release)
    if payload.force_lock is not None:
        FORCE_LOCK = bool(payload.force_lock)

    if payload.force_state is not None:
        s = payload.force_state.upper().strip()
        FORCE_STATE = s if s in STATE_ORDER else None

    if payload.force_confidence is not None:
        FORCE_CONFIDENCE = int(payload.force_confidence)

    return {
        "status": "ok",
        "overrides": {
            "FORCE_RELEASE": FORCE_RELEASE,
            "FORCE_LOCK": FORCE_LOCK,
            "FORCE_STATE": FORCE_STATE,
            "FORCE_CONFIDENCE": FORCE_CONFIDENCE,
        },
    }
