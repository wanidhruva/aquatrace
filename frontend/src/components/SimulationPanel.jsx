import { useEffect, useRef, useState } from 'react'
import { drillStatus, post, startDrill, stopDrill } from '../lib/api.js'
import { toast, useStore } from '../lib/store.js'
import { Panel, PanelHeader } from './ui.jsx'

const SCENARIOS = [
  {
    id: 'normal',
    label: 'normal',
    hint: 'calm channel · all nominal',
    color: '#10B981',
  },
  {
    id: 'gradual_rise',
    label: 'gradual rise',
    hint: '5% → 60% · verification gate',
    color: '#F59E0B',
  },
  {
    id: 'flash_surge',
    label: 'flash surge',
    hint: 'instant escalation · high conf',
    color: '#EF4444',
  },
]

export default function SimulationPanel() {
  const scenario = useStore((s) => s.scenario)
  const cameras = useStore((s) => s.cameras)
  const mode = useStore((s) => s.mode)
  const [busy, setBusy] = useState('')
  const [now, setNow] = useState(Date.now())

  // ---- drill state (live mode): safe synthetic flood drill as an extra
  // virtual camera — fires the full detection → alert → SNS/S3 pipeline
  // without touching the real vision engines. Polled every 5 s.
  const [drill, setDrill] = useState({ active: false, scenario: null })
  const drillStart = useRef(null)
  const wasActive = useRef(false)

  useEffect(() => {
    if (mode !== 'live') return undefined
    let alive = true
    const poll = async () => {
      try {
        const s = await drillStatus()
        if (!alive) return
        const active = !!s?.active
        setDrill({ active, scenario: s?.scenario ?? null })
        if (active && !wasActive.current) drillStart.current = Date.now()
        if (!active) drillStart.current = null
        wasActive.current = active
      } catch {
        // backend drill endpoints may not exist yet — keep last known state
      }
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [mode])

  // tick the drill elapsed clock only while a drill is active
  useEffect(() => {
    if (!drill.active) return undefined
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [drill.active])

  const runDrill = async (id) => {
    setBusy(id)
    try {
      await startDrill(id)
      toast({ kind: 'info', title: `DRILL STARTED · ${id.toUpperCase()}`, body: 'Safe synthetic drill — real engines untouched. Watch the pipeline fire.', tone: 'live' })
    } catch (e) {
      toast({ kind: 'warn', title: 'DRILL FAILED', body: e?.body?.detail || e.message || 'backend unreachable', tone: 'crit', sticky: true })
    } finally {
      setBusy('')
    }
  }

  const stopDrillRun = async () => {
    setBusy('stop')
    try {
      await stopDrill()
      toast({ kind: 'info', title: 'DRILL STOPPED', body: 'Drill camera removed — real monitoring continues.', tone: 'nominal' })
    } catch (e) {
      toast({ kind: 'warn', title: 'STOP FAILED', body: e?.body?.detail || e.message || 'backend unreachable', tone: 'crit', sticky: true })
    } finally {
      setBusy('')
    }
  }

  const running = scenario?.running

  // tick the elapsed clock only while a demo scenario is live
  useEffect(() => {
    if (!running) return undefined
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [running])

  const elapsed =
    running && scenario?.at
      ? Math.max(0, Math.floor((now - new Date(scenario.at).getTime()) / 1000))
      : 0
  const peak = cameras.reduce((m, c) => Math.max(m, c.anomaly || 0), 0)

  if (mode === 'live') {
    const drillElapsed =
      drill.active && drillStart.current
        ? Math.max(0, Math.floor((now - drillStart.current) / 1000))
        : 0
    return (
      <Panel className="shrink-0 overflow-hidden">
        <PanelHeader
          title="simulation"
          right={
            drill.active ? (
              <span className="flex items-center gap-2">
                <span className="num text-[9px] text-faint">T+{drillElapsed}s</span>
                <span className="flex items-center gap-1.5 rounded border border-purple-400/50 bg-purple-400/10 px-2 py-[3px]">
                  <span className="relative inline-block h-1.5 w-1.5 rounded-full bg-purple-400">
                    <span className="absolute inset-0 rounded-full bg-purple-400" style={{ animation: 'pulse-dot 1.4s infinite' }} />
                  </span>
                  <span className="text-[9px] font-semibold tracking-[0.16em] text-purple-300">DRILL</span>
                </span>
              </span>
            ) : (
              <span className="num text-[9px] text-faint">drill idle · real engines untouched</span>
            )
          }
        />
        <div className="space-y-1.5 p-2">
          {SCENARIOS.map((s) => {
            const active = drill.active && drill.scenario === s.id
            return (
              <button
                key={s.id}
                onClick={() => runDrill(s.id)}
                disabled={!!busy}
                className="group flex w-full items-center gap-2.5 rounded-md border px-2.5 py-2 text-left transition-all duration-300 disabled:opacity-50"
                style={{
                  borderColor: active ? `${s.color}88` : 'rgba(255,255,255,.08)',
                  background: active ? `${s.color}14` : 'rgba(255,255,255,.02)',
                }}
              >
                <span
                  className="h-6 w-1 rounded-full transition-all"
                  style={{ background: s.color, boxShadow: active ? `0 0 12px ${s.color}` : undefined }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-[11px] font-medium uppercase tracking-[0.14em] text-ink">{s.label}</span>
                  <span className="num block truncate text-[9px] text-faint">{s.hint}</span>
                </span>
                <span className="num text-[9px]" style={{ color: s.color }}>
                  {busy === s.id ? '…' : active ? 'live' : 'drill'}
                </span>
              </button>
            )
          })}

          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={stopDrillRun}
              disabled={!!busy || !drill.active}
              className="flex-1 rounded-md border border-white/10 px-2 py-1.5 text-[10px] uppercase tracking-[0.14em] text-muted transition hover:border-purple-400/50 hover:text-purple-300 disabled:opacity-35"
            >
              stop drill
            </button>
            <span className="num text-[9px] text-faint">POST /api/drill/*</span>
          </div>
        </div>
      </Panel>
    )
  }

  const run = async (id) => {
    setBusy(id)
    try {
      await post(`/simulate/${id}`)
      toast({
        kind: 'info',
        title: `SCENARIO · ${id.toUpperCase()}`,
        body: id === 'normal' ? 'Returned all cameras to the calm baseline.' : 'Vision engines restarted — escalation beginning.',
        tone: id === 'normal' ? 'nominal' : 'live',
      })
    } catch (e) {
      toast({ kind: 'warn', title: 'SCENARIO FAILED', body: e?.body?.detail || e.message || 'backend unreachable', tone: 'crit', sticky: true })
    } finally {
      setBusy('')
    }
  }

  const stop = async () => {
    setBusy('stop')
    try {
      await post('/simulate/stop')
      toast({ kind: 'info', title: 'SCENARIO STOPPED', body: 'Vision engines halted.', tone: 'faint' })
    } catch (e) {
      toast({ kind: 'warn', title: 'STOP FAILED', body: e?.body?.detail || e.message || 'backend unreachable', tone: 'crit', sticky: true })
    } finally {
      setBusy('')
    }
  }

  return (
    <Panel className="shrink-0 overflow-hidden">
      <PanelHeader
        title="simulation"
        right={
          running ? (
            <span className="flex items-center gap-2">
              <span className="num text-[9px] text-faint">
                T+{elapsed}s · peak {peak.toFixed(1)}×
              </span>
              <span className="flex items-center gap-1.5 rounded border border-watch/50 bg-watch/12 px-2 py-[3px]">
                <span className="relative inline-block h-1.5 w-1.5 rounded-full bg-watch">
                  <span className="absolute inset-0 rounded-full bg-watch" style={{ animation: 'pulse-dot 1.4s infinite' }} />
                </span>
                <span className="text-[9px] font-semibold tracking-[0.16em] text-watch">DEMO MODE</span>
              </span>
            </span>
          ) : (
            <span className="num text-[9px] text-faint">idle</span>
          )
        }
      />
      <div className="space-y-1.5 p-2">
        {SCENARIOS.map((s) => {
          const active = running && scenario?.scenario === s.id
          return (
            <button
              key={s.id}
              onClick={() => run(s.id)}
              disabled={!!busy}
              className="group flex w-full items-center gap-2.5 rounded-md border px-2.5 py-2 text-left transition-all duration-300 disabled:opacity-50"
              style={{
                borderColor: active ? `${s.color}88` : 'rgba(255,255,255,.08)',
                background: active ? `${s.color}14` : 'rgba(255,255,255,.02)',
              }}
            >
              <span
                className="h-6 w-1 rounded-full transition-all"
                style={{ background: s.color, boxShadow: active ? `0 0 12px ${s.color}` : undefined }}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-[11px] font-medium uppercase tracking-[0.14em] text-ink">{s.label}</span>
                <span className="num block truncate text-[9px] text-faint">{s.hint}</span>
              </span>
              <span className="num text-[9px]" style={{ color: s.color }}>
                {busy === s.id ? '…' : active ? 'live' : 'run'}
              </span>
            </button>
          )
        })}

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={stop}
            disabled={!!busy || !running}
            className="flex-1 rounded-md border border-white/10 px-2 py-1.5 text-[10px] uppercase tracking-[0.14em] text-muted transition hover:border-crit/50 hover:text-crit disabled:opacity-35"
          >
            stop engines
          </button>
          <span className="num text-[9px] text-faint">POST /simulate/*</span>
        </div>
      </div>
    </Panel>
  )
}
