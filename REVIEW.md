# AquaTrace — Deep Code Review

**Date:** 2026-09-25 · **Scope:** full source mirror at `~/workspace/aquatrace/` (42 files pulled read-only from `/Users/dhruvawani17/Documents/Aquatrace` on the Mac via device file tools) plus `reference/TeamMKKR.pdf` (the Knowbuild 2.0 idea doc).
**Method:** three focused deep-reads (Python backend+vision, React dashboard, docs/site/proposal) plus independent cross-checks of every REST/WS envelope against the actual backend code. Nothing was modified; no servers were started.

**Mirrored:** all `.py/.jsx/.js/.json/.md/.html/.css` source. **Skipped (by design):** `node_modules/`, `dist/`, `__pycache__/`, `package-lock.json` ×2, `backend/data/logs/` (cam-01..04.log, ~3.7 MB total), `backend/data/snapshots/cam-0{1..4}/`, binary assets (`assets/*.mp4`, `*.jpg` — names/sizes recorded only), empty `__init__.py` ×2.

**One-line verdict:** the core loop (synthetic vision → agentic decision loop → live dashboard) is genuinely built and mostly coherent, but **three crash-on-launch bugs** (two demo scripts + first-run scene rendering), **a contract violation in the alert record**, **no error boundary in a dashboard full of unguarded property access**, and **no `requirements.txt`** make this not demo-ready as-is. All fixable in a day.

---

## 1. Architecture summary (verified against README claims)

```
vision/synth.py ──renders──▶ assets/scenes/{normal,gradual_rise(+_secondary),flash_surge}.mp4
        │
backend/scenario.py ──spawns 1 subprocess per camera──▶ vision/engine.py ──POST 5 Hz──▶ POST /ingest/telemetry
        │                                                                          │
        │                                                              backend/main.py (FastAPI :8000)
        │                                                                          │  ├─ DecisionAgent.tick() @ ~1 Hz (backend/agent.py)
        │                                                                          │  ├─ stores: TimeSeriesStore (RAM) / SnapshotStore (backend/data/snapshots/) / AlertStore (RAM)
        │                                                                          │  └─ WS /stream → hello · telemetry_tick · state_change · alert · camera_status · scenario (+ undocumented ping/agent_note)
        │                                                                          │
frontend/ (Vite :5173) ◀── WS + REST ──────────────────────────────────────────────┘
site/     (Vite :5174) — static marketing site, zero backend calls
scripts/start.py — boots API + console + site; scripts/run_demo.py — scripted demo driver; scripts/seed_baseline.py — baseline inspector
contracts/ — telemetry.schema.json · alert.schema.json · state_change.schema.json
```

**What each component does (verified, not just claimed):**

- **`vision/engine.py`** (per-camera subprocess): `cv2.VideoCapture` → MOG2 background subtraction + HSV water segmentation → contour analysis (bbox / area ratio / waterline) → Farneback optical flow → per-component confidence fusion → POSTs telemetry at 5 Hz (+1 Hz annotated JPEG), polls `/cameras/{id}/control` for the 10 Hz verification boost. Surprisingly sophisticated perception code; honest docstrings.
- **`vision/synth.py`**: deterministic CCTV scene renderer (seeded `RandomState`, night-canal backdrop, analytic water-ratio curves, rain/debris overlays). The `normal` clip is engineered to loop seamlessly.
- **`backend/agent.py`** — the "agentic decision loop": per-camera ~1 Hz tick computing OLS expansion-rate slope vs. a learned 5-min baseline (`anomaly = rate/baseline`), sustain-clock arming (2×/10 s → watch, 3×/10 s → escalating, 5× → critical **iff** confidence ≥ 0.80 else → verifying), adjacent-camera corroboration + 10 Hz boost + 15 s timeout in `verifying`, 60 s step-down recovery, FP feedback (1.7× baseline for 10 min). Genuinely well-designed hysteresis; the re-arming footgun is even documented.
- **`backend/main.py`**: 17 REST routes + `WS /stream` (table in §4). `extra="forbid"` telemetry validation with a useful 422 handler.
- **`backend/stores.py`**: `TimeSeriesStore` (RAM deque, locked), `SnapshotStore` (JPEGs on disk, **unlocked**), `AlertStore` (RAM OrderedDict, **unlocked**). Header honestly states the AWS mapping is mock-only ("No AWS calls happen in the sprint build").
- **`backend/scenario.py`**: scenario→clip map, subprocess lifecycle, baseline reseed, WS scenario events. Scenario names: `normal`, `gradual_rise`, `flash_surge`.
- **`frontend/`**: React 18 + Tailwind 4 dashboard — TopBar, CameraList (1 Hz thumbnails), MetricTiles, ChartPanel (custom SVG), MapPanel (hand-drawn SVG), AlertFeed, EvidenceView (modal + printable report), SimulationPanel, Toasts. Hand-rolled `useSyncExternalStore` store with ring buffers.
- **`site/`**: standalone marketing/landing page. No `fetch`/WebSocket anywhere — fully static.
- **`scripts/`**: `start.py` boots everything; `run_demo.py` drives a scripted scenario sequence; `seed_baseline.py` prints baselines.

**README claim verification:** all 17 REST routes + `WS /stream` exist with the documented paths (verified by grepping route decorators); ports 5173/5174/8000 match the vite configs; WS event list matches except undocumented `hello`/`ping`/`agent_note`; decision-loop numbers (2×/3×/5×, 10 s/10 s, 60 s recovery, 0.80 gate, 1.7×/10 min FP) all match `config.py`. Three README claims are wrong — see §5 and the P0 list.

## 2. Per-file review — backend / vision / scripts

### `backend/agent.py` (the core; ~26 KB)
**Does well:** sustain-clock design (`_arm` — arm on rising edge, hold, disarm on fall) is correct and documented against the re-arming footgun; per-camera exceptions contained in `run()`; signal-loss holds last state instead of crashing; decisions capped at 200; docstring matches implementation.

