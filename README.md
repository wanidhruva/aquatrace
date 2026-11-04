# AquaTrace

**Visual intelligence for flood detection.** AquaTrace turns the CCTV cameras a city
already owns into a distributed water-level sensor network: an OpenCV pipeline measures
the waterline frame by frame, an agentic decision loop corroborates every anomaly before
anyone is woken up, and operators get annotated evidence behind each call.

```
┌──────────────┐  telemetry 5 Hz  ┌───────────────────────────┐   WebSocket   ┌──────────────────┐
│ vision.engine│ ───────────────► │ FastAPI  + decision agent │ ─────────────► │ React console    │
│ per camera   │ ◄─────────────── │ baseline · hysteresis     │  ticks/events  │ 5173             │
│ (OpenCV)     │   control  + Hz  │ verify · alerts · audit   │               │ 5174  marketing  │
└──────────────┘                  └───────────────────────────┘               └──────────────────┘
```

## Quick start

```bash
pip install -r requirements.txt            # Python deps (FastAPI, OpenCV 5, …)
python3 scripts/start.py                    # backend + console + site
python3 scripts/start.py --demo gradual_rise
```

| service | url |
|---|---|
| operator console | http://localhost:5173 |
| marketing site   | http://localhost:5174 |
| API + WebSocket   | http://localhost:8000  (`/docs` for OpenAPI) |

First run renders the synthetic scenes (~1 min). Then the calm scenario autostarts so a
cold boot is live immediately.

Manual start, if you prefer:

```bash
python3 -m uvicorn backend.main:app --port 8000      # terminal 1
(cd frontend && npm install && npm run dev)          # terminal 2
(cd site     && npm install && npm run dev)          # terminal 3
```

## The decision loop

Every camera, every second:

1. **Features** — expansion rate = OLS slope of water-area ratio over 30 s, compared
   against a **learned baseline**: the median of `|Δratio|` taken 2 s apart over the
   trailing 5 minutes of that camera (floored at `0.0004` ratio/s). `anomaly = rate / baseline`.
2. **Hysteresis** — `normal → watch → escalating → verifying → critical`, each gate armed
   on its rising edge and held while the condition stays true (`2× / 3× / 5×` anomaly,
   sustained `10 s / 10 s / 6 s`). Recovery steps **down one level** after 60 s calm —
   never a straight reset.
3. **Confidence gate** — the vision engine fuses contour stability, optical-flow coherence
   and detection validity into one score; the agent smooths it over 10 s:

   ```
   conf = 0.45·stability + 0.20·flow_coherence + 0.35·valid_frac
   agent = 0.85·mean_10s(conf) + 0.15·valid_frac
   ```

   * `agent ≥ 0.80` → Critical immediately, recorded as `high_confidence`.
   * `agent < 0.80`  → **Verifying**: hold a 5 s evidence window, query adjacent cameras
     (neighbour must show `+10 %` over 30 s **and** anomaly `≥1.5×`), raise sampling to
     10 Hz, and fall back to escalating on 15 s timeout-with-persistence.
4. **Audit** — every transition writes a plain-English reasoning string, the frozen
   annotated frame, the telemetry window and the corroboration result into the alert.

False-positive feedback: marking an alert a false positive raises that camera's baseline
`1.7×` for 10 minutes.

## Demo scenarios

| scenario | what you should see |
|---|---|
| `normal` | all four cameras Nominal, confidence ~0.93, no alerts |
| `gradual_rise` | cam-01 climbs 6 % → 68 % **under rain**: confidence drops to ~0.7, the agent enters **Verifying**, cam-02 corroborates `+137 % / 30 s`, then Critical with `adjacent_cameras` evidence |
| `flash_surge` | cam-01 jumps instantly: confidence ~0.7 triggers **Verifying**, 10 Hz sampling lifts it past 0.80 → Critical via `high_rate_sampling` |

