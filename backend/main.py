"""AquaTrace backend — FastAPI ingest, time-series, agent loop, alerts, WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue as _queue
import statistics
import threading
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from starlette.background import BackgroundTask

from . import config
from . import awsx
from . import db
from . import forecast
from . import outbox
from . import report
from .agent import DecisionAgent
from .drill import DrillManager
from .models import AckResponse, Telemetry
from .scenario import (SCENARIOS, ScenarioRunner, render_missing, scenes_ready,
                       start_engine, stop_engine_proc)
from .signalwatch import SignalWatch
from .stores import (AlertStore, ObjectStorage, SnapshotStore, now_iso,
                     parse_iso, timeseries)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aquatrace.api")


# --------------------------------------------------------------- ws hub


class Hub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, asyncio.Queue] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    def register(self, ws: WebSocket) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=800)
        self.clients[ws] = q
        return q

    def unregister(self, ws: WebSocket) -> None:
        self.clients.pop(ws, None)

    def _drop_oldest_put(self, q: asyncio.Queue, payload: str) -> None:
        """Runs on the event loop: bounded put with drop-oldest overflow."""
        try:
            q.put_nowait(payload)
        except _queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                pass

    def broadcast_sync(self, event: dict) -> None:
        # Called from worker threads (sync endpoints) and from the event loop
        # (the agent task). Never poke loop-owned queues from a worker thread
        # directly — schedule the put via the captured running loop.
        payload = json.dumps(event, default=str)
        loop = self.loop
        if loop is not None:
            try:
                for q in list(self.clients.values()):
                    loop.call_soon_threadsafe(self._drop_oldest_put, q, payload)
                return
            except RuntimeError:
                pass  # loop closed; fall through and drop the message
        for q in list(self.clients.values()):
            self._drop_oldest_put(q, payload)

    async def broadcast(self, event: dict) -> None:
        self.broadcast_sync(event)


hub = Hub()
storage = ObjectStorage(config.SNAPSHOT_DIR)
snapshots = SnapshotStore(storage)
alerts = AlertStore()


def _critical_aws_work(alert: dict[str, Any]) -> None:
    """S3 evidence bundle + SNS delivery for a critical alert.

    Runs on a daemon thread: boto3 calls block, and the agent loop must never
    wait on the network. Everything here is best-effort — awsx never raises.
    """
    cid = alert.get("camera_id", "?")
    aid = alert.get("alert_id", "unknown")
    aws_info: dict[str, Any] = {}
    try:
        frame_url = alert.get("evidence_snapshot_url") or ""
        key = frame_url.removeprefix("/snapshots/")
        body = storage.get(key) if key else None
        if body:
            url = awsx.s3_put(f"evidence/{aid}/frame.jpg", body, "image/jpeg")
            if url:
                aws_info["s3_frame_url"] = url
        bundle = json.dumps(alert, default=str).encode("utf-8")
        url = awsx.s3_put(f"evidence/{aid}/alert.json", bundle, "application/json")
        if url:
            aws_info["s3_alert_url"] = url
        cam = config.camera(cid)
        subject, message = awsx.format_critical_message(
            alert, cam["name"], aws_info.get("s3_frame_url"))
        mid = outbox.publish_sns(subject, message)
        if mid:
            aws_info["sns_message_id"] = mid
            # Persist the delivery receipt so the alert record carries it.
            try:
                db.save_alert(alert)
            except Exception:
                log.exception("alert persistence failed after SNS publish")
    except Exception:
        log.exception("critical AWS work failed for %s", cid)
    if aws_info:
        alert["aws"] = aws_info
        alert["updated_at"] = now_iso()
        hub.broadcast_sync({"type": "alert", "at": now_iso(),
                            "action": "updated", "alert": alert})
        log.info("alert %s backed to AWS: %s", aid, sorted(aws_info))


def _on_critical(alert: dict[str, Any]) -> None:
    # Hand the blocking AWS work to a daemon thread; return immediately.
    threading.Thread(target=_critical_aws_work, args=(alert,),
                     daemon=True, name=f"aws-critical-{alert.get('alert_id')}").start()


# --------------------------------------------------------------- agent wiring

agent = DecisionAgent(emit=hub.broadcast_sync, snapshots=snapshots, alerts=alerts,
                      on_critical=_on_critical)
runner = ScenarioRunner(emit=hub.broadcast_sync, agent=agent, alerts=alerts, snapshots=snapshots)
signalwatch = SignalWatch(alerts=alerts, emit=hub.broadcast_sync)
drill = DrillManager(emit=hub.broadcast_sync, agent=agent, signalwatch=signalwatch)
# Live-mode engine processes started from the camera registry (separate from
# the demo scenario runner's processes).
live_procs: dict[str, Any] = {}


# --------------------------------------------------------------- app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init SQLite, restore persisted alerts, seed baselines,
    capture the running loop for cross-thread broadcasts, warm scenes, and
    start the agent + autostart + signal-watch + outbox tasks.
    Shutdown: stop everything."""
    db_ok = db.init_db()
    if db_ok:
        restored = db.load_recent_alerts(200)
        if restored:
            alerts.restore(restored)
            log.info("restored %d alerts from SQLite", len(restored))
    for cid in config.CAMERA_IDS:
        timeseries().reset_and_seed(cid)
    hub.loop = asyncio.get_running_loop()
    runner.render_async()
    hub.loop.create_task(agent.run(), name="aquatrace-agent")
    hub.loop.create_task(_autostart(), name="aquatrace-autostart")
    signalwatch.start()
    outbox.start_retry_thread()
    forecast.start()
    log.info("API up — mode=%s, seeded baseline for %d cameras",
             config.MODE, len(config.CAMERA_IDS))
    yield
    forecast.stop()
    signalwatch.stop()
    outbox.stop_retry_thread()
    try:
        drill.stop(emit_event=False)
    except Exception:
        log.exception("drill stop failed on shutdown")
    for cid, proc in list(live_procs.items()):
        try:
            stop_engine_proc(proc, cid)
        except Exception:
            log.exception("live engine stop failed for %s", cid)
    live_procs.clear()
    agent.stop()
    runner.stop()


