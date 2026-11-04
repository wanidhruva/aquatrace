import { useEffect, useRef } from 'react'
import { get, wsUrl } from './lib/api.js'
import { store, storeActions, useStore } from './lib/store.js'
import AlertFeed from './components/AlertFeed.jsx'
import CameraList from './components/CameraList.jsx'
import ChartPanel from './components/ChartPanel.jsx'
import DecisionLog from './components/DecisionLog.jsx'
import EvidenceView from './components/EvidenceView.jsx'
import MapPanel from './components/MapPanel.jsx'
import MetricTiles from './components/MetricTiles.jsx'
import SimulationPanel from './components/SimulationPanel.jsx'
import Toasts from './components/Toasts.jsx'
import TopBar from './components/TopBar.jsx'

function useBackendStream() {
  const retry = useRef(0)

  useEffect(() => {
    let closed = false
    let socket

    const connect = () => {
      if (closed) return
      storeActions.setConnection('connecting')
      try {
        socket = new WebSocket(wsUrl())
      } catch {
        scheduleRetry()
        return
      }

      socket.onopen = () => {
        retry.current = 0
        storeActions.setConnection('open')
      }

      socket.onmessage = (ev) => {
        let e
        try {
          e = JSON.parse(ev.data)
        } catch {
          return
        }
        switch (e.type) {
          case 'hello':
            storeActions.onHello(e)
            break
          case 'telemetry_tick':
            storeActions.onTick(e)
            break
          case 'state_change':
            storeActions.onStateChange(e)
            break
          case 'alert':
            storeActions.onAlert(e)
            break
          case 'camera_status':
            storeActions.onCameraStatus(e)
            break
          case 'scenario':
            storeActions.onScenario(e)
            break
          case 'mode_change':
            storeActions.setMode(e.mode)
            break
          default:
            console.warn('[AquaTrace] unknown WS message type:', e.type)
            break
        }
      }

      socket.onclose = () => {
        if (closed) return
        storeActions.setConnection('closed')
        scheduleRetry()
      }
      socket.onerror = () => {
        try {
          socket.close()
        } catch {
          /* noop */
        }
      }
    }

    const scheduleRetry = () => {
      retry.current += 1
      const delay = Math.min(8000, 700 * retry.current)
      setTimeout(connect, delay)
    }

    connect()
    return () => {
      closed = true
      try {
        socket?.close()
      } catch {
        /* noop */
      }
    }
  }, [])

  // REST bootstrap + resilience poll (thumbnails, registry, worst state)
  // NOTE: the poll only feeds camera/registry data — it must never mark the
  // connection down. `connection` reflects actual WebSocket state only.
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const data = await get('/cameras')
        if (!alive) return
        storeActions.hydrate(data)
      } catch {
        if (!alive) return
        if (store.get().connection !== 'open') storeActions.setConnection('closed')
      }
    }
    load()
    const id = setInterval(load, 3000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  // hydrate alert feed once, then rely on the socket
  useEffect(() => {
    let alive = true
    get('/alerts')
      .then((d) => alive && storeActions.setAlerts(d.alerts || []))
      .catch(() => {})
    get('/agent/log?limit=60')
      .then((d) => alive && storeActions.setDecisions(d.decisions || []))
      .catch(() => {})
    // operating mode (demo | live) — controls which controls are shown
    get('/api/mode')
      .then((d) => alive && storeActions.setMode(d?.mode))
      .catch(() => {})
    // precipitation outlook (Open-Meteo) — server caches 30 min; poll every 5
    const loadForecast = () =>
      get('/api/forecast')
        .then((d) => alive && storeActions.setForecast(d))
        .catch(() => {})
    loadForecast()
    const forecastId = setInterval(loadForecast, 5 * 60 * 1000)
    return () => {
      alive = false
      clearInterval(forecastId)
    }
  }, [])
}

