# Architecture

## Overview

Siren VRAM Monitor is a lightweight Windows daemon that watches GPU VRAM usage and dispatches notifications when a large model (e.g. Gemma 4 via Ollama) unloads from memory.

## Data Flow

```
┌─────────────────────────────────────────────────┐
│  Ollama (Open WebUI)                            │
│  Gemma 4 loaded → ~15 GB VRAM occupied         │
│  Idle timeout (5 min) → model unloaded          │
└────────────────────┬────────────────────────────┘
                     │ VRAM drops
                     ▼
┌─────────────────────────────────────────────────┐
│  vram_monitor.py  (polling thread)              │
│  pynvml → reads free/used/total every 5s        │
│  State: IDLE → LOADED → triggers on release     │
└──────────┬──────────────────────┬───────────────┘
           │                      │
           ▼                      ▼
┌──────────────────┐   ┌──────────────────────────┐
│  Windows Toast   │   │  FastAPI HTTP Server      │
│  + winsound      │   │  localhost:8765           │
│  (user alert)    │   │                           │
└──────────────────┘   │  GET  /status             │
                       │  POST /subscribe           │
                       │  POST /test                │
                       └────────────┬─────────────┘
                                    │ POST (webhook)
                                    ▼
                       ┌────────────────────────────┐
                       │  Future Agents             │
                       │  OpenClaw / ComfyUI trigger │
                       │  Tailscale mesh nodes       │
                       └────────────────────────────┘
```

## State Machine

```
IDLE ──(free VRAM < threshold)──► LOADED
                                     │
                              (free VRAM ≥ threshold)
                                     │
                                     ▼
                                 fire event ──► back to LOADED
```

- **IDLE**: startup state, before any model is detected in VRAM
- **LOADED**: model is occupying VRAM; monitoring for release
- **Release trigger**: free VRAM crosses above threshold → notification fired → returns to LOADED (supports repeated load/unload cycles)

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Current VRAM stats and state |
| POST | `/subscribe` | Register agent webhook URL |
| DELETE | `/subscribe` | Remove webhook URL |
| GET | `/subscribers` | List registered webhooks |
| POST | `/test` | Manually fire a test notification |

### `/status` response

```json
{
  "state": "LOADED",
  "free_gb": 1.2,
  "used_gb": 14.8,
  "total_gb": 16.0,
  "last_event": "VRAM_RELEASED",
  "last_event_time": "2026-04-21T14:32:00",
  "notification_count": 3,
  "subscriber_count": 1
}
```

### `VRAM_RELEASED` webhook payload

```json
{
  "event": "VRAM_RELEASED",
  "free_gb": 14.5,
  "used_gb": 1.5,
  "total_gb": 16.0,
  "timestamp": "2026-04-21T14:32:00",
  "message": "GPU VRAM released. 14.5 GB now available."
}
```

## Future: Tailscale Integration

The HTTP server binds to `0.0.0.0`, so it is immediately accessible across the Tailscale mesh. Agents on other nodes can subscribe directly:

```bash
curl -X POST http://100.x.x.x:8765/subscribe \
     -H "Content-Type: application/json" \
     -d '{"url": "http://100.x.x.x:9000/on_vram_released"}'
```
