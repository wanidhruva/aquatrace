import { useSyncExternalStore } from 'react'

const RATE_MAX = 420 // ~7 min of 1 Hz agent readings
const RATIO_MAX = 400 // ~80 s of 5 fps telemetry

const initial = {
  loading: true,
  connection: 'connecting',
  cameras: [],
  selected: 'cam-01',
  series: {}, // camera_id -> { ratio: [], rate: [] }
  alerts: [],
  toasts: [],
  scenario: { running: false, scenario: null },
  worst: 'normal',
  decisions: [],
  evidenceId: null,
  mode: 'live', // 'demo' | 'live' — live mode by default
  muted: false,
  site: null,
  forecast: null, // per-zone precipitation outlook (GET /api/forecast); null = not loaded
  mapExpanded: false, // when true, map expands to show only the map
  showEvacuationRoutes: false, // when true, renders high-ground evacuation corridors & emergency shelters
  selectedShelterId: null, // id of selected evacuation center for directions routing
  evacFocusAlert: null, // alert that triggered evacuation map view
}

let state = initial
const listeners = new Set()

const set = (patch) => {
  state = { ...state, ...patch }
  listeners.forEach((l) => l())
}

export const store = {
  get: () => state,
  subscribe: (l) => {
    listeners.add(l)
    return () => listeners.delete(l)
  },
}

export function useStore(selector = (s) => s) {
  return useSyncExternalStore(
    store.subscribe,
    () => selector(state),
    () => selector(state),
  )
}

// ------------------------------------------------------------------ helpers

const push = (arr, item, max) => {
  arr.push(item)
  if (arr.length > max) arr.splice(0, arr.length - max)
}

function ensureSeries(cid) {
  if (!state.series[cid]) {
    state.series = { ...state.series, [cid]: { ratio: [], rate: [] } }
  }
  return state.series[cid]
}

function withCamera(cid, patch) {
  const cameras = state.cameras.map((c) => (c.camera_id === cid || c.id === cid ? { ...c, ...patch } : c))
  set({ cameras })
}

let toastSeq = 0
export function toast(t) {
  const id = ++toastSeq
  const item = { id, at: Date.now(), ...t }
  set({ toasts: [...state.toasts, item].slice(-5) })
  if (!item.sticky) setTimeout(() => storeActions.dismissToast(id), item.ttl || 7000)
}

