import { useEffect, useState } from 'react'
import { useStore } from '../lib/store.js'
import { Panel, PanelHeader, cameraId} from './ui.jsx'

function useCounter(target, dur = 700) {
  const [v, setV] = useState(target ?? 0)
  useEffect(() => {
    if (target == null) return undefined
    const from = v
    const to = target
    const t0 = performance.now()
    let raf
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur)
      const e = 1 - Math.pow(1 - k, 3)
      setV(from + (to - from) * e)
      if (k < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target])
  return v
}

function Tile({ label, value, sub, tone = '#E8EEF4', wide = false }) {
  return (
    <div
      className={`spotlight rounded-md border border-white/8 bg-white/2 p-3 transition-colors hover:border-white/16 ${wide ? 'col-span-2' : ''}`}
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect()
        e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`)
        e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`)
      }}
    >
      <div className="text-[9px] uppercase tracking-[0.18em] text-faint">{label}</div>
      <div className="num mt-1 text-[22px] leading-none" style={{ color: tone }}>
        {value}
      </div>
      <div className="num mt-1 text-[9px] text-muted">{sub}</div>
    </div>
  )
}

export default function MetricTiles() {
  const selected = useStore((s) => s.selected)
  const cameras = useStore((s) => s.cameras)
  const cam = cameras.find((c) => cameraId(c) === selected)
  // until the first telemetry_tick for this camera arrives, nothing here is real
  const hasTick = !!cam?.hasTick

  const ratio = cam?.ratio ?? null
  const flow = cam?.flow ?? null
  const wl = cam?.wl ?? null
  const conf = cam?.conf ?? null
  const anomaly = cam?.anomaly ?? null
  const baseline = cam?.baseline ?? null
  const wlDelta = cam?.wl_delta_10s ?? null
  const flowStd = cam?.flow_std ?? null
  const signal = cam?.signal || 'live'

  const aRatio = useCounter(ratio ?? 0)
  const aFlow = useCounter(flow ?? 0)
  const aConf = useCounter(conf ?? 0)
  const aWl = useCounter(wl ?? 0)

  const danger =
    !hasTick || anomaly == null
      ? '#E8EEF4'
      : anomaly > 5
        ? '#EF4444'
        : anomaly > 3
          ? '#F97316'
          : anomaly > 2
            ? '#F59E0B'
            : '#E8EEF4'
  const DASH = '—'
  const wlDeltaText =
    wlDelta != null ? `${wlDelta >= 0 ? '+' : ''}${wlDelta.toFixed(1)} px / 10 s` : `Δ ${DASH}`

  if (signal === 'signal_lost') {
    return (
      <Panel className="flex flex-col overflow-hidden">
        <PanelHeader title="telemetry" sub={selected} />
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-4 text-center">
          <div className="num text-[13px] text-watch">SIGNAL LOST</div>
          <p className="max-w-[200px] text-[10px] leading-relaxed text-faint">
            No frames from {selected}. The agent holds the last known state and will not escalate on missing data.
          </p>
        </div>
      </Panel>
    )
  }

  return (
    <Panel className="flex flex-col overflow-hidden">
      <PanelHeader title="telemetry" sub={selected} />
      <div className="grid min-h-0 flex-1 grid-cols-2 content-start gap-2 p-2">
        <Tile
          label="water area"
          value={hasTick ? `${(aRatio * 100).toFixed(1)}%` : DASH}
          sub={hasTick && ratio != null ? `of frame · ${(ratio * 640 * 360).toFixed(0)} px` : 'waiting for telemetry'}
          tone={danger}
        />
        <Tile
          label="flow mean"
          value={hasTick ? aFlow.toFixed(2) : DASH}
          sub={`px/frame · std ${flowStd != null ? flowStd.toFixed(2) : DASH}`}
        />
        <Tile
          label="waterline"
          value={hasTick ? `${aWl.toFixed(0)}` : DASH}
          sub={hasTick ? `px from top · ${wlDeltaText}` : 'waiting for telemetry'}
          tone={wlDelta != null && wlDelta < -1 ? '#22D3EE' : '#E8EEF4'}
        />
        <Tile
          label="agent confidence"
          value={hasTick ? aConf.toFixed(2) : DASH}
          sub={hasTick ? (conf >= 0.8 ? 'gate clear — direct path' : 'below 0.80 — verify first') : 'waiting for telemetry'}
          tone={!hasTick ? '#E8EEF4' : conf >= 0.8 ? '#10B981' : '#F59E0B'}
        />
        <div className="col-span-2 rounded-md border border-white/8 bg-white/2 p-3">
          <div className="flex items-center justify-between">
            <span className="text-[9px] uppercase tracking-[0.18em] text-faint">anomaly score</span>
            <span className="num text-[9px] text-faint">rate ÷ learned baseline</span>
          </div>
          <div className="mt-1 flex items-end justify-between">
            <span className="num text-[26px] leading-none" style={{ color: danger }}>
              {hasTick && anomaly != null ? anomaly.toFixed(2) : DASH}×
            </span>
            <span className="num text-[10px] text-muted">
              baseline {baseline != null ? baseline.toExponential(2) : DASH} ratio/s
            </span>
          </div>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/6">
            <div
              className="h-full rounded-full transition-all duration-700"
              style={{
                width: hasTick && anomaly != null ? `${Math.min(100, (anomaly / 8) * 100)}%` : '0%',
                background: danger,
                boxShadow: `0 0 14px ${danger}`,
              }}
            />
          </div>
        </div>
      </div>
    </Panel>
  )
}
