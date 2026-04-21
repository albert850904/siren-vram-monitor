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
import os
import sys
import threading
import time
import winsound
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import pynvml
import pystray
from PIL import Image, ImageDraw
from win11toast import toast


# ─────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────
DEFAULT_THRESHOLD_GB = 10.0
DEFAULT_POLL_SEC     = 5
DEFAULT_PORT         = 8765
COMFYUI_URL          = "http://100.113.6.11:8188"
APP_NAME             = "Siren.VRAMMonitor"
STARTUP_NAME         = "SirenVRAMMonitor"

# ─────────────────────────────────────────────
# Tray Icon
# ─────────────────────────────────────────────
def _make_tray_image(free_pct: float = 1.0) -> Image.Image:
    """32x32 RGBA icon — ring colour reflects VRAM state."""
    img  = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([1, 1, 30, 30], fill=(16, 24, 39, 255))
    color = (
        (239, 68,  68)  if free_pct < 0.2 else
        (245, 158, 11)  if free_pct < 0.5 else
        (16,  185, 129)
    )
    draw.ellipse([1,  1,  30, 30], outline=color, width=3)
    draw.ellipse([13, 13, 18, 18], fill=color)
    return img


def _build_tray() -> pystray.Icon:
    menu = pystray.Menu(
        pystray.MenuItem(
            lambda _: f"VRAM  {status.used_gb:.1f}/{status.total_gb:.0f} GB  [{status.state}]",
            None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open ComfyUI",           lambda _: os.startfile(COMFYUI_URL)),
        pystray.MenuItem("Fire test notification",  lambda _: trigger_event(status.free_gb or 12.0)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, _: icon.stop()),
    )
    return pystray.Icon(APP_NAME, _make_tray_image(), "Siren VRAM Monitor", menu)

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
def _vram_bar(used: float, total: float, width: int = 20) -> str:
    """ASCII progress bar — filled portion = free VRAM."""
    pct    = used / total if total > 0 else 0
    filled = int((1 - pct) * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]  {total - used:.1f} GB free"


def fire_notification(free_gb: float):
    bar = _vram_bar(status.used_gb, status.total_gb)
    try:
        toast(
            "🟢 Siren — VRAM Released",
            f"{bar}\n\nComfyUI is ready to generate images.",
            app_id=APP_NAME,
            duration="long",
            button={
                "activationType": "protocol",
                "arguments": COMFYUI_URL,
                "content": "Open ComfyUI",
            },
        )
    except Exception as e:
        print(f"[Notify] Toast failed: {e}")

    try:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass

    print(f"[Notify] 🔔 {free_gb:.1f} GB free — {datetime.now().strftime('%H:%M:%S')}")


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
def monitor_loop(threshold_gb: float, poll_sec: int, tray: pystray.Icon | None):
    handle = init_nvml()
    print(f"[Monitor] Threshold: {threshold_gb} GB | Poll: {poll_sec}s")
    print(f"[Monitor] Waiting for model to load...")

    while True:
        free, used, total = get_vram_gb(handle)
        status.free_gb  = free
        status.used_gb  = used
        status.total_gb = total

        if tray is not None:
            free_pct   = free / total if total > 0 else 1.0
            tray.icon  = _make_tray_image(free_pct)
            tray.title = f"Siren  {used:.1f}/{total:.0f} GB  [{status.state}]"

        if status.state == VRAMState.IDLE:
            if free < threshold_gb:
                status.state = VRAMState.LOADED
                print(f"[Monitor] ▲ Model LOADED — {free:.1f} GB free")

        elif status.state == VRAMState.LOADED:
            if free >= threshold_gb:
                print(f"[Monitor] ▼ Model RELEASED — {free:.1f} GB free")
                trigger_event(free)
                status.state = VRAMState.IDLE

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
# Startup install / remove
# ─────────────────────────────────────────────
def _startup_bat() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft/Windows/Start Menu/Programs/Startup"
        / f"{STARTUP_NAME}.bat"
    )


def install_startup():
    script = Path(sys.argv[0]).resolve()
    _startup_bat().write_text(f'@echo off\nstart "" pythonw "{script}"\n')
    print(f"[Startup] Installed → {_startup_bat()}")


def remove_startup():
    p = _startup_bat()
    if p.exists():
        p.unlink()
        print(f"[Startup] Removed → {p}")
    else:
        print("[Startup] Not installed.")


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
    parser.add_argument("--no-tray",         action="store_true")
    parser.add_argument("--install-startup", action="store_true")
    parser.add_argument("--remove-startup",  action="store_true")
    args = parser.parse_args()

    if args.install_startup:
        install_startup()
        return
    if args.remove_startup:
        remove_startup()
        return

    DEFAULT_PORT = args.port

    print("╔══════════════════════════════════════╗")
    print("║   Siren VRAM Monitor  v0.2.0         ║")
    print("╚══════════════════════════════════════╝")

    tray = None if args.no_tray else _build_tray()

    t = threading.Thread(
        target=monitor_loop,
        args=(args.threshold, args.poll, tray),
        daemon=True,
    )
    t.start()

    _loop = asyncio.new_event_loop()
    
    def run_api():
        asyncio.set_event_loop(_loop)
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")

    threading.Thread(target=run_api, daemon=True).start()

    if tray:
        tray.run()
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
