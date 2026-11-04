import { HEX, TONE } from '../lib/store.js'

const COLORS = {
  nominal: '#10B981',
  watch: '#F59E0B',
  escalating: '#F97316',
  verifying: '#F97316',
  critical: '#EF4444',
  signal_lost: '#5A6B80',
}

export function StateChip({ state, size = 'sm', pulse = true }) {
  const tone = TONE[state] || 'faint'
  const color = COLORS[state] || HEX[tone]
  const label = (state || 'normal').replace('_', ' ').toUpperCase()
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border ${
        size === 'sm' ? 'px-2 py-[3px] text-[10px]' : 'px-2.5 py-1 text-[11px]'
      } font-medium tracking-[0.14em]`}
      style={{ color, borderColor: `${color}66`, background: `${color}14` }}
    >
      <span className="relative inline-block h-1.5 w-1.5 rounded-full" style={{ background: color }}>
        {pulse && state !== 'normal' ? (
          <span className="absolute inset-0 rounded-full" style={{ background: color, animation: 'pulse-dot 1.6s cubic-bezier(.22,1,.36,1) infinite' }} />
        ) : null}
      </span>
      {label}
    </span>
  )
}

export function Panel({ className = '', children, ...rest }) {
  return (
    <div
      className={`relative rounded-lg border border-white/8 bg-white/3 backdrop-blur-[2px] ${className}`}
      {...rest}
    >
      {children}
    </div>
  )
}

export function PanelHeader({ title, right, sub }) {
  return (
    <div className="flex items-center justify-between border-b border-white/8 px-3 py-2">
      <div className="flex items-baseline gap-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted">{title}</h2>
        {sub ? <span className="num text-[10px] text-faint">{sub}</span> : null}
      </div>
      {right}
    </div>
  )
}

export function Skeleton({ className = '' }) {
  return <div className={`skeleton rounded ${className}`} />
}

export function formatClock(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
}

export function fmtTime(iso) {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
}

export function cameraId(c) {
  return c.camera_id || c.id
}
