# AquaTrace backend fixes — 2026-09-25

All changes are in `backend/` + `contracts/`. All 17 REST routes and WS event
envelopes are behavior-identical unless noted.

## Contracts
- `contracts/alert.schema.json`: `"verifying"` added to the `state` enum
  (`["normal","watch","escalating","verifying","critical"]`). The agent already
  emits `state:"verifying"`; strict consumers no longer reject those alerts.
- `contracts/state_change.schema.json`: `"hello"`, `"ping"`, `"agent_note"`
  added to the `type` enum with minimal `oneOf` branches
  (`hello`/`ping`: `type`+`at`; `agent_note`: `type`+`camera_id`+`at`). The dead
  `"frame"` enum member was removed — it is never emitted anywhere in the code.

## New telemetry_tick fields (for the frontend worker)
Each `telemetry_tick` now carries two extra fields computed from the
`TimeSeriesStore` of that camera:
- `flow_std` (number, rounded to 3 dp) — population std-dev of the vision
  engine's per-tick `flow_std` values over the trailing 30 s of **valid** rows;
  `0.0` when there is no history. (Note: this is the *spread* of the engine's
  `flow_std` readings, not the per-tick `flow_std` — the engine still sends its
  own per-tick `flow_std` too.)
- `wl_delta_10s` (number, rounded to 2 dp, pixels) — latest `waterline_y` minus
  `waterline_y` ~10 s earlier; `0.0` when < 2 rows of history.
- The previously-dead dashboard tiles (`flow_std`, `wl_delta_10s`) should now
  receive data.

## backend/agent.py
- `tick()` returns `None` (with a warning log) on an unknown `camera_id`
  instead of raising `KeyError` — consistent with `camera_state`'s offline stub.
- `_verify()` now uses the new `_baseline_readonly()` for neighbours: same
  median-of-|Δratio| math, but it never appends to the neighbour's
  `baseline_hist` and never touches `fp_mult`/`fp_until`.
- Evidence window is now `store.query(camera_id, 90.0, valid_only=True)` —
  invalid detections (ratio 0.0) are no longer charted as real data.
- Alert records are validated with `Alert.model_validate()` in a try/except at
  both creation and update: a warning is logged on failure, but the raw dict is
  kept so a validation bug can never crash the agent.
- `models.Alert.state` is now
  `Literal["normal","watch","escalating","verifying","critical"]` (imported in
  agent.py).
- `base_flow_state` renamed to `latest_ratio` (it was the latest ratio, not a
  baseline).

## backend/main.py
- `@app.on_event("startup"/"shutdown")` replaced with a `lifespan` async
  context manager on the `FastAPI` app; `asyncio.get_event_loop()` replaced with
  `asyncio.get_running_loop()`.
- `Hub` captures the running loop in lifespan startup (`hub.loop`); 
  `Hub.broadcast_sync` now uses `loop.call_soon_threadsafe(_drop_oldest_put, q,
  payload)` — safe when called from worker threads (sync endpoints). Falls
  back to direct put if the loop is not captured/closed.
- `ingest_telemetry`: base64 decode + JPEG disk write moved to
  `asyncio.to_thread` (off the event loop).
- `GET /snapshots/{path:path}`: traversal guard — key is normpath'd and the
  resolved path must stay under the snapshot root, else 400; missing file 404.
  (Also fixes the double file read — serves via `FileResponse` directly.)

## backend/stores.py
- `SnapshotStore` and `AlertStore` now have `threading.Lock`; all public
  mutators/readers are guarded.
- `SnapshotStore` eviction deletes the evicted JPEG files from disk (new
  `ObjectStorage.delete`) — the snapshot dir no longer grows without bound.
- `AlertStore` capped at 300 alerts, oldest evicted first.

## backend/scenario.py
- `start(reseed=True)` resolves any currently-active alerts first:
  `status="resolved"`, `resolution_note="scenario changed"`, `closed_at`/
  `updated_at` stamped, a transition appended, and an `alert`/`updated` WS
  event emitted — new scenarios no longer append to the old alert record.
- `stop()` gained a keyword-only `emit_stopped=True`; internal calls from
  `start()` pass `emit_stopped=False`, so only user-invoked stops emit
  `scenario/stopped`.
- Engine subprocesses read the backend URL from env `AQUATRACE_BACKEND`
  (default `http://127.0.0.1:8000`); the effective URL is logged at scenario
  start.

## backend/config.py
- `parse_window`: bare numbers are now seconds (`unit = w[-1] if w[-1] in
  "smh" else "s"; value = float(w[:-1]) if w[-1] in "smh" else float(w)`), so
  `?window=90` → 90 s, not 90 min.

## Verification
- `py_compile` passed for all six edited files (venv
  `~/workspace/aquatrace-venv/bin/python`).
- All three JSON contracts parse.
- Smoke-tested: parse_window (`90`→90s, `5m`→300s, `2h`, `45s`, default),
  `Alert.model_validate` accepts `state:"verifying"`, `tick("unknown")` →
  `None`, `_baseline_readonly` leaves neighbour `baseline_hist`/`fp_*`
  untouched, `AlertStore` caps at 300 (oldest evicted), `SnapshotStore`
  eviction deletes files (3 kept of 5), `ScenarioRunner.stop` has
  `emit_stopped`, lifespan wired with no `@app.on_event`/`get_event_loop`
  left.