**Bugs:**
1. **Alert `state` violates the frozen contract.** `_update_alert` writes the raw machine state into the alert record — `backend/agent.py:494` (`"state": to` in the new-alert dict) and `:514` (`alert["state"] = to` on update). When the machine enters `verifying`, the alert's `state` becomes `"verifying"`, but `contracts/alert.schema.json` restricts `state` to `["normal","watch","escalating","critical"]`. Any strict consumer (or judge running a validator) rejects verifying-state alerts. Fix: add `"verifying"` to the contract enum, or map verifying→escalating in the alert record.
2. **`_verify` pollutes the neighbor's learned baseline** (line ~341): it calls `self._baseline(nb, ...)` for each adjacent camera, which appends to the neighbor's `baseline_hist` and can reset its `fp_mult`. Baseline estimation is supposed to be per-camera/per-tick; a verifying camera now injects extra samples into its neighbor's history.
3. **Evidence window includes invalid detections** (line ~476): `raw = store.query(camera_id, 90.0)` without `valid_only=True`, so rows where vision found nothing (`water_area_ratio: 0.0`, `detection_valid: False`) are downsampled into `telemetry_window` and charted as genuine zero-water readings.
4. **Recovery during `verifying` is odd** (lines 280–281): the recovery block sits outside the state-machine chain, so a verifying camera that goes calm steps to `STEP_DOWN["verifying"] = "escalating"` without re-arming conditions. Defensible, undocumented, surprising.
5. **`tick` raises `KeyError` on unknown camera** (line 167: `cam = self.cameras[camera_id]`) while sibling `camera_state` returns an offline stub. `tick` is public; a direct caller gets an unhandled `KeyError`.
6. Misleading name (line 214): `base_flow_state = latest["water_area_ratio"]` is the *latest ratio*, not a baseline — confusing during incident debugging. Cosmetic.
7. `asyncio.ensure_future` in `_broadcast` (line 453) is dead in practice (`emit` is `hub.broadcast_sync` → returns `None`); the coroutine path is untested.

### `backend/main.py` (~15 KB)
**Does well:** `extra="forbid"` contract enforcement with a genuinely useful 422 handler (logs violation, echoes contract path); ingest validates `ts` parseability and camera-registry membership; snapshot routes 404 cleanly; `POST /simulate/stop` is registered *before* `POST /simulate/{scenario}` so it isn't swallowed by the path param (correct but fragile — reorder and it silently 404s).

**Bugs:**
1. **Thread-safety: `AlertStore` and `SnapshotStore` have no locks** while `TimeSeriesStore` does. Sync endpoints (threadpool) mutate the same alert dicts the agent's event-loop task mutates in `_update_alert` (e.g. operator acknowledges while the agent appends a transition); `AlertStore.new_id`'s `self._seq += 1` races across threads.
2. **`asyncio.Queue.put_nowait` from worker threads** — `Hub.broadcast_sync` is called from sync (threadpool) endpoints and pokes `q.put_nowait` on queues created on the event loop. Not thread-safe; `loop.call_soon_threadsafe` is the correct primitive.
3. **`/snapshots/{path:path}` has no traversal guard** — `ObjectStorage.get` does `os.path.join(self.root, key)` + `isfile` with no normalization; `GET /snapshots/../../etc/hostname` reads outside the snapshot dir. Low severity for a demo, real bug.
4. **`ingest_telemetry` does blocking disk I/O on the event loop** — `snapshots.put_jpeg_b64` (base64 decode + file write, ~1 Hz/camera) runs inline in the async handler; should be `run_in_executor`.
5. `snapshot()` reads the file twice (`storage.get(path)` loads all bytes, result discarded except the None check, then `FileResponse` reads again).
6. Deprecated `@app.on_event("startup"/"shutdown")` (lines 117, 127) and `asyncio.get_event_loop()` (lines 122–123) — warnings today, breakage risk on newer Python/FastAPI. Use `lifespan`.
7. **Import-time side effects**: importing `backend.main` creates `backend/data/snapshots/` + `backend/data/logs/` (via `ObjectStorage.__init__` and `ScenarioRunner.__init__`), instantiates the agent, calls `logging.basicConfig`.
8. Dead imports: `Optional` (line 10), `BackgroundTask` (line 16); `Transition`/`Corroboration`/`Alert` from `models.py` never instantiated — the alert contract has *no* runtime validation.
9. `camera_frame` uses `str.removeprefix` — Python 3.9+ floor.

### `backend/config.py`
**Does well:** single source of truth for thresholds; adjacency derived from the registry so it can't drift. Camera registry has `id/name/zone/lat/lng/adjacent/source` for all 4 cameras.

**Bugs:**
1. **`parse_window` misparses bare numbers** (lines ~100–111):
   ```python
   unit = w[-1] if w[-1] in "smh" else "m"
   value = float(w[:-1] if unit in w else w)
   ```
   For `window="90"`: `unit` falls back to `"m"`, and `"m" in "90"` is `False`, so `value = float("90")` → **90 minutes (5400 s)**, not 90 seconds. `GET /telemetry/cam-01?window=90` silently returns a 90-minute window. The strip condition should test the last character.
2. `VISION_MODULE = "vision.engine"` is defined but never referenced. Dead constant.
3. `SCENE_DIR` points at `assets/scenes/`, which doesn't exist in the repo — guarded everywhere it matters today, but fragile.

### `backend/models.py`
**Does well:** `Telemetry` mirrors `telemetry.schema.json` field-for-field (11 required, ts regex, bbox length 4, bounds); `extra="forbid"` is the right call.

**Issues:** `Transition`, `CorroborationCamera`, `Corroboration`, `Alert` are **dead code** — never imported or instantiated (`main.py` imports only `AckResponse, Telemetry`). And `Alert.state: str` is unconstrained, so even if used it wouldn't catch the `"verifying"`-vs-contract-enum violation.

### `backend/scenario.py`
**Does well:** clean subprocess lifecycle (terminate → 3 s grace → kill), per-camera logs, reseed resets agent state/conditions/boost/signal. Scenario names exactly: `normal`, `gradual_rise`, `flash_surge` — consistent with `synth.py`, `main.py`, `start.py`, `run_demo.py`, and the frontend's `SimulationPanel`.