app = FastAPI(title="AquaTrace API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    body = exc.body if isinstance(exc.body, dict) else {}
    log.warning("422 contract violation on %s %s: %s",
                request.method, request.url.path, json.dumps(exc.errors(), default=str)[:600])
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "contract": "contracts/telemetry.schema.json",
            "received": {k: str(v)[:120] for k, v in (body or {}).items()},
        },
    )


async def _autostart() -> None:
    """Bring the vision engines up.

    Live mode: start one engine per enabled registry camera on its configured
    source (real RTSP or sim://). No reseeding, no demo scenario.
    Demo mode: the calm scenario, as before, so a cold boot is live.
    """
    if config.IS_LIVE:
        for cam in config.CAMERAS:
            if not cam.get("enabled", True) or not cam.get("source"):
                continue
            cid = cam["id"]
            try:
                live_procs[cid] = start_engine(cid, cam["source"], loop=True)
            except Exception:
                log.exception("live autostart failed for %s", cid)
        log.info("autostart: live mode, %d registry engines", len(live_procs))
        return
    for _ in range(60):
        if runner.current:
            return
        if scenes_ready():
            try:
                await runner.start("normal")
                log.info("autostart: normal scenario live")
            except Exception:
                log.exception("autostart failed")
            return
        await asyncio.sleep(1.0)


# --------------------------------------------------------------- ingest


def _flow_std_30s(camera_id: str) -> float:
    """Std-dev of the vision engine's flow_std field over the trailing 30 s
    of valid rows (0.0 when there is no history yet)."""
    rows = timeseries().query(camera_id, 30.0, valid_only=True)
    if not rows:
        return 0.0
    vals = [r.get("flow_std", 0.0) or 0.0 for r in rows]
    return statistics.pstdev(vals) if len(vals) > 1 else 0.0


def _waterline_delta_10s(camera_id: str) -> float:
    """Latest waterline_y minus waterline_y ~10 s ago (0.0 if insufficient
    history)."""
    rows = timeseries().query(camera_id, 11.0, valid_only=True)
    if len(rows) < 2:
        return 0.0
    latest = rows[-1]
    ref_epoch = latest["_epoch"] - 10.0
    ref = rows[0]
    for r in rows:
        if r["_epoch"] <= ref_epoch:
            ref = r
        else:
            break
    # In image coordinates y=0 is top. When water rises, waterline_y decreases.
    # Therefore (ref - latest) is positive when water is rising.
    return (ref.get("waterline_y", 0.0) or 0.0) - (latest.get("waterline_y", 0.0) or 0.0)


