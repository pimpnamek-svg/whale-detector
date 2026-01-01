from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1>Whale Detector Online</h1>"
@app.get("/status")
def status():
    return {
        "engine": "running",
        "phase": "POSITIONING",
        "confidence": 0,
        "permission": "LOCKED"
    }
