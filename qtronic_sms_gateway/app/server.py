"""Ingress web UI and REST API for the Q-Tronic SMS Gateway add-on."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import os
import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
import uvicorn

from qtronic_gateway.config import AddonConfig, load_config
from qtronic_gateway.gateway import GatewayService
from qtronic_gateway.homeassistant_bridge import HomeAssistantEventBridge
from qtronic_gateway.mqtt_bridge import MQTTBridge
from qtronic_gateway.security import (
    API_VERSION,
    APIAuthenticator,
    load_or_create_api_token,
)
from qtronic_gateway.sms import sms_segment_info
from qtronic_gateway.validation import (
    CallRequest,
    CancelRequest,
    HistoryQuery,
    RequestValidationError,
    SmsSendRequest,
    UssdRequest,
)

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
        self.authenticator: APIAuthenticator | None = None


runtime = RuntimeState()


def _dashboard_html(nonce: str) -> str:
    html = """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Q-Tronic SMS Gateway</title>
  <style nonce="__CSP_NONCE__">
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
    .card--full {
      grid-column: 1 / -1;
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
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
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
          <div id="sms-segments" class="muted">Segmenty: —</div>
          <select id="sms-encoding">
            <option value="auto">auto</option>
            <option value="passthrough">passthrough</option>
            <option value="transliterate">transliterate</option>
            <option value="ucs2">ucs2</option>
          </select>
          <button id="send-sms-button" type="button">Wyślij SMS</button>
        </div>
      </section>

      <section class="card">
        <h2>Wykonaj połączenie</h2>
        <div class="actions">
          <input id="call-recipient" placeholder="+48xxxxxx542 lub ID odbiorcy">
          <input id="call-ring" type="number" min="1" value="20" placeholder="Czas dzwonienia [s]">
          <div class="row">
            <button id="call-button" type="button">Zadzwoń</button>
            <button id="hangup-button" type="button">Rozłącz / anuluj</button>
          </div>
        </div>
      </section>

      <section class="card card--full">
        <h2>Trwała kolejka SMS</h2>
        <div id="outbox" class="events"></div>
      </section>

      <section class="card">
        <h2>Ostatnie zdarzenia</h2>
        <div id="events" class="events"></div>
      </section>

      <section class="card card--full">
        <h2>Konfiguracja</h2>
        <pre id="config" class="mono muted"></pre>
      </section>
    </div>

    <div class="footer">
      Q-Tronic SMS Gateway Add-on ·
      <a id="history-json-link" href="#">Eksport historii JSON</a> ·
      <a id="history-csv-link" href="#">Eksport historii CSV</a> ·
      <a id="config-export-link" href="#">Eksport bezpiecznej konfiguracji</a>
    </div>
  </div>

  <script nonce="__CSP_NONCE__">
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
    const API_SEGMENTS_URL = ingressApiUrl("api/segments");
    const API_OUTBOX_URL = ingressApiUrl("api/outbox?limit=30");

    function setText(id, value) {
      const node = document.getElementById(id);
      if (node) node.textContent = value;
    }

    function renderDefinitionList(id, items) {
      const node = document.getElementById(id);
      node.replaceChildren();
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
      const [statusResp, configResp, eventsResp, outboxResp] = await Promise.all([
        fetch(API_STATUS_URL),
        fetch(API_CONFIG_URL),
        fetch(API_EVENTS_URL),
        fetch(API_OUTBOX_URL)
      ]);
      const status = await statusResp.json();
      const config = await configResp.json();
      const events = await eventsResp.json();
      const outbox = await outboxResp.json();

      const available = status.available ? "ESP: OK" : "ESP: OFFLINE";
      const pill = document.getElementById("availability-pill");
      pill.textContent = available;
      pill.className = "pill " + (status.available ? "good" : "bad");

      const espStatus = status.component_status?.esp === "ok" ? "OK" : "OFFLINE";
      const sim800Labels = {
        online: "ONLINE (zarejestrowany)",
        offline: "OFFLINE (brak odpowiedzi)",
        not_registered: "OFFLINE / brak rejestracji",
        unknown: "NIEZNANY"
      };
      const sim800Status = sim800Labels[status.component_status?.sim800] || "NIEZNANY";

      renderDefinitionList("status-grid", [
        ["ESP", espStatus],
        ["SIM800C", sim800Status],
        ["ESPHome host", status.host],
        ["Urządzenie", status.device?.name || "—"],
        ["Model", status.device?.model || "—"],
        ["RSSI", status.states?.rssi ?? "—"],
        ["Modem online", status.states?.modem_online ?? "—"],
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
      recipients.replaceChildren();
      for (const recipient of (status.saved_recipients || [])) {
        const item = document.createElement("li");
        item.textContent = `${recipient.name} (${recipient.phone}) [${recipient.id}]`;
        recipients.appendChild(item);
      }
      if (!recipients.childElementCount) {
        const emptyRecipient = document.createElement("li");
        emptyRecipient.textContent = "Brak odbiorców zapisanych";
        recipients.appendChild(emptyRecipient);
      }

      document.getElementById("config").textContent = JSON.stringify(config, null, 2);

      const eventsNode = document.getElementById("events");
      eventsNode.replaceChildren();
      for (const event of events.events || []) {
        const block = document.createElement("div");
        block.className = "event";
        const eventType = document.createElement("div");
        eventType.className = "event-type";
        eventType.textContent = String(event.type ?? "unknown");
        const eventTime = document.createElement("div");
        eventTime.className = "muted";
        eventTime.textContent = formatTime(event.timestamp);
        const eventPayload = document.createElement("pre");
        eventPayload.className = "mono";
        eventPayload.textContent = JSON.stringify(event, null, 2);
        block.append(eventType, eventTime, eventPayload);
        eventsNode.appendChild(block);
      }
      if (!eventsNode.childElementCount) {
        const emptyEvent = document.createElement("div");
        emptyEvent.className = "muted";
        emptyEvent.textContent = "Brak zdarzeń";
        eventsNode.appendChild(emptyEvent);
      }

      renderOutbox(outbox.jobs || []);
    }

    function renderOutbox(jobs) {
      const node = document.getElementById("outbox");
      node.replaceChildren();
      const terminal = new Set(["sent", "failed", "unknown", "canceled"]);
      for (const job of jobs) {
        const block = document.createElement("div");
        block.className = "event";
        const title = document.createElement("div");
        title.className = "event-type";
        title.textContent = `${job.status} · ${job.job_id}`;
        const detail = document.createElement("div");
        detail.className = "muted";
        const recipients = Array.isArray(job.payload?.recipients)
          ? job.payload.recipients.join(", ")
          : "—";
        detail.textContent = `${formatTime(job.created_at)} · ${recipients} · próba ${job.attempts}/${job.max_attempts}`;
        block.append(title, detail);
        if (!terminal.has(job.status)) {
          const cancel = document.createElement("button");
          cancel.type = "button";
          cancel.textContent = "Anuluj";
          cancel.addEventListener("click", async () => {
            try {
              await postJson(ingressApiUrl(`api/jobs/${encodeURIComponent(job.job_id)}/cancel`), {});
            } catch (err) {
              alert(err.message);
            }
          });
          block.appendChild(cancel);
        }
        node.appendChild(block);
      }
      if (!node.childElementCount) {
        const empty = document.createElement("div");
        empty.className = "muted";
        empty.textContent = "Kolejka jest pusta";
        node.appendChild(empty);
      }
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
      const payload = { message, encoding, wait: false };
      if (recipient.startsWith("+") || recipient.match(/^\\d/)) payload.recipient = recipient;
      else payload.recipient_id = recipient;
      try {
        const result = await postJson(API_SEND_SMS_URL, payload);
        renderSegmentInfo(result.segment_info);
        alert(`SMS przyjęty do kolejki: ${result.job_id}`);
      } catch (err) {
        alert(err.message);
      }
    }

    function renderSegmentInfo(info) {
      if (!info) {
        setText("sms-segments", "Segmenty: —");
        return;
      }
      setText(
        "sms-segments",
        `Segmenty: ${info.segments} · alfabet: ${info.alphabet} · jednostki: ${info.units}`
      );
    }

    let segmentTimer;
    async function refreshSegments() {
      const message = document.getElementById("sms-message").value;
      const encoding = document.getElementById("sms-encoding").value;
      if (!message) {
        renderSegmentInfo(null);
        return;
      }
      try {
        const response = await fetch(API_SEGMENTS_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, encoding })
        });
        const info = await response.json();
        if (!response.ok) throw new Error(info.detail || "Nie można policzyć segmentów");
        renderSegmentInfo(info);
      } catch (err) {
        setText("sms-segments", err.message);
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

    document.getElementById("send-sms-button").addEventListener("click", sendSms);
    document.getElementById("call-button").addEventListener("click", callRecipient);
    document.getElementById("hangup-button").addEventListener("click", hangup);
    document.getElementById("sms-message").addEventListener("input", () => {
      clearTimeout(segmentTimer);
      segmentTimer = setTimeout(refreshSegments, 600);
    });
    document.getElementById("sms-encoding").addEventListener("change", refreshSegments);
    document.getElementById("history-json-link").href = ingressApiUrl("api/history/export?format=json&limit=5000");
    document.getElementById("history-csv-link").href = ingressApiUrl("api/history/export?format=csv&limit=5000");
    document.getElementById("config-export-link").href = ingressApiUrl("api/config/export");

    loadStatus();
    setInterval(loadStatus, 5000);
  </script>
</body>
</html>"""
    return html.replace("__CSP_NONCE__", nonce)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_path = os.environ.get("QTRONIC_OPTIONS", "/data/options.json")
    try:
        runtime.config = load_config(config_path)
        token = load_or_create_api_token()
        runtime.authenticator = APIAuthenticator(
            token,
            require_auth=runtime.config.security.require_auth,
            allow_ingress=runtime.config.security.allow_ingress,
            rate_limit_requests=runtime.config.security.rate_limit_requests,
            rate_limit_window_s=runtime.config.security.rate_limit_window_s,
        )
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


@app.middleware("http")
async def protect_api(request: Request, call_next):
    """Protect direct API access while allowing verified Supervisor Ingress traffic."""
    path = request.scope.get("path", request.url.path)
    if path.startswith("/api/") or path == "/api":
        authenticator = runtime.authenticator
        if authenticator is None:
            return JSONResponse(
                {
                    "detail": runtime.config_error
                    or "API authentication is not initialized."
                },
                status_code=503,
            )
        client_host = request.client.host if request.client else None
        if not authenticator.authorized(request.headers, client_host):
            return JSONResponse(
                {"detail": "A valid Bearer token is required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Dashboard/integration polling is read-only and must not consume the write quota.
        if request.method not in {"GET", "HEAD", "OPTIONS"} and path != "/api/segments":
            key = client_host or "unknown"
            if not authenticator.allow_request(key):
                return JSONResponse(
                    {"detail": "API write rate limit exceeded."},
                    status_code=429,
                    headers={
                        "Retry-After": str(runtime.config.security.rate_limit_window_s)
                    },
                )
    return await call_next(request)


def _gateway_or_400() -> GatewayService:
    if runtime.gateway is None:
        raise HTTPException(
            status_code=400,
            detail=runtime.config_error or "Gateway is not initialized.",
        )
    return runtime.gateway


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> str:
    nonce = secrets.token_urlsafe(18)
    html = _dashboard_html(nonce)
    _LOGGER.info(
        "Serving ingress dashboard with path=%s root_path=%s",
        request.url.path,
        request.scope.get("root_path", ""),
    )
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Content-Security-Policy": (
                "default-src 'self'; base-uri 'none'; object-src 'none'; "
                f"script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; "
                "connect-src 'self'; img-src 'self' data:; frame-ancestors 'self'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "api_version": API_VERSION,
        "gateway_uuid": runtime.gateway.gateway_uuid if runtime.gateway else None,
        "config_error": runtime.config_error,
        "gateway_available": runtime.gateway.available if runtime.gateway else False,
        "authentication_required": (
            runtime.config.security.require_auth if runtime.config else True
        ),
    }


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    if runtime.gateway is None:
        return {
            "available": False,
            "api_version": API_VERSION,
            "gateway_uuid": None,
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
    try:
        payload = await request.json()
        command = SmsSendRequest.parse(
            payload, default_encoding=gateway.config.sms.default_encoding
        )
        _LOGGER.info("REST send-sms requested")
        recipients = gateway.resolve_recipient_numbers(**command.recipient_kwargs())
        segment_info, message_segments = command.segments(
            unicode_available=gateway.can_send_unicode_sms,
            max_segments=gateway.config.sms.max_segments,
            split_long=gateway.config.sms.split_long,
        )
        accepted = await gateway.async_enqueue_sms_batch(
            message=command.message,
            recipients=recipients,
            encoding=command.encoding,
            idempotency_key=command.idempotency_key,
            message_segments=message_segments,
        )
        accepted["segment_info"] = segment_info.as_dict()
        if not command.wait:
            return JSONResponse(accepted, status_code=202)
        result = await gateway.async_wait_outbox_job(
            accepted["job_id"], timeout_s=command.wait_timeout_s
        )
        result["segment_info"] = segment_info.as_dict()
        return JSONResponse(
            result,
            status_code=202 if result.get("wait_timed_out") else 200,
        )
    except RequestValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=400, detail="Request body must be valid JSON."
        ) from err
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.post("/api/call")
async def api_call(request: Request) -> JSONResponse:
    gateway = _gateway_or_400()
    try:
        payload = await request.json()
        command = CallRequest.parse(
            payload,
            default_ring_time_s=gateway.config.calling.default_ring_time_s,
        )
        _LOGGER.info("REST call requested")
        recipients = gateway.resolve_recipient_numbers(**command.recipient_kwargs())
        result = await gateway.async_call_batch(
            recipients=recipients,
            ring_time_s=command.ring_time_s,
            batch_id=command.idempotency_key,
        )
        return JSONResponse(result)
    except RequestValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=400, detail="Request body must be valid JSON."
        ) from err
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


@app.post("/api/send-ussd")
async def api_send_ussd(request: Request) -> JSONResponse:
    gateway = _gateway_or_400()
    try:
        command = UssdRequest.parse(await request.json())
        return JSONResponse(
            await gateway.async_send_ussd(command.code), status_code=202
        )
    except RequestValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=400, detail="Request body must be valid JSON."
        ) from err
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.post("/api/cancel")
async def api_cancel(request: Request) -> JSONResponse:
    gateway = _gateway_or_400()
    try:
        payload = await request.json()
        command = CancelRequest.parse(payload)
        return JSONResponse(await gateway.async_cancel(command.job_id))
    except RequestValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=400, detail="Request body must be valid JSON."
        ) from err
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.get("/api/outbox")
async def api_outbox(limit: int = 100) -> dict[str, Any]:
    gateway = _gateway_or_400()
    if isinstance(limit, bool) or limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=422, detail="'limit' must be between 1 and 1000."
        )
    return {"jobs": await gateway.store.list_outbox(limit=limit)}


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str) -> dict[str, Any]:
    gateway = _gateway_or_400()
    row = await gateway.store.get_outbox(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Outbox job was not found.")
    return row


@app.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: str) -> JSONResponse:
    gateway = _gateway_or_400()
    try:
        result = await gateway.async_cancel(job_id)
        return JSONResponse(result)
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.post("/api/segments")
async def api_segments(request: Request) -> dict[str, Any]:
    gateway = _gateway_or_400()
    try:
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), str):
            raise RequestValidationError("'message' must be a string.")
        encoding = payload.get("encoding") or gateway.config.sms.default_encoding
        info = sms_segment_info(
            payload["message"],
            str(encoding),
            unicode_available=gateway.can_send_unicode_sms,
        )
        return {
            **info.as_dict(),
            "max_segments": gateway.config.sms.max_segments,
            "accepted": info.segments <= gateway.config.sms.max_segments,
        }
    except RequestValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    except (TypeError, ValueError) as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


def _history_query(request: Request) -> HistoryQuery:
    try:
        return HistoryQuery.parse(dict(request.query_params))
    except RequestValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@app.get("/api/history")
async def api_history(request: Request) -> dict[str, Any]:
    gateway = _gateway_or_400()
    query = _history_query(request)
    return {"history": await gateway.store.query_history(query)}


@app.get("/api/history/export")
async def api_history_export(request: Request) -> Response:
    gateway = _gateway_or_400()
    query_values = dict(request.query_params)
    export_format = str(query_values.pop("format", "json")).lower()
    try:
        query = HistoryQuery.parse(query_values)
        content, media_type = await gateway.store.export_history(query, export_format)
    except (RequestValidationError, ValueError) as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    extension = "csv" if export_format == "csv" else "json"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="qtronic-history.{extension}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/config/export")
async def api_config_export() -> Response:
    if runtime.config is None:
        raise HTTPException(
            status_code=503, detail=runtime.config_error or "Config unavailable."
        )
    return Response(
        content=json.dumps(runtime.config.sanitized(), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="qtronic-config-sanitized.json"',
            "Cache-Control": "no-store",
        },
    )


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8099, log_level="info")


if __name__ == "__main__":
    main()
