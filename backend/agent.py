"""Agentic decision loop.

Every second, for every camera:

    feature extraction   -> expansion rate (30 s slope), acceleration,
                            learned baseline (median |Δratio| over trailing 5 min),
                            anomaly = rate / baseline
    state machine        -> Normal -> Watch -> Escalating -> Critical with
                            mandatory hysteresis (sustain + 60 s cool-down)
    confidence gate      -> confidence >= 0.80 escalates immediately,
                            otherwise a verifying sub-state cross-checks
                            adjacent cameras / requests high-rate sampling /
                            escalates on timeout-with-persistence
    audit                -> every decision writes a human-readable reasoning
                            string, logged and attached to the alert
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import config
from . import db
from . import forecast
from .models import Alert
from .stores import now_iso, parse_iso, timeseries

log = logging.getLogger("aquatrace.agent")

ORDER = {"normal": 0, "watch": 1, "escalating": 2, "verifying": 2, "critical": 3}
STEP_DOWN = {"critical": "escalating", "verifying": "escalating", "escalating": "watch", "watch": "normal"}
SEVERITY = {"watch": "watch", "escalating": "escalating", "verifying": "escalating", "critical": "critical"}


def slope(rows: list[dict[str, Any]]) -> float:
    """Ordinary-least-squares slope of water_area_ratio over time (ratio/s)."""
    n = len(rows)
    if n < 3:
        return 0.0
    ts = [r["_epoch"] for r in rows]
    ys = [r["water_area_ratio"] for r in rows]
    t0 = ts[0]
    xs = [t - t0 for t in ts]
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-9:
        return 0.0
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den


def _window(rows: list[dict[str, Any]], seconds: float, now: float) -> list[dict[str, Any]]:
    cut = now - seconds
    return [r for r in rows if r["_epoch"] >= cut]


@dataclass
class CameraAgent:
    camera_id: str
    state: str = "normal"
    since: float = field(default_factory=time.time)
    cond_watch: Optional[float] = None
    cond_esc: Optional[float] = None
    cond_crit: Optional[float] = None
    cond_normal: Optional[float] = None
    verify_since: Optional[float] = None
    verification: Optional[dict] = None
    boost_until: float = 0.0
    signal: str = "live"
    fp_mult: float = 1.0
    fp_until: float = 0.0
    features: dict = field(default_factory=dict)
    baseline_hist: list = field(default_factory=list)

    def reset_conditions(self) -> None:
        self.cond_watch = None
        self.cond_esc = None
        self.cond_crit = None
        self.cond_normal = None


class DecisionAgent:
    def __init__(
        self,
        emit: Callable[[dict], Any],
        snapshots: Any,
        alerts: Any,
        on_critical: Optional[Callable[[dict], Any]] = None,
    ) -> None:
        self.emit = emit
        self.snapshots = snapshots
        self.alerts = alerts
        # Hook fired once per escalation INTO critical (AWS SNS/S3 live here).
        # Must never raise and must never block the agent loop.
        self.on_critical = on_critical
        self.cameras: dict[str, CameraAgent] = {
            cid: CameraAgent(cid) for cid in config.CAMERA_IDS
        }
        self.decisions: list[dict] = []
        self._running = True

    # ---------------------------------------------------------------- control

    def ensure_camera(self, camera_id: str) -> None:
        """Register a runtime camera (drill engines) with the agent loop."""
        if camera_id not in self.cameras:
            self.cameras[camera_id] = CameraAgent(camera_id)

    def forget_camera(self, camera_id: str) -> None:
        """Drop a runtime camera (drill stop)."""
        self.cameras.pop(camera_id, None)

    def camera_state(self, camera_id: str) -> dict:
        cam = self.cameras.get(camera_id)
        if cam is None:
            return {"state": "normal", "signal": "offline", "substate": None}
        return {
            "state": cam.state,
            "substate": "verifying" if cam.state == "verifying" else None,
            "signal": cam.signal,
            "since": now_iso(cam.since),
            **cam.features,
        }

    def control(self, camera_id: str) -> dict:
        cam = self.cameras.get(camera_id)
        if cam is None:
            return {"state": "normal", "sample_hz": config.DEFAULT_SAMPLE_HZ}
        hz = config.BOOST_SAMPLE_HZ if cam.boost_until > time.time() else config.DEFAULT_SAMPLE_HZ
        return {"state": cam.state, "sample_hz": hz}

    def worst_state(self) -> str:
        worst = "normal"
        for cam in self.cameras.values():
            if cam.signal != "live":
                continue
            if ORDER.get(cam.state, 0) > ORDER.get(worst, 0):
                worst = cam.state
        return worst

    def apply_false_positive(self, camera_id: str) -> dict:
        """Operator feedback -> the learned baseline for this camera rises."""
        cam = self.cameras.get(camera_id)
        if cam is None:
            return {}
        cam.fp_mult = config.FP_BOOST
        cam.fp_until = time.time() + config.FP_BOOST_TTL_S
        note = (
            f"Operator marked the last event a false positive; baseline for "
            f"{camera_id} raised {config.FP_BOOST:.1f}x for "
            f"{int(config.FP_BOOST_TTL_S / 60)} min so equivalent readings stay Normal."
        )
        self._log(camera_id, cam.state, cam.state, cam.features.get("confidence", 0), note)
        log.info("[%s] %s", camera_id, note)
        return {"camera_id": camera_id, "baseline_multiplier": cam.fp_mult, "note": note}

    # ------------------------------------------------------------------ loop

    async def run(self) -> None:
        log.info("agent loop started at %.1f Hz", config.TICK_HZ)
        while self._running:
            started = time.time()
            for cid in config.CAMERA_IDS:
                try:
                    self.tick(cid, started)
                except Exception:
                    log.exception("[%s] agent tick failed", cid)
            elapsed = time.time() - started
            await asyncio.sleep(max(0.05, 1.0 / config.TICK_HZ - elapsed))

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ tick

    def tick(self, camera_id: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        cam = self.cameras.get(camera_id)
        if cam is None:
            log.warning("[%s] tick on unknown camera_id — ignoring", camera_id)
            return
        store = timeseries()
        latest = store.latest(camera_id)

        # ---- signal health: never crash on missing telemetry -------------
        stale = latest is None or (now - latest["_epoch"]) > config.TELEMETRY_STALE_S
        if stale:
            if cam.signal != "signal_lost":
                cam.signal = "signal_lost"
                self._log(camera_id, cam.state, cam.state, cam.features.get("confidence", 0),
                          f"No telemetry for >{config.TELEMETRY_STALE_S:.0f}s — marking signal_lost; "
                          f"holding last known state {cam.state.upper()} and pausing escalation.")
                self._broadcast({
                    "type": "camera_status", "camera_id": camera_id,
                    "status": "signal_lost", "at": now_iso(now),
                })
            return
        if cam.signal != "live":
            cam.signal = "live"
            self._broadcast({
                "type": "camera_status", "camera_id": camera_id,
                "status": "live", "at": now_iso(now),
            })

        rows = store.query(camera_id, config.BASELINE_WINDOW_S, valid_only=True)
        recent = _window(rows, config.RATE_WINDOW_S, now)
        if len(recent) < config.RATE_MIN_POINTS:
            cam.features = {"signal": "live", "insufficient_data": True}
            return

        # ---- feature extraction ------------------------------------------
        rate = slope(recent)
        prev_rows = _window(rows, config.RATE_WINDOW_S * 2, now)[:len(recent)]
        rate_prev = slope(prev_rows) if len(prev_rows) >= config.RATE_MIN_POINTS else rate
        accel = (rate - rate_prev) / config.RATE_WINDOW_S
        baseline = self._baseline(camera_id, rows, now)
        anomaly = (rate / baseline) if rate > 0 else 0.0

        ten = _window(rows, 10.0, now)
        conf_samples = [r.get("confidence", 0.0) for r in ten] or [latest.get("confidence", 0)]
        valid_frac = sum(1 for r in ten if r.get("detection_valid", True)) / max(1, len(ten))
        confidence = 0.85 * statistics.fmean(conf_samples) + 0.15 * valid_frac

        line_rows = _window(rows, 10.0, now)
        waterline_disp = (line_rows[-1]["waterline_y"] - line_rows[0]["waterline_y"]) if len(line_rows) > 1 else 0.0
        flow = statistics.fmean([r.get("flow_mean", 0.0) for r in recent])
        flow_base = statistics.fmean([r.get("flow_mean", 0.0) for r in rows]) or 0.0
        latest_ratio = latest["water_area_ratio"]

        cam.features = {
            "signal": "live",
            "expansion_rate": round(rate, 6),
            "baseline_rate": round(baseline, 6),
            "anomaly": round(anomaly, 2),
            "acceleration": round(accel, 6),
            "confidence": round(confidence, 3),
            "water_area_ratio": round(latest_ratio, 5),
            "flow_mean": round(flow, 3),
            "flow_baseline": round(flow_base, 3),
            "waterline_y": latest["waterline_y"],
            "waterline_delta_10s": round(waterline_disp, 2),
            "camera_id": camera_id,
            "ts": now_iso(now),
        }

        # ---- condition timers (hysteresis) -------------------------------
        # Arm the clock the first time a condition becomes true, then HOLD it
        # while the condition stays true; disarm only when it clears. (Re-arming
        # every tick would reset the sustain window forever.)
        rising_line = waterline_disp <= -config.WATERLINE_ESCALATE_PX
        cam.cond_watch = self._arm(cam.cond_watch, anomaly > config.WATCH_THRESHOLD, now)
        cam.cond_esc = self._arm(cam.cond_esc, anomaly > config.ESCALATE_THRESHOLD or rising_line, now)
        cam.cond_crit = self._arm(cam.cond_crit, anomaly > config.CRITICAL_THRESHOLD, now)
        cam.cond_normal = self._arm(cam.cond_normal, anomaly < config.NORMAL_THRESHOLD, now)

        # ---- state machine ----------------------------------------------
        if cam.state == "normal":
            if cam.cond_watch and now - cam.cond_watch >= config.SUSTAIN_WATCH_S:
                reason = self._reason(camera_id, anomaly, baseline, rate, flow, flow_base, confidence,
                                      extra=f"sustained {config.SUSTAIN_WATCH_S:.0f}s above the {config.WATCH_THRESHOLD:.0f}x gate")
                self._transition(camera_id, "watch", reason, confidence)
        elif cam.state == "watch":
            if cam.cond_esc and now - cam.cond_esc >= config.SUSTAIN_ESCALATE_S:
                why = (f"anomaly {anomaly:.1f}x above the {config.ESCALATE_THRESHOLD:.0f}x gate sustained "
                       f"{config.SUSTAIN_ESCALATE_S:.0f}s") if anomaly > config.ESCALATE_THRESHOLD else \
                      (f"waterline displaced {abs(waterline_disp):.0f} px in 10 s (persistent rise)")
                reason = self._reason(camera_id, anomaly, baseline, rate, flow, flow_base, confidence, extra=why)
                self._transition(camera_id, "escalating", reason, confidence)
        elif cam.state == "escalating":
            if cam.cond_crit and now - cam.cond_crit >= config.SUSTAIN_CRITICAL_S:
                if confidence >= config.VERIFY_MIN_CONF:
                    reason = self._reason(
                        camera_id, anomaly, baseline, rate, flow, flow_base, confidence,
                        extra=(f"confidence {confidence:.2f} ≥ {config.VERIFY_MIN_CONF:.2f} — gate passed without "
                               f"corroboration; escalating immediately"))
                    self._transition(camera_id, "critical", reason, confidence,
                                     corroboration=self._corroboration_obj(True, "high_confidence", confidence, [],
                                                                           f"Confidence {confidence:.2f} cleared the "
                                                                           f"{config.VERIFY_MIN_CONF:.2f} gate directly."))
                else:
                    cam.verify_since = now
                    cam.verification = {"started": now, "boost_requested": False, "checked": []}
                    reason = self._reason(
                        camera_id, anomaly, baseline, rate, flow, flow_base, confidence,
                        extra=(f"confidence {confidence:.3f} < {config.VERIFY_MIN_CONF:.3f} — entering verification: "
                               f"querying adjacent telemetry and requesting 10 Hz sampling before Critical"))
                    self._transition(camera_id, "verifying", reason, confidence)
        elif cam.state == "verifying":
            self._verify(camera_id, cam, now, anomaly, baseline, rate, flow, flow_base, confidence)
        elif cam.state == "critical":
            pass

        # ---- recovery (steps down one level only after 60 s of normal) ----
        if cam.state != "normal" and cam.cond_normal and now - cam.cond_normal >= config.RECOVER_S:
            nxt = STEP_DOWN[cam.state]
            reason = (f"Readings back inside ±{config.NORMAL_THRESHOLD:.1f}x of the learned baseline for "
                      f"{int(config.RECOVER_S)}s — stepping {cam.state.upper()} → {nxt.upper()}, alert kept for review.")
            self._transition(camera_id, nxt, reason, confidence)

    @staticmethod
    def _arm(current: Optional[float], condition: bool, now: float) -> Optional[float]:
        """Arm a sustain clock on the rising edge, hold it, clear it on the falling edge."""
        if not condition:
            return None
        return now if current is None else current

    # -------------------------------------------------------------- baseline

    def _baseline(self, camera_id: str, rows: list[dict[str, Any]], now: float) -> float:
        cam = self.cameras[camera_id]
        if cam.fp_until and now > cam.fp_until:
            cam.fp_mult = 1.0
            cam.fp_until = 0.0
        # 2 s-spaced increments: robust to 5 fps quantisation noise
        pairs: list[tuple[float, float]] = []
        last_t = None
        for r in rows:
            if last_t is None or r["_epoch"] - last_t >= config.BASELINE_STEP_S:
                pairs.append((r["_epoch"], r["water_area_ratio"]))
                last_t = r["_epoch"]
        if len(pairs) < 4:
            return config.BASELINE_FLOOR * cam.fp_mult

        deltas = []
        for i in range(1, len(pairs)):
            dt = pairs[i][0] - pairs[i - 1][0]
            if dt > 0:
                deltas.append(abs(pairs[i][1] - pairs[i - 1][1]) / dt)
        if not deltas:
            return config.BASELINE_FLOOR * cam.fp_mult
        med = statistics.median(deltas)
        cam.baseline_hist.append(max(med, config.BASELINE_FLOOR))
        del cam.baseline_hist[:-7]
        stable = statistics.median(cam.baseline_hist)
        return max(stable, config.BASELINE_FLOOR) * cam.fp_mult

    def _baseline_readonly(self, camera_id: str, rows: list[dict[str, Any]], now: float) -> float:
        """Estimate a camera's baseline WITHOUT mutating its learned state.

        Same median-of-|Δratio| math as _baseline, but it never appends to
        baseline_hist and never touches fp_mult/fp_until. Used when verifying
        one camera against its neighbours so the verify pass cannot pollute
        the neighbour's learned baseline.
        """
        cam = self.cameras.get(camera_id)
        mult = cam.fp_mult if cam else 1.0
        hist = list(cam.baseline_hist) if cam else []
        # 2 s-spaced increments: robust to 5 fps quantisation noise
        pairs: list[tuple[float, float]] = []
        last_t = None
        for r in rows:
            if last_t is None or r["_epoch"] - last_t >= config.BASELINE_STEP_S:
                pairs.append((r["_epoch"], r["water_area_ratio"]))
                last_t = r["_epoch"]
        if len(pairs) < 4:
            return config.BASELINE_FLOOR * mult

        deltas = []
        for i in range(1, len(pairs)):
            dt = pairs[i][0] - pairs[i - 1][0]
            if dt > 0:
                deltas.append(abs(pairs[i][1] - pairs[i - 1][1]) / dt)
        if not deltas:
            return config.BASELINE_FLOOR * mult
        med = statistics.median(deltas)
        # Fold the current sample into the existing history copy — median of
        # what _baseline would see, without appending anything.
        stable = statistics.median(hist + [max(med, config.BASELINE_FLOOR)]) if hist else max(med, config.BASELINE_FLOOR)
        return max(stable, config.BASELINE_FLOOR) * mult

    # ---------------------------------------------------------- verification

    def _verify(self, camera_id: str, cam: CameraAgent, now: float, anomaly: float,
                baseline: float, rate: float, flow: float, flow_base: float,
                confidence: float) -> None:
        store = timeseries()
        ver = cam.verification or {"started": now, "boost_requested": False, "checked": []}

        held = now - ver["started"] >= config.VERIFY_MIN_HOLD_S

        # 1. adjacent cameras corroborate the rise?
        corrob_cameras = []
        corroborated = False
        for nb in config.ADJACENCY.get(camera_id, []):
            nb_rows = store.query(nb, config.BASELINE_WINDOW_S, valid_only=True)
            nb_recent = _window(nb_rows, config.RATE_WINDOW_S, now)
            if len(nb_recent) < config.RATE_MIN_POINTS:
                corrob_cameras.append({"camera_id": nb, "delta_pct": 0.0, "state": "unknown"})
                continue
            nb_rate = slope(nb_recent)
            nb_base = self._baseline_readonly(nb, nb_rows, now)
            nb_anom = (nb_rate / nb_base) if nb_rate > 0 else 0.0
            nb_30 = _window(nb_rows, 30.0, now)
            r_now = nb_recent[-1]["water_area_ratio"]
            r_then = nb_30[0]["water_area_ratio"] if nb_30 else r_now
            delta_pct = (r_now - r_then) / max(r_then, 1e-4) * 100.0
            nb_state = self.cameras[nb].state if nb in self.cameras else "unknown"
            corrob_cameras.append({
                "camera_id": nb,
                "delta_pct": round(delta_pct, 1),
                "state": nb_state,
                "_anomaly": round(nb_anom, 2),
            })
            if delta_pct >= config.CORROBORATION_MIN_PCT and nb_anom >= config.CORROBORATION_MIN_ANOMALY:
                corroborated = True

        if corroborated and held:
            nb_txt = "; ".join(
                f"adjacent {c['camera_id']} water area +{c['delta_pct']:.1f}% over 30 s (anomaly {c.get('_anomaly', 0):.1f}x)"
                for c in corrob_cameras if c["delta_pct"] > 0
            ) or "adjacent cameras corroborate"
            note = f"{nb_txt} → corroborates the rise"
            reason = self._reason(camera_id, anomaly, baseline, rate, flow, flow_base, confidence,
                                  extra=(f"confidence {confidence:.3f} < {config.VERIFY_MIN_CONF:.3f} — {note}"))
            self._transition(camera_id, "critical", reason, confidence,
                             corroboration=self._corroboration_obj(True, "adjacent_cameras", confidence,
                                                                   corrob_cameras, note))
            return

        if corroborated and not held:
            ver["note"] = (f"corroboration seen — holding the verification window for "
                           f"{config.VERIFY_MIN_HOLD_S:.0f}s before acting on it")

        # 2. request higher-rate sampling from the vision engine (once)
        if not ver.get("boost_requested"):
            ver["boost_requested"] = True
            cam.boost_until = now + 20.0
            ver["note"] = "adjacent feeds inconclusive — sampling raised to 10 Hz for 20 s"
            self._log(camera_id, cam.state, cam.state, confidence,
                      f"Verification: adjacent telemetry inconclusive; requesting 10 Hz sampling from the vision "
                      f"engine for {int(cam.boost_until - now)}s and continuing to watch anomaly {anomaly:.1f}x.")
        cam.verification = ver

        # 3. high-confidence from the boosted stream
        if confidence >= config.VERIFY_MIN_CONF:
            reason = self._reason(camera_id, anomaly, baseline, rate, flow, flow_base, confidence,
                                  extra=(f"high-rate sampling lifted confidence to {confidence:.2f} ≥ "
                                         f"{config.VERIFY_MIN_CONF:.2f} — verification passed"))
            self._transition(camera_id, "critical", reason, confidence,
                             corroboration=self._corroboration_obj(True, "high_rate_sampling", confidence,
                                                                   corrob_cameras,
                                                                   f"Confidence recovered to {confidence:.2f} at 10 Hz."))
            return

        # 4. timeout with persistence
        # Forecast fusion (conservative): forecasts never create alerts. At
        # most an open heavy-rain gate shrinks this verification window by
        # FORECAST_TIGHTEN_PCT (hard-capped at 20%), and every influence is
        # labelled in the reasoning text below. Flags: FORECAST_TIGHTEN
        # (behaviour) / FORECAST_ADVISORY (label only) — both default on.
        timeout_s = float(config.VERIFY_TIMEOUT_S)
        label = ""
        if config.FORECAST_TIGHTEN or config.FORECAST_ADVISORY:
            hint = forecast.fusion_hint(camera_id)
            if hint:
                mm6 = hint.get("mm_6h")
                prob6 = hint.get("prob_6h")
                if config.FORECAST_TIGHTEN:
                    pct = min(max(config.FORECAST_TIGHTEN_PCT, 0.0), 0.20)
                    tight = timeout_s * (1.0 - pct)
                    label = (f" — [forecast advisory: {mm6} mm/6 h at {prob6}% in "
                             f"{hint['zone']} — verification window tightened "
                             f"{timeout_s:.0f}s → {tight:.0f}s]")
                    timeout_s = tight
                else:
                    label = (f" — [forecast advisory: {mm6} mm/6 h at {prob6}% in "
                             f"{hint['zone']} — observation unchanged]")
        if cam.verify_since and now - cam.verify_since >= timeout_s and anomaly >= config.CRITICAL_THRESHOLD:
            reason = self._reason(camera_id, anomaly, baseline, rate, flow, flow_base, confidence,
                                  extra=(f"verification timed out after {timeout_s:.0f}s with anomaly "
                                         f"still {anomaly:.1f}x — escalating on persistence" + label))
            self._transition(camera_id, "critical", reason, confidence,
                             corroboration=self._corroboration_obj(True, "timeout_persistence", confidence,
                                                                   corrob_cameras,
                                                                   f"No corroboration in {timeout_s:.0f}s, "
                                                                   f"but anomaly held at {anomaly:.1f}x."))

    # ------------------------------------------------------------ transitions

    def _transition(self, camera_id: str, to: str, reason: str, confidence: float,
                    corroboration: Optional[dict] = None) -> None:
        cam = self.cameras[camera_id]
        frm = cam.state
        if frm == to:
            return
        at = now_iso()
        cam.state = to
        cam.since = time.time()
        cam.reset_conditions()
        if to != "verifying":
            cam.verify_since = None
            if frm == "verifying":
                cam.verification = None
        else:
            cam.boost_until = time.time() + 20.0

        self._log(camera_id, frm, to, confidence, reason)
        log.info("[%s] %s -> %s (conf %.2f) %s", camera_id, frm.upper(), to.upper(), confidence, reason)

        alert = self._update_alert(camera_id, frm, to, at, reason, confidence, corroboration)
        self._broadcast({
            "type": "state_change",
            "camera_id": camera_id,
            "from": frm,
            "to": to,
            "at": at,
            "confidence": round(confidence, 3),
            "reasoning": reason,
            "alert_id": alert["alert_id"] if alert else None,
        })

    def _log(self, camera_id: str, frm: str, to: str, confidence: float, reasoning: str) -> None:
        entry = {
            "at": now_iso(), "camera_id": camera_id, "from": frm, "to": to,
            "confidence": round(float(confidence), 3), "reasoning": reasoning,
        }
        self.decisions.append(entry)
        del self.decisions[:-200]
        # Durable decision log (best-effort; never breaks the agent loop).
        try:
            db.save_decision(
                at=entry["at"], agent="DecisionAgent",
                input_summary=f"{camera_id}: {frm} -> {to}", decision=to,
                confidence=entry["confidence"], rationale=reasoning)
        except Exception:
            log.exception("[%s] decision persistence failed", camera_id)

    def _broadcast(self, event: dict) -> None:
        try:
            res = self.emit(event)
            if asyncio.iscoroutine(res):
                asyncio.ensure_future(res)
        except Exception:
            log.exception("broadcast failed")

    # ---------------------------------------------------------------- alerts

    def _update_alert(self, camera_id: str, frm: str, to: str, at: str, reason: str,
                      confidence: float, corroboration: Optional[dict]) -> Optional[dict]:
        store = timeseries()
        alert = self.alerts.active_for(camera_id)

        if to == "normal":
            if alert and alert["status"] in ("active", "acknowledged"):
                alert["status"] = "resolved"
                alert["closed_at"] = at
                alert["state"] = "normal"
                alert["updated_at"] = at
                try:
                    db.save_alert(alert)
                except Exception:
                    log.exception("[%s] alert persistence failed", camera_id)
                self._broadcast({"type": "alert", "at": at, "action": "updated", "alert": alert})
            return alert

        severity = SEVERITY.get(to, "watch")
        if alert is None:
            raw = store.query(camera_id, 90.0, valid_only=True)
            # downsample to ~1 Hz for the payload
            picked: list[dict] = []
            last_bucket = None
            for r in raw:
                bucket = int(r["_epoch"])
                if bucket != last_bucket:
                    picked.append({
                        "ts": r["ts"],
                        "water_area_ratio": r["water_area_ratio"],
                        "flow_mean": r.get("flow_mean", 0),
                        "waterline_y": r.get("waterline_y", 0),
                        "confidence": r.get("confidence", 0),
                    })
                    last_bucket = bucket
            alert = {
                "alert_id": self.alerts.new_id(camera_id),
                "camera_id": camera_id,
                "type": "flood",
                "severity": severity,
                "state": to,
                "started_at": at,
                "updated_at": at,
                "closed_at": None,
                "status": "active",
                "transitions": [{"from": frm, "to": to, "at": at, "reason": reason}],
                "evidence_snapshot_url": self.snapshots.freeze_latest(camera_id),
                "telemetry_window": picked,
                "reasoning": reason,
                "corroboration": corroboration,
                "agent_confidence": round(confidence, 3),
                "acknowledged_at": None,
                "ack_by": None,
                "false_positive": False,
                # Drill engines use drill-* camera ids; tag their alerts so the
                # console and reports can separate drills from real events.
                "drill": camera_id.startswith("drill-"),
            }
            # Validate against the frozen alert contract; a validation bug
            # must never crash the agent — keep the raw dict on failure.
            try:
                Alert.model_validate(alert)
            except Exception:
                log.warning("[%s] new alert record failed contract validation; keeping raw dict",
                            camera_id, exc_info=True)
            self.alerts.add(alert)
            try:
                db.save_alert(alert)
            except Exception:
                log.exception("[%s] alert persistence failed", camera_id)
            action = "created"
        else:
            rank = {"watch": 1, "escalating": 2, "critical": 3}
            if rank.get(severity, 0) > rank.get(alert["severity"], 0):
                alert["severity"] = severity
            alert["state"] = to
            alert["updated_at"] = at
            alert["reasoning"] = reason
            alert["agent_confidence"] = round(confidence, 3)
            alert["transitions"].append({"from": frm, "to": to, "at": at, "reason": reason})
            if corroboration:
                alert["corroboration"] = corroboration
            if to in ("escalating", "verifying", "critical"):
                frozen = self.snapshots.freeze_latest(camera_id)
                if frozen:
                    alert["evidence_snapshot_url"] = frozen
            action = "updated"
            try:
                Alert.model_validate(alert)
            except Exception:
                log.warning("[%s] updated alert record failed contract validation; keeping raw dict",
                            camera_id, exc_info=True)
            try:
                db.save_alert(alert)
            except Exception:
                log.exception("[%s] alert persistence failed", camera_id)

        self._broadcast({"type": "alert", "at": at, "action": action, "alert": alert})
        # Fire once per escalation INTO critical (frm != "critical" guards
        # repeats while the camera stays critical). The hook must be fast and
        # non-raising — heavy AWS work happens on its own thread.
        if to == "critical" and frm != "critical" and self.on_critical is not None:
            try:
                self.on_critical(alert)
            except Exception:
                log.exception("[%s] on_critical hook failed", camera_id)
        return alert

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _corroboration_obj(verified: bool, method: str, confidence: float,
                           cameras: list[dict], note: str) -> dict:
        clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cameras]
        return {
            "verified": verified,
            "method": method,
            "confidence": round(confidence, 3),
            "cameras": clean,
            "note": note,
        }

    @staticmethod
    def _reason(camera_id: str, anomaly: float, baseline: float, rate: float, flow: float,
                flow_base: float, confidence: float, extra: str = "") -> str:
        parts = [
            f"Water area expanding {anomaly:.1f}x faster than 5-min baseline "
            f"({baseline:.4f} → {rate:.4f} ratio/s)",
            f"flow mean {flow:.1f} px/f vs {flow_base:.1f} baseline",
        ]
        if extra:
            parts.append(extra)
        return "; ".join(parts)
