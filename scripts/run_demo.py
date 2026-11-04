#!/usr/bin/env python3
"""Run the scripted AquaTrace demo: normal → gradual_rise → flash_surge.

    python3 scripts/run_demo.py            # needs the API on :8000
    python3 scripts/run_demo.py --scenario gradual_rise --hold 120

Prints every agent transition as it happens so it can drive a live pitch.

Each step ends early once the scenario's target state has been reached and
held for --settle seconds; --hold is the absolute maximum per step (the fixed
holds of the old script outlasted the synthetic clips, so engines EOF-held
static frames while the agent recovered mid-demo).
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

API = os.environ.get("AQUATRACE_BACKEND", "http://127.0.0.1:8000")
SEQUENCE = ["normal", "gradual_rise", "flash_surge"]
MAX_HOLDS = {"normal": 45, "gradual_rise": 120, "flash_surge": 120}
TARGET_STATE = {"normal": "normal", "gradual_rise": "escalating", "flash_surge": "critical"}
STATE_ORDER = ["normal", "watch", "escalating", "critical"]


def rank(state: str) -> int:
    """Severity rank: 'verifying' counts as 'escalating', unknown as 'normal'."""
    s = "escalating" if state == "verifying" else state
    return STATE_ORDER.index(s) if s in STATE_ORDER else 0


def get(path: str, timeout: float = 20.0):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read())


def post(path: str, timeout: float = 300.0):
    with urllib.request.urlopen(urllib.request.Request(API + path, method="POST"),
                                timeout=timeout) as r:
        return json.loads(r.read())


def watch_step(name: str, max_hold: int, settle: int) -> dict:
    """POST the scenario, then watch until its target state has been held for
    `settle` seconds or `max_hold` seconds elapse. Returns the last-seen
    /agent/state payload."""
    print(f"\n=== {name} — POST /simulate/{name}", flush=True)
    post(f"/simulate/{name}")
    target = TARGET_STATE.get(name, "escalating")
    seen: set[tuple] = set()
    settled_since: float | None = None
    st: dict = {"cameras": {}}
    t0 = time.time()
    while True:
        elapsed = time.time() - t0
        if elapsed >= max_hold:
            print(f"  — max hold {max_hold}s reached", flush=True)
            break
        st = get("/agent/state")
        # /agent/state returns "cameras" as a dict {cid: {...}}, not a list.
        cams = list(st["cameras"].values())
        for cam in cams:
            key = (cam.get("camera_id", "?"), cam.get("state", "normal"))
            if key not in seen and cam.get("state") != "normal":
                seen.add(key)
                print(f"  {elapsed:6.1f}s  {cam.get('camera_id', '?')} → "
                      f"{str(cam.get('state', '?')).upper():11s} conf={cam.get('confidence')} "
                      f"anom={cam.get('anomaly')}x", flush=True)
        worst = max((c.get("state", "normal") for c in cams), key=rank, default="normal")
        if rank(worst) >= rank(target):
            if settled_since is None:
                settled_since = time.time()
            elif time.time() - settled_since >= settle:
                print(f"  — target '{target}' held {settle}s "
                      f"— ending step early ({elapsed:.0f}s < {max_hold}s max)", flush=True)
                break
        else:
            settled_since = None
        time.sleep(1.0)
    return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=SEQUENCE, help="run a single scenario")
    ap.add_argument("--hold", type=int, default=None,
                    help="max seconds per step (default 120; 45 for 'normal' in the full sequence)")
    ap.add_argument("--settle", type=int, default=15,
                    help="seconds the target state must be held before a step ends early")
    args = ap.parse_args()

    steps = [(args.scenario, args.hold or 120)] if args.scenario else \
            [(s, args.hold or MAX_HOLDS[s]) for s in SEQUENCE]

    try:
        get("/health")
    except Exception:
        print("API is not reachable — start it first:  python3 scripts/start.py")
        return 1

    for name, hold in steps:
        st = watch_step(name, hold, args.settle)
        worst = max((c.get("state", "normal") for c in st["cameras"].values()),
                    key=rank, default="normal")
        print(f"  — steady state: {worst}", flush=True)

    print("\ndemo complete — acknowledge an alert in the console to export the report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
