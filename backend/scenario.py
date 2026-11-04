"""Demo scenario orchestration.

POST /simulate/{scenario} spawns one vision-engine process per camera with the
matching synthetic clip, re-seeds a calm baseline so the agent starts from a
learned "normal", and broadcasts a scenario event to every connected client.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional

from . import config
from .stores import now_iso, timeseries

log = logging.getLogger("aquatrace.scenario")

# camera -> (clip, loop?)
PLAN: dict[str, dict[str, tuple[str, bool]]] = {
    "normal": {cid: ("normal.mp4", True) for cid in config.CAMERA_IDS},
    "gradual_rise": {
        "cam-01": ("gradual_rise.mp4", False),
        "cam-02": ("gradual_rise_secondary.mp4", False),
        "cam-03": ("normal.mp4", True),
        "cam-04": ("normal.mp4", True),
    },
    "flash_surge": {
        "cam-01": ("flash_surge.mp4", False),
        "cam-02": ("normal.mp4", True),
        "cam-03": ("normal.mp4", True),
        "cam-04": ("normal.mp4", True),
    },
}

SCENARIOS = list(PLAN.keys())

# Engine subprocesses POST telemetry here; overridable so the API and the
# engines don't have to live on the same port/host.
BACKEND_URL = os.environ.get("AQUATRACE_BACKEND", "http://127.0.0.1:8000")

_LOGDIR = os.path.join(config.DATA_DIR, "logs")


def resolve_source(source: str) -> str:
    """Map a camera source to something the vision engine can open.

    sim://<scene> -> assets/scenes/<scene>.mp4 (falls back to normal.mp4 when
    the named scene does not exist). Anything else (rtsp:// URL, file path)
    passes through unchanged.
    """
    if source.startswith("sim://"):
        name = source[len("sim://"):]
        cand = os.path.join(config.SCENE_DIR, f"{name}.mp4")
        if os.path.isfile(cand):
            return cand
        return os.path.join(config.SCENE_DIR, "normal.mp4")
    return source


def start_engine(camera_id: str, source: str, loop: bool = False) -> subprocess.Popen:
    """Spawn one vision-engine process for camera_id on source.

    Shared by the demo ScenarioRunner, the live-mode registry startup, and
    drill engines — one pipeline, swappable input.
    """
    video = resolve_source(source)
    cmd = [
        sys.executable, "-m", "vision.engine",
        "--camera", camera_id,
        "--video", video,
        "--backend", BACKEND_URL,
        "--hz", "5",
    ]
    if loop:
        cmd.append("--loop")
    os.makedirs(_LOGDIR, exist_ok=True)
    log_path = os.path.join(_LOGDIR, f"{camera_id}.log")
    fh = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        cwd=config.ROOT,
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    fh.close()
    log.info("spawned engine %s pid=%s source=%s", camera_id, proc.pid, source)
    return proc


def stop_engine_proc(proc: subprocess.Popen, camera_id: str = "?") -> None:
    """Terminate one engine process, escalating to kill after 3 s."""
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        if proc.wait(timeout=3.0) is None:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    log.info("stopped engine %s", camera_id)


def scenes_ready() -> bool:
    need = {"normal.mp4", "gradual_rise.mp4", "gradual_rise_secondary.mp4", "flash_surge.mp4"}
    have = set(os.listdir(config.SCENE_DIR)) if os.path.isdir(config.SCENE_DIR) else set()
    return need.issubset(have)


def render_missing(quiet: bool = False) -> None:
    if scenes_ready():
        return
    sys.path.insert(0, config.ROOT)
    from vision.synth import render_all  # local import: needs vision/ on path

    log.info("rendering synthetic demo scenes (one-time, ~60s)…")
    render_all(quiet=quiet)
    log.info("scenes ready")


class ScenarioRunner:
    def __init__(self, emit: Callable[[dict], Any], agent: Any, alerts: Any, snapshots: Any) -> None:
        self.emit = emit
        self.agent = agent
        self.alerts = alerts
        self.snapshots = snapshots
        self.procs: dict[str, subprocess.Popen] = {}
        self.current: Optional[str] = None
        self.started_at: Optional[str] = None
        os.makedirs(_LOGDIR, exist_ok=True)

    # ---------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        return {
            "scenario": self.current,
            "running": bool(self.procs),
            "started_at": self.started_at,
            "cameras": {cid: {"pid": p.pid, "alive": p.poll() is None}
                        for cid, p in self.procs.items()},
            "scenes_ready": scenes_ready(),
        }

    # ----------------------------------------------------------------- start

    async def start(self, scenario: str, reseed: bool = True) -> dict[str, Any]:
        if scenario not in PLAN:
            raise ValueError(f"unknown scenario; expected one of {SCENARIOS}")

        # scenes may still be rendering on a cold boot
        deadline = time.time() + 180
        while not scenes_ready() and time.time() < deadline:
            await asyncio.sleep(1.0)
        if not scenes_ready():
            raise RuntimeError("demo scenes are not ready yet")

        # stop_engine_proc waits up to 3 s per engine (~12 s for four): never
        # run that on the event loop, or every response stalls and clients
        # (run_demo, the console WebSocket) time out mid-switch.
        await asyncio.to_thread(self.stop, emit_stopped=False)
        await asyncio.sleep(0.4)

        if reseed:
            at = now_iso()
            for cid in config.CAMERA_IDS:
                timeseries().reset_and_seed(cid)
                self.agent.cameras[cid].state = "normal"
                self.agent.cameras[cid].since = time.time()
                self.agent.cameras[cid].reset_conditions()
                self.agent.cameras[cid].verification = None
                self.agent.cameras[cid].boost_until = 0.0
                self.agent.cameras[cid].signal = "live"
                # The learned baseline belongs to the scenario that produced
                # it. Carrying rise-inflated medians into the next scenario
                # keeps anomaly permanently below the 5x critical gate, so a
                # chained run (normal -> gradual_rise -> flash_surge) could
                # never reach Critical.
                self.agent.cameras[cid].baseline_hist.clear()
                # Resolve any alert the previous scenario left open so the new
                # scenario's transitions never append to the old record.
                prev = self.alerts.active_for(cid)
                if prev is not None:
                    prev["status"] = "resolved"
                    prev["resolution_note"] = "scenario changed"
                    prev["closed_at"] = at
                    prev["updated_at"] = at
                    prev["transitions"].append({
                        "from": prev.get("state", "normal"),
                        "to": prev.get("state", "normal"),
                        "at": at,
                        "reason": "scenario changed — alert auto-resolved",
                    })
                    try:
                        from . import db
                        db.save_alert(prev)
                    except Exception:
                        log.exception("alert persistence failed on scenario change")
                    self._emit({"type": "alert", "at": at, "action": "updated", "alert": prev})

        plan = PLAN[scenario]
        for cid, (clip, loop) in plan.items():
            self._spawn(cid, clip, loop)

        self.current = scenario
        self.started_at = now_iso()
        event = {
            "type": "scenario",
            "scenario": scenario,
            "status": "started",
            "cameras": list(plan.keys()),
            "at": self.started_at,
        }
        self._emit(event)
        log.info("scenario %s started (backend %s) on %s", scenario, BACKEND_URL, ", ".join(plan))
        return self.status()

    def _spawn(self, camera_id: str, clip: str, loop: bool) -> None:
        video = os.path.join(config.SCENE_DIR, clip)
        self.procs[camera_id] = start_engine(camera_id, video, loop)

    # ------------------------------------------------------------------ stop

    def stop(self, *, emit_stopped: bool = True) -> None:
        """Terminate the engine processes.

        emit_stopped=False suppresses the "scenario/stopped" WS event — used
        when start() stops the previous scenario internally, so clients only
        see the user-invoked stop flap.
        """
        for cid, proc in list(self.procs.items()):
            stop_engine_proc(proc, cid)
        self.procs = {}
        if self.current and emit_stopped:
            self._emit({
                "type": "scenario", "scenario": self.current, "status": "stopped",
                "cameras": [], "at": now_iso(),
            })
        self.current = None
        self.started_at = None

    def render_async(self) -> None:
        """Warm the scene cache off the event loop."""
        if scenes_ready():
            return
        threading.Thread(target=render_missing, daemon=True).start()

    def _emit(self, event: dict) -> None:
        try:
            res = self.emit(event)
            if asyncio.iscoroutine(res):
                asyncio.ensure_future(res)
        except Exception:
            log.exception("scenario broadcast failed")