function Ticker() {
  const cameras = useStore((s) => s.cameras)
  const scenario = useStore((s) => s.scenario)
  const items = cameras.length
    ? cameras.flatMap((c) => [
        `${(c.camera_id || c.id)} ${(c.ratio * 100 || 0).toFixed(1)}% AREA`,
        `FLOW ${(c.flow || 0).toFixed(2)} px/f`,
        `STATE ${(c.state || 'normal').toUpperCase()}`,
        `CONF ${(c.conf || 0).toFixed(2)}`,
        `ANOMALY ${(c.anomaly || 0).toFixed(2)}x`,
      ])
    : ['AQUATRACE TELEMETRY STREAM STANDBY']
  const loop = [...items, ...items, ...items, ...items]

  return (
    <div className="relative flex h-8 shrink-0 items-center gap-3 overflow-hidden border-t border-white/8 bg-glass px-3 backdrop-blur">
      <span className="z-10 flex shrink-0 items-center gap-1.5 rounded border border-live/40 bg-live/10 px-2 py-[3px] text-[9px] font-semibold uppercase tracking-[0.16em] text-live">
        <span className="relative inline-block h-1.5 w-1.5 rounded-full bg-live">
          <span className="absolute inset-0 rounded-full bg-live" style={{ animation: 'pulse-dot 1.6s infinite' }} />
        </span>
        live telemetry
      </span>
      <div className="relative min-w-0 flex-1 overflow-hidden">
        <div className="marquee-track flex w-max gap-6 whitespace-nowrap">
          {loop.map((t, i) => (
            <span key={i} className="num text-[10px] tracking-[0.08em] text-muted">
              <span className="mr-1.5 text-live">·</span>
              {t}
            </span>
          ))}
        </div>
        <div className="pointer-events-none absolute inset-y-0 left-0 w-10 bg-gradient-to-r from-abyss to-transparent" />
        <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-abyss to-transparent" />
      </div>
      <span className="num hidden shrink-0 text-[10px] text-faint sm:block">
        {scenario?.running ? `DEMO ${scenario.scenario}` : 'monitoring'} · 5 Hz × {cameras.length || 0} cams
      </span>
    </div>
  )
}

export default function App() {
  useBackendStream()
  const connection = useStore((s) => s.connection)
  const mapExpanded = useStore((s) => s.mapExpanded)

  return (
    <div className="noise-texture relative flex h-full flex-col overflow-hidden bg-abyss">
      <TopBar />
      {connection === 'closed' ? (
        <div className="flex shrink-0 items-center justify-center gap-2 border-b border-watch/30 bg-watch/10 px-3 py-1.5">
          <span className="relative inline-block h-1.5 w-1.5 rounded-full bg-watch">
            <span className="absolute inset-0 rounded-full bg-watch" style={{ animation: 'pulse-dot 1.6s infinite' }} />
          </span>
          <span className="num text-[10px] uppercase tracking-[0.16em] text-watch">
            Backend unreachable — retrying…
          </span>
        </div>
      ) : null}

      {mapExpanded ? (
        <main className="relative flex min-h-0 flex-1 flex-col overflow-hidden p-2">
          <MapPanel isExpanded={true} />
        </main>
      ) : (
        <main className="grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-y-auto p-2 lg:grid-cols-[300px_minmax(0,1fr)_340px] lg:overflow-hidden">
          <section className="flex min-h-0 flex-col gap-2 lg:overflow-hidden">
            <CameraList />
            <SimulationPanel />
          </section>

          <section className="flex min-h-0 flex-col gap-2 lg:overflow-hidden">
            <MapPanel isExpanded={false} />
            <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 xl:grid-cols-[minmax(0,1fr)_268px]">
              <ChartPanel />
              <MetricTiles />
            </div>
          </section>

          <section className="flex min-h-0 flex-col gap-2 lg:overflow-hidden">
            <div className="flex min-h-0 flex-1 flex-col">
              <AlertFeed />
            </div>
            <DecisionLog />
          </section>
        </main>
      )}

      <Ticker />
      <Toasts />
      <EvidenceView />
    </div>
  )
}
