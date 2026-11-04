import { useState } from 'react'
import { storeActions, useStore } from '../lib/store.js'
import { Panel, PanelHeader, Skeleton, StateChip, fmtTime } from './ui.jsx'

const SEV = {
  watch: { c: '#F59E0B', label: 'WATCH' },
  escalating: { c: '#F97316', label: 'ESCALATING' },
  critical: { c: '#EF4444', label: 'CRITICAL' },
}

const FILTERS = ['all', 'critical', 'escalating', 'watch']

function AlertCard({ alert, onOpen }) {
  const sev = SEV[alert.severity] || SEV.watch
  const active = alert.status === 'active'
  return (
    <div
      onClick={onOpen}
      className="group w-full rounded-lg border bg-white/2 p-2.5 text-left transition-all duration-300 hover:bg-white/4 cursor-pointer"
      style={{
        borderColor: active ? `${sev.c}66` : 'rgba(255,255,255,.08)',
        borderLeftWidth: 3,
        borderLeftColor: sev.c,
        boxShadow: active && alert.severity === 'critical' ? `0 0 26px ${sev.c}33` : undefined,
        opacity: alert.status === 'resolved' || alert.status === 'false_positive' ? 0.62 : 1,
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className="text-[9px] font-semibold uppercase tracking-[0.18em]"
          style={{ color: sev.c }}
        >
          {sev.label}
        </span>
        <span className="num text-[9px] text-faint">{fmtTime(alert.started_at)}</span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="truncate text-[12px] font-medium text-ink">{alert.camera_id}</span>
        <span className="flex items-center gap-1">
          {alert.drill ? (
            <span
              className="num rounded border border-purple-400/40 bg-purple-400/10 px-1.5 py-[1px] text-[9px] uppercase text-purple-300"
              title="Synthetic flood drill — safe test alert, not a real event"
            >
              drill
            </span>
          ) : null}
          {alert.aws?.sns_message_id ? (
            <span
              className="num rounded border border-emerald-400/30 bg-emerald-400/10 px-1.5 py-[1px] text-[9px] uppercase text-emerald-300"
              title={`SNS delivered: ${alert.aws.sns_message_id}`}
            >
              sns ✓
            </span>
          ) : null}
          {alert.aws?.s3_frame_url ? (
            <span
              className="num rounded border border-sky-400/30 bg-sky-400/10 px-1.5 py-[1px] text-[9px] uppercase text-sky-300"
              title="Evidence backed to Amazon S3"
            >
              s3 ✓
            </span>
          ) : null}
          <span className="num rounded border border-white/10 px-1.5 py-[1px] text-[9px] uppercase text-muted">
            {(alert.status ?? 'active').replace('_', ' ')}
          </span>
        </span>
      </div>
      <p className="mt-1.5 line-clamp-2 text-[10px] leading-relaxed text-muted">{alert.reasoning}</p>

      {/* Evacuation Routes Expand Map Button on Alert */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          storeActions.openEvacuationMap(alert)
        }}
        className="mt-2.5 flex w-full items-center justify-center gap-1.5 rounded border border-cyan-400/40 bg-cyan-500/15 py-1.5 px-2 text-[10px] font-semibold uppercase tracking-wider text-cyan-300 hover:bg-cyan-500/25 hover:border-cyan-400/70 hover:text-white transition cursor-pointer shadow-sm"
        title="Expand map and display emergency evacuation corridors & high-ground shelters"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
          <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
        </svg>
        <span>Expand Map · Evacuation Routes</span>
      </button>

      <div className="mt-2 flex items-center gap-1">
        {(alert.transitions ?? []).slice(-4).map((t, i) => (
          <span key={i} className="h-1 flex-1 rounded-full" style={{ background: tint(t.to) }} />
        ))}
        <span className="num ml-1 text-[9px] text-faint">{(alert.transitions ?? []).length}×</span>
      </div>
    </div>
  )
}

const tint = (to) =>
  to === 'critical' ? '#EF4444' : to === 'escalating' || to === 'verifying' ? '#F97316' : to === 'watch' ? '#F59E0B' : '#10B981'

export default function AlertFeed() {
  const alerts = useStore((s) => s.alerts)
  const loading = useStore((s) => s.loading)
  const [filter, setFilter] = useState('all')
  const active = alerts.filter((a) => a.status === 'active' || a.status === 'acknowledged')
  const counts = { all: alerts.length }
  for (const f of FILTERS.slice(1)) counts[f] = alerts.filter((a) => a.severity === f).length
  const shown = filter === 'all' ? alerts : alerts.filter((a) => a.severity === filter)

  return (
    <Panel className="flex min-h-0 flex-col overflow-hidden">
      <PanelHeader
        title="alert feed"
        sub={`${active.length} active`}
        right={
          <div className="flex items-center gap-2">
            <button
              onClick={() => storeActions.setMapExpanded(true, { showEvacuationRoutes: true })}
              className="flex items-center gap-1 rounded border border-cyan-400/30 bg-cyan-500/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-cyan-300 hover:bg-cyan-500/20 hover:border-cyan-400/60 hover:text-white transition cursor-pointer"
              title="Expand map and display emergency evacuation corridors"
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
              </svg>
              <span>Evac Map</span>
            </button>
            {active.length ? (
              <span className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.16em] text-crit">
                <span className="relative inline-block h-1.5 w-1.5 rounded-full bg-crit">
                  <span className="absolute inset-0 rounded-full bg-crit" style={{ animation: 'pulse-dot 1.5s infinite' }} />
                </span>
                action required
              </span>
            ) : null}
          </div>
        }
      />
      <div className="flex shrink-0 items-center gap-1.5 border-b border-white/8 px-2 py-1.5">
        {FILTERS.map((f) => {
          const on = filter === f
          const c = f === 'all' ? '#22D3EE' : SEV[f].c
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className="num rounded-full border px-2 py-[3px] text-[9px] uppercase tracking-[0.12em] transition"
              style={{
                borderColor: on ? `${c}88` : 'rgba(255,255,255,.1)',
                background: on ? `${c}18` : 'transparent',
                color: on ? c : '#8B98A9',
              }}
            >
              {f === 'all' ? 'all' : SEV[f].label.toLowerCase()} · {counts[f] ?? 0}
            </button>
          )
        })}
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
        {loading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-lg border border-white/8 p-2.5">
              <Skeleton className="mb-2 h-3 w-20" />
              <Skeleton className="mb-1.5 h-3 w-full" />
              <Skeleton className="h-3 w-4/5" />
            </div>
          ))
        ) : alerts.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-white/10 px-4 text-center">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#10B981" strokeWidth="1.4">
              <path d="M12 3 4 6.5V11c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6.5L12 3Z" />
              <path d="m8.5 12 2.4 2.4L15.5 10" />
            </svg>
            <div>
              <div className="text-[12px] font-medium text-ink">No active alerts</div>
              <div className="mt-1 text-[10px] leading-relaxed text-faint">
                All cameras nominal. Run a demo scenario to watch the agent escalate.
              </div>
            </div>
          </div>
        ) : shown.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-white/10 px-4 text-center">
            <div className="num text-[11px] uppercase tracking-[0.16em] text-faint">
              no {filter} alerts
            </div>
            <button
              onClick={() => setFilter('all')}
              className="text-[10px] uppercase tracking-[0.14em] text-live hover:underline"
            >
              show all
            </button>
          </div>
        ) : (
          shown.map((a) => <AlertCard key={a.alert_id} alert={a} onOpen={() => storeActions.setEvidence(a.alert_id)} />)
        )}
      </div>
    </Panel>
  )
}

export { SEV, tint }
