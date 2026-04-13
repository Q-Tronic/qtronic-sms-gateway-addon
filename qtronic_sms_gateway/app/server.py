"""Ingress web UI and REST API for the Q-Tronic SMS Gateway add-on."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from qtronic_gateway.config import AddonConfig, load_config
from qtronic_gateway.gateway import GatewayService
from qtronic_gateway.homeassistant_bridge import HomeAssistantEventBridge
from qtronic_gateway.mqtt_bridge import MQTTBridge

_LOGGER = logging.getLogger("qtronic_sms_gateway.server")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)


class RuntimeState:
    """Shared app runtime state."""

    def __init__(self) -> None:
        self.config: AddonConfig | None = None
        self.config_error: str | None = None
        self.gateway: GatewayService | None = None
        self.mqtt: MQTTBridge | None = None
        self.ha_events: HomeAssistantEventBridge | None = None


runtime = RuntimeState()


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Q-Tronic SMS Gateway</title>
  <style>
    :root {
      --bg: #0d0f12;
      --panel: #171b21;
      --panel-2: #1f2630;
      --text: #e8edf4;
      --muted: #9fb0c3;
      --accent: #16a4d8;
      --good: #5fd18b;
      --bad: #ff6e6e;
      --warn: #f0c15b;
      --border: #2b3440;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at top, #162333 0%, var(--bg) 55%);
      color: var(--text);
      font: 14px/1.5 "Segoe UI", system-ui, sans-serif;
    }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 28px; }
    .hero {
      display: flex; justify-content: space-between; align-items: center; gap: 24px;
      margin-bottom: 24px;
    }
    .hero h1 { margin: 0 0 8px; font-size: 34px; }
    .hero p { margin: 0; color: var(--muted); max-width: 720px; }
    .grid {
      display: grid; gap: 18px;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }
    .card {
      background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(0,0,0,0.08)), var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.25);
    }
    .card h2 { margin: 0 0 14px; font-size: 18px; }
    .pill {
      display: inline-flex; align-items: center; gap: 8px;
      border-radius: 999px; padding: 6px 12px; font-size: 12px;
      background: var(--panel-2); border: 1px solid var(--border);
    }
    .good { color: var(--good); }
    .bad { color: var(--bad); }
    .warn { color: var(--warn); }
    dl { margin: 0; display: grid; grid-template-columns: 1fr auto; gap: 8px 12px; }
    dt { color: var(--muted); }
    dd { margin: 0; text-align: right; }
    .stack { display: grid; gap: 12px; }
    .list { margin: 0; padding-left: 18px; }
    .mono { font-family: Consolas, monospace; }
    input, textarea, button, select {
      width: 100%; border-radius: 12px; border: 1px solid var(--border);
      background: #10151b; color: var(--text); padding: 12px 14px; font: inherit;
    }
    textarea { min-height: 112px; resize: vertical; }
    button {
      background: linear-gradient(180deg, #1bb0e7, #117ca4);
      border: none; cursor: pointer; font-weight: 600;
    }
    button:hover { filter: brightness(1.06); }
    .actions { display: grid; gap: 10px; }
    .row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .events { max-height: 420px; overflow: auto; }
    .event {
      border-top: 1px solid rgba(255,255,255,0.06);
      padding: 10px 0;
    }
    .event:first-child { border-top: 0; padding-top: 0; }
    .event-type { font-weight: 700; margin-bottom: 4px; }
    .muted { color: var(--muted); }
    .footer { margin-top: 18px; color: var(--muted); font-size: 12px; }
    @media (max-width: 720px) {
      .wrap { padding: 16px; }
      .hero { display: block; }
      .row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div>
        <h1>Q-Tronic SMS Gateway</h1>
        <p>Ingress dashboard dla bramki GSM opartej o ESPHome. REST i MQTT działają na tym samym backendzie, więc to jest docelowa podstawa pod przyszłą integrację Home Assistant.</p>
      </div>
      <div class="pill" id="availability-pill">Ładowanie...</div>
    </div>

    <div class="grid">
      <section class="card">
        <h2>Status</h2>
        <dl id="status-grid"></dl>
      </section>

      <section class="card">
        <h2>Połączenia i MQTT</h2>
        <dl id="transport-grid"></dl>
      </section>

      <section class="card">
        <h2>Odbiorcy zapisani</h2>
        <ul id="recipients" class="list"></ul>
      </section>

      <section class="card">
        <h2>Wyślij SMS</h2>
        <div class="actions">
          <input id="sms-recipient" placeholder="+48xxxxxx542 lub ID odbiorcy">
          <textarea id="sms-message" placeholder="Treść wiadomości"></textarea>
          <select id="sms-encoding">
            <option value="auto">auto</option>
            <option value="passthrough">passthrough</option>
            <option value="transliterate">transliterate</option>
            <option value="ucs2">ucs2</option>
          </select>
          <button onclick="sendSms()">Wyślij SMS</button>
        </div>
      </section>

      <section class="card">
        <h2>Wykonaj połączenie</h2>
        <div class="actions">
          <input id="call-recipient" placeholder="+48xxxxxx542 lub ID odbiorcy">
          <input id="call-ring" type="number" min="1" value="20" placeholder="Czas dzwonienia [s]">
          <div class="row">
            <button onclick="callRecipient()">Zadzwoń</button>
            <button onclick="hangup()">Rozłącz</button>
          </div>
        </div>
      </section>

      <section class="card">
        <h2>Ostatnie zdarzenia</h2>
        <div id="events" class="events"></div>
      </section>

      <section class="card">
        <h2>Konfiguracja</h2>
        <pre id="config" class="mono muted"></pre>
      </section>
    </div>

    <div class="footer">Q-Tronic SMS Gateway Add-on</div>
  </div>

  <script>
    function ingressApiUrl(relativePath) {
      const basePath = window.location.pathname.endsWith("/")
        ? window.location.pathname
        : window.location.pathname + "/";
      const cleanPath = relativePath.replace(/^\\//, "");
      return basePath + cleanPath;
    }

    const API_STATUS_URL = ingressApiUrl("api/status");
    const API_CONFIG_URL = ingressApiUrl("api/config");
    const API_EVENTS_URL = ingressApiUrl("api/events");
    const API_SEND_SMS_URL = ingressApiUrl("api/send-sms");
    const API_CALL_URL = ingressApiUrl("api/call");
    const API_HANGUP_URL = ingressApiUrl("api/hangup");

    function setText(id, value) {
      const node = document.getElementById(id);
      if (node) node.textContent = value;
    }

    function renderDefinitionList(id, items) {
      const node = document.getElementById(id);
      node.innerHTML = "";
      for (const [label, value] of items) {
        const dt = document.createElement("dt");
        dt.textContent = label;
        const dd = document.createElement("dd");
        dd.textContent = value ?? "—";
        node.appendChild(dt);
        node.appendChild(dd);
      }
    }

    function formatTime(ts) {
      if (!ts) return "—";
      return new Date(ts * 1000).toLocaleString();
    }

    async function loadStatus() {
      const [statusResp, configResp, eventsResp] = await Promise.all([
        fetch(API_STATUS_URL),
        fetch(API_CONFIG_URL),
        fetch(API_EVENTS_URL)
      ]);
      const status = await statusResp.json();
      const config = await configResp.json();
      const events = await eventsResp.json();

      const available = status.available ? "Połączono" : "Rozłączono";
      const pill = document.getElementById("availability-pill");
      pill.textContent = available;
      pill.className = "pill " + (status.available ? "good" : "bad");

      renderDefinitionList("status-grid", [
        ["ESPHome host", status.host],
        ["Urządzenie", status.device?.name || "—"],
        ["Model", status.device?.model || "—"],
        ["RSSI", status.states?.rssi ?? "—"],
        ["Registered", status.states?.registered ?? "—"],
        ["Call state", status.states?.call_state ?? "—"],
        ["Last error", status.last_connect_error || "—"]
      ]);

      renderDefinitionList("transport-grid", [
        ["MQTT", config.mqtt?.enabled ? "włączone" : "wyłączone"],
        ["MQTT host", config.mqtt?.host || "—"],
        ["MQTT prefix", config.mqtt?.topic_prefix || "—"],
        ["Queue depth", status.queue_depth],
        ["Active job", status.active_job_kind || "—"],
        ["Last SMS batch", status.last_sms_batch?.status || "—"],
        ["Last Call batch", status.last_call_batch?.status || "—"]
      ]);

      const recipients = document.getElementById("recipients");
      recipients.innerHTML = "";
      for (const recipient of (status.saved_recipients || [])) {
        const item = document.createElement("li");
        item.textContent = `${recipient.name} (${recipient.phone}) [${recipient.id}]`;
        recipients.appendChild(item);
      }
      if (!recipients.innerHTML) recipients.innerHTML = "<li>Brak odbiorców zapisanych</li>";

      document.getElementById("config").textContent = JSON.stringify(config, null, 2);

      const eventsNode = document.getElementById("events");
      eventsNode.innerHTML = "";
      for (const event of events.events || []) {
        const block = document.createElement("div");
        block.className = "event";
        block.innerHTML = `
          <div class="event-type">${event.type}</div>
          <div class="muted">${formatTime(event.timestamp)}</div>
          <pre class="mono">${JSON.stringify(event, null, 2)}</pre>
        `;
        eventsNode.appendChild(block);
      }
      if (!eventsNode.innerHTML) eventsNode.innerHTML = "<div class='muted'>Brak zdarzeń</div>";
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Request failed");
      }
      await loadStatus();
      return data;
    }

    async function sendSms() {
      const recipient = document.getElementById("sms-recipient").value.trim();
      const message = document.getElementById("sms-message").value;
      const encoding = document.getElementById("sms-encoding").value;
      const payload = { message, encoding };
      if (recipient.startsWith("+") || recipient.match(/^\\d/)) payload.recipient = recipient;
      else payload.recipient_id = recipient;
      try {
        await postJson(API_SEND_SMS_URL, payload);
        alert("SMS został zlecony");
      } catch (err) {
        alert(err.message);
      }
    }

    async function callRecipient() {
      const recipient = document.getElementById("call-recipient").value.trim();
      const ringTime = parseInt(document.getElementById("call-ring").value, 10) || 20;
      const payload = { ring_time_s: ringTime };
      if (recipient.startsWith("+") || recipient.match(/^\\d/)) payload.recipient = recipient;
      else payload.recipient_id = recipient;
      try {
        await postJson(API_CALL_URL, payload);
        alert("Połączenie zostało zlecone");
      } catch (err) {
        alert(err.message);
      }
    }

    async function hangup() {
      try {
        await postJson(API_HANGUP_URL, {});
        alert("Rozłączenie zostało zlecone");
      } catch (err) {
        alert(err.message);
      }
    }

    loadStatus();
    setInterval(loadStatus, 5000);
  </script>
</body>
</html>"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_path = os.environ.get("QTRONIC_OPTIONS", "/data/options.json")
    try:
        runtime.config = load_config(config_path)
        runtime.gateway = GatewayService(runtime.config)
        await runtime.gateway.async_start()
        runtime.ha_events = HomeAssistantEventBridge(runtime.gateway)
        await runtime.ha_events.start()
        runtime.mqtt = MQTTBridge(runtime.gateway)
        await runtime.mqtt.start()
        _LOGGER.info("Q-Tronic SMS Gateway runtime initialized successfully")
    except Exception as err:  # pragma: no cover - runtime guard
        runtime.config_error = str(err)
        _LOGGER.exception("Failed to initialize add-on runtime: %s", err)
    try:
        yield
    finally:
        if runtime.mqtt is not None:
            await runtime.mqtt.stop()
        if runtime.ha_events is not None:
            await runtime.ha_events.stop()
        if runtime.gateway is not None:
            await runtime.gateway.async_stop()


app = FastAPI(title="Q-Tronic SMS Gateway", lifespan=lifespan)


def _gateway_or_400() -> GatewayService:
    if runtime.gateway is None:
        raise HTTPException(status_code=400, detail=runtime.config_error or "Gateway is not initialized.")
    return runtime.gateway


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> str:
    html = _dashboard_html()
    _LOGGER.info(
        "Serving ingress dashboard with path=%s root_path=%s",
        request.url.path,
        request.scope.get("root_path", ""),
    )
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "config_error": runtime.config_error,
        "gateway_available": runtime.gateway.available if runtime.gateway else False,
    }


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    if runtime.gateway is None:
        return {
            "available": False,
            "config_error": runtime.config_error,
        }
    return runtime.gateway.snapshot()


@app.get("/api/config")
async def api_config() -> dict[str, Any]:
    if runtime.config is None:
        return {"config_error": runtime.config_error}
    return runtime.config.sanitized()


@app.get("/api/events")
async def api_events() -> dict[str, Any]:
    if runtime.gateway is None:
        return {"events": []}
    return {"events": runtime.gateway.events_snapshot()}


@app.post("/api/send-sms")
async def api_send_sms(request: Request) -> JSONResponse:
    gateway = _gateway_or_400()
    payload = await request.json()
    if not payload.get("message"):
        raise HTTPException(status_code=400, detail="Message is required.")
    try:
        _LOGGER.info("REST send-sms requested")
        recipients = gateway.resolve_recipient_numbers(
            recipient=payload.get("recipient"),
            recipient_id=payload.get("recipient_id"),
            recipients=payload.get("recipients"),
            recipient_ids=payload.get("recipient_ids"),
        )
        result = await gateway.async_send_sms_batch(
            message=str(payload["message"]),
            recipients=recipients,
            encoding=payload.get("encoding"),
        )
        return JSONResponse(result)
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.post("/api/call")
async def api_call(request: Request) -> JSONResponse:
    gateway = _gateway_or_400()
    payload = await request.json()
    try:
        _LOGGER.info("REST call requested")
        recipients = gateway.resolve_recipient_numbers(
            recipient=payload.get("recipient"),
            recipient_id=payload.get("recipient_id"),
            recipients=payload.get("recipients"),
            recipient_ids=payload.get("recipient_ids"),
        )
        result = await gateway.async_call_batch(
            recipients=recipients,
            ring_time_s=payload.get("ring_time_s"),
        )
        return JSONResponse(result)
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.post("/api/hangup")
async def api_hangup() -> JSONResponse:
    gateway = _gateway_or_400()
    try:
        _LOGGER.info("REST hangup requested")
        result = await gateway.async_hangup()
        return JSONResponse(result)
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8099, log_level="info")


if __name__ == "__main__":
    main()
