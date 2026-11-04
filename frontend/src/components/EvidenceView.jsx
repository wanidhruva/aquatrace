import { useEffect, useState } from 'react'
import { acknowledge, buildUrl, downloadReport, falsePositive } from '../lib/api.js'
import { storeActions, toast, useStore } from '../lib/store.js'
import { StateChip, fmtTime } from './ui.jsx'

const COLORS = {
  normal: '#10B981',
  watch: '#F59E0B',
  escalating: '#F97316',
  verifying: '#F97316',
  critical: '#EF4444',
}

function Chip({ state }) {
  const c = COLORS[state] || '#8B98A9'
  return (
    <span
      className="rounded border px-1.5 py-[2px] text-[9px] font-semibold uppercase tracking-[0.16em]"
      style={{ color: c, borderColor: `${c}66`, background: `${c}14` }}
    >
      {(state ?? 'unknown').replace('_', ' ')}
    </span>
  )
}

export default function EvidenceView() {
  const evidenceId = useStore((s) => s.evidenceId)
  const alerts = useStore((s) => s.alerts)
  const [busy, setBusy] = useState('')
  const [imgOk, setImgOk] = useState(true)

  const alert = alerts.find((a) => a.alert_id === evidenceId)

  useEffect(() => {
    setImgOk(true)
  }, [evidenceId])

  useEffect(() => {
    const esc = (e) => {
      if (e.key === 'Escape') storeActions.setEvidence(null)
    }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [])

  if (!alert) return null

  // waterline displacement + flow summary from the alert's telemetry window
  // (falling waterline_y = rising water — matches the agent's escalation cue)
  const wlFlow = (() => {
    const w = alert.telemetry_window
    if (!w?.length) return null
    const wl = w.map((p) => p.waterline_y ?? 0)
    const fl = w.map((p) => p.flow_mean ?? 0)
    return {
      dWl: wl[wl.length - 1] - wl[0],
      avgFl: fl.reduce((a, b) => a + b, 0) / fl.length,
      maxFl: Math.max(...fl),
      wl,
      maxWl: Math.max(...wl, 1),
    }
  })()

  const act = async (kind) => {
    setBusy(kind)
    try {
      if (kind === 'ack') {
        await acknowledge(alert.alert_id)
        toast({ kind: 'info', title: 'ALERT ACKNOWLEDGED', body: `${alert.alert_id} — returned to monitoring.`, tone: 'nominal' })
      } else if (kind === 'fp') {
        await falsePositive(alert.alert_id)
        toast({ kind: 'info', title: 'FALSE POSITIVE LOGGED', body: 'Baseline for this camera raised — feedback loop applied.', tone: 'watch' })
      } else if (kind === 'export') {
        // real incident report PDF — downloads as a file, no print dialog
        try {
          const blob = await downloadReport(alert.alert_id)
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          a.href = url
          a.download = `aquatrace-incident-${alert.alert_id}.pdf`
          document.body.appendChild(a)
          a.click()
          a.remove()
          setTimeout(() => URL.revokeObjectURL(url), 4000)
          toast({ kind: 'info', title: 'REPORT DOWNLOADED', body: `aquatrace-incident-${alert.alert_id}.pdf`, tone: 'nominal' })
        } catch (err) {
          toast({ kind: 'warn', title: 'REPORT FAILED', body: err?.message || 'backend unreachable', tone: 'crit', sticky: true })
        }
      }
    } catch (e) {
      toast({ kind: 'warn', title: 'ACTION FAILED', body: e?.message || 'request failed', tone: 'crit' })
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-abyss/85 p-4 backdrop-blur-sm" onClick={() => storeActions.setEvidence(null)}>
      <div
        className="rise-in grid max-h-[92vh] w-full max-w-[1180px] grid-cols-1 gap-3 overflow-hidden rounded-xl border border-white/10 bg-glass p-3 lg:grid-cols-[1.05fr_1fr]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ------------------------------------------------ evidence frame */}
        <div className="flex min-h-0 flex-col gap-3">
          <div className="flex items-center justify-between px-1">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-[0.24em] text-live">evidence</span>
              <span className="num text-[10px] text-faint">{alert.alert_id}</span>
            </div>
            <button
              onClick={() => storeActions.setEvidence(null)}
              className="rounded border border-white/10 px-2 py-1 text-[10px] text-muted transition hover:border-crit/50 hover:text-crit"
            >
              esc
            </button>
          </div>

          <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border border-white/10 bg-abyss">
            {alert.evidence_snapshot_url && imgOk ? (
              <img
                src={buildUrl(alert.evidence_snapshot_url)}
                alt="annotated evidence frame"
                className="h-full w-full object-cover"
                onError={() => setImgOk(false)}
              />
            ) : (
              <div className="grid h-full place-items-center p-6 text-center">
                <div>
                  <div className="num text-[12px] text-watch">SNAPSHOT UNAVAILABLE</div>
                  <p className="mt-2 max-w-[320px] text-[10px] leading-relaxed text-faint">
                    The evidence frame could not be loaded. Telemetry and reasoning below remain valid.
                  </p>
                </div>
              </div>
            )}
            <div className="absolute bottom-2 left-2 flex items-center gap-2 rounded border border-white/10 bg-abyss/85 px-2 py-1 backdrop-blur">
              <Chip state={alert.state} />
              <span className="num text-[10px] text-muted">captured {fmtTime(alert.updated_at)}Z</span>
              {alert.aws?.s3_frame_url ? (
                <a
                  href={alert.aws.s3_frame_url}
                  target="_blank"
                  rel="noreferrer"
                  className="num rounded border border-sky-400/30 bg-sky-400/10 px-1.5 py-[1px] text-[9px] uppercase text-sky-300 transition hover:border-sky-300/60"
                  title="Evidence backed to Amazon S3 — opens the vault copy"
                >
                  s3 vault ↗
                </a>
              ) : null}
            </div>
          </div>

          {/* reasoning trace */}
          <div className="rounded-lg border border-white/10 bg-white/3 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[9px] uppercase tracking-[0.2em] text-faint">agent reasoning trace</span>
              <span className="num text-[9px] text-faint">conf {alert.agent_confidence?.toFixed?.(2) ?? '—'}</span>
            </div>
            <p className="num text-[11px] leading-relaxed text-ink">
              <span className="text-live">›</span> {alert.reasoning}
            </p>
            {alert.corroboration ? (
              <div className="mt-3 rounded border border-white/8 bg-abyss/60 p-2.5">
                <div className="flex items-center gap-2">
                  <span
                    className="text-[9px] font-semibold uppercase tracking-[0.16em]"
                    style={{ color: alert.corroboration.verified ? '#10B981' : '#F59E0B' }}
                  >
                    verification gate · {alert.corroboration.method.replace(/_/g, ' ')}
                  </span>
                  <span className="num text-[9px] text-faint">{alert.corroboration.verified ? 'PASSED' : 'PENDING'}</span>
                </div>
                <p className="mt-1.5 text-[10px] leading-relaxed text-muted">{alert.corroboration.note}</p>
                {alert.corroboration.cameras?.length ? (
                  <div className="mt-2 space-y-1">
                    {alert.corroboration.cameras.map((c) => (
                      <div key={c.camera_id} className="flex items-center justify-between border-t border-white/6 pt-1">
                        <span className="num text-[10px] text-muted">{c.camera_id}</span>
                        <span className="num text-[10px]" style={{ color: c.delta_pct >= 10 ? '#10B981' : '#8B98A9' }}>
                          {c.delta_pct >= 0 ? '+' : ''}
                          {c.delta_pct.toFixed(1)}% / 30 s
                        </span>
                        <span className="num text-[10px] text-faint">{c.state}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        {/* ------------------------------------------------- timeline + actions */}
        <div className="flex min-h-0 flex-col gap-3">
          <div className="rounded-lg border border-white/10 bg-white/3 p-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-[14px] font-semibold text-ink">Incident timeline</div>
                <div className="num mt-1 text-[10px] text-faint">
                  {alert.camera_id} · started {fmtTime(alert.started_at)}Z · {(alert.status ?? 'active').replace('_', ' ')}
                </div>
              </div>
              <StateChip state={alert.state} size="md" />
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-white/10 bg-white/2 p-3">
            <ol className="relative space-y-4 border-l border-white/10 pl-4">
              {(alert.transitions ?? []).map((t, i) => (
                <li key={i} className="relative">
                  <span
                    className="absolute -left-[22px] top-1 h-2.5 w-2.5 rounded-full border-2 border-abyss"
                    style={{ background: COLORS[t.to] || '#8B98A9' }}
                  />
                  <div className="flex items-center gap-2">
                    <Chip state={t.from} />
                    <span className="text-faint">→</span>
                    <Chip state={t.to} />
                    <span className="num ml-auto text-[10px] text-faint">{fmtTime(t.at)}Z</span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-muted">{t.reason}</p>
                </li>
              ))}
            </ol>
          </div>

          {alert.telemetry_window?.length ? (
            <div className="rounded-lg border border-white/10 bg-white/2 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[9px] uppercase tracking-[0.2em] text-faint">telemetry window</span>
                <span className="num text-[9px] text-faint">{alert.telemetry_window.length} samples</span>
              </div>
              <div className="flex h-14 items-end gap-[2px]">
                {alert.telemetry_window.slice(-72).map((p, i, arr) => {
                  const max = Math.max(...arr.map((x) => x.water_area_ratio), 0.01)
                  const h = Math.max(4, (p.water_area_ratio / max) * 100)
                  return (
                    <div
                      key={i}
                      className="flex-1 rounded-t-sm"
                      style={{
                        height: `${h}%`,
                        background: i === arr.length - 1 ? '#22D3EE' : 'rgba(34,211,238,.35)',
                      }}
                      title={`${p.ts} · ${(p.water_area_ratio * 100).toFixed(1)}%`}
                    />
                  )
                })}
              </div>
              <div className="num mt-1 flex justify-between text-[9px] text-faint">
                <span>{fmtTime(alert.telemetry_window[0].ts)}Z</span>
                <span>{fmtTime(alert.telemetry_window[alert.telemetry_window.length - 1].ts)}Z</span>
              </div>
              {wlFlow ? (
                <div className="mt-3 border-t border-white/8 pt-2">
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="text-[9px] uppercase tracking-[0.2em] text-faint">waterline displacement</span>
                    <span className="num text-[9px] text-faint">
                      Δ {wlFlow.dWl >= 0 ? '+' : ''}
                      {wlFlow.dWl.toFixed(0)} px · flow avg {wlFlow.avgFl.toFixed(2)} · peak{' '}
                      {wlFlow.maxFl.toFixed(2)} px/f
                    </span>
                  </div>
                  <div className="flex h-8 items-end gap-[2px]">
                    {wlFlow.wl.slice(-72).map((v, i, arr) => {
                      const h = Math.max(3, (v / wlFlow.maxWl) * 100)
                      return (
                        <div
                          key={i}
                          className="flex-1 rounded-t-sm"
                          style={{
                            height: `${h}%`,
                            background: i === arr.length - 1 ? '#F59E0B' : 'rgba(245,158,11,.35)',
                          }}
                          title={`${v.toFixed(0)} px`}
                        />
                      )
                    })}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => act('ack')}
              disabled={busy || alert.status === 'acknowledged'}
              className="flex-1 rounded-md border border-live/50 bg-live/12 px-3 py-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-live transition hover:bg-live/20 disabled:opacity-40"
            >
              {alert.status === 'acknowledged' ? 'acknowledged' : 'acknowledge'}
            </button>
            <button
              onClick={() => act('fp')}
              disabled={busy}
              className="flex-1 rounded-md border border-white/14 px-3 py-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted transition hover:border-watch/50 hover:text-watch disabled:opacity-40"
            >
              mark false positive
            </button>
            <button
              onClick={() => act('export')}
              disabled={busy}
              className="rounded-md border border-white/14 px-3 py-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted transition hover:border-white/30 hover:text-ink disabled:opacity-40"
            >
              export report
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
