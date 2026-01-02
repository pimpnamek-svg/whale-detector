from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import time

app = FastAPI()

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

def compute_confidence(phase: str) -> int:
    base = {
        "POSITIONING": 10,
        "TRANSITION": 25,
        "DISTRIBUTION": 15,
        "RELEASE": 40,
    }.get(phase, 0)

    whale_accumulation = 30 if SIM_WHALE_ACCUMULATION else 0
    volume_alignment = 20 if SIM_VOLUME_ALIGNMENT else 0
    structure_intact = 10 if SIM_STRUCTURE_INTACT else 0

    confidence = base + whale_accumulation + volume_alignment + structure_intact
    return min(confidence, 100)



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

    return {
        "phase": phase,
        "confidence": confidence,
        **decision
    }

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    phase = current_phase()
    confidence = compute_confidence(phase)
    decision = decision_state(phase, confidence)

    color = "green" if decision["decision"] == "ALLOW" else "red"

    return f"""
    <html>
        <head>
            <title>Whale Detector Dashboard</title>
        </head>
        <body style="font-family: sans-serif; padding: 40px;">
            <h1>🐋 Whale Detector</h1>

            <h2>Phase: {phase}</h2>
            <h3>Confidence: {confidence}</h3>

            <h2 style="color: {color};">
                {decision["decision"]}
            </h2>

            <p>{decision["reason"]}</p>

            <hr />
            <p><em>Display-only. No trades placed.</em></p>
        </body>
    </html>
    """
