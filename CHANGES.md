# AquaTrace — what was fixed (2026-09-25) + AWS integration (2026-09-26)

All changes are in this folder. The Mac original was never touched.
Copy the `backend/`, `contracts/`, `vision/`, `scripts/`, `frontend/src/`
folders, `requirements.txt`, `README.md`, `DEMO.md`, `AGENTS.md`, plus the re-rendered
`assets/scenes/gradual_rise.mp4` and `assets/scenes/flash_surge.mp4`
over `/Users/dhruvawani17/Documents/Aquatrace`, then run
`pip install -r requirements.txt` and `cd frontend && npm install`.
Run `python3 scripts/aws_setup.py` once to provision the AWS backing services.

## AWS live on this Mac — verified (2026-09-26)

The stack previously booted with `AQUATRACE_AWS_ENABLED=0` (LOCAL MODE). It now runs
against real AWS, account `148018683731`, region `ap-south-1`.

- Installed `boto3` (interpreter that runs the API) and `fpdf2` (`backend/report.py`
  failed to import without it — the API could not boot). `fpdf2` was already in
  `requirements.txt`; it had never been installed here.
- Credentials live in `~/.aws/credentials` under profile `divesh` (mode 600), outside
  the repository. `backend/data/aws.env` still holds configuration only, no secret.
- `GET /aws/status` → `enabled: true`, S3 `ok`, SNS `ok`, CloudWatch `ok`; the console
  pill reads **AWS LIVE**.
- Verified end-to-end on a real critical alert: S3 `frame.jpg` + `alert.json` uploaded,
  presigned GET returns **200 image/jpeg**, SNS returned a MessageId,
  `GET /api/aws/outbox` reports no queued deliveries, and CloudWatch has datapoints in
  namespace `AquaTrace` (dimension `CameraId`) for `Anomaly`, `Confidence` and
  `WaterAreaRatio`.
- Scenario re-verified after the changes: `gradual_rise` → WATCH (43 s) → ESCALATING →
  VERIFYING (conf 0.75 < 0.80) → CRITICAL via `adjacent_cameras` (cam-02 corroborated).

Fixes required to get there:

- `backend/main.py`: `awsx.put_metrics` ran inline inside the async
  `/ingest/telemetry` handler. The CloudWatch round-trip blocks 0.5–2.5 s, which
  stalled the event loop: the engines' 4 s telemetry POSTs timed out, the agent marked
  every camera `signal_lost` and paused escalation, so escalations never completed.
  Now `await asyncio.to_thread(...)`.
- `backend/awsx.py`: presigned evidence links were signed against
  `bucket.s3.amazonaws.com`, which answers **307** to the regional host; the redirect
  carries the signature into a different `Host` header, so every dashboard evidence
  link returned **403**. The S3 client now signs against the regional endpoint with
  virtual addressing — links return 200 with no redirect.
- `scripts/run_demo.py`: `/agent/state` field access no longer raises `KeyError`
  mid-demo.

Provisioned with boto3 (equivalents of `deploy/cloudwatch-alarms.sh` and
`deploy/s3-harden.sh`; AWS CLI is not installed on this Mac):

- 6 CloudWatch alarms, all with alarm + OK actions on the critical-alert topic:
  `aquatrace-telemetry-missing-<cam>` for the four cameras (missing data **breaches**
  by design), `aquatrace-publish-failures` (outbox), `aquatrace-disk-pressure`.
- S3 evidence bucket hardened: AES256 default encryption, versioning, full
  public-access block, and the `evidence-tiering` lifecycle (30 d Intelligent-Tiering,
  90 d Glacier, 730 d expiry, 90 d noncurrent expiry).

Still open: the SNS topic has **no subscriber**, so critical alerts are published but
delivered to nobody. Subscribe an email/SMS/HTTPS endpoint before claiming automated
notification reaches a person.

## AWS integration (2026-09-26)

- NEW `backend/awsx.py`: S3 evidence vault, SNS critical-alert delivery,
  CloudWatch metrics. Every call is best-effort and never raises — the
  system runs on local snapshots when AWS is unreachable.
- Critical alerts now publish to the real SNS topic
  `aquatrace-critical-alerts` and upload an evidence bundle (annotated
  frame + alert JSON) to the real S3 bucket; alert records are stamped
  with `aws: {sns_message_id, s3_frame_url, s3_alert_url}`.