export const storeActions = {
  dismissToast: (id) => set({ toasts: state.toasts.filter((t) => t.id !== id) }),
  select: (camera_id) => set({ selected: camera_id }),
  setMuted: (muted) => set({ muted }),
  setEvidence: (evidenceId) => set({ evidenceId }),
  setMode: (mode) => set({ mode: mode === 'live' ? 'live' : 'demo' }),
  setMapExpanded: (expanded, options = {}) =>
    set({
      mapExpanded: Boolean(expanded),
      ...(options.showEvacuationRoutes != null ? { showEvacuationRoutes: Boolean(options.showEvacuationRoutes) } : {}),
      ...(options.selectedShelterId !== undefined ? { selectedShelterId: options.selectedShelterId } : {}),
      ...(options.alert !== undefined ? { evacFocusAlert: options.alert } : {}),
    }),
  toggleMapExpand: () => set({ mapExpanded: !state.mapExpanded }),
  setShowEvacuationRoutes: (show) => set({ showEvacuationRoutes: Boolean(show) }),
  selectShelter: (shelterId) =>
    set({
      selectedShelterId: shelterId,
      showEvacuationRoutes: true,
    }),
  clearShelter: () => set({ selectedShelterId: null }),
  openEvacuationMap: (alert) => {
    const camId = alert?.camera_id || state.selected
    const shelterMap = {
      'cam-01': 'shelter-narendra',
      'cam-02': 'shelter-aiims',
      'cam-03': 'shelter-railway',
      'cam-04': 'shelter-rajaji',
    }
    const defaultShelter = shelterMap[camId] || 'shelter-aiims'
    set({
      mapExpanded: true,
      showEvacuationRoutes: true,
      selectedShelterId: defaultShelter,
      evacFocusAlert: alert || null,
      selected: camId,
    })
  },

  // MERGE — never rebuild blindly. The 3 s /cameras poll runs alongside the
  // WebSocket stream; fields the poll payload doesn't carry (rate, baseline,
  // anomaly, at, flow_std, wl_delta_10s, hasTick) are owned by WS events and
  // must survive. `worst` / `alerts` are owned by state_change / alert WS
  // events and are deliberately never overwritten here.
  hydrate(payload) {
    const WS_OWNED = ['rate', 'baseline', 'anomaly', 'at', 'flow_std', 'wl_delta_10s', 'hasTick']
    const prev = new Map(state.cameras.map((c) => [c.camera_id || c.id, c]))
    const cameras = (payload.cameras || []).map((c) => {
      const cid = c.camera_id || c.id
      const keep = {}
      const old = prev.get(cid)
      if (old) {
        for (const k of WS_OWNED) {
          if (c[k] == null) keep[k] = old[k]
        }
      }
      return {
        ...c,
        ...keep,
        camera_id: cid,
        state: c.state || 'normal',
        signal: c.status || c.signal || 'live',
        ratio: c.latest?.water_area_ratio ?? null,
        flow: c.latest?.flow_mean ?? null,
        wl: c.latest?.waterline_y ?? null,
        conf: c.latest?.confidence ?? null,
      }
    })
    set({
      cameras,
      scenario: payload.scenario || state.scenario,
      site: payload.site || state.site,
      loading: false,
    })
  },

  setAlerts(alerts) {
    set({ alerts })
  },

  setForecast(forecast) {
    set({ forecast: forecast || null })
  },

  onTick(e) {
    const cid = e.camera_id
    const s = ensureSeries(cid)
    state.series = { ...state.series, [cid]: s }
    push(s.ratio, { t: e.at, v: e.water_area_ratio, flow: e.flow_mean, wl: e.waterline_y, conf: e.confidence }, RATIO_MAX)
    const lastRate = s.rate[s.rate.length - 1]
    if (!lastRate || lastRate.rate !== e.expansion_rate || lastRate.baseline !== e.baseline_rate) {
      push(s.rate, { t: e.at, rate: e.expansion_rate, baseline: e.baseline_rate, anomaly: e.anomaly }, RATE_MAX)
    }
    withCamera(cid, {
      ratio: e.water_area_ratio,
      flow: e.flow_mean,
      wl: e.waterline_y,
      conf: e.confidence,
      state: e.state,
      signal: e.signal,
      rate: e.expansion_rate,
      baseline: e.baseline_rate,
      anomaly: e.anomaly,
      at: e.at,
      // broadcast by the backend worker (may be absent on older backends)
      flow_std: e.flow_std ?? null,
      wl_delta_10s: e.wl_delta_10s ?? null,
      hasTick: true,
    })
    // series must be a new object for react to see it
    set({ series: { ...state.series } })
  },

  onStateChange(e) {
    withCamera(e.camera_id, { state: e.to, substate: e.to === 'verifying' ? 'verifying' : undefined })
    const cam = state.cameras.find((c) => (c.camera_id || c.id) === e.camera_id)
    if (e.to !== 'normal') {
      set({
        decisions: [{ ...e }, ...state.decisions].slice(0, 120),
      })
    }
    if (e.to === 'verifying') {
      toast({
        kind: 'info',
        title: `${e.camera_id} · VERIFYING`,
        body: e.reasoning,
        tone: 'escal',
        ttl: 9000,
      })
    } else {
      toast({
        kind: 'state',
        title: `${e.camera_id} · ${e.from?.toUpperCase?.() || '?'} → ${e.to?.toUpperCase?.() || '?'}`,
        body: e.reasoning,
        tone: e.to === 'critical' ? 'crit' : e.to === 'escalating' ? 'escal' : e.to === 'watch' ? 'watch' : 'nominal',
        sticky: e.to === 'critical',
        ttl: e.to === 'critical' ? 30000 : 9000,
      })
    }
    if (e.to === 'critical' && cam) storeActions.playAlertTone()
    const worst = worstOf(state.cameras.map((c) => c.state).concat([e.to]))
    set({ worst })
  },

  onAlert(e) {
    const a = e.alert
    const idx = state.alerts.findIndex((x) => x.alert_id === a.alert_id)
    const alerts = [...state.alerts]
    if (idx >= 0) alerts[idx] = a
    else alerts.unshift(a)
    set({ alerts: alerts.slice(0, 60) })
    if (e.action === 'created') {
      toast({
        kind: 'alert',
        title: `ALERT ${a.alert_id} · ${a.severity?.toUpperCase?.() || 'WATCH'}`,
        body: a.reasoning,
        tone: a.severity === 'critical' ? 'crit' : a.severity === 'escalating' ? 'escal' : 'watch',
        sticky: a.severity === 'critical',
        ttl: 20000,
      })
    }
  },

  onCameraStatus(e) {
    withCamera(e.camera_id, { signal: e.status })
    if (e.status === 'signal_lost') {
      toast({ kind: 'warn', title: `${e.camera_id} · SIGNAL LOST`, body: 'Telemetry stream interrupted — agent holds last state.', tone: 'faint', ttl: 12000 })
    }
  },

  onScenario(e) {
    set({
      scenario: {
        running: e.status === 'started',
        scenario: e.scenario,
        at: e.at,
        cameras: e.cameras,
      },
    })
    if (e.status === 'started') {
      toast({ kind: 'info', title: `DEMO MODE · ${e.scenario}`, body: 'Scenario running — watch the escalation unfold.', tone: 'live', ttl: 8000 })
    }
  },

  onHello(e) {
    const cameras = (e.cameras || []).map((c) => ({
      ...c,
      camera_id: c.camera_id || c.id,
      state: c.state,
      signal: c.signal,
      substate: c.substate,
      thumbnail_url: c.thumbnail_url,
    }))
    if (cameras.length) set({ cameras: mergeCameras(cameras) })
    set({ connection: 'open', scenario: e.scenario || state.scenario, worst: e.worst_state || state.worst })
  },

  setConnection(connection) {
    set({ connection })
  },

  setDecisions(decisions) {
    set({ decisions })
  },

  playAlertTone() {
    if (state.muted) return
    try {
      const AC = window.AudioContext || window.webkitAudioContext
      const ctx = storeActions._ctx || (storeActions._ctx = new AC())
      if (ctx.state === 'suspended') ctx.resume()
      const now = ctx.currentTime
      const gain = ctx.createGain()
      gain.connect(ctx.destination)
      gain.gain.setValueAtTime(0.0001, now)
      gain.gain.exponentialRampToValueAtTime(0.16, now + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 1.1)
      ;[660, 990].forEach((f, i) => {
        const o = ctx.createOscillator()
        o.type = 'sine'
        o.frequency.value = f
        const g = ctx.createGain()
        g.gain.value = i ? 0.5 : 1
        o.connect(g)
        g.connect(gain)
        o.start(now + i * 0.16)
        o.stop(now + 1.2)
      })
    } catch {
      /* audio unavailable */
    }
  },
}

function mergeCameras(incoming) {
  if (!state.cameras.length) return incoming
  return state.cameras.map((c) => {
    const hit = incoming.find((i) => i.camera_id === c.camera_id)
    return hit ? { ...c, ...hit, ratio: c.ratio, flow: c.flow, wl: c.wl, conf: c.conf } : c
  })
}

export const ORDER = { normal: 0, watch: 1, escalating: 2, verifying: 2, critical: 3 }
export const worstOf = (states) =>
  states.reduce((a, b) => ((ORDER[b] ?? 0) > (ORDER[a] ?? 0) ? b : a), 'normal')

export const TONE = {
  normal: 'nominal',
  watch: 'watch',
  escalating: 'escal',
  verifying: 'escal',
  critical: 'crit',
  signal_lost: 'faint',
  offline: 'faint',
}

export const HEX = {
  nominal: '#10B981',
  watch: '#F59E0B',
  escal: '#F97316',
  crit: '#EF4444',
  live: '#22D3EE',
  faint: '#5A6B80',
}
