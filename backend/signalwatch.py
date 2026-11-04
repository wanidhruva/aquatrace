"""Operational signal-loss alerting.

A camera that stops sending telemetry (dead stream, frozen frame, crashed
engine) must raise a real, durable alert — not just a grey tile in the UI.

    watch.note_telemetry(camera_id, stream_status, epoch)   # from /ingest
    watch.start()  /  watch.stop()                          # background check

Every CHECK_INTERVAL_S the watch compares now against each camera's
last_fresh_at. When stream_status == "down" or the camera is silent for more
than STALE_AFTER_S, it creates (or refreshes) one signal_lost alert per
camera: {"type": "signal_lost", "severity": "high", "drill": False}.
When the stream recovers the alert auto-resolves.

No spam: at most one active signal_lost alert per camera; an existing one
gets its timestamp refreshed (at most once a minute) instead of a new alert.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from . import config, db
from .stores import now_iso

log = logging.getLogger("aquatrace.signalwatch")

STALE_AFTER_S = 60.0
CHECK_INTERVAL_S = 5.0
REFRESH_MIN_S = 60.0


class SignalWatch:
    def __init__(self, alerts: Any, emit: Callable[[dict], Any]) -> None:
        self.alerts = alerts
        self.emit = emit
        self.last_fresh: dict[str, float] = {}
        self.stream_down: dict[str, bool] = {}
        self._refreshed_at: dict[str, float] = {}
        self._started_at = time.time()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ input

    def note_telemetry(self, camera_id: str,
                       stream_status: Optional[str], epoch: float) -> None:
        """Called on every accepted ingest row. stream_status is one of
        "ok" | "down" | "reconnecting" (None when the engine is too old to
        send it — treated as ok)."""
        self.last_fresh[camera_id] = epoch
        self.stream_down[camera_id] = (stream_status == "down")

    def forget(self, camera_id: str) -> None:
        """Drop tracking for a camera (e.g. a drill engine that stopped);
        auto-resolve any active signal_lost alert for it."""
        self.last_fresh.pop(camera_id, None)
        self.stream_down.pop(camera_id, None)
        self._refreshed_at.pop(camera_id, None)
        existing = self._active_signal_alert(camera_id)
        if existing is not None:
            self._resolve(existing, "camera removed")

    # ------------------------------------------------------------------ loop

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="aquatrace-signalwatch")
        self._thread.start()
        log.info("signal watch started (stale after %.0f s)", STALE_AFTER_S)

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.check(time.time())
            except Exception:
                log.exception("signal watch check failed")
            time.sleep(CHECK_INTERVAL_S)

    # ----------------------------------------------------------------- check

    def _active_signal_alert(self, camera_id: str) -> Optional[dict[str, Any]]:
        for a in self.alerts.all(limit=300):
            if (a.get("camera_id") == camera_id
                    and a.get("type") == "signal_lost"
                    and a.get("status") in ("active", "acknowledged")):
                return a
        return None

    def check(self, now: float) -> None:
        warmup = (now - self._started_at) < STALE_AFTER_S
        for cid in list(config.CAMERA_IDS):
            down = self.stream_down.get(cid, False)
            last = self.last_fresh.get(cid)
            if last is None:
                stale = not warmup  # grace period on a fresh boot
                age = 0.0
            else:
                age = now - last
                stale = age > STALE_AFTER_S
            existing = self._active_signal_alert(cid)
            if down or stale:
                if existing is None:
                    self._raise(cid, down, age, now)
                elif now - self._refreshed_at.get(cid, 0.0) >= REFRESH_MIN_S:
                    self._refresh(existing, down, age, now)
            elif existing is not None:
                self._resolve(existing, "stream recovered")

    # ------------------------------------------------------------ transitions

    def _raise(self, camera_id: str, down: bool, age: float, now: float) -> None:
        at = now_iso(now)
        reason = ("stream reported down by vision engine"
                  if down else
                  f"no telemetry for {age:.0f}s (stale threshold {STALE_AFTER_S:.0f}s)")
        alert = {
            "alert_id": self.alerts.new_id(camera_id),
            "camera_id": camera_id,
            "type": "signal_lost",
            "severity": "high",
            "state": "signal_lost",
            "started_at": at,
            "updated_at": at,
            "closed_at": None,
            "status": "active",
            "transitions": [{"from": "live", "to": "signal_lost",
                             "at": at, "reason": reason}],
            "evidence_snapshot_url": None,
            "telemetry_window": [],
            "reasoning": f"Signal lost on {camera_id}: {reason}.",
            "corroboration": None,
            "agent_confidence": 1.0,
            "acknowledged_at": None,
            "ack_by": None,
            "false_positive": False,
            "drill": camera_id.startswith("drill-"),
        }
        self.alerts.add(alert)
        db.save_alert(alert)
        db.save_action(at, "system", "signal_lost_raised", camera_id, reason)
        self._refreshed_at[camera_id] = now
        self._broadcast({"type": "alert", "at": at, "action": "created", "alert": alert})
        log.warning("[%s] signal_lost raised: %s", camera_id, reason)

    def _refresh(self, alert: dict[str, Any], down: bool, age: float, now: float) -> None:
        at = now_iso(now)
        reason = ("stream still down"
                  if down else
                  f"still no telemetry ({age:.0f}s silent)")
        alert["updated_at"] = at
        alert["reasoning"] = f"Signal lost on {alert['camera_id']}: {reason}."
        alert["transitions"].append({"from": "signal_lost", "to": "signal_lost",
                                    "at": at, "reason": reason})
        db.save_alert(alert)
        self._refreshed_at[alert["camera_id"]] = now
        self._broadcast({"type": "alert", "at": at, "action": "updated", "alert": alert})

    def _resolve(self, alert: dict[str, Any], note: str) -> None:
        at = now_iso()
        alert["status"] = "resolved"
        alert["closed_at"] = at
        alert["updated_at"] = at
        alert["transitions"].append({"from": "signal_lost", "to": "live",
                                    "at": at, "reason": note})
        db.save_alert(alert)
        db.save_action(at, "system", "signal_lost_resolved",
                       alert["camera_id"], note)
        self._refreshed_at.pop(alert["camera_id"], None)
        self._broadcast({"type": "alert", "at": at, "action": "updated", "alert": alert})
        log.info("[%s] signal_lost resolved: %s", alert["camera_id"], note)

    def _broadcast(self, event: dict) -> None:
        try:
            self.emit(event)
        except Exception:
            log.exception("signal watch broadcast failed")
