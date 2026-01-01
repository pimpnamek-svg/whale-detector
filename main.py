from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1>Whale Detector Online</h1>"
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


import time

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
    elapsed = (int(time.time()) - ENGINE_START) % CYCLE_LENGTH
    running = 0
    for state in STATE_ORDER:
        running += PHASE_DURATIONS[state]
        if elapsed < running:
            return state
    return "POSITIONING"
# ==========================
# CONFIDENCE ENGINE (v1 - hooks only)
# ==========================

def compute_confidence(phase: str) -> int:
    """
    Display-only confidence score.
    This is a scaffold — real whale inputs plug in later.
    """
    base = {
        "POSITIONING": 10,
        "TRANSITION": 25,
        "DISTRIBUTION": 15,
        "RELEASE": 40,
    }.get(phase, 0)

    # Future hooks (placeholders)
    whale_accumulation = 0   # +30 later
    volume_alignment = 0     # +20 later
    structure_intact = 0     # +10 later

    confidence = base + whale_accumulation + volume_alignment + structure_intact
    return min(confidence, 100)
