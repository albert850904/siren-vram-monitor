# Siren VRAM Monitor

A lightweight Windows daemon that watches GPU VRAM usage and fires a notification the moment a large language model (e.g. Gemma 4 via Ollama) unloads from memory — so you know exactly when ComfyUI is safe to run.

Part of the **Siren Project** — a self-hosted AI pipeline running over a Tailscale mesh network.

---

## How It Works

```
Open WebUI → Ollama loads Gemma 4 (~15 GB VRAM)
     │
     │  (conversation ends, Ollama idle-unloads after ~5 min)
     │
     ▼
vram_monitor.py detects free VRAM > 10 GB
     ├── Windows Toast notification + sound
     └── POST to all registered agent webhooks
```

The monitor uses a simple state machine:

```
IDLE → LOADED (VRAM occupied) → release detected → notify → back to LOADED
```

This means it handles repeated load/unload cycles automatically.

---

## Requirements

- Windows 10 / 11
- Python 3.11+
- NVIDIA GPU with driver support
- Ollama (or any inference backend)

---

## Installation

```bash
git clone https://github.com/your-username/siren-vram-monitor.git
cd siren-vram-monitor

pip install -r requirements.txt
```

---

## Usage

```bash
# Default: threshold 10 GB, poll every 5s, server on port 8765
python vram_monitor.py

# Custom
python vram_monitor.py --threshold 10 --poll 5 --port 8765
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--threshold` | `10.0` | Free VRAM (GB) that triggers notification |
| `--poll` | `5` | Seconds between VRAM checks |
| `--port` | `8765` | Local HTTP server port |

---

## HTTP API

The monitor runs a local HTTP server for agent integration.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/status` | Current VRAM stats and state |
| `POST` | `/subscribe` | Register a webhook URL |
| `DELETE` | `/subscribe` | Remove a webhook URL |
| `GET` | `/subscribers` | List all registered webhooks |
| `POST` | `/test` | Fire a test notification immediately |

### Example: Check Status

```bash
curl http://localhost:8765/status
```

```json
{
  "state": "LOADED",
  "free_gb": 1.2,
  "used_gb": 14.8,
  "total_gb": 16.0,
  "notification_count": 2,
  "subscriber_count": 1
}
```

### Example: Register an Agent Webhook

```bash
curl -X POST http://localhost:8765/subscribe \
     -H "Content-Type: application/json" \
     -d '{"url": "http://your-agent/on_vram_released"}'
```

When VRAM is released, the monitor will POST this payload to your URL:

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

---

## Dashboard

A React dashboard (`dashboard/vram_dashboard.jsx`) is included for visual monitoring. It connects to the local HTTP server and shows live VRAM usage, state changes, event log, and webhook management.

---

## Tailscale / Mesh Network

The HTTP server binds to `0.0.0.0`, making it reachable from any node on your Tailscale mesh without additional configuration:

```bash
curl -X POST http://100.x.x.x:8765/subscribe \
     -d '{"url": "http://100.x.x.x:9000/on_vram_released"}'
```

See [docs/architecture.md](docs/architecture.md) for the full design.

---

## Tip: Instant Unload with Ollama

By default Ollama keeps a model in VRAM for 5 minutes after the last request. To unload immediately after each conversation, set:

```bash
# In your Ollama environment
OLLAMA_KEEP_ALIVE=0
```

Or pass `keep_alive: 0` in your Open WebUI model settings.

---

## Roadmap

- [ ] System tray icon with live VRAM bar
- [ ] Auto-trigger ComfyUI workflow via API
- [ ] Tailscale-aware multi-node event broadcast
- [ ] OpenClaw agent integration

---

## License

MIT
