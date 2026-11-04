import { useStore } from '../lib/store.js'
import { Panel, PanelHeader, Skeleton, fmtTime } from './ui.jsx'

const TO_COLOR = {
  normal: '#10B981',
  watch: '#F59E0B',
  escalating: '#F97316',
  verifying: '#F97316',
  critical: '#EF4444',
}

/**
 * Renders the agent's decision log (GET /agent/log + WS state_change events
 * collected by the store) — the audit trail of why the agent moved each
 * camera between states.
 */
export default function DecisionLog() {
  const decisions = useStore((s) => s.decisions)
  const loading = useStore((s) => s.loading)
  const rows = (decisions || []).slice(0, 8)

  return (
    <Panel className="flex min-h-0 shrink-0 flex-col overflow-hidden">
      <PanelHeader
        title="agent reasoning"
        sub={`${decisions?.length || 0} decisions`}
      />
      <div className="max-h-[240px] min-h-0 space-y-1.5 overflow-y-auto p-2">
        {loading && !rows.length ? (
          Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)
        ) : !rows.length ? (
          <div className="rounded border border-dashed border-white/10 px-3 py-4 text-center">
            <div className="text-[11px] font-medium text-ink">No decisions yet</div>
            <div className="mt-1 text-[10px] leading-relaxed text-faint">
              The agent logs a reasoning entry on every state transition.
            </div>
          </div>
        ) : (
          rows.map((d, i) => {
            const color = TO_COLOR[d.to] || '#8B98A9'
            return (
              <div key={d.at && d.camera_id ? `${d.at}-${d.camera_id}-${i}` : i} className="rounded border border-white/6 bg-white/2 px-2.5 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="num text-[9px] text-faint">{fmtTime(d.at)}</span>
                  <span className="num truncate text-[9px] uppercase tracking-[0.14em] text-muted">
                    {d.camera_id || '—'}
                  </span>
                  <span className="num shrink-0 text-[9px] uppercase tracking-[0.1em]" style={{ color }}>
                    {(d.from ?? '?')} → {(d.to ?? '?')}
                  </span>
                </div>
                {d.reasoning ? (
                  <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-muted">{d.reasoning}</p>
                ) : null}
              </div>
            )
          })
        )}
      </div>
    </Panel>
  )
}
