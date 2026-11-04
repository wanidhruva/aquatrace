#!/usr/bin/env python3
"""Inspect (or re-seed) the per-camera learned baseline.

    python3 scripts/seed_baseline.py            # show what the agent has learned
    python3 scripts/seed_baseline.py --reseed   # restart the calm scenario (re-plants seeds)

The stores are in-memory and seeded deterministically at API startup
(see backend/stores.py); --reseed replays the calm plan so a fresh baseline
window grows again under real telemetry.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request

API = os.environ.get("AQUATRACE_BACKEND", "http://127.0.0.1:8000")


def get(path):
    with urllib.request.urlopen(API + path, timeout=5) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reseed", action="store_true", help="POST /simulate/normal")
    args = ap.parse_args()

    if args.reseed:
        with urllib.request.urlopen(urllib.request.Request(f"{API}/simulate/normal", method="POST"),
                                    timeout=60) as r:
            body = json.loads(r.read())
        print("reseeded:", json.dumps(body, indent=2))
        return 0

    st = get("/agent/state")
    print(f"{'camera':<8} {'state':<11} {'baseline':>10} {'rate':>10} {'anomaly':>8} {'conf':>6}")
    # /agent/state returns "cameras" as a dict {cid: {...}}, not a list.
    for c in st["cameras"].values():
        print(f"{c['camera_id']:<8} {c['state']:<11} "
              f"{c.get('baseline_rate', 0):>10.5f} {c.get('expansion_rate', 0):>10.5f} "
              f"{c.get('anomaly', 0):>7.2f}x {c.get('confidence') or 0:>6.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
