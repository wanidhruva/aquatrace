"""Judge-friendly drills on the live pipeline.

POST /api/drill/start/{scenario} spawns ONE extra vision engine on a synthetic
sim:// source WITHOUT touching the existing engines — the drill flows through
the exact same ingest -> agent -> alert -> AWS pipeline as real cameras, so a
judge can trigger a flood on a live system safely.

Drill engines use camera ids prefixed "drill-" (drill-cam-1); the agent tags
every alert from such a camera with drill:true. Stopping the drill unregisters
the camera so it stops appearing in the console and the signal watch.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Callable, Optional

from . import config, db
from .scenario import SCENARIOS, start_engine, stop_engine_proc
from .stores import now_iso, timeseries

log = logging.getLogger("aquatrace.drill")

DRILL_CAM_ID = "drill-cam-1"


class DrillManager:
    def __init__(self, emit: Callable[[dict], Any], agent: Any,
                 signalwatch: Any = None) -> None:
        self.emit = emit
        self.agent = agent
        self.signalwatch = signalwatch
        self.procs: dict[str, subprocess.Popen] = {}
        self.scenario: Optional[str] = None
        self.started_at: Optional[str] = None

    # ----------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        alive = {cid: {"pid": p.pid, "alive": p.poll() is None}
                 for cid, p in self.procs.items()}
        return {
            "active": bool(self.procs),
            "scenario": self.scenario,
            "camera_id": DRILL_CAM_ID if self.procs else None,
            "started_at": self.started_at,
            "engines": alive,
        }

    # ------------------------------------------------------------------ start

    def _ensure_camera(self) -> None:
        """Register the drill camera at runtime so ingest, the agent loop,
        and the console treat it like any other camera."""
        if DRILL_CAM_ID not in config.CAMERA_IDS:
            base = config.camera("cam-01")
            config.register_camera({
                "id": DRILL_CAM_ID,
                "name": "Drill Camera 1",
                "zone": "Drill",
                "location": "Drill harness (synthetic)",
                "lat": base.get("lat"),
                "lng": base.get("lng"),
                "adjacent": [],
                "source": f"sim://{self.scenario}",
                "enabled": True,
            })
        if hasattr(self.agent, "ensure_camera"):
            self.agent.ensure_camera(DRILL_CAM_ID)

    def start(self, scenario: str) -> dict[str, Any]:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario; expected one of {SCENARIOS}")
        self.stop(emit_event=False)
        self.scenario = scenario
        self._ensure_camera()
        # Plant 5 minutes of calm history BEFORE the engine starts. Without a
        # calm window the agent learns "normal" from the ramp itself, the
        # anomaly ratio stays pinned near 1x, and the drill can never clear
        # the 2x watch gate. Mirrors ScenarioRunner.start(reseed=True).
        timeseries().reset_and_seed(DRILL_CAM_ID)
        cam = getattr(self.agent, "cameras", {}).get(DRILL_CAM_ID)
        if cam is not None:
            cam.state = "normal"
            cam.since = time.time()
            cam.reset_conditions()
            cam.verification = None
            cam.boost_until = 0.0
            cam.signal = "live"
            cam.baseline_hist.clear()
        # One extra engine on a synthetic source; existing engines untouched.
        proc = start_engine(DRILL_CAM_ID, f"sim://{scenario}", loop=False)
        self.procs[DRILL_CAM_ID] = proc
        self.started_at = now_iso()
        at = self.started_at
        db.save_action(at, "operator", "drill_start", DRILL_CAM_ID,
                       f"scenario={scenario}")
        self._emit({
            "type": "drill", "status": "started", "scenario": scenario,
            "camera_id": DRILL_CAM_ID, "at": at,
        })
        log.info("drill %s started on %s (pid %s)", scenario, DRILL_CAM_ID, proc.pid)
        return self.status()

    # ------------------------------------------------------------------- stop

    def stop(self, *, emit_event: bool = True) -> dict[str, Any]:
        for cid, proc in list(self.procs.items()):
            stop_engine_proc(proc, cid)
        self.procs = {}
        if self.signalwatch is not None:
            self.signalwatch.forget(DRILL_CAM_ID)
        if hasattr(self.agent, "forget_camera"):
            self.agent.forget_camera(DRILL_CAM_ID)
        config.unregister_camera(DRILL_CAM_ID)
        if self.scenario and emit_event:
            at = now_iso()
            db.save_action(at, "operator", "drill_stop", DRILL_CAM_ID,
                           f"scenario={self.scenario}")
            self._emit({
                "type": "drill", "status": "stopped", "scenario": self.scenario,
                "camera_id": DRILL_CAM_ID, "at": at,
            })
            log.info("drill %s stopped", self.scenario)
        self.scenario = None
        self.started_at = None
        return self.status()

    # ------------------------------------------------------------------ utils

    def _emit(self, event: dict) -> None:
        try:
            self.emit(event)
        except Exception:
            log.exception("drill broadcast failed")