@app.post("/ingest/telemetry")
async def ingest_telemetry(t: Telemetry) -> dict[str, Any]:
    try:
        epoch = parse_iso(t.ts)
    except Exception:
        log.warning("422 unparsable ts=%s from %s", t.ts, t.camera_id)
        raise HTTPException(status_code=422, detail="ts must be ISO-8601 UTC with milliseconds")
    if t.camera_id not in config.CAMERA_IDS:
        log.warning("422 unknown camera_id=%s", t.camera_id)
        raise HTTPException(status_code=422, detail=f"unknown camera_id {t.camera_id}")

    row = {
        "camera_id": t.camera_id,
        "ts": t.ts,
        "_epoch": epoch,
        "frame_id": t.frame_id,
        "water_bbox": t.water_bbox,
        "water_area_px": t.water_area_px,
        "water_area_ratio": t.water_area_ratio,
        "flow_mean": t.flow_mean,
        "flow_std": t.flow_std,
        "waterline_y": t.waterline_y,
        "waterline_delta": t.waterline_delta,
        "confidence": t.confidence,
        "detection_valid": t.detection_valid,
        "flow_dir_deg": t.flow_dir_deg,
        # Stream health (newer engines only; absent on older ones).
        "frame_hash": t.frame_hash,
        "captured_at": t.captured_at,
        "stream_status": t.stream_status,
    }
    timeseries().write(row)
    signalwatch.note_telemetry(t.camera_id, t.stream_status, epoch)

    if t.annotated_jpeg_b64:
        # base64 decode + JPEG disk write is CPU/IO bound — keep it off the
        # event loop (~1 Hz per camera).
        try:
            await asyncio.to_thread(snapshots.put_jpeg_b64, t.camera_id, t.ts, t.annotated_jpeg_b64)
        except Exception:
            log.exception("snapshot store failed for %s", t.camera_id)

    feat = agent.camera_state(t.camera_id)
    flow_std = _flow_std_30s(t.camera_id)
    wl_delta_10s = _waterline_delta_10s(t.camera_id)
    # CloudWatch telemetry (throttled inside awsx; no-op when AWS is down).
    # boto3 blocks for the full CloudWatch round-trip (~0.5-2.5 s): run it off
    # the event loop or a stalled loop times out the engines' 4 s telemetry
    # POSTs and trips signal_lost mid-escalation.
    await asyncio.to_thread(awsx.put_metrics, t.camera_id, {
        "WaterAreaRatio": t.water_area_ratio,
        "Anomaly": feat.get("anomaly", 0.0),
        "Confidence": t.confidence,
        "FlowMean": t.flow_mean or 0.0,
    })
    hub.broadcast_sync({
        "type": "telemetry_tick",
        "at": t.ts,
        "camera_id": t.camera_id,
        "water_area_ratio": t.water_area_ratio,
        "flow_mean": t.flow_mean,
        "flow_std": round(flow_std, 3),
        "waterline_y": t.waterline_y,
        "waterline_delta": t.waterline_delta,
        "wl_delta_10s": round(wl_delta_10s, 2),
        "confidence": t.confidence,
        "expansion_rate": feat.get("expansion_rate", 0.0),
        "baseline_rate": feat.get("baseline_rate", 0.0),
        "anomaly": feat.get("anomaly", 0.0),
        "state": feat.get("state", "normal"),
        "signal": feat.get("signal", "live"),
    })
    return {"ok": True, "received_at": now_iso()}


# --------------------------------------------------------------- cameras


@app.get("/cameras")
def list_cameras() -> dict[str, Any]:
    out = []
    for c in config.CAMERAS:
        cid = c["id"]
        latest = timeseries().latest(cid)
        st = agent.camera_state(cid)
        out.append({
            **c,
            "status": st.get("signal", "live"),
            "state": st.get("state", "normal"),
            "substate": st.get("substate"),
            "since": st.get("since"),
            "latest": {
                "ts": latest["ts"] if latest else None,
                "water_area_ratio": latest["water_area_ratio"] if latest else None,
                "flow_mean": latest.get("flow_mean") if latest else None,
                "waterline_y": latest.get("waterline_y") if latest else None,
                "confidence": latest.get("confidence") if latest else None,
            } if latest else None,
            "thumbnail_url": snapshots.latest_url(cid),
            "features": st,
        })
    return {
        "cameras": out,
        "site": {"name": config.SITE_NAME, "center": config.SITE_CENTER},
        "worst_state": agent.worst_state(),
        "scenario": runner.status(),
        "scenes_ready": scenes_ready(),
    }


