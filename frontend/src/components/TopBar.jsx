import { useEffect, useRef, useState } from 'react'
import { awsStatus, setMode, simulate } from '../lib/api.js'
import { storeActions, toast, useStore } from '../lib/store.js'
import { StateChip, formatClock } from './ui.jsx'

const AWS_DOT = { ok: '#10B981', unconfigured: '#5A6B80', error: '#EF4444' }

function AwsRow({ label, detail, status }) {
  const dot = AWS_DOT[status] || '#5A6B80'
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <div className="min-w-0">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink">{label}</div>
        <div className="num mt-0.5 truncate text-[9px] text-faint">{detail || '—'}</div>
      </div>
      <span className="flex shrink-0 items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: dot }} />
        <span className="num text-[9px] uppercase tracking-[0.12em]" style={{ color: dot }}>
          {status || 'unknown'}
        </span>
      </span>
    </div>
  )
}

const WORST_COLOR = {
  normal: '#10B981',
  watch: '#F59E0B',
  escalating: '#F97316',
  verifying: '#F97316',
  critical: '#EF4444',
}

export default function TopBar() {
  const worst = useStore((s) => s.worst)
  const mode = useStore((s) => s.mode)
  const [modePending, setModePending] = useState(false)

  const toggleMode = async () => {
    if (modePending) return
    const nextMode = mode === 'live' ? 'demo' : 'live'
    setModePending(true)
    try {
      await setMode(nextMode)
      storeActions.setMode(nextMode)
      toast({
        kind: 'info',
        title: `SWITCHED TO ${nextMode.toUpperCase()} MODE`,
        body: nextMode === 'live'
          ? 'Live camera feeds & real FloodNet perception active.'
          : 'Synthetic scenarios enabled.',
        tone: nextMode === 'live' ? 'nominal' : 'watch',
        ttl: 4000,
      })
    } catch (err) {
      toast({
        kind: 'error',
        title: 'MODE SWITCH FAILED',
        body: String(err?.message || err),
        tone: 'crit',
      })
    } finally {
      setModePending(false)
    }
  }
  const scenario = useStore((s) => s.scenario)
  const muted = useStore((s) => s.muted)
  const connection = useStore((s) => s.connection)
  const forecast = useStore((s) => s.forecast)
  const [clock, setClock] = useState(formatClock())
  const [busy, setBusy] = useState(false)
  const [aws, setAws] = useState(null)
  const [awsOpen, setAwsOpen] = useState(false)
  const [fcOpen, setFcOpen] = useState(false)
  const awsWrap = useRef(null)
  const fcWrap = useRef(null)

  useEffect(() => {
    const id = setInterval(() => setClock(formatClock()), 1000)
    return () => clearInterval(id)
  }, [])

  // close popovers on outside click / Escape
  useEffect(() => {
    if (!awsOpen && !fcOpen) return undefined
    const onDown = (e) => {
      if (awsWrap.current && !awsWrap.current.contains(e.target)) setAwsOpen(false)
      if (fcWrap.current && !fcWrap.current.contains(e.target)) setFcOpen(false)
    }
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setAwsOpen(false)
        setFcOpen(false)
      }
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [awsOpen, fcOpen])

  // AWS integration health: refreshed every minute; failure = local mode.
  useEffect(() => {
    let alive = true
    const load = () => {
      awsStatus()
        .then((s) => { if (alive) setAws(s) })
        .catch(() => { if (alive) setAws(null) })
    }
    load()
    const id = setInterval(load, 60000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const awsLive = !!(aws?.enabled && (aws.s3 === 'ok' || aws.sns === 'ok'))

  // precipitation outlook (Open-Meteo via /api/forecast) — same pill pattern
  // as AWS: neutral when unavailable, never an error state.
  const fcZones = (forecast?.zones || []).filter((z) => z.status && z.status !== 'unavailable')
  const fcProbs = fcZones.map((z) => z.prob_max_6h).filter((v) => v != null)
  const fcMm = fcZones.map((z) => z.mm_6h).filter((v) => v != null)
  const fcProb = fcProbs.length ? Math.max(...fcProbs) : null
  const fcMmMax = fcMm.length ? Math.max(...fcMm) : null
  const fcAdv = fcZones.map((z) => z.advisory).filter(Boolean)[0] || null
  const fcEnabled = forecast ? forecast.enabled !== false : true
  const fcLive = fcEnabled && fcZones.length > 0
  const fcDot = !fcEnabled ? '#5A6B80' : fcLive ? (fcAdv ? '#F59E0B' : '#22D3EE') : '#64748B'
  const fcLabel = !fcEnabled
    ? 'FORECAST OFF'
    : fcLive && fcProb != null
      ? `RAIN ${fcProb}%`
      : 'FORECAST —'

  const runScenario = async () => {
    setBusy(true)
    try {
      await simulate('gradual_rise')
      toast({ kind: 'info', title: 'SCENARIO STARTED', body: 'gradual_rise · cam-01 is flooding. Watch the state machine.', tone: 'live' })
    } catch (e) {
      toast({ kind: 'warn', title: 'SCENARIO FAILED', body: e?.body?.detail || e.message, tone: 'crit', sticky: true })
    } finally {
      setBusy(false)
    }
  }

  const demoLive = scenario?.running

  return (
    <header className="relative z-20 flex h-14 shrink-0 items-center gap-4 border-b border-white/8 bg-glass px-4 backdrop-blur-md">
      <div className="flex items-center gap-2.5">
        <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
          <rect width="32" height="32" rx="8" fill="#05080D" stroke="rgba(34,211,238,.5)" />
          <path d="M5 20c3-5 5 5 8 0s5-7 8-2 4 0 6-1" stroke="#22D3EE" strokeWidth="2.2" fill="none" strokeLinecap="round" />
          <circle cx="23" cy="11" r="2.4" fill="#22D3EE" />
        </svg>
        <div className="leading-none">
          <div className="text-[15px] font-semibold tracking-[0.26em] text-ink">AQUATRACE</div>
          <div className="mt-1 text-[9px] uppercase tracking-[0.3em] text-faint">operator console</div>
        </div>
      </div>

      <div className="h-7 w-px bg-white/8" />

      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-[0.18em] text-faint">network status</span>
        <StateChip state={worst} size="md" />
      </div>

      <div className="flex items-center gap-2 rounded-full border border-white/8 bg-white/3 px-3 py-1.5">
        <span
          className="relative inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: connection === 'open' ? '#22D3EE' : '#F59E0B' }}
        >
          <span
            className="absolute inset-0 rounded-full"
            style={{ background: connection === 'open' ? '#22D3EE' : '#F59E0B', animation: 'pulse-dot 1.8s infinite' }}
          />
        </span>
        <span className="num text-[11px] tracking-[0.1em] text-muted">
          {connection === 'open' ? 'WS LIVE' : connection === 'closed' ? 'RECONNECTING' : 'CONNECTING'}
        </span>
      </div>

      <div ref={awsWrap} className="relative">
        <button
          onClick={() => setAwsOpen((o) => !o)}
          className="flex items-center gap-2 rounded-full border border-white/8 bg-white/3 px-3 py-1.5 transition hover:border-live/40"
          title={aws ? `S3: ${aws.s3} · SNS: ${aws.sns} · CloudWatch: ${aws.cloudwatch} (${aws.region}) — click for detail` : 'AWS status unavailable — click for detail'}
        >
          <span
            className="relative inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: awsLive ? '#10B981' : '#64748B' }}
          >
            {awsLive ? (
              <span className="absolute inset-0 rounded-full" style={{ background: '#10B981', animation: 'pulse-dot 1.8s infinite' }} />
            ) : null}
          </span>
          <span className="num text-[11px] tracking-[0.1em] text-muted">
            {awsLive ? 'AWS LIVE' : 'LOCAL MODE'}
          </span>
        </button>
        {awsOpen ? (
          <div className="rise-in absolute right-0 top-full z-50 mt-2 w-[290px] rounded-lg border border-white/10 bg-glass p-3 shadow-2xl backdrop-blur-md">
            <div className="mb-1 flex items-center justify-between border-b border-white/8 pb-2">
              <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-live">aws integration</span>
              <span className="num text-[9px] uppercase tracking-[0.12em] text-faint">
                {aws?.enabled ? 'enabled' : 'disabled'}
              </span>
            </div>
            <AwsRow label="S3 evidence vault" detail={aws?.bucket} status={aws?.s3} />
            <AwsRow label="SNS critical alerts" detail={aws?.topic} status={aws?.sns} />
            <AwsRow label="CloudWatch metrics" detail="namespace AquaTrace" status={aws?.cloudwatch} />
            <div className="num mt-1 flex items-center justify-between border-t border-white/8 pt-2 text-[9px] text-faint">
              <span>profile {aws?.profile || '—'}</span>
              <span>{aws?.region || '—'}</span>
            </div>
            {!awsLive ? (
              <p className="mt-2 text-[9px] leading-relaxed text-faint">
                Local mode — evidence stays on this machine. Restart the backend with AWS enabled to link the cloud vault.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      <div ref={fcWrap} className="relative">
        <button
          onClick={() => setFcOpen((o) => !o)}
          className="flex items-center gap-2 rounded-full border border-white/8 bg-white/3 px-3 py-1.5 transition hover:border-live/40"
          title={fcAdv || forecast?.source || 'Precipitation forecast — click for detail'}
        >
          <span
            className="relative inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: fcDot }}
          >
            {fcLive ? (
              <span className="absolute inset-0 rounded-full" style={{ background: fcDot, animation: 'pulse-dot 1.8s infinite' }} />
            ) : null}
          </span>
          <span className="num text-[11px] tracking-[0.1em]" style={{ color: fcDot }}>
            {fcLabel}
          </span>
        </button>
        {fcOpen ? (
          <div className="rise-in absolute right-0 top-full z-50 mt-2 w-[320px] rounded-lg border border-white/10 bg-glass p-3 shadow-2xl backdrop-blur-md">
            <div className="mb-1 flex items-center justify-between border-b border-white/8 pb-2">
              <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-live">rain outlook</span>
              <span className="num text-[9px] uppercase tracking-[0.12em] text-faint">
                {fcEnabled ? 'next 6 / 24 h' : 'disabled'}
              </span>
            </div>
            {fcEnabled && fcZones.length ? (
              fcZones.map((z) => (
                <div key={z.zone} className="flex items-center justify-between gap-3 py-1.5">
                  <div className="min-w-0">
                    <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink">{z.zone}</div>
                    <div className="num mt-0.5 truncate text-[9px] text-faint">
                      {z.mm_6h != null ? `${z.mm_6h} mm / 6 h` : '—'}
                      {z.mm_24h != null ? ` · ${z.mm_24h} mm / 24 h` : ''}
                      {z.worst_hour ? ` · peak ${String(z.worst_hour).slice(11, 16)}` : ''}
                    </div>
                  </div>
                  <span className="num shrink-0 text-[12px] text-live">
                    {z.prob_max_6h != null ? `${z.prob_max_6h}%` : '—'}
                  </span>
                </div>
              ))
            ) : (
              <div className="py-2 text-[10px] leading-relaxed text-faint">
                {fcEnabled
                  ? 'Forecast unavailable — the feed did not respond. Camera telemetry is unaffected.'
                  : 'Forecast display is disabled for this deployment.'}
              </div>
            )}
            {fcAdv ? (
              <p className="mt-1 rounded border border-watch/40 bg-watch/10 px-2 py-1.5 text-[9px] leading-relaxed text-watch">
                {fcAdv}
              </p>
            ) : null}
            <div className="num mt-1 flex items-center justify-between border-t border-white/8 pt-2 text-[9px] text-faint">
              <span className="truncate pr-2">{forecast?.source || '—'}</span>
              <span className="shrink-0">{forecast?.fetched_at ? String(forecast.fetched_at).slice(11, 16) + ' UTC' : '—'}</span>
            </div>
          </div>
        ) : null}
      </div>

      <button
        onClick={toggleMode}
        disabled={modePending}
        className="flex items-center gap-2 rounded-full border border-white/12 bg-white/4 px-3 py-1.5 transition hover:border-white/30 hover:bg-white/8 cursor-pointer disabled:opacity-50"
        title={mode === 'live' ? 'Click to switch to Demo Mode' : 'Click to switch to Live Mode'}
      >
        <span
          className="relative inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: mode === 'live' ? '#10B981' : '#F59E0B' }}
        >
          <span
            className="absolute inset-0 rounded-full"
            style={{ background: mode === 'live' ? '#10B981' : '#F59E0B', animation: 'pulse-dot 1.8s infinite' }}
          />
        </span>
        <span className="num text-[11px] font-semibold tracking-[0.1em]" style={{ color: mode === 'live' ? '#10B981' : '#F59E0B' }}>
          {mode === 'live' ? 'LIVE MODE' : 'DEMO MODE'}
        </span>
        <span className="text-[9px] uppercase tracking-[0.08em] text-faint ml-0.5">
          {modePending ? '…' : '⇋ switch'}
        </span>
      </button>

      <div className="ml-auto flex items-center gap-3">
        {demoLive ? (
          <div className="flex items-center gap-2 rounded-md border border-watch/60 bg-watch/12 px-3 py-1.5">
            <span className="relative inline-block h-1.5 w-1.5 rounded-full bg-watch">
              <span className="absolute inset-0 rounded-full bg-watch" style={{ animation: 'pulse-dot 1.4s infinite' }} />
            </span>
            <span className="text-[10px] font-semibold tracking-[0.2em] text-watch">DEMO MODE</span>
            <span className="num text-[10px] text-watch/70">{scenario.scenario}</span>
          </div>
        ) : null}

        <div className="num rounded-md border border-white/8 bg-white/3 px-3 py-1.5 text-[13px] text-ink">
          {clock}
          <span className="ml-1.5 text-[9px] text-faint">UTC</span>
        </div>

        <button
          onClick={() => storeActions.setMuted(!muted)}
          title={muted ? 'Unmute critical alert tone' : 'Mute critical alert tone'}
          className="grid h-8 w-8 place-items-center rounded-md border border-white/8 bg-white/3 text-muted transition hover:border-live/40 hover:text-live"
        >
          {muted ? (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M11 5 6 9H3v6h3l5 4V5Z" />
              <path d="m17 9 4 6M21 9l-4 6" />
            </svg>
          ) : (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M11 5 6 9H3v6h3l5 4V5Z" />
              <path d="M16 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11" />
            </svg>
          )}
        </button>

        <a
          href={
            typeof window !== 'undefined' &&
            (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
              ? 'http://localhost:5174/'
              : '/site/'
          }
          target="_blank"
          rel="noreferrer"
          className="hidden rounded-md border border-white/8 bg-white/3 px-3 py-2 text-[11px] uppercase tracking-[0.16em] text-muted transition hover:border-live/40 hover:text-live xl:block"
        >
          marketing site
        </a>

        {mode !== 'live' ? (
          <button
            onClick={runScenario}
            disabled={busy}
            className="glow-live group relative rounded-md bg-live px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-abyss transition disabled:opacity-60"
            style={{ boxShadow: '0 0 28px rgba(34,211,238,.38)' }}
          >
            {busy ? 'starting…' : 'run demo scenario'}
          </button>
        ) : null}
      </div>
    </header>
  )
}
