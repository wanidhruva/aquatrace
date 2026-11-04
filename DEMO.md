# AquaTrace — 5-minute demo run-of-show

One terminal:

```bash
python3 scripts/start.py
```

First run renders the clips (~1 min), then everything is live. Open the console at
**http://localhost:5173** (site at **:5174**).

## 0:00 — the pitch (console open on the camera wall)

"A city already owns thousands of CCTV cameras. AquaTrace turns them into a water-level
sensor network — no new hardware — and an agent that **verifies before it wakes anyone up**."

Point at: four live camera tiles with annotated frames, the state chips
(`nominal / watch / escalating / verifying / critical`), and the teal waterline + mask
drawn on each frame.

## 0:45 — `normal` (already autostarted)

Confidence sits ~0.88–0.93, expansion rate hugs the learned baseline, **no alerts**.
The baseline is learned per camera: median of `|Δratio|` over the trailing 5 minutes.

## 1:15 — the hard case: `gradual_rise`

```bash
python3 scripts/run_demo.py --scenario gradual_rise --hold 150
```
(or hit **Gradual rise** in the console's demo panel)

Watch, in order:

1. **cam-01 → WATCH** (~40 s): `Water area expanding 6.0x faster than 5-min baseline …`
2. **→ ESCALATING**: 3× gate sustained 10 s.
3. **→ VERIFYING**: confidence is only **0.75 < 0.80** — rain is degrading the
   contour. The agent raises sampling to 10 Hz and **queries cam-02**.
4. **→ CRITICAL**: *"adjacent cam-02 water area +136.9% over 30 s (anomaly 3.6x) →
   corroborates the rise"* — method `adjacent_cameras`.

Open the critical alert in the evidence panel: annotated frame at the freeze moment, the
full reasoning trace, the corroboration card, telemetry sparkline, transition timeline.

## 3:00 — the easy case: `flash_surge`

```bash
curl -X POST http://localhost:8000/simulate/flash_surge
```

Instant jump → confidence **0.69 < 0.80**, so the agent **verifies**: 10 Hz sampling
lifts confidence to **0.82 ≥ 0.80** → Critical, method `high_rate_sampling`.
Two alerts, two different verification resolutions — same agent.

## 4:00 — operator actions

* **Acknowledge** the critical alert (status → acknowledged, audit trail keeps the note).
* **False positive** → the agent explains it just raised cam-01's baseline 1.7× for 10 min.
* **Export** → printable incident report (`Cmd/Ctrl-P` prints only the report).

## Recovery (say this out loud while it happens)

After 60 s calm the agent steps **down exactly one level** —
CRITICAL → ESCALATING → WATCH → NORMAL — never a straight reset. Alerts stay in the feed
for review.

## Contract check

Payload shapes are frozen in `contracts/*.schema.json`; the API rejects anything that
drifts (`extra="forbid"` → 422).

> If a port is busy: `lsof -nP -iTCP:5173 -sTCP:LISTEN` and stop the other process,
> or run `python3 scripts/start.py --no-site`.
