/**
 * Siren VRAM Monitor — Dashboard
 *
 * A React component that connects to the local vram_monitor.py
 * HTTP server (default: localhost:8765) and visualises GPU VRAM
 * state in real time.
 *
 * Intended to be embedded in a local web app or Electron shell.
 */

import { useState, useEffect, useRef } from "react";

const API = "http://localhost:8765";

const STATE_CONFIG = {
  IDLE:     { label: "Standby",        color: "#6b7280", glow: "#6b728020", icon: "○" },
  LOADED:   { label: "Model Running",  color: "#f59e0b", glow: "#f59e0b30", icon: "▲" },
  RELEASED: { label: "VRAM Released",  color: "#10b981", glow: "#10b98130", icon: "▼" },
};

// ─── Sub-components ──────────────────────────────────────────────

function GaugeRing({ used, total, size = 160 }) {
  const r    = 58;
  const circ = 2 * Math.PI * r;
  const pct  = total > 0 ? used / total : 0;
  const dash = pct * circ;
  const color = pct > 0.85 ? "#ef4444" : pct > 0.6 ? "#f59e0b" : "#10b981";

  return (
    <svg width={size} height={size} viewBox="0 0 140 140">
      <circle cx="70" cy="70" r={r} fill="none" stroke="#1f2937" strokeWidth="14" />
      <circle
        cx="70" cy="70" r={r}
        fill="none" stroke={color} strokeWidth="14"
        strokeDasharray={`${dash} ${circ}`}
        strokeLinecap="round"
        transform="rotate(-90 70 70)"
        style={{ transition: "stroke-dasharray 0.8s cubic-bezier(.4,0,.2,1), stroke 0.5s" }}
      />
      <text x="70" y="64" textAnchor="middle" fill="#f9fafb" fontSize="22" fontWeight="700"
        fontFamily="'JetBrains Mono', monospace">
        {used.toFixed(1)}
      </text>
      <text x="70" y="83" textAnchor="middle" fill="#6b7280" fontSize="11" fontFamily="monospace">
        / {total.toFixed(0)} GB
      </text>
    </svg>
  );
}

function HistoryBar({ history }) {
  return (
    <div style={{ display: "flex", gap: "3px", alignItems: "flex-end", height: "40px" }}>
      {history.map((pt, i) => {
        const pct   = pt.total > 0 ? pt.used / pt.total : 0;
        const color = pct > 0.85 ? "#ef4444" : pct > 0.6 ? "#f59e0b" : "#10b981";
        return (
          <div key={i} style={{
            width: "10px",
            height: `${Math.max(4, pct * 40)}px`,
            background: color,
            borderRadius: "2px 2px 0 0",
            opacity: 0.3 + (i / history.length) * 0.7,
            transition: "height 0.4s ease",
          }} title={`${pt.used.toFixed(1)} GB used`} />
        );
      })}
    </div>
  );
}

function StatBox({ label, value, unit, accent }) {
  return (
    <div style={{
      background: "#111827", border: `1px solid ${accent}30`,
      borderRadius: "10px", padding: "12px 16px", flex: 1,
    }}>
      <div style={{ color: "#6b7280", fontSize: "11px", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: "4px" }}>
        {label}
      </div>
      <div style={{ color: "#f9fafb", fontSize: "20px", fontFamily: "'JetBrains Mono', monospace", fontWeight: "700" }}>
        {value}<span style={{ fontSize: "12px", color: accent, marginLeft: "3px" }}>{unit}</span>
      </div>
    </div>
  );
}

// ─── Main Component ──────────────────────────────────────────────

