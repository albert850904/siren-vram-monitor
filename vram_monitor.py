"""
Siren VRAM Monitor
------------------
Watches GPU VRAM, detects when a large model (e.g. Gemma 4) unloads,
fires a Windows Toast notification + sound, and exposes a local HTTP
server so future agents (OpenClaw, etc.) can subscribe to VRAM events.

Requirements:
    pip install -r requirements.txt

Usage:
    python vram_monitor.py
    python vram_monitor.py --threshold 10 --poll 5 --port 8765
"""

import argparse
import asyncio
import threading
import time
import winsound
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Callable

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import pynvml
from win11toast import toast


# ─────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────
DEFAULT_THRESHOLD_GB = 10.0
DEFAULT_POLL_SEC     = 5
DEFAULT_PORT         = 8765


# ─────────────────────────────────────────────
# State Machine
# ─────────────────────────────────────────────
class VRAMState:
    IDLE     = "IDLE"      # startup, before any model loads
    LOADED   = "LOADED"    # model occupying VRAM (free < threshold)
    RELEASED = "RELEASED"  # transient — triggers notification


@dataclass
class MonitorStatus:
    state: str              = VRAMState.IDLE
    free_gb: float          = 0.0
    used_gb: float          = 0.0
    total_gb: float         = 0.0
    last_event: str         = ""
    last_event_time: str    = ""
    notification_count: int = 0
    subscribers: list       = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d.pop("subscribers")
        d["subscriber_count"] = len(self.subscribers)
        return d


status: MonitorStatus = MonitorStatus()
_event_callbacks: list[Callable] = []


# ─────────────────────────────────────────────
# VRAM Reading
# ─────────────────────────────────────────────
def init_nvml():
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    name = pynvml.nvmlDeviceGetName(handle)
    print(f"[VRAM Monitor] GPU: {name}")
    return handle


def get_vram_gb(handle) -> tuple[float, float, float]:
    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
    to_gb = lambda b: round(b / (1024 ** 3), 2)
    return to_gb(mem.free), to_gb(mem.used), to_gb(mem.total)


# ─────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────
def fire_notification(free_gb: float):
    msg = (
        f"VRAM released — {free_gb:.1f} GB now available.\n"
        "ComfyUI is ready to generate images."
    )
    try:
        toast("Siren Monitor — VRAM Released", msg, app_id="Siren.VRAMMonitor", duration="long")
    except Exception as e:
        print(f"[Notify] Toast failed: {e}")

    try:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass

    print(f"[Notify] 🔔 VRAM Released — {free_gb:.1f} GB free at {datetime.now().strftime('%H:%M:%S')}")


# ─────────────────────────────────────────────
# Event Dispatch
# ─────────────────────────────────────────────
async def dispatch_webhooks(payload: dict):
    if not status.subscribers:
        return
    import httpx
    async with httpx.AsyncClient(timeout=5.0) as client:
        for url in status.subscribers:
            try:
                await client.post(url, json=payload)
                print(f"[Webhook] ✓ {url}")
            except Exception as e:
                print(f"[Webhook] ✗ {url}: {e}")


def trigger_event(free_gb: float):
    now = datetime.now().isoformat()
    status.last_event      = "VRAM_RELEASED"
    status.last_event_time = now
    status.notification_count += 1

    payload = {
        "event":    "VRAM_RELEASED",
        "free_gb":  free_gb,
        "used_gb":  status.used_gb,
        "total_gb": status.total_gb,
        "timestamp": now,
        "message":  f"GPU VRAM released. {free_gb:.1f} GB now available.",
    }

    fire_notification(free_gb)

    for cb in _event_callbacks:
        cb(payload)


# ─────────────────────────────────────────────
# Monitor Loop
# ─────────────────────────────────────────────
def monitor_loop(threshold_gb: float, poll_sec: int):
    handle = init_nvml()
    print(f"[Monitor] Threshold: {threshold_gb} GB | Poll: {poll_sec}s")
    print(f"[Monitor] Waiting for model to load...")

    while True:
        free, used, total = get_vram_gb(handle)
        status.free_gb  = free
        status.used_gb  = used
        status.total_gb = total

        if status.state == VRAMState.IDLE:
            if free < threshold_gb:
                status.state = VRAMState.LOADED
                print(f"[Monitor] ▲ Model LOADED — {free:.1f} GB free")

        elif status.state == VRAMState.LOADED:
            if free >= threshold_gb:
                print(f"[Monitor] ▼ Model RELEASED — {free:.1f} GB free")
                trigger_event(free)
                # Stay in LOADED so repeated load/unload cycles re-trigger
                status.state = VRAMState.LOADED

        time.sleep(poll_sec)


# ─────────────────────────────────────────────
# FastAPI Server
# ─────────────────────────────────────────────
app = FastAPI(title="Siren VRAM Monitor", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/status", summary="Get current VRAM state")
def get_status():
    return JSONResponse(status.to_dict())


@app.post("/subscribe", summary="Register a webhook URL for VRAM_RELEASED events")
def subscribe(body: dict):
    """Body: { "url": "http://your-agent/webhook" }"""
    url = body.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "url required"}, status_code=400)
    if url not in status.subscribers:
        status.subscribers.append(url)
        print(f"[Webhook] + Subscriber: {url}")
    return JSONResponse({"ok": True, "subscribers": len(status.subscribers)})


@app.delete("/subscribe", summary="Remove a webhook URL")
def unsubscribe(body: dict):
    url = body.get("url", "").strip()
    if url in status.subscribers:
        status.subscribers.remove(url)
        print(f"[Webhook] - Removed: {url}")
    return JSONResponse({"ok": True, "subscribers": len(status.subscribers)})


@app.get("/subscribers", summary="List registered webhook URLs")
def list_subscribers():
    return JSONResponse({"subscribers": status.subscribers})


@app.post("/test", summary="Fire a test notification immediately")
def test_notification():
    trigger_event(status.free_gb or 12.0)
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────
# Async bridge: sync monitor thread → async webhooks
# ─────────────────────────────────────────────
_loop: asyncio.AbstractEventLoop | None = None

def _webhook_callback(payload: dict):
    if _loop and status.subscribers:
        asyncio.run_coroutine_threadsafe(dispatch_webhooks(payload), _loop)

_event_callbacks.append(_webhook_callback)


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
def main():
    global _loop, DEFAULT_PORT

    parser = argparse.ArgumentParser(description="Siren VRAM Monitor")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_GB,
                        help="Free VRAM GB to trigger notification (default: 10)")
    parser.add_argument("--poll",      type=int,   default=DEFAULT_POLL_SEC,
                        help="Poll interval in seconds (default: 5)")
    parser.add_argument("--port",      type=int,   default=DEFAULT_PORT,
                        help="Webhook server port (default: 8765)")
    args = parser.parse_args()
    DEFAULT_PORT = args.port

    print("╔══════════════════════════════════════╗")
    print("║   Siren VRAM Monitor  v0.1.0         ║")
    print("╚══════════════════════════════════════╝")

    t = threading.Thread(
        target=monitor_loop,
        args=(args.threshold, args.poll),
        daemon=True,
    )
    t.start()

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