**Bugs:**
1. **Reseed doesn't resolve open alerts** (lines ~93–100): `start()` resets agent camera state to `"normal"` but leaves `AlertStore` untouched — a previous scenario's `active` alert is still returned by `active_for`, so the new scenario's transitions get appended to the *old* scenario's alert record. Alert history mixes scenarios.
2. **Backend URL hardcoded** (line ~130): `--backend http://127.0.0.1:8000`. Change the API port and all engines silently fail to POST (they back off into `_pending` at debug log level).
3. `stop()` emits `scenario/stopped` even when called from `start()` immediately before `started` — clients see a spurious stopped→started flap on every scenario switch.
4. `start()` can block up to 180 s inside the request handler while rendering scenes; the frontend's `fetch` has no such patience — scenario clicks can time out client-side while the server keeps working.
5. `render_missing` does a global, permanent `sys.path.insert(0, config.ROOT)` — also from inside the daemon thread. Harmless here, hidden mutation.

### `backend/stores.py`
**Does well:** `TimeSeriesStore` locked on every op; ISO-8601 helpers consistent with engine `ts` and the contract regex; header honestly documents the AWS→local mapping.

**Bugs:**
1. **Snapshot JPEGs leak on disk.** `SnapshotStore(keep=30)` drops old *keys* from the deque but never deletes the JPEG files. At ~1 frame/s × 4 cameras, `backend/data/snapshots/` grows without bound for the life of the process.
2. **`AlertStore` grows without bound** — no eviction, no cap (unlike `decisions`, capped at 200).
3. `RETENTION_S = 900.0` is documented but **never enforced** — retention is actually `deque(maxlen=4600)` ≈ 920 s at 5 Hz but only ~460 s at 10 Hz boosted sampling. The constant lies; effective window varies with sample rate.
4. `seed_row` hand-duplicates the calm-curve math from `synth._normal_ratio` (magic numbers `0.070`, `0.0060`, …) instead of importing it — the two can drift (they currently match).

### `vision/engine.py` (~19 KB)
**Does well:** the most sophisticated code in the repo — sub-pixel waterline, MOG2 warm-up gating, percentile-clipped flow selection, per-component confidence with clear comments. EOF-hold (keep emitting static frames) is deliberate and documented. `STATE_COLORS["critical"] = (68, 68, 239)` with comment `#EF4444 (BGR)` — verified correct BGR conversion.

**Bugs:**
1. **Retry-after-422 logic is wrong** (lines 431–437):
   ```python
   if r.status_code == 422:
       log.error("[%s] telemetry rejected: %s", ...)
   while self._pending and r.status_code < 500:
   ```
   422 < 500, so after a rejection it *still* drains the pending queue — replaying payloads that will likely also 422. Worse: if a *retry* POST raises, the `except` appends the **current** payload (already POSTed successfully) to `_pending` — duplicated later. Guard should be `r.status_code < 300` (or `< 400`).
2. `waterline = self._prev_waterline or float(h)` (line 246): latent falsy-zero bug if `_prev_waterline` is ever exactly `0.0`. Can't happen with current scene geometry; still wrong idiom.
3. `skip = max(1, int(self.video_fps // hz))` — integer division makes the control-plane Hz approximate (hz=6 @ 15 fps → skip=2 → actual 7.5 Hz while `sample_hz` claims 6).
4. Inconsistent invalid-row filtering: the agent *excludes* invalid rows from features but *includes* them in the 10 s confidence window. Mostly benign.

### `vision/synth.py` (~20 KB)
**Does well:** pixel-deterministic (`RandomState` seeded per frame), seamless-loop math for the `normal` clip, calm-curve periods (12/6/4 s) all divide the 120 s clip, skips existing valid clips via frame-count check.

**Issues:**
- Render cost: 4,830 frames of heavy per-frame numpy. The "~60 s" claims (`scenario.py`, `start.py`, README, DEMO.md) are optimistic — several minutes on modest hardware, and it **blocks `start.py` before the API boots**.
- `synth.py:526`: `force = "--force" in argv` is computed but **never passed** to `render_all()` (line 528 calls `render_all(quiet=quiet)`), and `render()` skips existing files > 40 KB. So the README's troubleshooting command `python3 -m vision.synth all --force` does *not* re-render. Doc/command mismatch.

### `scripts/start.py`
**Does well:** one command boots API (`python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`) + console (`npm run dev` in `frontend/`) + site (`npm run dev` in `site/`); hard-checks ports 8000/5173/5174; `--demo`/`--no-site` flags; Ctrl-C teardown via process groups; banner shows URLs.

**Bug (P0): `ensure_scenes` always crashes** (lines 76–79):
```python
proc = subprocess.run([sys.executable, "-c",
                       "import sys; sys.path.insert(0, sys.argv[1]);"
                       "from vision.synth import render_all; render_all(quiet=True)"],
                      cwd=ROOT)
```
No `ROOT` argument is passed after the `-c` string, so the child sees `sys.argv == ['-c']` and `sys.argv[1]` raises `IndexError`. **First-run rendering via `start.py` never works** — it prints "scene rendering failed" and continues. Masked only because the backend's `render_async` daemon thread renders later. Also: if `npm run dev` fails silently the 45 s `wait_http` just burns time; banner box misaligns on long URLs (cosmetic).

### `scripts/run_demo.py`
**Bug (P0, crash):** `/agent/state` returns `"cameras"` as a **dict** (`{cid: {...}}`), but line 55 does `for cam in st["cameras"]:` (iterates dict *keys* = strings) then `cam["camera_id"]` → **`TypeError: string indices must be integers`**. Line 63 has the same bug. **The script crashes on its first poll loop — the flagship demo driver is unusable.** Fix: `st["cameras"].values()`.
**Demo-flow risks even after fixing:** holds (150 s / 130 s) exceed clip lengths (78 s / 46 s) — engines EOF-hold static frames, telemetry goes flat, and after 60 s calm the agent *recovers*, so the printed "steady state" may show an already-stepped-down state; ~70 s of dead air post-recovery in the gradual_rise hold.

### `scripts/seed_baseline.py`
**Bug (P0, crash):** same dict-iteration bug at line 40: `for c in st["cameras"]:` then `c['camera_id']` → `TypeError`. **The script's primary mode always crashes.**

## 3. Per-file review — frontend dashboard

**Headline:** visually polished, genuinely good empty states in places, working WS reconnect — but **no error boundary anywhere** in a tree full of unguarded property access, a **3-second REST poll that fights the WebSocket** and corrupts live state, and two permanently dead tiles.