@app.get("/cameras/{camera_id}/frame.jpg")
def camera_frame(camera_id: str) -> FileResponse:
    """Latest annotated frame for a camera (dashboard thumbnails + evidence)."""
    if camera_id not in config.CAMERA_IDS:
        raise HTTPException(status_code=404, detail="unknown camera")
    url = snapshots.latest_url(camera_id)
    if not url:
        raise HTTPException(status_code=404, detail="no frame yet")
    path = url.removeprefix("/snapshots/")
    file = os.path.join(config.SNAPSHOT_DIR, path)
    if not os.path.isfile(file):
        raise HTTPException(status_code=404, detail="frame expired")
    return FileResponse(file, media_type="image/jpeg", headers={"cache-control": "no-store"})


@app.get("/cameras/{camera_id}/control")
def camera_control(camera_id: str) -> dict[str, Any]:
    if camera_id not in config.CAMERA_IDS:
        raise HTTPException(status_code=404, detail="unknown camera")
    return agent.control(camera_id)


# --------------------------------------------------------------- telemetry


@app.get("/telemetry/{camera_id}")
def telemetry(camera_id: str, window: str = Query(default="5m")) -> dict[str, Any]:
    if camera_id not in config.CAMERA_IDS:
        raise HTTPException(status_code=404, detail="unknown camera")
    secs = config.parse_window(window)
    rows = timeseries().query(camera_id, secs)
    step = max(1, len(rows) // 900)      # keep payloads chart-friendly
    points = [
        {
            "ts": r["ts"],
            "epoch": r["_epoch"],
            "water_area_ratio": r["water_area_ratio"],
            "flow_mean": r.get("flow_mean", 0),
            "waterline_y": r.get("waterline_y", 0),
            "confidence": r.get("confidence", 0),
            "detection_valid": r.get("detection_valid", True),
        }
        for r in rows[::step]
    ]
    return {
        "camera_id": camera_id,
        "window": window,
        "window_s": secs,
        "count": len(points),
        "points": points,
        "features": agent.camera_state(camera_id),
    }


# --------------------------------------------------------------- alerts


@app.get("/alerts")
def list_alerts(limit: int = 60) -> dict[str, Any]:
    return {"alerts": alerts.all(limit=limit)}


@app.get("/alerts/{alert_id}")
def get_alert(alert_id: str) -> dict[str, Any]:
    a = alerts.get(alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="alert not found")
    return a


@app.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: str) -> AckResponse:
    a = alerts.get(alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="alert not found")
    at = now_iso()
    a["status"] = "acknowledged"
    a["acknowledged_at"] = at
    a["ack_by"] = "operator"  # no auth yet; attribution placeholder
    a["updated_at"] = at
    db.save_alert(a)
    db.save_action(at, "operator", "acknowledge", alert_id, "")
    hub.broadcast_sync({"type": "alert", "at": at, "action": "acknowledged", "alert": a})
    log.info("alert %s acknowledged", alert_id)
    return AckResponse(alert_id=alert_id, status="acknowledged", acknowledged_at=at)


@app.post("/alerts/{alert_id}/false-positive")
def false_positive(alert_id: str) -> dict[str, Any]:
    a = alerts.get(alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="alert not found")
    at = now_iso()
    a["status"] = "false_positive"
    a["false_positive"] = True
    a["updated_at"] = at
    a["closed_at"] = at
    a["reasoning"] = (a.get("reasoning") or "") + " [operator: marked false positive]"
    feedback = agent.apply_false_positive(a["camera_id"])
    db.save_alert(a)
    db.save_action(at, "operator", "false_positive", alert_id,
                   f"camera={a['camera_id']}")
    hub.broadcast_sync({"type": "alert", "at": at, "action": "false_positive", "alert": a})
    hub.broadcast_sync({"type": "agent_note", "at": at, "camera_id": a["camera_id"], **feedback})
    log.info("alert %s marked false positive; baseline feedback applied", alert_id)
    return {"alert": a, "feedback": feedback}