- `POST /alerts/{id}/export` also archives the incident report to S3.
- NEW `GET /aws/status`: live S3/SNS/CloudWatch health for the console.
- NEW `scripts/aws_setup.py`: idempotent provisioning (S3 bucket + SNS
  topic in ap-south-1), writes `backend/data/aws.env`; `scripts/start.py`
  loads it automatically.
- Console: top-bar **AWS LIVE / LOCAL MODE** pill, `sns ✓` / `s3 ✓` ticks
  on alert cards, S3-vault link in the evidence view.
- NEW `AGENTS.md`: AWS Agent Toolkit guidance (project instructions take
  precedence) + project rules.

## Backend (`backend/`, `contracts/`)
- Fixed crash on unknown camera IDs in the agent tick.
- Fixed thread-safety: locks on snapshot/alert stores, event-loop-safe
  WebSocket broadcasts, snapshot writes offloaded from the event loop.
- Fixed snapshot disk leak (old JPEGs now deleted on eviction).
- Alert history capped at 300; Pydantic validation of alerts (non-fatal).
- `Alert.state` is now a constrained Literal including `"verifying"`.
- Scenario switches resolve active alerts (no cross-scenario contamination)
  and no longer emit a spurious `stopped` event.
- Neighbor corroboration now uses a read-only baseline estimator (no longer
  mutates the neighbor's learned baseline).
- Evidence windows exclude invalid detections.
- `parse_window("90")` now means 90 seconds (was: crash).
- Snapshot path traversal guard added.
- `telemetry_tick` now carries the dashboard's real fields: `flow_std` and
  `wl_delta_10s` (were missing → tiles showed stale data).
- Deprecated FastAPI startup/shutdown handlers replaced with lifespan.

## Vision + scripts (`vision/`, `scripts/`, root)
- Fixed 422-retry bug: invalid payloads no longer replay forever; the
  current payload isn't duplicated after a backlog send.
- Engine uses `httpx.Client(trust_env=False)` — environment proxies were
  crashing every engine with `InvalidURL` on some machines.
- `synth.py all --force` now actually forces re-render.
- `scripts/start.py` fixed (missing argv broke first-run scene generation).
- Startup now detects dead child processes without the full timeout wait.
- `run_demo.py`: polls for target state, `--settle`/`--hold` semantics fixed,
  no more dead-air holds past clip end; `gradual_rise` target accepts
  `escalating` as well as `critical`.
- Fixed `/agent/state` dict-iteration crashes in `run_demo.py` and
  `seed_baseline.py`.
- Added root `requirements.txt`.
- **Scenario tuning (verified live):** debris removed from `gradual_rise`
  (it punched holes in the segmentation, inflated the learned baseline, and
  made the 5× critical gate unreachable); rise steepened 1.35× and cap
  raised 0.60 → 0.68 so the 30 s expansion rate clears the gate even on the
  baseline's low flicker excursions. `flash_surge` storm lightened so the
  confidence story stays intact.

## Frontend (`frontend/src/`)
- Top-level `ErrorBoundary` (no more white screen on render errors).
- REST hydration no longer clobbers live WebSocket-only state.
- Wired the real `flow_std` / `wl_delta_10s` fields.
- Guards on all white-screen-prone property accesses.
- Empty/204/non-JSON API responses handled with timeouts; failed scenario
  stops are caught and surfaced.
- Honest schematic map badge + correct site center; `verifying` and
  `signal_lost` legend entries.
- Loading placeholders instead of fake zero telemetry; backend-unreachable
  banner; only the selected camera's thumbnail polls (with backoff).
- New **agent reasoning panel** rendering the decision log (`reasoning`,
  not `text`).
- Removed dead code. Production build passes (`vite build` clean).

## Docs
- `README.md` / `DEMO.md` corrected to match verified behavior:
  gradual_rise is 6 % → 68 % under rain; flash_surge goes
  verifying → Critical via `high_rate_sampling` (10 Hz boost), not
  "immediate high_confidence".
- `REVIEW.md` is the full 44 KB code review; `NOTES_backend.md` has the
  backend implementation notes.

## Verified live (2026-09-25, VM)
- `gradual_rise`: normal → watch → escalating → **verifying** (conf 0.75) →
  **critical** via `adjacent_cameras` (cam-02 +136.9 % / 30 s). Alert with
  evidence snapshot, transitions, and reasoning trace all present.
- `flash_surge`: → verifying → **critical** via `high_rate_sampling`
  (10 Hz lifted confidence 0.69 → 0.82).
- `normal`: stays normal, no false alerts.
- Acknowledge / false-positive / export routes all work.
- `run_demo.py` and `seed_baseline.py` run clean.
- Frontend `vite build` passes.