### `src/App.jsx`
**Does well:** reconnect loop with cleanup (`closed` flag); centralized message dispatch; REST bootstrap guarded by `alive` flags.

**Bugs:**
1. **The 3-second "resilience poll" fights the WebSocket** (lines 100–118): every 3 s `get('/cameras')` → `storeActions.hydrate(data)` rebuilds every camera object and **drops WS-only fields** (`rate`, `baseline`, `anomaly`, `at` — set by `onTick` but not re-derived in `hydrate`). Net effect every 3 s: anomaly tile flashes to `0.00×`, chart baseline falls back to the hardcoded `0.0004`, and `worst: payload.worst_state || 'normal'` can **reset the network status chip to NORMAL mid-escalation**. Same clobber race for `alerts`.
2. Line 108: a failed `/cameras` poll calls `setConnection('closed')`, making the TopBar pill read **RECONNECTING even when the WebSocket is open** (TopBar.jsx:57–61).
3. Unknown WS message types silently dropped (`default: break`) — a backend rename fails with zero signal.
4. Unused imports line 3: `store` and `toast` never referenced.

### `src/lib/store.js`
**Does well:** ring-buffered series (`RATE_MAX`/`RATIO_MAX`), `worstOf` reducer, capped toasts, audio alert with `muted` guard.

