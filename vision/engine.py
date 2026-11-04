"""AquaTrace perception engine (OpenCV).

One process per camera. Reads a video source (file path or RTSP URL — both go
through cv2.VideoCapture identically), runs the per-frame pipeline:

    MOG2 background subtraction  +  HSV water range  ->  morphological cleanup
    contour analysis             ->  water bbox / area ratio / waterline
    Farneback optical flow       ->  flow magnitude, dispersion, direction

and POSTs telemetry JSON (~5 fps) to the backend, attaching an annotated JPEG
every second so alerts always have visual evidence.

    python -m vision.engine --camera cam-01 --video assets/scenes/normal.mp4

Live mode: the source may be an RTSP/HTTP URL instead of a file. Network
sources get TCP transport, frozen-frame detection (a stuck decoder replaying
one frame is marked "down", never fed to the detector), and backoff
reconnects. Every telemetry POST — detection or heartbeat — carries the
stream-health contract: frame_hash, captured_at, stream_status,
reconnect_attempts.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import signal
import sys
import time
from typing import Any

import cv2
import httpx
import numpy as np

log = logging.getLogger("aquatrace.vision")

# Multi-band water segmentation calibrated against real FloodNet flood imagery + synthetic CCTV:
# Band 1: Turbid / muddy brown / tan silt flood water (FloodNet Hurricane signature)
FLOOD_TURBID_HSV_LO = np.array([8, 28, 30], dtype=np.uint8)
FLOOD_TURBID_HSV_HI = np.array([42, 230, 220], dtype=np.uint8)

# Band 2: Deep canal / slate / cyan-blue water
FLOOD_DEEP_HSV_LO = np.array([42, 30, 30], dtype=np.uint8)
FLOOD_DEEP_HSV_HI = np.array([135, 240, 245], dtype=np.uint8)

# Band 3: Dark storm runoff / flooded asphalt with reflective surface sheen
FLOOD_PAVEMENT_HSV_LO = np.array([0, 8, 28], dtype=np.uint8)
FLOOD_PAVEMENT_HSV_HI = np.array([180, 70, 160], dtype=np.uint8)

# Legacy alias for backward compatibility
WATER_HSV_LO = (8, 28, 30)
WATER_HSV_HI = (135, 240, 245)

STATE_COLORS = {
    "normal": (16, 185, 129),       # #10B981
    "watch": (245, 158, 11),        # #F59E0B
    "escalating": (249, 115, 22),   # #F97316
    "critical": (68, 68, 239),      # #EF4444 (BGR)
    "verifying": (249, 115, 22),
    "signal_lost": (90, 107, 130),
}
CYAN = (238, 211, 34)               # #22D3EE in BGR

#: Identical frame hashes for this long on a network source => frozen stream.
FROZEN_AFTER_S = 10.0
#: Reconnect backoff: 1s, 2s, 4s … capped here.
RECONNECT_BASE_S = 1.0
RECONNECT_MAX_S = 60.0
#: Heartbeat cadence while down/reconnecting (backend staleness window is 6s).
HEARTBEAT_EVERY_S = 2.0


def _is_network_source(source: str) -> bool:
    """rtsp(s)/http(s) URLs are live network streams; anything else (file
    paths, sim:// demo names) is a local/demo source."""
    return source.lower().startswith(("rtsp://", "rtsps://", "http://", "https://"))


def _utcnow_iso() -> str:
    now = time.time()
    return (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
            + f".{int(now * 1000) % 1000:03d}Z")


class VisionEngine:
    def __init__(
        self,
        camera_id: str,
        source: str,
        backend: str | None = None,
        sample_hz: float = 5.0,
        video_fps: float = 15.0,
        loop: bool = False,
        on_eof: str = "hold",
        annotate_width: int = 640,
    ) -> None:
        self.camera_id = camera_id
        self.source = source
        self.backend = (backend or os.environ.get("AQUATRACE_BACKEND")
                        or "http://127.0.0.1:8000").rstrip("/")
        self.sample_hz = sample_hz
        self.video_fps = video_fps
        self.loop = loop
        self.on_eof = on_eof
        self.annotate_width = annotate_width

        self._running = True
        self._frame_id = 0
        self._mog = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=32, detectShadows=True
        )
        self._prev_gray: np.ndarray | None = None
        self._prev_bbox: tuple[int, int, int, int] | None = None
        self._prev_waterline: float | None = None
        self._valid_history: list[bool] = []
        self._wl_deltas: list[float] = []
        self._state = "normal"
        self._control_next = 0.0
        self._control = {"state": "normal", "sample_hz": sample_hz}
        self._last_emit = 0.0
        self._frames_seen = 0
        self._frames_valid = 0
        self._window_start = time.monotonic()
        # trust_env=False: the engine talks to the local backend and must never
        # route through a proxy. Proxy env vars (e.g. bracketed IPv6 entries in
        # NO_PROXY) can even crash httpx.Client() construction outright.
        self._client = httpx.Client(timeout=4.0, trust_env=False)
        self._pending: list[dict] = []
        # --- stream health (live contract) --------------------------------
        self._net = _is_network_source(source)
        self._stream_status = "ok"          # ok | down | reconnecting
        self._reconnect_attempts = 0
        self._last_hash: str | None = None
        self._hash_first_seen = 0.0
        self._last_captured_at: str | None = None

    # ------------------------------------------------------------------ state

    def stop(self, *_: Any) -> None:
        self._running = False

    # ------------------------------------------------------------------- loop

    def run(self) -> int:
        if self._net and self.source.lower().startswith(("rtsp://", "rtsps://")):
            # Force TCP transport for RTSP: with UDP, ffmpeg packet loss shows
            # up as torn/frozen frames. setdefault so an operator export wins.
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

        cap: cv2.VideoCapture | None = cv2.VideoCapture(self.source)
        warm: tuple[np.ndarray, str] | None = None
        if not cap.isOpened():
            cap.release()
            cap = None
            if self._net:
                # A live camera that is down at boot must not kill the engine:
                # enter the backoff reconnect loop and heartbeat the backend.
                log.warning("[%s] cannot open %s at startup — entering reconnect loop",
                            self.camera_id, self.source)
                self._stream_status = "reconnecting"
                cap, warm = self._reconnect(None)
                if cap is None:
                    return 0
            else:
                log.error("[%s] cannot open source %s", self.camera_id, self.source)
                return 2
        log.info("[%s] engine up backend=%s source=%s hz=%.1f",
                 self.camera_id, self.backend, self.source, self.sample_hz)

        next_tick = time.monotonic()
        wall_start = time.monotonic()
        frames_read = 0
        eof_hits = 0

        while self._running:
            hz = float(self._control.get("sample_hz") or self.sample_hz)
            hz = max(1.0, min(20.0, hz))
            target_dt = 1.0 / hz
            skip = max(1, int(self.video_fps // hz))

            frame = None
            captured_at: str | None = None
            held = False
            if warm is not None:
                # First frame carried out of a successful reconnect handshake.
                frame, captured_at = warm
                warm = None
                frames_read += 1
                eof_hits = 0
            else:
                for _ in range(skip):
                    ok, f = cap.read()
                    if ok:
                        frame = f
                        captured_at = _utcnow_iso()
                        frames_read += 1
                        eof_hits = 0
                        continue
                    eof_hits += 1
                    if not self._net and self.loop and eof_hits == 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, f = cap.read()
                        if ok:
                            frame = f
                            captured_at = _utcnow_iso()
                            frames_read += 1
                            eof_hits = 0
                            continue
                    break

            if frame is None:
                if self._net:
                    # Read failure / EOF on a network source. Never re-post
                    # the last good frame with fresh timestamps — that lie is
                    # exactly what the backend's staleness clock must see
                    # through. Heartbeat once as "down", then reconnect.
                    log.warning("[%s] read failed on %s — marking down",
                                self.camera_id, self.source)
                    self._stream_status = "down"
                    self._post_heartbeat()
                    cap, warm = self._reconnect(cap)
                    if cap is None:
                        return 0
                    continue
                # File source: unchanged demo behaviour — hold the last good
                # frame so the scenario settles instead of going dark.
                frame = getattr(self, "_last_frame", None)
                if frame is None:
                    time.sleep(0.1)
                    continue
                held = True

            self._last_frame = frame
            if not held:
                prev_hash = self._last_hash
                h = self._frame_hash(frame)
                self._last_hash = h
                self._last_captured_at = captured_at
                if self._net and self._frozen_since(prev_hash, h, time.monotonic()):
                    # Same frame hash for >FROZEN_AFTER_S: the decoder is
                    # stuck replaying one frame. Treat as down, heartbeat,
                    # and reconnect — do not feed it to the detector.
                    log.warning("[%s] stream frozen (identical frames for >%.0fs)",
                                self.camera_id, FROZEN_AFTER_S)
                    self._stream_status = "down"
                    self._post_heartbeat()
                    cap, warm = self._reconnect(cap)
                    if cap is None:
                        return 0
                    continue

            now = time.monotonic()
            self._maybe_poll_control(now)
            sample = self.process(frame)

            # Stream-health contract: every detection POST carries these.
            sample["frame_hash"] = self._last_hash or "0" * 16
            sample["captured_at"] = self._last_captured_at or _utcnow_iso()
            sample["stream_status"] = self._stream_status
            sample["reconnect_attempts"] = self._reconnect_attempts

            if now >= next_tick:
                self.emit(sample, frame)
                next_tick = now + target_dt

            if held:
                time.sleep(max(0.05, target_dt))
            else:
                # real-time pacing: wall clock follows the video clock, so the
                # scenario unfolds at the speed the agent would really see it
                delay = (wall_start + frames_read / self.video_fps) - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, 0.25))

            self._frames_seen += 1
            if time.monotonic() - self._window_start >= 10.0:
                self._log_stats(time.monotonic())

        if cap is not None:
            cap.release()
        self._client.close()
        log.info("[%s] engine stopped", self.camera_id)
        return 0

    # ------------------------------------------------------- stream health

    @staticmethod
    def _frame_hash(frame: np.ndarray) -> str:
        """md5 (first 16 hex chars) of a 32x32 grayscale thumbnail.

        Cheap enough to run on every sampled frame; stable enough that two
        genuinely different frames never collide, noisy enough that a live
        sensor never repeats a hash — a stuck decoder does.
        """
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (32, 32))
        return hashlib.md5(small.tobytes()).hexdigest()[:16]

    def _frozen_since(self, prev: str | None, h: str, now_m: float) -> bool:
        """True when the same frame hash has persisted past FROZEN_AFTER_S.

        Only consulted for network sources: a live sensor's noise means an
        unchanged 32x32 hash for >10 s is a stuck stream, not a still scene.
        """
        if h != prev:
            self._hash_first_seen = now_m
            return False
        return prev is not None and (now_m - self._hash_first_seen) > FROZEN_AFTER_S

    def _reconnect(
        self, cap: cv2.VideoCapture | None
    ) -> tuple[cv2.VideoCapture | None, tuple[np.ndarray, str] | None]:
        """Backoff reconnect loop for network sources.

        Releases the dead capture, then retries with 1s, 2s, 4s … 60s
        sleeps, heartbeating the backend the whole time so its staleness
        clock sees an honest down/reconnecting stream instead of silence.
        Returns (cap, (frame, captured_at)) on success, (None, None) when
        the engine was stopped mid-retry.
        """
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        self._stream_status = "reconnecting"
        delay = RECONNECT_BASE_S
        while self._running:
            self._reconnect_attempts += 1
            log.warning("[%s] reconnect attempt %d — retrying in %.0fs",
                        self.camera_id, self._reconnect_attempts, delay)
            deadline = time.monotonic() + delay
            while self._running and time.monotonic() < deadline:
                self._post_heartbeat()
                time.sleep(min(HEARTBEAT_EVERY_S,
                               max(0.0, deadline - time.monotonic())))
            if not self._running:
                return None, None
            cap = cv2.VideoCapture(self.source)
            if cap.isOpened() and self._running:
                ok, f = cap.read()
                if ok:
                    captured_at = _utcnow_iso()
                    self._reconnect_attempts = 0
                    self._stream_status = "ok"
                    # Fresh stream: relearn the background and reset the
                    # frozen-frame baseline so stale state cannot poison it.
                    self._mog = cv2.createBackgroundSubtractorMOG2(
                        history=300, varThreshold=32, detectShadows=True)
                    self._prev_gray = None
                    self._valid_history = []
                    self._wl_deltas = []
                    self._last_hash = None
                    self._hash_first_seen = 0.0
                    log.info("[%s] stream reconnected", self.camera_id)
                    return cap, (f, captured_at)
            cap.release()
            delay = min(delay * 2, RECONNECT_MAX_S)
        return None, None

    def _heartbeat_payload(self) -> dict[str, Any]:
        now_iso = _utcnow_iso()
        return {
            "camera_id": self.camera_id,
            "ts": now_iso,
            "frame_id": self._frame_id,
            "water_bbox": [0.0, 0.0, 0.0, 0.0],
            "water_area_px": 0.0,
            "water_area_ratio": 0.0,
            "flow_mean": 0.0,
            "flow_std": 0.0,
            "flow_dir_deg": None,
            "waterline_y": 0.0,
            "waterline_delta": 0.0,
            "confidence": 0.0,
            "detection_valid": False,
            "sample_hz": float(self._control.get("sample_hz") or self.sample_hz),
            # stream-health contract — identical keys to detection telemetry
            "frame_hash": self._last_hash or "0" * 16,
            "captured_at": self._last_captured_at or now_iso,
            "stream_status": self._stream_status,
            "reconnect_attempts": self._reconnect_attempts,
        }

    def _post_heartbeat(self) -> None:
        """Fire-and-forget status heartbeat: no detection data, no evidence
        frame, no backlog queue — it is superseded by the next one."""
        payload = json.dumps(self._heartbeat_payload())
        try:
            r = self._client.post(f"{self.backend}/ingest/telemetry",
                                  content=payload,
                                  headers={"content-type": "application/json"})
            if r.status_code == 422:
                log.error("[%s] heartbeat rejected: %s", self.camera_id, r.text[:200])
        except Exception:
            pass  # backend unreachable — the silence itself is the signal

    def _log_stats(self, now: float) -> None:
        dt = now - self._window_start
        if self._frames_seen:
            det = self._frames_valid / max(1, self._frames_seen)
            log.info(
                "[%s] fps=%.1f detect_rate=%.0f%% state=%s",
                self.camera_id,
                self._frames_seen / dt,
                det * 100,
                self._state,
            )
        self._frames_seen = 0
        self._frames_valid = 0
        self._window_start = now

    def _maybe_poll_control(self, now: float) -> None:
        if now < self._control_next:
            return
        self._control_next = now + 2.0
        try:
            r = self._client.get(f"{self.backend}/cameras/{self.camera_id}/control")
            if r.status_code == 200:
                data = r.json()
                self._control.update(data)
                self._state = str(data.get("state") or "normal")
        except Exception as exc:  # backend may be booting
            log.debug("[%s] control poll failed: %s", self.camera_id, exc)

    # -------------------------------------------------------------- pipeline

    def process(self, frame: np.ndarray) -> dict[str, Any]:
        h, w = frame.shape[:2]
        self._frame_id += 1
        now = time.time()
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}Z"

        # --- 1. background subtraction ---------------------------------
        fg = self._mog.apply(frame, learningRate=-1)
        fg_binary = (fg == 255).astype(np.uint8) * 255

        # --- 2. Multi-band calibrated HSV water segmentation -----------
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask_turbid = cv2.inRange(hsv, FLOOD_TURBID_HSV_LO, FLOOD_TURBID_HSV_HI)
        mask_deep = cv2.inRange(hsv, FLOOD_DEEP_HSV_LO, FLOOD_DEEP_HSV_HI)
        mask_pave = cv2.inRange(hsv, FLOOD_PAVEMENT_HSV_LO, FLOOD_PAVEMENT_HSV_HI)
        water = cv2.bitwise_or(cv2.bitwise_or(mask_turbid, mask_deep), mask_pave)

        # --- 3. fusion + morphology ------------------------------------
        warm = self._frame_id > 45          # MOG2 needs a few seconds to learn
        if warm:
            near = cv2.dilate(water, np.ones((15, 15), np.uint8))
            mask = cv2.bitwise_or(water, cv2.bitwise_and(fg_binary, near))
        else:
            mask = water
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

        # --- 4. contour analysis ---------------------------------------
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(h * w)
        min_area = frame_area * 0.004
        best = None
        best_area = 0.0
        for c in contours:
            a = cv2.contourArea(c)
            if a > best_area:
                best_area, best = a, c

        valid = best is not None and best_area >= min_area
        if valid:
            x, y, bw, bh = cv2.boundingRect(best)
            bbox = (int(x), int(y), int(bw), int(bh))
            area_px = float(best_area)
            ratio = area_px / frame_area
            waterline = self._subpixel_waterline(mask, bbox)
            self._frames_valid += 1
        else:
            bbox = self._prev_bbox or (0, 0, 0, 0)
            area_px = 0.0
            ratio = 0.0
            waterline = self._prev_waterline or float(h)

        # --- 5. dense optical flow & waterline movement speed ----------
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        flow_mean = flow_std = flow_dir = 0.0
        wl_flow_speed = 0.0
        wl_flow_vectors: list[tuple[int, int, float, float]] = []
        if self._prev_gray is not None and valid:
            flow = cv2.calcOpticalFlowFarneback(
                self._prev_gray, gray, None,
                pyr_scale=0.5, levels=3, winsize=15,
                iterations=3, poly_n=5, poly_sigma=1.2, flags=0,
            )
            mag = np.linalg.norm(flow, axis=2)
            sel = cv2.dilate(mask, np.ones((9, 9), np.uint8)) > 0
            sel &= mag < np.percentile(mag[sel], 99.5) if sel.any() else False
            if sel.any():
                m = mag[sel]
                flow_mean = float(m.mean())
                flow_std = float(m.std())
                ang = np.degrees(np.arctan2(-flow[sel][:, 1], flow[sel][:, 0]))
                hist, edges = np.histogram(ang, bins=12, range=(-180, 180), weights=m)
                bi = int(np.argmax(hist))
                flow_dir = float((edges[bi] + edges[bi + 1]) / 2.0)

            # Farneback waterline movement: analyze vertical motion in the waterline band
            y_wl = int(round(waterline))
            y_min = max(0, y_wl - 12)
            y_max = min(h, y_wl + 12)
            if y_max > y_min:
                # Vertical flow (y component): in image coordinates, negative is moving UP (water rising)
                # Upward advance of waterline = -flow[..., 1]
                wl_strip_flow = -flow[y_min:y_max, :, 1]
                wl_strip_mask = sel[y_min:y_max, :]
                if wl_strip_mask.any():
                    wl_flow_speed = float(np.median(wl_strip_flow[wl_strip_mask]))
                else:
                    wl_flow_speed = float(np.median(wl_strip_flow))

                # Collect flow vectors along the waterline for visual verification
                step = max(16, w // 20)
                for vx in range(x, min(x + bw, w), step):
                    vy = int(np.clip(y_wl, 0, h - 1))
                    fx = float(flow[vy, vx, 0])
                    fy = float(flow[vy, vx, 1])
                    wl_flow_vectors.append((vx, vy, fx, fy))
        self._prev_gray = gray
        self._prev_wl_vectors = wl_flow_vectors

        # --- 6. confidence & waterline dynamics -------------------------
        conf = self._confidence(valid, bbox, waterline, flow_mean, flow_std)
        self._prev_bbox = bbox if valid else self._prev_bbox
        delta = 0.0
        if self._prev_waterline is not None and valid:
            diff_delta = float(self._prev_waterline - waterline)   # + => water rising
            if abs(wl_flow_speed) > 0.01:
                # Fuse Farneback optical-flow waterline speed with discrete contour change
                delta = float(0.65 * wl_flow_speed + 0.35 * diff_delta)
            else:
                delta = diff_delta
        elif valid and abs(wl_flow_speed) > 0.01:
            delta = float(wl_flow_speed)
        if valid:
            self._prev_waterline = waterline

        return {
            "camera_id": self.camera_id,
            "ts": ts,
            "frame_id": self._frame_id,
            "water_bbox": [int(v) for v in bbox],
            "water_area_px": round(area_px, 1),
            "water_area_ratio": round(ratio, 5),
            "flow_mean": round(flow_mean, 3),
            "flow_std": round(flow_std, 3),
            "flow_dir_deg": round(flow_dir, 1),
            "waterline_y": round(waterline, 1),
            "waterline_delta": round(delta, 2),
            "confidence": round(conf, 3),
            "detection_valid": bool(valid),
            "sample_hz": float(self._control.get("sample_hz") or self.sample_hz),
        }

    @staticmethod
    def _subpixel_waterline(mask: np.ndarray, bbox: tuple[int, int, int, int]) -> float:
        """Sub-pixel top edge of the water region.

        Uses row coverage relative to the widest row: foam and rain splash sit
        above the true surface at partial coverage, debris knocks it around —
        both show up as jitter, which is exactly what confidence should feel.
        """
        x, y, w, h = bbox
        if w <= 0 or h <= 0:
            return float(mask.shape[0])
        band = mask[y:y + min(h, 40), x:x + w]
        counts = band.sum(axis=1).astype(np.float64) / 255.0
        if counts.size == 0 or counts.max() < 8:
            return float(y)
        rel = counts / counts.max()
        idx = int(np.argmax(rel >= 0.5))
        if idx == 0:
            return float(y)
        prev = rel[idx - 1]
        cur = rel[idx]
        denom = (cur - prev) or 1.0
        frac = float(np.clip((0.5 - prev) / denom, 0.0, 1.0))
        return float(y + idx - 1 + frac)

    def _confidence(
        self,
        valid: bool,
        bbox: tuple[int, int, int, int],
        waterline: float,
        flow_mean: float,
        flow_std: float,
    ) -> float:
        if not valid:
            self._valid_history.append(False)
            self._valid_history = self._valid_history[-30:]
            return 0.0

        # --- contour stability: bbox overlap frame-to-frame -------------
        iou = 1.0
        if self._prev_bbox and self._prev_bbox[2] > 0:
            ax, ay, aw, ah = self._prev_bbox
            bx, by, bw, bh = bbox
            ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
            iy = max(0, min(ay + ah, by + bh) - max(ay, by))
            inter = ix * iy
            union = aw * ah + bw * bh - inter
            iou = inter / union if union else 0.0
        bbox_stab = float(np.clip((iou - 0.45) / 0.50, 0.0, 1.0))

        # --- waterline tracking consistency (jitter vs. smooth trend) ----
        wl_delta = 0.0
        if self._prev_waterline is not None:
            wl_delta = self._prev_waterline - waterline
        self._wl_deltas.append(wl_delta)
        self._wl_deltas = self._wl_deltas[-24:]
        wl_trend = float(np.median(self._wl_deltas[-12:])) if self._wl_deltas else 0.0
        wl_err = abs(wl_delta - wl_trend)
        # tolerate the ±1 row quantisation of a flat waterline; real tracking
        # failures (debris, droplets, foam) blow well past this
        line_stab = float(np.clip(1.0 - max(0.0, wl_err - 0.15) / 1.2, 0.0, 1.0))
        stability = 0.35 * bbox_stab + 0.65 * line_stab

        # --- flow coherence: turbulent debris-choked water scatters it ---
        ratio_fs = flow_std / max(flow_mean, 1e-3)
        flow_consistency = float(np.clip(1.0 - ratio_fs / 1.2, 0.0, 1.0))

        # --- fraction of frames with a *consistent* valid detection ------
        consistent = bool(valid) and iou >= 0.70 and wl_err < 1.6
        self._valid_history.append(consistent)
        self._valid_history = self._valid_history[-30:]
        valid_frac = sum(self._valid_history) / len(self._valid_history)

        return float(np.clip(0.45 * stability + 0.20 * flow_consistency + 0.35 * valid_frac, 0.0, 1.0))

    # ---------------------------------------------------------------- output

    def annotate(self, frame: np.ndarray, s: dict[str, Any]) -> np.ndarray:
        img = frame.copy()
        x, y, w, h = s["water_bbox"]
        state = (self._control.get("state") or "normal").lower()
        col = STATE_COLORS.get(state, STATE_COLORS["normal"])

        if s["detection_valid"] and w > 0:
            cv2.rectangle(img, (x, y), (x + w, y + h), CYAN, 2)
            wl = int(s["waterline_y"])
            x0, x1 = x, x + w
            for xx in range(x0, x1, 14):
                cv2.line(img, (xx, wl), (min(xx + 8, x1), wl), CYAN, 2)
            cv2.putText(img, f"y={wl}", (x1 - 66, wl - 7),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, CYAN, 1, cv2.LINE_AA)
            # Draw Farneback optical flow waterline motion vectors
            for vx, vy, fx, fy in getattr(self, "_prev_wl_vectors", []):
                pt2 = (int(vx + np.clip(fx * 4.0, -25, 25)), int(vy + np.clip(fy * 4.0, -25, 25)))
                cv2.arrowedLine(img, (vx, vy), pt2, (34, 211, 238), 1, tipLength=0.35)

        # state chip
        chip = state.upper().replace("_", " ")
        (tw, th), _ = cv2.getTextSize(chip, cv2.FONT_HERSHEY_COMPLEX, 0.5, 1)
        cv2.rectangle(img, (8, 8), (18 + tw, 14 + th), (10, 14, 20), -1)
        cv2.rectangle(img, (8, 8), (18 + tw, 14 + th), col, 1)
        cv2.putText(img, chip, (14, 14 + th - 4), cv2.FONT_HERSHEY_COMPLEX, 0.5, col, 1, cv2.LINE_AA)

        # metric overlay (JetBrains Mono is not bundled with OpenCV: Hershey)
        lines = [
            f"{self.camera_id.upper()}  {s['sample_hz']:.0f} Hz",
            f"AREA  {s['water_area_ratio'] * 100:5.1f}%",
            f"FLOW  {s['flow_mean']:5.2f} px/f",
            f"LINE  {s['waterline_y']:5.0f} px  d{s['waterline_delta']:+.1f}",
            f"CONF  {s['confidence']:.2f}",
        ]
        y0 = 40
        cv2.rectangle(img, (6, y0 - 18), (150, y0 + 11 * len(lines) + 2), (8, 12, 18), -1)
        for i, line in enumerate(lines):
            cv2.putText(img, line, (12, y0 + i * 13 + 6),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.36, (232, 238, 244) if i else CYAN, 1, cv2.LINE_AA)

        w_img = img.shape[1]
        cv2.putText(img, "AQUATRACE", (w_img - 118, img.shape[0] - 12),
                    cv2.FONT_HERSHEY_COMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
        cv2.putText(img, s["ts"][11:19] + "Z", (w_img - 118, img.shape[0] - 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (139, 152, 169), 1, cv2.LINE_AA)
        return img

    def emit(self, sample: dict[str, Any], frame: np.ndarray) -> None:
        now = time.time()
        if now - self._last_emit >= 0.95:      # ~1 Hz evidence frames
            self._last_emit = now
            ann = self.annotate(frame, sample)
            small = ann if ann.shape[1] <= self.annotate_width else cv2.resize(
                ann, (self.annotate_width, int(ann.shape[0] * self.annotate_width / ann.shape[1]))
            )
            ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
            if ok:
                sample["annotated_jpeg_b64"] = base64.b64encode(buf.tobytes()).decode("ascii")

        payload = json.dumps(sample)
        headers = {"content-type": "application/json"}
        try:
            r = self._client.post(f"{self.backend}/ingest/telemetry", content=payload,
                                  headers=headers)
            if r.status_code == 422:
                log.error("[%s] telemetry rejected: %s", self.camera_id, r.text[:400])
            if r.status_code < 300:
                # Current payload accepted: drain the backlog. A payload that
                # is rejected (or whose drain POST fails) goes back to the
                # head of the queue instead of being dropped, and we never
                # drain after a 422 (the backlog would just 422 again).
                while self._pending:
                    old = self._pending.pop(0)
                    try:
                        dr = self._client.post(f"{self.backend}/ingest/telemetry",
                                               content=old, headers=headers)
                        if dr.status_code >= 300:
                            self._pending.insert(0, old)
                            break
                    except Exception:
                        self._pending.insert(0, old)
                        break
        except Exception:
            # Only reached when the current POST never got a response, i.e.
            # the current payload was never accepted. Never re-queue a payload
            # that was already sent successfully.
            self._pending.append(payload)
            del self._pending[:-60]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="AquaTrace vision engine")
    p.add_argument("--camera", required=True)
    p.add_argument("--video", required=True, help="file path or RTSP url")
    p.add_argument("--backend",
                   default=os.environ.get("AQUATRACE_BACKEND", "http://127.0.0.1:8000"),
                   help="backend base URL (env AQUATRACE_BACKEND)")
    p.add_argument("--hz", type=float, default=5.0)
    p.add_argument("--video-fps", type=float, default=15.0)
    p.add_argument("--loop", action="store_true")
    p.add_argument("--on-eof", choices=["hold", "loop"], default="hold")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    loop = args.loop or args.on_eof == "loop"
    eng = VisionEngine(
        camera_id=args.camera,
        source=args.video,
        backend=args.backend,
        sample_hz=args.hz,
        video_fps=args.video_fps,
        loop=loop,
        on_eof=args.on_eof,
    )
    signal.signal(signal.SIGTERM, eng.stop)
    signal.signal(signal.SIGINT, eng.stop)
    return eng.run()


if __name__ == "__main__":
    sys.exit(main())
