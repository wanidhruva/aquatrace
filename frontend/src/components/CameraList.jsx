import { useEffect, useRef, useState } from 'react'
import { buildUrl } from '../lib/api.js'
import { storeActions, useStore } from '../lib/store.js'
import { Panel, PanelHeader, Skeleton, StateChip, cameraId } from './ui.jsx'

const EDGE = {
  normal: 'rgba(16,185,129,.65)',
  watch: 'rgba(245,158,11,.7)',
  escalating: 'rgba(249,115,22,.75)',
  verifying: 'rgba(249,115,22,.75)',
  critical: 'rgba(239,68,68,.85)',
  signal_lost: 'rgba(90,107,130,.6)',
}

function Thumb({ cid, state, active, offline, index }) {
  const [src, setSrc] = useState(null)
  const [frame, setFrame] = useState(0)
  const [visible, setVisible] = useState(false)
  const [backing, setBacking] = useState(false)
  const fails = useRef(0)

  // every camera polls its own frame (staggered by index to spread load):
  // the selected camera refreshes at 1 Hz, the rest at ~0.5 Hz.
  // after 3 consecutive failures, back off to one attempt every 8 s.
  const period = active ? 1000 : 2000
  useEffect(() => {
    if (offline) return undefined
    let id
    const start = setTimeout(() => {
      setFrame((f) => f + 1)
      id = setInterval(() => setFrame((f) => f + 1), backing ? 8000 : period)
    }, (index % 4) * 500)
    return () => {
      clearTimeout(start)
      if (id) clearInterval(id)
    }
  }, [offline, backing, period, index])

  useEffect(() => {
    if (offline) return
    setSrc(buildUrl(`/cameras/${cid}/frame.jpg?t=${frame}`))
  }, [cid, frame, offline])

  const onOk = () => {
    setVisible(true)
    fails.current = 0
    if (backing) setBacking(false)
  }
  const onErr = () => {
    setVisible(false)
    fails.current += 1
    if (fails.current >= 3 && !backing) setBacking(true)
  }

  return (
    <div className="relative h-[86px] w-full overflow-hidden rounded-md border border-white/8 bg-abyss">
      {src ? (
        <img
          src={src}
          alt={`${cid} annotated feed`}
          className={`h-full w-full object-cover transition-opacity duration-200 ${visible ? 'opacity-100' : 'opacity-0'}`}
          style={{ objectPosition: 'center 62%' }}
          onLoad={onOk}
          onError={onErr}
        />
      ) : null}
      {!visible && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1">
          <span className="num text-[9px] uppercase tracking-[0.18em] text-faint">
            {offline ? 'backend unreachable' : backing ? 'feed retrying…' : 'waiting for feed'}
          </span>
        </div>
      )}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-abyss/85 via-transparent to-abyss/30" />
      <div
        className="pointer-events-none absolute inset-0"
        style={{ boxShadow: `inset 0 0 0 1px ${EDGE[state] || EDGE.normal}` }}
      />
      <div className="scanline pointer-events-none absolute inset-x-0 h-10 bg-gradient-to-b from-transparent via-live/12 to-transparent" />
      <div className="absolute left-1.5 top-1.5">
        <StateChip state={state} />
      </div>
      <div className="num absolute bottom-1.5 right-2 text-[9px] text-live/80">AQUATRACE</div>
    </div>
  )
}

export default function CameraList() {
  const cameras = useStore((s) => s.cameras)
  const selected = useStore((s) => s.selected)
  const loading = useStore((s) => s.loading)
  const connection = useStore((s) => s.connection)
  const forecast = useStore((s) => s.forecast)
  const offline = connection === 'closed'

  return (
    <Panel className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <PanelHeader title="cameras" sub={`${cameras.length} online`} />
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
        {loading && offline ? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-white/10 px-4 text-center">
            <div className="num text-[12px] text-watch">BACKEND UNREACHABLE</div>
            <div className="max-w-[220px] text-[10px] leading-relaxed text-faint">
              No response from the API or stream. Check that the backend is running, then this
              panel will populate automatically.
            </div>
          </div>
        ) : loading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-white/8 p-2">
              <Skeleton className="mb-2 h-3 w-24" />
              <Skeleton className="h-[86px] w-full" />
            </div>
          ))
        ) : (
          cameras.map((c, i) => {
            const cid = cameraId(c)
            const isSel = selected === cid
            const state = c.signal === 'signal_lost' ? 'signal_lost' : c.state
            return (
              <button
                key={cid}
                onClick={() => storeActions.select(cid)}
                className={`w-full rounded-lg border p-2 text-left transition-all duration-300 ${
                  isSel
                    ? 'bg-live/8'
                    : 'border-white/8 bg-white/2 hover:border-white/16 hover:bg-white/4'
                }`}
                style={{
                  borderColor: isSel ? 'rgba(34,211,238,.55)' : undefined,
                  boxShadow: isSel ? '0 0 24px rgba(34,211,238,.22)' : undefined,
                }}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-[12px] font-medium text-ink">{c.name || cid}</div>
                    <div className="num truncate text-[9px] uppercase tracking-[0.16em] text-faint">
                      {cid} · {c.zone}
                    </div>
                  </div>
                  <StateChip state={state} />
                </div>

                <Thumb cid={cid} state={state} active={isSel} offline={offline} index={i} />

                <div className="mt-2 grid grid-cols-3 gap-1">
                  <Metric label="AREA" value={c.ratio != null ? `${(c.ratio * 100).toFixed(1)}%` : '—'} tone={state} />
                  <Metric label="FLOW" value={c.flow != null ? c.flow.toFixed(2) : '—'} unit="px/f" tone={state} />
                  <Metric label="CONF" value={c.conf != null ? c.conf.toFixed(2) : '—'} tone={state} />
                </div>

                <ForecastBadge forecast={forecast} zone={c.zone} />
              </button>
            )
          })
        )}
      </div>
    </Panel>
  )
}

function Metric({ label, value, unit, tone }) {
  const color = tone === 'critical' ? '#EF4444' : tone === 'escalating' || tone === 'verifying' ? '#F97316' : tone === 'watch' ? '#F59E0B' : '#E8EEF4'
  return (
    <div className="rounded border border-white/6 bg-white/2 px-1.5 py-1">
      <div className="text-[8px] uppercase tracking-[0.14em] text-faint">{label}</div>
      <div className="num text-[12px] leading-tight" style={{ color }}>
        {value}
        {unit ? <span className="ml-0.5 text-[8px] text-faint">{unit}</span> : null}
      </div>
    </div>
  )
}

// Per-camera precipitation badge (Open-Meteo via /api/forecast). Neutral when
// the feed is unavailable — an unavailable forecast is never an error state.
function ForecastBadge({ forecast, zone }) {
  const enabled = forecast?.enabled !== false
  const fz = (forecast?.zones || []).find((z) => z.zone === zone)
  const live = enabled && fz && fz.status && fz.status !== 'unavailable'
  const color = !live ? '#5A6B80' : fz.gate?.active ? '#F59E0B' : '#22D3EE'
  const text = !enabled
    ? 'off'
    : live
      ? `${fz.prob_max_6h ?? '—'}% · ${fz.mm_6h ?? '—'} mm/6h`
      : 'unavailable'
  return (
    <div className="mt-1.5 flex items-center justify-between gap-2 rounded border border-white/6 bg-white/2 px-1.5 py-1">
      <span className="text-[8px] uppercase tracking-[0.14em] text-faint">forecast</span>
      <span className="num text-[10px]" style={{ color }} title={live ? fz.advisory || forecast?.source : undefined}>
        {text}
      </span>
    </div>
  )
}