export default function VRAMDashboard() {
  const [status,    setStatus]    = useState(null);
  const [history,   setHistory]   = useState([]);
  const [events,    setEvents]    = useState([]);
  const [connected, setConnected] = useState(false);
  const [subUrl,    setSubUrl]    = useState("");
  const [subMsg,    setSubMsg]    = useState("");
  const prevState = useRef(null);

  // Google Fonts
  useEffect(() => {
    const link = document.createElement("link");
    link.rel  = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap";
    document.head.appendChild(link);
  }, []);

  // Poll /status every 3 s
  useEffect(() => {
    const poll = async () => {
      try {
        const r    = await fetch(`${API}/status`);
        const data = await r.json();
        setStatus(data);
        setConnected(true);
        setHistory(h => [...h, { used: data.used_gb, total: data.total_gb }].slice(-30));

        if (prevState.current && prevState.current !== data.state) {
          const cfg = STATE_CONFIG[data.state];
          setEvents(ev => [{
            time:  new Date().toLocaleTimeString("en-US"),
            state: data.state,
            label: cfg?.label ?? data.state,
            free:  data.free_gb,
          }, ...ev].slice(0, 8));
        }
        prevState.current = data.state;
      } catch {
        setConnected(false);
      }
    };

    poll();
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, []);

  const handleSubscribe = async () => {
    if (!subUrl) return;
    try {
      const r = await fetch(`${API}/subscribe`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ url: subUrl }),
      });
      const d = await r.json();
      setSubMsg(`✓ Subscribed — ${d.subscribers} subscriber(s)`);
      setSubUrl("");
    } catch {
      setSubMsg("✗ Connection failed");
    }
    setTimeout(() => setSubMsg(""), 3000);
  };

  const handleTest = async () => {
    try {
      await fetch(`${API}/test`, { method: "POST" });
      setEvents(ev => [{
        time:  new Date().toLocaleTimeString("en-US"),
        state: "TEST",
        label: "Test Notification",
        free:  status?.free_gb ?? 0,
      }, ...ev].slice(0, 8));
    } catch {}
  };

  const cfg = STATE_CONFIG[status?.state] ?? STATE_CONFIG.IDLE;

  return (
    <div style={{
      minHeight: "100vh", background: "#030712", color: "#f9fafb",
      fontFamily: "'Syne', sans-serif", padding: "32px 24px",
      maxWidth: "520px", margin: "0 auto",
    }}>
      {/* Header */}
      <div style={{ marginBottom: "28px", display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: "11px", letterSpacing: "0.2em", color: "#4b5563", textTransform: "uppercase", marginBottom: "4px" }}>
            SIREN PROJECT
          </div>
          <h1 style={{ fontSize: "26px", fontWeight: "800", margin: 0, letterSpacing: "-0.02em" }}>
            VRAM Monitor
          </h1>
        </div>
        <div style={{
          display: "flex", alignItems: "center", gap: "6px",
          background: connected ? "#052e1640" : "#1f0f0f",
          border: `1px solid ${connected ? "#10b98140" : "#ef444430"}`,
          borderRadius: "20px", padding: "6px 12px",
          fontSize: "12px", color: connected ? "#10b981" : "#ef4444",
        }}>
          <div style={{
            width: "7px", height: "7px", borderRadius: "50%",
            background: connected ? "#10b981" : "#ef4444",
            boxShadow: connected ? "0 0 6px #10b981" : "none",
            animation: connected ? "pulse 2s infinite" : "none",
          }} />
          {connected ? "Connected" : "Disconnected"}
        </div>
      </div>

      {/* State Banner */}
      <div style={{
        background: cfg.glow, border: `1px solid ${cfg.color}40`,
        borderRadius: "14px", padding: "20px", marginBottom: "20px",
        display: "flex", alignItems: "center", gap: "20px",
        transition: "all 0.6s ease",
      }}>
        <GaugeRing used={status?.used_gb ?? 0} total={status?.total_gb ?? 16} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: "11px", color: "#6b7280", letterSpacing: "0.1em", marginBottom: "6px" }}>
            CURRENT STATE
          </div>
          <div style={{ fontSize: "20px", fontWeight: "700", color: cfg.color, marginBottom: "10px" }}>
            {cfg.icon} {cfg.label}
          </div>
          <HistoryBar history={history} />
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: "flex", gap: "10px", marginBottom: "20px" }}>
        <StatBox label="Free"          value={(status?.free_gb ?? 0).toFixed(1)}  unit="GB"  accent="#10b981" />
        <StatBox label="In Use"        value={(status?.used_gb ?? 0).toFixed(1)}  unit="GB"  accent="#f59e0b" />
        <StatBox label="Notifications" value={status?.notification_count ?? 0}    unit="×"   accent="#6366f1" />
      </div>

      {/* Event Log */}
      <div style={{
        background: "#0d1117", border: "1px solid #1f2937",
        borderRadius: "12px", padding: "16px", marginBottom: "20px",
      }}>
        <div style={{ fontSize: "11px", color: "#4b5563", letterSpacing: "0.1em", marginBottom: "12px" }}>
          EVENT LOG
        </div>
        {events.length === 0 ? (
          <div style={{ color: "#374151", fontSize: "13px", fontFamily: "monospace", textAlign: "center", padding: "12px 0" }}>
            — awaiting events —
          </div>
        ) : events.map((ev, i) => (
          <div key={i} style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            padding: "7px 0",
            borderBottom: i < events.length - 1 ? "1px solid #1f2937" : "none",
            opacity: 1 - i * 0.1,
          }}>
            <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
              <div style={{
                width: "7px", height: "7px", borderRadius: "50%",
                background: ev.state === "TEST" ? "#6366f1" : STATE_CONFIG[ev.state]?.color ?? "#6b7280",
              }} />
              <span style={{ fontSize: "13px", color: "#d1d5db" }}>{ev.label}</span>
            </div>
            <div style={{ display: "flex", gap: "12px", fontSize: "12px", color: "#6b7280", fontFamily: "monospace" }}>
              <span>{ev.free.toFixed(1)} GB free</span>
              <span>{ev.time}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Webhook Subscribe */}
      <div style={{
        background: "#0d1117", border: "1px solid #1f2937",
        borderRadius: "12px", padding: "16px", marginBottom: "16px",
      }}>
        <div style={{ fontSize: "11px", color: "#4b5563", letterSpacing: "0.1em", marginBottom: "12px" }}>
          AGENT WEBHOOK
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <input
            value={subUrl}
            onChange={e => setSubUrl(e.target.value)}
            placeholder="http://your-agent/webhook"
            style={{
              flex: 1, background: "#111827", border: "1px solid #374151",
              borderRadius: "8px", padding: "9px 12px",
              color: "#f9fafb", fontSize: "13px", fontFamily: "monospace", outline: "none",
            }}
          />
          <button onClick={handleSubscribe} style={{
            background: "#1d4ed8", border: "none", borderRadius: "8px",
            padding: "9px 16px", color: "#fff", fontSize: "13px",
            cursor: "pointer", fontFamily: "'Syne', sans-serif", fontWeight: "700",
          }}>
            Subscribe
          </button>
        </div>
        {subMsg && <div style={{ marginTop: "8px", fontSize: "12px", color: "#10b981" }}>{subMsg}</div>}
        <div style={{ marginTop: "10px", fontSize: "11px", color: "#374151", fontFamily: "monospace", lineHeight: "1.8" }}>
          GET&nbsp;&nbsp;localhost:8765/status<br />
          POST localhost:8765/subscribe {"{ \"url\": \"...\" }"}
        </div>
      </div>

      {/* Test Button */}
      <button onClick={handleTest} style={{
        width: "100%", background: "transparent",
        border: "1px solid #374151", borderRadius: "10px",
        padding: "11px", color: "#6b7280", fontSize: "13px",
        cursor: "pointer", fontFamily: "'Syne', sans-serif",
        transition: "all 0.2s",
      }}
        onMouseEnter={e => { e.target.style.borderColor = "#6366f1"; e.target.style.color = "#818cf8"; }}
        onMouseLeave={e => { e.target.style.borderColor = "#374151"; e.target.style.color = "#6b7280"; }}
      >
        ⚡ Fire Test Notification
      </button>

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </div>
  );
}