```bash
curl -X POST http://localhost:8000/simulate/gradual_rise
curl -X POST http://localhost:8000/simulate/flash_surge
curl -X POST http://localhost:8000/simulate/normal
curl -X POST http://localhost:8000/simulate/stop
```

## API surface

```
GET  /health                      GET  /cameras
GET  /cameras/{id}/frame.jpg      GET  /cameras/{id}/control
GET  /telemetry/{id}?window=5m    GET  /alerts ? /alerts/{id}
POST /alerts/{id}/acknowledge     POST /alerts/{id}/false-positive
POST /alerts/{id}/export          GET  /agent/log  /agent/state
POST /simulate/{scenario}         POST /simulate/stop   GET /simulate
GET  /snapshots/{file}            WS   /stream
POST /ingest/telemetry            (vision engine → backend, 5 Hz)
      (hello · telemetry_tick · state_change · alert · camera_status · scenario)
```

Contracts in `contracts/*.schema.json` are the single source of truth for telemetry,
alert and state-change payloads; the pydantic mirror rejects anything that drifts
(`extra="forbid"` → 422).

## Layout

```
vision/     synth.py  deterministic scene generator (calm / rise / surge, rain)
            engine.py per-camera OpenCV pipeline + telemetry client
backend/    main.py   FastAPI, WebSocket hub, routes
            agent.py  decision loop, verification, alerts, audit log
            awsx.py   AWS layer: S3 evidence vault, SNS critical alerts,
                      CloudWatch metrics (all degrade to local on failure)
            scenario.py scenario → vision subprocess orchestration
            stores.py time-series / snapshot / alert stores (seeded baselines)
            config.py registry + every tuning constant
frontend/   operator console (Vite + React 18 + Tailwind 4)
site/       marketing site (Vite + React 18)
contracts/  frozen JSON schemas
scripts/    start.py · run_demo.py · seed_baseline.py · aws_setup.py
assets/     hero video, poster, rendered scenes
```

## AWS backing services (live, with local fallback)

Provision once with `python3 scripts/aws_setup.py` (creates the S3 evidence
bucket + the SNS critical-alert topic in `ap-south-1`, and writes
`backend/data/aws.env`, which `scripts/start.py` loads automatically):

* **Amazon S3** — every critical alert's evidence bundle (annotated frame +
  alert JSON) and every exported incident report lands in the evidence vault;
  the console links straight to the presigned S3 copy.
* **Amazon SNS** — critical alerts are published to
  `aquatrace-critical-alerts`; subscribe an email/SMS to get woken up.
* **Amazon CloudWatch** — per-camera `WaterAreaRatio`, `Anomaly`,
  `Confidence`, `FlowMean` metrics under the `AquaTrace` namespace.
* `GET /aws/status` reports live S3/SNS/CloudWatch health; the console shows
  **AWS LIVE** vs **LOCAL MODE** in the top bar.

Everything is best-effort: if AWS is unreachable (or `AQUATRACE_AWS_ENABLED=0`),
the system keeps running on local snapshots with zero errors — the demo
survives dead venue Wi-Fi.

## Tuning knobs (`backend/config.py`)

`WATCH/ESCALATE/CRITICAL_THRESHOLD` · `SUSTAIN_*_S` · `RECOVER_S` ·
`VERIFY_MIN_CONF` · `VERIFY_MIN_HOLD_S` · `VERIFY_TIMEOUT_S` ·
`CORROBORATION_MIN_PCT` · `BASELINE_FLOOR` · `FP_BOOST`

## Troubleshooting

* **ports busy** — `lsof -nP -iTCP:8000 -sTCP:LISTEN` (likewise 5173/5174), or edit them.
* **scenes missing/stale** — `python3 -m vision.synth all --force` re-renders every clip.
* **engines not running** — they are spawned per scenario; `GET /simulate` shows the plan
  and pids, logs live in `backend/data/logs/cam-*.log`.
* **dashboard shows no thumbnails** — frames appear ~1 s after the first telemetry arrives;
  check `backend/data/snapshots/`.