@app.post("/alerts/{alert_id}/export")
def export_report(alert_id: str) -> dict[str, Any]:
    """Printable incident report payload (rendered by the dashboard)."""
    a = alerts.get(alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="alert not found")
    cam = config.camera(a["camera_id"])
    report = {
        "title": f"AquaTrace Incident Report — {alert_id}",
        "generated_at": now_iso(),
        "site": config.SITE_NAME,
        "camera": cam,
        "alert": a,
        "summary": f"{cam['name']} ({cam['zone']}) escalated to {a['severity'].upper()} "
                   f"and {a['status']}.",
    }
    # A copy of every exported report lands in the S3 evidence vault.
    s3_url = awsx.s3_put(f"evidence/{alert_id}/incident-report.json",
                         json.dumps(report, default=str).encode("utf-8"),
                         "application/json")
    if s3_url:
        report["s3_url"] = s3_url
    return report


@app.get("/api/alerts/{alert_id}/report")
def incident_report_pdf(alert_id: str) -> Response:
    """Downloadable incident report PDF (alert + timeline + evidence + decisions)."""
    a = alerts.get(alert_id)
    if not a:
        raise HTTPException(status_code=404, detail="alert not found")
    try:
        cam = config.camera(a["camera_id"])
    except KeyError:
        # Camera may no longer be registered (e.g. a drill camera after a
        # restart) — the alert record itself is still reportable.
        cid = a["camera_id"]
        cam = {"id": cid, "name": cid, "zone": "", "location": ""}
    decisions = [d for d in reversed(agent.decisions)
                 if d.get("camera_id") == a["camera_id"]][:10]
    try:
        pdf_bytes = report.build_report(a, cam, decisions, now_iso())
    except Exception:
        log.exception("incident report build failed for %s", alert_id)
        raise HTTPException(status_code=500, detail="report generation failed")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="incident-{alert_id}.pdf"'},
    )


# --------------------------------------------------------------- aws


@app.get("/aws/status")
def aws_status() -> dict[str, Any]:
    """Live integration health for the dashboard (S3 / SNS / CloudWatch +
    a compact precipitation-forecast block, so the TopBar can show both pills
    from one status payload)."""
    out = dict(awsx.status())
    out["forecast"] = (forecast.status() if config.FORECAST_DISPLAY
                       else {"enabled": False, "status": "disabled"})
    return out


@app.get("/api/forecast")
def api_forecast() -> dict[str, Any]:
    """Per-zone precipitation forecast (Open-Meteo, keyless).

    Serves from cache; a stale cache schedules a background refresh instead of
    blocking. Zones that have never fetched report "unavailable" — never a
    synthetic value."""
    if not config.FORECAST_DISPLAY:
        return {"enabled": False, "status": "disabled", "zones": [],
                "source": forecast.settings.source, "at": 0}
    return forecast.payload(schedule=True)


@app.get("/api/aws/outbox")
def aws_outbox() -> dict[str, Any]:
    """Queued / dead AWS deliveries (SNS retry outbox)."""
    return {"pending": outbox.pending()}


# --------------------------------------------------------------- mode


@app.get("/api/layers/flood-history")
def get_flood_history() -> dict[str, Any]:
    """Return GEE DEM contours, HydroSHEDS rivers, and flood inundation extents."""
    export_path = os.path.join(config.DATA_DIR, "flood_history_export.json")
    if os.path.isfile(export_path):
        try:
            with open(export_path) as f:
                data = json.load(f)
            return {"ready": True, "layer": data}
        except Exception as exc:
            log.warning("could not read flood_history_export.json: %s", exc)
    return {"ready": False}


@app.get("/api/mode")
def get_mode() -> dict[str, Any]:
    """Runtime mode: demo (default) or live (AQUATRACE_MODE=live)."""
    return {"mode": config.MODE, "is_live": config.IS_LIVE}