**Bugs:**
1. **Crash on missing `from`** — line 154: `` `${e.from.toUpperCase()}` `` throws if a `state_change` omits `from`; the `worst` recompute never runs → stale network chip.
2. **Crash on missing `severity`** — line 176: `` `ALERT ${a.alert_id} · ${a.severity.toUpperCase()}` ``.
3. **`hello` handler has no `camera_id` fallback** (lines 207–209, unlike `hydrate`'s `c.camera_id || c.id`) — if the backend ever sends `id`, every id becomes `undefined` and the hello payload is silently ignored.
4. **Dead store fields:** `registry` (written, never read), `site` (written, never read — MapPanel hardcodes coordinates), `tick` (incremented, never selected), `decisions` (**fetched from `/agent/log` at App.jsx:126, stored, never rendered — an entire audit-log feature that exists only in the store**).
5. `onHello` unconditionally sets `connection: 'open'` even if the socket is down.
6. In-place mutation in `ensureSeries`/`push` outside `set()` — works today because selectors re-run, but fragile.
7. Full-store `set()` on every tick → every subscriber re-renders at 5 Hz (selectors limit damage, but `Ticker`/`MetricTiles` re-render 5×/s regardless).

### `src/lib/api.js`
**Bugs:**
1. **204/empty/HTML responses break everything** — line 2: `r.json()` throws `SyntaxError` on empty body. A 204 from `POST /simulate/stop` (or a proxy 502 HTML page) surfaces as failure → false "SCENARIO FAILED" sticky toast.
2. No `AbortController`/timeout — a hung backend leaves `await get('/cameras')` pending while the 3 s interval stacks overlapping requests.
3. `simulate()` helper (line 14) exists but `SimulationPanel.jsx:34` bypasses it with a raw `post()` — two code paths for the same call.
4. `wsUrl` is host-relative — correct only when the frontend is served same-origin with the API (Vite dev proxy or FastAPI serving the bundle). Deploy elsewhere and everything 404s.

### `src/components/MapPanel.jsx`
**Bugs:**
1. **Highest white-screen risk in the app** — line 127: `width={Math.max(96, p.name.length * 6.6)}`. If any camera lacks `name`, this throws inside render → **entire dashboard unmounts** (no error boundary). (Mitigated today because `config.CAMERAS` always has names — but one backend change or registry edit away from a dead demo.)
2. **NaN map on missing coords** — lines 26–37: `Math.min(...lats)` with any undefined `lat`/`lng` → `NaN` → every pin at `NaN,NaN`, blank map, no fallback message.
3. Line 142 badge reads `OSM-STYLISED · VECTOR TILE ADAPTER` — **fiction**. It's hand-drawn SVG; no OSM, no tiles, no adapter. Embarrassing if a judge asks about the map provider.
4. Line 59 hardcodes `28.61°N 77.22°E` (Delhi) while the camera registry is Bangalore (12.97°N, 77.59°E) and the store already hydrates a `site` field nobody reads.
5. Legend omits `verifying` and `signal_lost`.

### `src/components/MetricTiles.jsx`
**Bugs:**
1. **Two tiles are permanently dead** — line 55 `cam?.wl_delta_10s ?? 0` and line 89 `cam?.flow_std`: `onTick` never sets either field (verified against the backend broadcast at `main.py:173–188`, which sends neither) → waterline tile always `+0.0 px / 10 s`, flow tile always `std —`. The UI was built for backend fields that don't arrive.
2. **No loading state** — before data arrives, tiles show `0.00%` / `0.00` as if real readings (misleading; contrast with the skeletons elsewhere).
3. Anomaly/baseline readouts wiped to `0` every 3 s by the hydrate clobber, restored ~200 ms later by the next tick — visible flicker.

### `src/components/CameraList.jsx`
**Bugs:**
1. **Thumbnail poll spam** (lines 19–24): one 1 s interval *per camera* → 4 cache-busted `frame.jpg` requests/second, forever. Backend down or frames not ready = permanent failed-GET stream in the network tab and black thumbnail boxes during setup.
2. `{c.name}` renders a blank tile title if `name` is ever missing; `key={cid}` with undefined ids → duplicate-key warnings. (Both mitigated today by the registry; still unguarded.)

### `src/components/AlertFeed.jsx` / `EvidenceView.jsx`
**Bugs:**
1. **Crash on missing `transitions`** — `AlertFeed.jsx:43` (`alert.transitions.slice(-4).map`) and `EvidenceView.jsx:171` (`alert.transitions.map`). White-screen class. (Mitigated today: the backend always sets `transitions` — verified at `agent.py:494` — but zero defensive margin.)
2. `AlertFeed.jsx:20`: `alert.status.replace('_', ' ')` throws if `status` undefined.
3. `EvidenceView.jsx:273`: `` `${report.camera.name} (${report.camera.id})` `` throws if `POST /alerts/{id}/export` doesn't return `report.camera`.
4. After ack/FP succeed, local alert status is **not** updated — waits for a WS `alert` push; without one, the button re-enables and a re-click can 4xx → "ACTION FAILED" toast.
5. `EvidenceView.jsx:61`: `setTimeout(() => window.print(), 260)` — print fires even if the user closed the modal within 260 ms.
6. Unused imports in AlertFeed (line 1: `acknowledge, falsePositive, exportReport`; line 2: `toast`); dead exports `SEV, tint` (line 106).

### `src/components/ChartPanel.jsx`
**Bugs:**
1. Dead code line 61: `yPct` defined, never used.
2. **Silent blank chart on bad timestamps** (lines 47–51): unparseable `t` → `NaN` span → all X coords `NaN` → SVG renders nothing, but `data.length >= 2` so the empty state doesn't trigger. No error, no message — just a dead chart.
3. Window buttons `[60, 120, 300]` slice *point counts* while labels claim minutes (`{w/60}m`) — only accurate at exactly 1 Hz.
4. Line 33: `0.0004` baseline fallback duplicates README's `BASELINE_FLOOR` — consistent, but a second source of truth.

### `src/components/SimulationPanel.jsx` / `TopBar.jsx` / `Toasts.jsx` / `ui.jsx`
- `SimulationPanel.stop()` (lines 49–56) has **no catch** — a failed `POST /simulate/stop` is completely silent. Hint says `5% → 60%`, DEMO.md says `6% → 60%` — minor doc inconsistency. Line 114: hardcoded cosmetic `POST /simulate/*` label.
- `TopBar.jsx:112`: hardcoded `href="http://localhost:5174/"` — dead link in any non-local deployment. The big demo button hardcodes `gradual_rise` (second trigger path alongside the panel).
- `ui.jsx`: dead exports `useCameras`, `LiveDot` (imported nowhere). `fmtTime` correctly guards invalid dates.
- `Toasts.jsx`: the bottom "progress" bar is a static full-width div — decorative, not a countdown.

### `src/index.css` / `index.html` / `package.json` / `vite.config.js`
- Tailwind v4 `@theme` tokens cover every class used. Dead CSS: `.pulse-dot::after`, `@keyframes draw-in`. `body { overflow: hidden }` — fixed-viewport app, clips on small screens.
- `index.html`: Google Fonts is a remote dependency — offline demo degrades to system fonts. Favicon is an inline data URI (good).
- `package.json`: React 18.3.1; **no test or lint scripts at all**.
- `vite.config.js`: dev proxy covers `/cameras /alerts /simulate /agent /snapshots /health /ingest /telemetry` → `127.0.0.1:8000` and `/stream` → ws. **Dev-only** — a production build has no proxy; the bundle only works same-origin with the API. Hardcoded `127.0.0.1:8000` means "backend on another host" needs a config edit.

**Resilience summary:** WS reconnect exists and is correct (linear backoff, 8 s cap, no jitter, unbounded attempts; StrictMode-safe). But there is **no global "backend unreachable" banner** — with the backend down: CameraList + AlertFeed skeleton forever, MetricTiles shows fake `0.00`s, MapPanel renders an empty grid with no message, thumbnails 404 once per second per camera. The TopBar pill is the only signal, and it can lie (App.jsx:108).

## 4. Per-file review — site / contracts / docs

### `site/` — the marketing site
**What it is:** a standalone landing page — `App.jsx` renders `HeroSection` + six marketing sections (`Problem`, `Pipeline`, `Verification`, `Evidence`, `Stack`, `CTA`) + `Footer` (`AquaTraceSections.jsx` 345 lines, `HeroSection.jsx` 126 lines). Not a duplicate dashboard; shares no components with `frontend/`.
**Backend connectivity: NONE.** Zero `fetch`/`WebSocket` in `site/src/`. Fully static marketing copy; the only backend touch is `<a href="http://localhost:8000/docs">` and a ports caption. Media served from `../assets/` via `publicDir` (`/aquatrace-hero.mp4`, `/aquatrace-hero-poster.jpg`, `/evidence-sample.jpg`) — **if assets are missing, the hero video silently fails to a poster-less black backdrop**.
**Referenced in docs/scripts?** Yes, deliberately: README port table (:5174), architecture diagram ("5174 marketing"), quickstart (`start.py` boots "backend + console + site"), DEMO.md, `start.py --no-site`.
**Verdict: KEEP.** It's wired into the boot script and the pitch story; a judge-facing landing page is a legitimate hackathon artifact. Two caveats: (1) never claim it "shows live data" — it's static; (2) if you want fewer moving parts, fold it into the console app as a route and drop a whole Vite dev server.

### `contracts/`
- **`telemetry.schema.json`**: 11 required fields; engine output matches exactly; pydantic `Telemetry` mirrors it; `additionalProperties: false` satisfied. ✅ Clean.
- **`alert.schema.json`**: matches the backend record **except** `state: "verifying"` is emitted but not in the enum (`["normal","watch","escalating","critical"]`). ❌ See agent.py bug #1.
- **`state_change.schema.json`**: the six documented event types match their branches, **but** the stream also emits `hello`, `ping`, and `agent_note` — none in the `type` enum, none matching any `oneOf` branch. The *very first message* a client receives (`hello`) fails strict validation. ❌ Also: `frame` is in the enum but has **no corresponding `oneOf` branch** — dead enum member.

### `README.md` / `DEMO.md`
**README — verified accurate except:** (1) omits `POST /ingest/telemetry` from the API surface — the single most important route for the vision→backend story; (2) troubleshooting `python3 -m vision.synth all --force` doesn't re-render (`--force` never passed through); (3) layout tree omits `scripts/seed_baseline.py`; (4) "~1 min" scene rendering is optimistic (see demo risks).
**DEMO.md — step-by-step audit:** all steps workable (`start.py`, console :5173, `run_demo.py --scenario gradual_rise --hold 150`, SimulationPanel buttons, WATCH→ESCALATING→VERIFYING→CRITICAL progression with reasoning strings matching `agent.py:548`, `curl /simulate/flash_surge`, ack/FP/export, 60 s step-down recovery) **except**: the false-positive beat says "the agent explains it just raised cam-02's baseline 1.7×" — wrong camera. In the gradual_rise flow the critical alert belongs to **cam-01** (`main.py:319` → `agent.apply_false_positive` raises the *alert's* camera). README's "raises that camera's baseline" is correct; DEMO's cam-02 will confuse a live presenter. One-line fix. (Also: DEMO implies `run_demo.py` works — it crashes; see P0.)

---

## 5. Cross-file consistency (independently verified)

| Check | Result |
|---|---|
| Scenario names (`normal`, `gradual_rise`, `flash_surge`) | ✅ consistent in `scenario.PLAN`, `synth.SCENARIOS`, `main.py` validation, `start.py --demo`, `run_demo.py`, frontend `SimulationPanel` |
| Telemetry JSON keys (engine → `POST /ingest/telemetry`) | ✅ exact match to `telemetry.schema.json` |
| Alert record vs `alert.schema.json` | ❌ `state: "verifying"` emitted (`agent.py:494,514`), not in enum |
| WS events vs `state_change.schema.json` | ❌ `hello` / `ping` / `agent_note` emitted, not in enum; `frame` in enum but no `oneOf` branch |
| Frontend-expected WS fields vs backend broadcast | ✅ `telemetry_tick` carries `expansion_rate`, `baseline_rate`, `anomaly`, `state`, `signal` (`main.py:173–188`); `state_change` carries `from/to/reasoning`; `alert` carries `action` + full record; `camera_status` carries `status`; `scenario` carries `status/scenario/cameras` |
| Frontend handles `hello` + `verifying`; contracts define neither | ❌ contract drift in both directions |
| `GET /cameras` envelope vs `hydrate()` | ✅ `{cameras[], site, worst_state, scenario, scenes_ready}` matches (`alerts` key absent — hydrate keeps existing; benign). Camera objects spread registry `id` → frontend's `c.camera_id \|\| c.id` handles it; `name/lat/lng/zone` present |
| `GET /alerts` → `{alerts}` vs `d.alerts` | ✅ match |
| `GET /agent/log` → `{decisions}` vs `d.decisions` | ✅ match (but never rendered — dead feature) |
| `GET /agent/state` cameras shape | ❌ returns **dict** `{cid: {...}}` — crashes `run_demo.py:55,63` and `seed_baseline.py:40` |
| Endpoints in README vs `main.py` | ✅ all 17 REST + `WS /stream` present |
| Endpoints README lists vs frontend actually calls | ⚠️ frontend never calls `GET /telemetry/{id}`, `GET /alerts/{id}`, `GET /agent/state`, `GET /cameras/{id}/control`, `GET /health`, `GET /simulate` — documented surface is bigger than the used surface |
| Dead tiles | ❌ `wl_delta_10s`, `flow_std` rendered but never sent by any backend message |
| Config constants vs docs | ✅ 2×/3×/5×, 10 s/10 s sustain, 60 s recovery, 0.80 confidence gate, 1.7×/10 min FP all match README/DEMO/ChartPanel/MetricTiles copy |

---

## 6. Gaps vs the proposal (TeamMKKR.pdf, Knowbuild 2.0)

| # | Proposal promise | Status |
|---|---|---|
| 1 | OpenCV vision: background subtraction + water segmentation → bbox + area-expansion JSON | ✅ Built — but it's classical HSV-thresholding + contours, not *semantic segmentation* as claimed; ~5 Hz, not strictly frame-by-frame |
| 2 | Ingest simulated RTSP feed | ⚠️ Partial — engine accepts RTSP URLs via `cv2.VideoCapture`, but nothing streams RTSP; demo uses mp4 files |
| 3 | OpenCV 5 | ❓ Unverifiable — no requirements file in the repo at all |
| 4 | Agentic decision loop: rolling window, acceleration vs baseline, threshold state changes | ✅ Built (well-built) |
| 5 | Dashboard: mock map, live expansion-rate graph, alert feed with annotated frames at Critical | ✅ Built |
| 6 | Confidence gate → pull historical data / request feed from adjacent camera | ⚠️ Partial — corroboration *reuses* neighbors' existing telemetry (`agent.py:333–368`); it never requests a new feed or triggers fresh processing |
| 7 | Absolute confidence → "immediately triggers emergency protocols" | ❌ Not built — raises a dashboard alert only (`agent.py:261–263`) |
| 8 | Alerts via AWS SNS (automated notifications) | ✅ Built 2026-09-26 — Critical alerts publish to the real SNS topic `aquatrace-critical-alerts` (ap-south-1); verified live with a returned MessageId. No SMS/email subscriber is attached — add one before claiming "automated notifications reach a phone". |
| 9 | Kinesis / S3 archival / Lambda / EC2 | ⚠️ Partial 2026-09-26 — S3 archival is real (evidence bundles + incident reports land in `aquatrace-evidence-148018683731-ap-south-1`, verified live); CloudWatch metrics are real. Kinesis/Lambda/EC2 remain unbuilt. |
| 10 | FastAPI **+ Pandas** | ⚠️ FastAPI yes; **zero pandas imports anywhere** — dropped without updating the doc |
| 11 | Risk categories Watch → Escalating → Critical | ✅ Built (superset: adds `normal`, `verifying`) |
| 12 | React + Vite + Tailwind | ✅ Built (both `frontend/` and `site/`) |

**Pitch guidance (updated 2026-09-26):** safe claims are "dashboard alerts + adjacent-camera corroboration + live AWS backing (SNS critical alerts, S3 evidence vault, CloudWatch metrics) with local fallback". Still do *not* claim semantic segmentation, RTSP streaming, Pandas, emergency-service integration, Kinesis/Lambda/EC2, or a cloud deployment — none of those exist.

## 7. Demo-readiness gaps (what breaks live, in likely order of embarrassment)

1. **No `requirements.txt` / `pyproject.toml` / `setup.py` anywhere.** On a fresh machine (judge's laptop, hackathon re-image) the Python env is guesswork: `fastapi uvicorn pydantic httpx numpy opencv-python(-headless)`. README never lists them. Single biggest first-run risk.
2. **`start.py`'s first-run scene render always fails** (`sys.argv[1]` IndexError) — masked only by the backend's fallback render thread, which takes *minutes* (4,830 frames of numpy), not the claimed "~60 s". `_autostart` gives up after 60 s, so **a cold boot is not live immediately** despite README's claim. Plan the boot order around this: render scenes *before* the demo slot.
3. **`run_demo.py` and `seed_baseline.py` crash on launch** (dict-iteration `TypeError`). If anyone runs the "scripted demo" path, it dies instantly.
4. **No error boundary in the React tree.** Any unguarded access (`p.name.length`, `transitions.map`, `e.from.toUpperCase()`) white-screens the *entire* dashboard with no recovery except refresh. The fields are present today, but one backend hiccup during a live demo = dead screen.
5. **The 3 s `/cameras` poll visibly fights the WebSocket**: anomaly gauge flickers to `0.00×`, worst chip can flash NORMAL mid-escalation, chart baseline resets — all on camera during the escalation beat you're narrating.
6. **Demo holds exceed clip lengths** (`run_demo.py`: 150 s > 78 s clip, 130 s > 46 s clip) → engines EOF-hold static frames → agent recovers mid-demo → the "steady state" printout can show a stepped-down state while you're describing a crisis.
7. **Thumbnail 404 storm**: 4 cache-busted `frame.jpg` req/s while the backend is down or frames aren't ready — red network tab on the projector, black tiles with only the watermark.
8. **Stale scenario state**: switching scenarios doesn't clear `AlertStore` — new transitions append to the previous scenario's alert; `scenario/stopped` flaps to `started` on every switch.
9. **Hardcoded `127.0.0.1:8000`** in three places (engine spawn, vite proxy, `wsUrl` same-origin assumption) — "backend on another host/port" silently breaks engines at debug log level.
10. **Ports 8000/5173/5174 hard-required** — any conflict → `start.py` hard-exits; no fallback.
11. **AudioContext blocked pre-user-gesture** — the first critical alert may be silent until someone clicks.
12. **Google Fonts remote dependency** — offline venue Wi-Fi degrades typography (minor).
13. **Disk/memory growth**: snapshot JPEGs never deleted, alerts never evicted — fine for a 10-minute demo, not for a day-long booth.

---

## 8. Prioritized fix / improvement list

### P0 — must fix before any live demo (all small, all concrete)
1. **Fix `scripts/run_demo.py:55,63`** — iterate `st["cameras"].values()` instead of `st["cameras"]`. (2-line fix; script currently unusable.)
2. **Fix `scripts/seed_baseline.py:40`** — same dict-iteration fix. (1-line fix.)
3. **Fix `scripts/start.py:76–79`** — pass `ROOT` to the child: add `str(ROOT)` after the `-c` string in the `subprocess.run` argv. (1-line fix; first-run rendering currently always fails.)
4. **Add `requirements.txt`** (pin: `fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `numpy`, `opencv-python-headless`) **and** document `npm install` for both frontends in README quickstart. Verify on a clean machine/venv.
5. **Add a top-level React error boundary** (`frontend/src/`) so a render throw shows a fallback panel, not a white screen.
6. **Fix the alert contract violation** — either add `"verifying"` to `state`'s enum in `contracts/alert.schema.json` *and* constrain `models.Alert.state`, or map verifying→escalating when writing the alert record (`agent.py:494,514`). Don't demo with a self-contradicting contract.
7. **Fix the 3 s poll clobber** — make `hydrate()` merge instead of replace: stop overwriting `worst`/`alerts` and stop rebuilding camera objects with dropped fields (`rate`, `baseline`, `anomaly`, `at`). Cheapest: remove `worst`/`alerts` from the poll patch and carry WS fields through.
8. **Fix DEMO.md false-positive beat** — cam-02 → cam-01 (the alert's camera).
9. **Pass `--force` through in `vision/synth.py:528** (`render_all(quiet=quiet)` → `render_all(quiet=quiet, force=force)`) or fix the README troubleshooting line.
10. **Pre-render scenes before the demo slot** — run `python3 -m vision.synth all` ahead of time; don't rely on the "~60 s" claim on stage.

### P1 — quality / polish (do if time)
11. Guard the white-screen-class accesses: `p.name?.length`, `alert.transitions ?? []`, `e.from?.toUpperCase?.()`, `a.severity?.toUpperCase()`, `alert.status?.replace(...)`.
12. Handle 204/empty/HTML in `api.js` (`if (r.status === 204) return null`) and add `.catch` to `SimulationPanel.stop()`.
13. Add locks (or a single-threaded mutation path) around `AlertStore` and `SnapshotStore`; replace cross-thread `Queue.put_nowait` with `loop.call_soon_threadsafe`.
14. Delete snapshot JPEGs on eviction (`SnapshotStore`) and cap/evict `AlertStore`; clear `AlertStore` on scenario reseed.
15. Fix `config.parse_window("90")` → treat bare numbers as seconds (test the last char, not substring membership).
16. Fix engine 422-retry (`vision/engine.py:431–437`): only drain `_pending` on `status_code < 300`, and don't re-queue an already-POSTed payload.
17. Add traversal guard to `/snapshots/{path:path}` (`os.path.normpath` + `startswith` check).
18. Either send `wl_delta_10s`/`flow_std` from the backend or remove the dead tiles; either render `decisions` from `/agent/log` somewhere or stop fetching it.
19. Replace the fictional `OSM-STYLISED · VECTOR TILE ADAPTER` badge copy; use the hydrated `site` center instead of hardcoded Delhi coords (registry is Bangalore).
20. Move `ingest_telemetry`'s base64-decode + JPEG write off the event loop (`run_in_executor`).
21. Reduce thumbnail polling (only poll the *selected* camera, or back off on failure) and add a global "backend unreachable" banner instead of skeletons-forever.
22. Add `hello`, `ping`, `agent_note` to `state_change.schema.json` (or stop emitting them); remove the dead `frame` enum member or implement it.
23. Make the engine backend URL configurable (env var / flag) instead of hardcoded `http://127.0.0.1:8000`.
24. Add `requirements` note: `str.removeprefix` needs Python ≥ 3.9 — state the floor in README.

### P2 — nice-to-have
25. Replace deprecated `@app.on_event` + `asyncio.get_event_loop()` with `lifespan`.
26. Remove import-time side effects (`makedirs`, `basicConfig`, agent instantiation at import).
27. Delete dead code: `VISION_MODULE`, `Transition`/`Corroboration*` models (or wire them up), `registry`/`site`/`tick` store fields, `yPct`, `SEV`/`tint`/`useCameras`/`LiveDot` exports, `.pulse-dot::after`/`draw-in` CSS, unused imports.
28. Deduplicate the calm-curve math (`stores.seed_row` vs `synth._normal_ratio`) — import, don't copy.
29. Fix `ChartPanel` window buttons to slice by time, not point count; handle NaN timestamps with the empty state.
30. Add a lint + test script to `frontend/package.json` (currently zero); add at least contract-validation tests for emitted WS events.
31. Reconcile the `5%` vs `6%` gradual_rise copy (SimulationPanel hint vs DEMO.md).
32. Consider folding `site/` into the console app as a route to drop a whole Vite dev server — only if boot complexity becomes a problem.

## 9. AWS integration + Mac write-back verification (2026-09-26)

### AWS resources (provisioned live, ap-south-1)
- Account `148018683731`, profile `divesh`, region `ap-south-1`.
- S3 bucket `aquatrace-evidence-148018683731-ap-south-1` — critical evidence bundles (annotated frame + alert JSON) and exported incident reports.
- SNS topic `arn:aws:sns:ap-south-1:148018683731:aquatrace-critical-alerts` — Critical alert publication.
- CloudWatch namespace `AquaTrace` — `Anomaly`, `FlowMean`, `Confidence` and other telemetry metrics.
- No SNS subscriber is attached. Do not add one without an explicit email from the user.

### VM verification (all observed live 2026-09-26)
- `GET /aws/status` returned S3 `ok`, SNS `ok`, CloudWatch `ok`.
- gradual-rise scenario reached Critical: S3 contained `frame.jpg`, `alert.json`, `incident-report.json`; SNS returned MessageId `4cabae3e-c7d2-5d5a-80f5-cd0ff864c9be`; CloudWatch listed AquaTrace metrics.
- Offline fallback (`AQUATRACE_AWS_ENABLED=0`): flash-surge still reached Critical with `aws=None` and no backend errors.
- `compileall OK`; frontend production build passed in 1.78 s.
- AWS work runs on daemon threads; every AWS call is best-effort and never raises — the system runs on local snapshots when AWS is unreachable.
- Frontend: top-bar **AWS LIVE / LOCAL MODE** pill (60 s refresh), `sns ✓` / `s3 ✓` ticks on alert cards, S3-vault link in the evidence view, 7-day presigned S3 links.

### Agent Toolkit limitation
- AWS CLI v2.37.4 + `uv` installed; `aws agent-toolkit list-available-skills` succeeded; official AWS rules placed in `AGENTS.md`.
- `aws configure agent-toolkit` failed (exit 253, no supported coding-agent directory). Do not claim complete MCP/coding-agent integration. Remaining path: establish a supported agent directory/config, rerun setup, preserve existing MCP entries, restart only after setup truly succeeds.

### Mac write-back (verified 2026-09-26)
- Backup kept untouched: `/Users/dhruvawani17/Documents/Aquatrace-backup-20260926`.
- Destination `/Users/dhruvawani17/Documents/Aquatrace` updated surgically (no wholesale replace; `site/` and unrelated files preserved; `assets/` untouched).
- Every source file verified **byte-for-byte identical** to the VM canonical tree via upload+diff: backend (incl. new `awsx.py`), vision, scripts (incl. new `aws_setup.py`), contracts, frontend `src/` (all components, incl. AWS UI changes), `requirements.txt`, `README.md`, `CHANGES.md`, `DEMO.md`, `AGENTS.md`, `package.json`, and this file.
- `backend/data/aws.env` written on the Mac with AWS enabled, profile `divesh`, region `ap-south-1`, the verified bucket and topic ARN. Contains configuration only — **no secret key** (never copy the exposed root secret into project files).
- Mac runtime validation (backend boot, `/health`, `/aws/status`, scenario runs) is QUEUED, not done: the Mac exposes no shell to this agent and Terminal is on the computer-control blocked-apps list. Run `python3 scripts/start.py` on the Mac to validate; the identical VM build passed all checks above.
- ⚠️ The static root secret was exposed in chat during provisioning. Delete/rotate it in the AWS console and replace with least-privileged IAM credentials.

### Costs and cleanup
- S3/SNS/CloudWatch usage at this scale is fractions of a cent; the bucket holds a handful of small evidence objects.
- Cleanup when done: `aws s3 rb s3://aquatrace-evidence-148018683731-ap-south-1 --force --profile divesh --region ap-south-1` and `aws sns delete-topic --topic-arn arn:aws:sns:ap-south-1:148018683731:aquatrace-critical-alerts --profile divesh --region ap-south-1`.

---

## Appendix — file inventory of the mirror

```
README.md · DEMO.md
backend/agent.py · backend/config.py · backend/main.py · backend/models.py · backend/scenario.py · backend/stores.py
vision/engine.py · vision/synth.py
scripts/run_demo.py · scripts/seed_baseline.py · scripts/start.py
contracts/alert.schema.json · contracts/state_change.schema.json · contracts/telemetry.schema.json
frontend/index.html · frontend/package.json · frontend/vite.config.js
frontend/src/App.jsx · frontend/src/main.jsx · frontend/src/index.css
frontend/src/lib/api.js · frontend/src/lib/store.js
frontend/src/components/{AlertFeed,CameraList,ChartPanel,EvidenceView,MapPanel,MetricTiles,SimulationPanel,Toasts,TopBar,ui}.jsx
site/index.html · site/package.json · site/vite.config.js
site/src/App.jsx · site/src/main.jsx · site/src/index.css
site/src/components/AquaTraceSections.jsx · site/src/components/HeroSection.jsx
reference/TeamMKKR.pdf
```
*Not mirrored (recorded by name/size only): node_modules, dist, __pycache__, package-lock.json ×2, backend/data/logs (4 × ~0.9 MB), backend/data/snapshots/cam-0{1..4}/, assets/*.mp4 (normal 10.3 MB, gradual_rise 67.7 MB, gradual_rise_secondary 6.8 MB, flash_surge 15.3 MB, hero 8.4 MB, continuation 10.7 MB), assets/*.jpg (hero-poster 113 KB, evidence-sample 65.7 KB).*
