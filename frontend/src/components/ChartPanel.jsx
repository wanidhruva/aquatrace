import { useMemo, useState } from 'react'
import { storeActions, useStore } from '../lib/store.js'
import { Panel, PanelHeader, StateChip } from './ui.jsx'

const VB_W = 800
const VB_H = 260

function smoothPath(pts) {
  if (pts.length < 2) return ''
  let d = `M ${pts[0][0].toFixed(2)} ${pts[0][1].toFixed(2)}`
  for (let i = 0; i < pts.length - 1; i += 1) {
    const p0 = pts[i === 0 ? 0 : i - 1]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[i + 2] || p2
    const c1x = p1[0] + (p2[0] - p0[0]) / 6
    const c1y = p1[1] + (p2[1] - p0[1]) / 6
    const c2x = p2[0] - (p3[0] - p1[0]) / 6
    const c2y = p2[1] - (p3[1] - p1[1]) / 6
    d += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`
  }
  return d
}

export default function ChartPanel() {
  const selected = useStore((s) => s.selected)
  const series = useStore((s) => s.series)
  const cameras = useStore((s) => s.cameras)
  const [window_, setWindow] = useState(120)

  const cam = cameras.find((c) => (c.camera_id || c.id) === selected)
  const rate = series[selected]?.rate || []
  const baseline = cam?.baseline ?? (rate.length ? rate[rate.length - 1].baseline : 0.0004)
  const anomaly = cam?.anomaly ?? (rate.length ? rate[rate.length - 1].anomaly : 0)
  const state = cam?.signal === 'signal_lost' ? 'signal_lost' : cam?.state || 'normal'

  const pts = useMemo(() => {
    const data = rate.slice(-window_)
    if (!data.length) return []
    const maxRate = Math.max(
      ...data.map((d) => d.rate),
      baseline * 5.6,
      0.0012,
    )
    const yMax = maxRate * 1.18
    const t0 = new Date(data[0].t).getTime()
    const t1 = new Date(data[data.length - 1].t).getTime()
    const span = Math.max(1, t1 - t0)
    const X = (t) => ((new Date(t).getTime() - t0) / span) * VB_W
    const Y = (v) => VB_H - (Math.max(0, v) / yMax) * (VB_H - 14) - 6
    return { data, X, Y, yMax, t0, t1 }
  }, [rate, window_, baseline])

  const thresholds = [2, 3, 5].map((m) => ({ m, v: baseline * m }))
  const line = pts.data ? pts.data.map((d) => [pts.X(d.t), pts.Y(d.rate)]) : []
  const path = smoothPath(line)
  const area = path ? `${path} L ${VB_W} ${VB_H} L 0 ${VB_H} Z` : ''
  const last = line[line.length - 1]
  const current = pts.data?.[pts.data.length - 1]

  const yPct = (v) => (pts.yMax ? (1 - (v / pts.yMax) * ((VB_H - 20) / VB_H) - 6 / VB_H) * 100 : 100)

  return (
    <Panel className="flex min-h-[220px] flex-col overflow-hidden">
      <PanelHeader
        title="water expansion rate"
        sub={`ratio/s · ${selected}`}
        right={
          <div className="flex items-center gap-2">
            <StateChip state={state} />
            {[60, 120, 300].map((w) => (
              <button
                key={w}
                onClick={() => setWindow(w)}
                className={`num rounded border px-1.5 py-0.5 text-[9px] transition ${
                  window_ === w
                    ? 'border-live/50 bg-live/12 text-live'
                    : 'border-white/8 text-faint hover:border-white/20 hover:text-muted'
                }`}
              >
                {w / 60}m
              </button>
            ))}
          </div>
        }
      />

      <div className="relative min-h-0 flex-1 px-3 pb-2 pt-2">
        {!pts.data || pts.data.length < 2 ? (
          <div className="flex h-full min-h-[160px] flex-col items-center justify-center gap-2 rounded border border-dashed border-white/10">
            <div className="num text-[11px] text-faint">waiting for telemetry…</div>
            <div className="text-[10px] text-faint/70">expansion-rate series appears at 1 Hz once cameras report</div>
          </div>
        ) : (
          <>
            <svg
              viewBox={`0 0 ${VB_W} ${VB_H}`}
              preserveAspectRatio="none"
              className="absolute inset-x-3 bottom-6 top-2 h-[calc(100%-2rem)] w-[calc(100%-1.5rem)]"
            >
              <defs>
                <linearGradient id="rateFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22D3EE" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#22D3EE" stopOpacity="0.02" />
                </linearGradient>
                <filter id="rateGlow" x="-20%" y="-40%" width="140%" height="180%">
                  <feGaussianBlur stdDeviation="3.4" result="b" />
                  <feMerge>
                    <feMergeNode in="b" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>

              {/* learned baseline band */}
              <rect x="0" y={VB_H - (baseline / pts.yMax) * VB_H} width={VB_W} height={(baseline / pts.yMax) * VB_H} fill="rgba(16,185,129,.14)" />
              {/* grid */}
              {[0.25, 0.5, 0.75].map((f) => (
                <line key={f} x1="0" y1={VB_H * f} x2={VB_W} y2={VB_H * f} stroke="rgba(255,255,255,.06)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
              ))}
              {/* anomaly thresholds */}
              {thresholds.map(({ m, v }, i) => (
                <g key={m}>
                  <line
                    x1="0"
                    y1={(VB_H - (v / pts.yMax) * VB_H)}
                    x2={VB_W}
                    y2={(VB_H - (v / pts.yMax) * VB_H)}
                    stroke={['#F59E0B', '#F97316', '#EF4444'][i]}
                    strokeOpacity="0.55"
                    strokeDasharray="6 6"
                    strokeWidth="1"
                    vectorEffect="non-scaling-stroke"
                  />
                </g>
              ))}

              {area ? <path d={area} fill="url(#rateFill)" /> : null}
              {path ? (
                <path d={path} fill="none" stroke="#22D3EE" strokeWidth="2" vectorEffect="non-scaling-stroke" filter="url(#rateGlow)" strokeLinejoin="round" />
              ) : null}

              {last ? (
                <g>
                  <circle cx={last[0]} cy={last[1]} r="9" fill="#22D3EE" opacity="0.18">
                    <animate attributeName="r" values="6;15;6" dur="1.9s" repeatCount="indefinite" />
                    <animate attributeName="opacity" values="0.3;0;0.3" dur="1.9s" repeatCount="indefinite" />
                  </circle>
                  <circle cx={last[0]} cy={last[1]} r="4" fill="#22D3EE" />
                </g>
              ) : null}
            </svg>

            {/* axis + threshold labels (HTML keeps type crisp) */}
            <div className="pointer-events-none absolute inset-x-3 bottom-1 flex justify-between">
              <span className="num text-[9px] text-faint">
                {new Date(pts.t0).toISOString().slice(11, 19)}Z
              </span>
              <span className="num text-[9px] text-faint">now</span>
            </div>
            <div className="pointer-events-none absolute inset-y-2 left-3 flex flex-col justify-between">
              <span className="num text-[9px] text-faint">{pts.yMax.toExponential(1)}</span>
              <span className="num text-[9px] text-faint">0</span>
            </div>

            <div className="pointer-events-none absolute right-4 top-3 space-y-1 text-right">
              {thresholds.map(({ m, v }, i) => (
                <div key={m} className="num text-[9px]" style={{ color: ['#F59E0B', '#F97316', '#EF4444'][i] }}>
                  {m}× = {v.toExponential(2)}
                </div>
              ))}
              <div className="num text-[9px] text-nominal">baseline = {baseline.toExponential(2)}</div>
            </div>

            <div className="pointer-events-none absolute right-4 bottom-8 rounded-md border border-live/40 bg-abyss/90 px-2.5 py-1.5 backdrop-blur">
              <div className="text-[8px] uppercase tracking-[0.16em] text-faint">anomaly</div>
              <div
                className="num text-[18px] leading-none"
                style={{
                  color: anomaly > 5 ? '#EF4444' : anomaly > 3 ? '#F97316' : anomaly > 2 ? '#F59E0B' : '#22D3EE',
                }}
              >
                {anomaly.toFixed(1)}×
              </div>
              <div className="num text-[9px] text-muted">
                {current ? current.rate.toFixed(5) : '—'} ratio/s
              </div>
            </div>
          </>
        )}
      </div>
    </Panel>
  )
}