@app.post("/api/mode")
async def set_mode(req: Request) -> dict[str, Any]:
    """Toggle runtime mode between live and demo."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    target = str(body.get("mode") or "live").strip().lower()
    if target not in ("live", "demo"):
        raise HTTPException(status_code=400, detail="mode must be 'live' or 'demo'")

    if target == "live":
        config.MODE = "live"
        config.IS_LIVE = True
        runner.stop()
        # Bring up live engines for all cameras with loop=True
        for cam in config.CAMERAS:
            if not cam.get("enabled", True) or not cam.get("source"):
                continue
            cid = cam["id"]
            if cid in live_procs and live_procs[cid].poll() is None:
                continue
            try:
                live_procs[cid] = start_engine(cid, cam["source"], loop=True)
            except Exception:
                log.exception("failed starting live engine for %s", cid)
        log.info("switched to live mode — %d engines active", len(live_procs))
    else:
        config.MODE = "demo"
        config.IS_LIVE = False
        for cid, proc in list(live_procs.items()):
            try:
                stop_engine_proc(proc, cid)
            except Exception:
                pass
        live_procs.clear()
        try:
            await runner.start("normal")
        except Exception:
            pass
        log.info("switched to demo mode — normal scenario active")

    hub.broadcast_sync({"type": "mode_change", "mode": config.MODE, "is_live": config.IS_LIVE})
    return {"mode": config.MODE, "is_live": config.IS_LIVE}


def _require_demo() -> None:
    if config.IS_LIVE:
        raise HTTPException(status_code=403, detail="disabled in live mode")


# --------------------------------------------------------------- simulation


@app.post("/simulate/stop")
def simulate_stop() -> dict[str, Any]:
    _require_demo()
    runner.stop()
    return runner.status()


@app.post("/simulate/{scenario}")
async def simulate(scenario: str) -> JSONResponse:
    _require_demo()
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown scenario; one of {SCENARIOS}")
    try:
        status = await runner.start(scenario)
    except RuntimeError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    return JSONResponse(status_code=202, content={"started": scenario, **status})


@app.get("/simulate")
def simulate_status() -> dict[str, Any]:
    return {"scenarios": SCENARIOS, **runner.status()}


# --------------------------------------------------------------- drill
# Judge-friendly harness: one extra synthetic engine on the live pipeline.
# Available in BOTH demo and live modes; never touches existing engines.


@app.post("/api/drill/start/{scenario}")
def drill_start(scenario: str) -> JSONResponse:
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail=f"unknown scenario; one of {SCENARIOS}")
    try:
        status = drill.start(scenario)
    except RuntimeError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    return JSONResponse(status_code=202, content={"started": scenario, **status})


@app.post("/api/drill/stop")
def drill_stop() -> dict[str, Any]:
    return drill.stop()


@app.get("/api/drill/status")
def drill_status() -> dict[str, Any]:
    s = drill.status()
    return {"active": s["active"], "scenario": s["scenario"]}


# --------------------------------------------------------------- agent audit


@app.get("/agent/log")
def agent_log(limit: int = 100) -> dict[str, Any]:
    return {"decisions": list(reversed(agent.decisions[-limit:]))}


@app.get("/agent/state")
def agent_state() -> dict[str, Any]:
    return {
        "worst_state": agent.worst_state(),
        "cameras": {cid: agent.camera_state(cid) for cid in config.CAMERA_IDS},
        "scenario": runner.status(),
        "site": {"name": config.SITE_NAME, "center": config.SITE_CENTER},
    }


# --------------------------------------------------------------- misc


@app.get("/snapshots/{path:path}")
def snapshot(path: str) -> FileResponse:
    # Traversal guard: normalize the key and verify the resolved path stays
    # under the snapshot root.
    root = os.path.realpath(config.SNAPSHOT_DIR)
    key = os.path.normpath(path)
    full = os.path.realpath(os.path.join(root, key))
    if not full.startswith(root + os.sep) or full == root:
        raise HTTPException(status_code=400, detail="invalid snapshot path")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="snapshot not found")
    return FileResponse(full, media_type="image/jpeg")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "at": now_iso(),
        "cameras": len(config.CAMERA_IDS),
        "scenario": runner.current,
        "worst_state": agent.worst_state(),
    }


@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    q = hub.register(ws)
    hello = {
        "type": "hello",
        "at": now_iso(),
        "worst_state": agent.worst_state(),
        "scenario": runner.status(),
        "cameras": [
            {
                "camera_id": cid,
                **agent.camera_state(cid),
                "thumbnail_url": snapshots.latest_url(cid),
            }
            for cid in config.CAMERA_IDS
        ],
        "alerts": [a["alert_id"] for a in alerts.all(limit=20)],
    }
    try:
        await ws.send_text(json.dumps(hello, default=str))
        idle = 0.0
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=20.0)
                await ws.send_text(item)
                idle = 0.0
            except asyncio.TimeoutError:
                idle += 20.0
                await ws.send_text(json.dumps({"type": "ping", "at": now_iso(), "idle": idle}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unregister(ws)
        log.debug("ws client disconnected (%d remain)", len(hub.clients))
