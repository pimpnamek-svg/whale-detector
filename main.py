from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import time

app = FastAPI()
# ==========================
# EVALUATOR INPUT SCHEMA
# ==========================

class EvaluatorSignal(BaseModel):
    whale_accumulation: bool
    volume_alignment: bool
    structure_intact: bool
    pullback_severity: int  # 0–3
    structure_break: bool

# ==========================
# MANUAL OVERRIDES (testing only)
# ==========================

FORCE_RELEASE = True   # True => force RELEASE phase
FORCE_LOCK = False      # True => force LOCKED no matter what
# ==========================
# SIMULATED WHALE SIGNALS (testing only)
# ==========================

SIM_WHALE_ACCUMULATION = True   # +30
SIM_VOLUME_ALIGNMENT = True    # +20
SIM_STRUCTURE_INTACT = True    # +10

# ==========================
# CONFIDENCE DECAY (testing)
# ==========================

SIM_PULLBACK_SEVERITY = 2
# 0 = no pullback
# 1 = shallow pullback
# 2 = deep pullback
# 3 = structure break
# ==========================
# STRUCTURE STATE (testing)
# ==========================

SIM_STRUCTURE_BREAK = False
# False = structure intact
# True  = structure broken (hard exit condition)

# ==========================
# PHASE ENGINE (v1)
# ==========================

STATE_ORDER = ["POSITIONING", "TRANSITION", "DISTRIBUTION", "RELEASE"]

PHASE_DURATIONS = {
    "POSITIONING": 6 * 60,
    "TRANSITION": 4 * 60,
    "DISTRIBUTION": 3 * 60,
    "RELEASE": 2 * 60,
}

ENGINE_START = int(time.time())
CYCLE_LENGTH = sum(PHASE_DURATIONS.values())


def current_phase():
    if FORCE_RELEASE:
        return "RELEASE"

    elapsed = (int(time.time()) - ENGINE_START) % CYCLE_LENGTH
    running = 0
    for state in STATE_ORDER:
        running += PHASE_DURATIONS[state]
        if elapsed < running:
            return state
    return "POSITIONING"


# ==========================
# CONFIDENCE ENGINE (v1)
# ==========================

def compute_confidence(
    phase: str,
    whale_accumulation: bool,
    volume_alignment: bool,
    structure_intact: bool,
    pullback_severity: int,
    structure_break: bool
) -> int:

    base = {
        "POSITIONING": 10,
        "TRANSITION": 25,
        "DISTRIBUTION": 15,
        "RELEASE": 40,
    }.get(phase, 0)

    whale_score = 30 if whale_accumulation else 0
    volume_score = 20 if volume_alignment else 0
    structure_score = 10 if structure_intact else 0
    
    confidence = base + whale_score + volume_score + structure_score
    

    # === Confidence decay ===
    if SIM_PULLBACK_SEVERITY == 1:
        confidence -= 10
    elif SIM_PULLBACK_SEVERITY == 2:
        confidence -= 25
    elif SIM_PULLBACK_SEVERITY == 3:
        confidence = 0  # structure broken
    # === Structure break overrides everything ===
    if SIM_STRUCTURE_BREAK:
        return 0

    return max(min(confidence, 100), 0)




# ==========================
# DECISION ENGINE (v1)
# ==========================

MIN_CONFIDENCE_TO_ALLOW = 60

def decision_state(phase: str, confidence: int):
    if FORCE_LOCK:
        return {
            "decision": "LOCKED",
            "reason": "FORCE_LOCK enabled"
        }

    if SIM_STRUCTURE_BREAK:
        return {
            "decision": "LOCKED",
            "reason": "Structure broken"
        }

    if phase != "RELEASE":
        return {
            "decision": "LOCKED",
            "reason": f"Not in RELEASE phase ({phase})"
        }

    if confidence < MIN_CONFIDENCE_TO_ALLOW:
        return {
            "decision": "LOCKED",
            "reason": f"Confidence {confidence} < required {MIN_CONFIDENCE_TO_ALLOW}"
        }

    return {
        "decision": "ALLOW",
        "reason": f"RELEASE phase with confidence {confidence}"
    }
@app.post("/evaluate")
def evaluate(signal: EvaluatorSignal):
    phase = current_phase()

    confidence = compute_confidence(
        phase=phase,
        whale_accumulation=signal.whale_accumulation,
        volume_alignment=signal.volume_alignment,
        structure_intact=signal.structure_intact,
        pullback_severity=signal.pullback_severity,
        structure_break=signal.structure_break
    )

    decision = decision_state(phase, confidence)
    management = trade_management(confidence)

    return {
        "phase": phase,
        "confidence": confidence,
        **decision,
        **management
    }

# ==========================
# TRADE MANAGEMENT TIERS
# ==========================

def trade_management(confidence: int):
    if confidence >= 90:
        return {
            "mode": "RUNNER",
            "instruction": "Let trade run. No early TP. Trail only on structure break."
        }

    if confidence >= 75:
        return {
            "mode": "TREND",
            "instruction": "Hold trade. Use loose trailing stop."
        }

    if confidence >= 60:
        return {
            "mode": "CAUTIOUS",
            "instruction": "Tighten stop. No new adds."
        }

    return {
        "mode": "NO_TRADE",
        "instruction": "Do not hold or enter trade."
    }


# ==========================
# ROUTES
# ==========================

@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1>🐋 Whale Detector Online</h1>"


@app.get("/status")
def status():
    phase = current_phase()
    confidence = compute_confidence(phase)
    permission = "ALLOW" if phase == "RELEASE" else "LOCKED"

    return {
        "engine": "running",
        "phase": phase,
        "confidence": confidence,
        "permission": permission,
        "note": "Display-only. No trades placed."
    }


@app.get("/decision")
def decision():
    phase = current_phase()
    confidence = compute_confidence(phase)
    decision = decision_state(phase, confidence)
    management = trade_management(confidence)

    return {
        "phase": phase,
        "confidence": confidence,
        **decision,
        **management
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    phase = current_phase()
    confidence = compute_confidence(phase)
    decision = decision_state(phase, confidence)
    management = trade_management(confidence)

    color = "green" if decision["decision"] == "ALLOW" else "red"

    return f"""
    <html>
        <head><title>Whale Detector Dashboard</title></head>
        <body style="font-family:sans-serif;padding:40px;">
            <h1>🐋 Whale Detector</h1>

            <h2>Phase: {phase}</h2>
            <h3>Confidence: {confidence}</h3>
            <p>Structure: {"BROKEN" if SIM_STRUCTURE_BREAK else "INTACT"}</p>

            <h2 style="color:{color};">{decision["decision"]}</h2>
            <p>{decision["reason"]}</p>

            <h3>Mode: {management["mode"]}</h3>
            <p>{management["instruction"]}</p>

            <hr/>
            <em>Display-only. No trades placed.</em>
        </body>
    </html>
    """
