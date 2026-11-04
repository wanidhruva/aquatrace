import { storeActions, useStore } from '../lib/store.js'

const TONE_STYLE = {
  nominal: { c: '#10B981', label: 'NOMINAL' },
  watch: { c: '#F59E0B', label: 'WATCH' },
  escal: { c: '#F97316', label: 'ESCALATING' },
  crit: { c: '#EF4444', label: 'CRITICAL' },
  live: { c: '#22D3EE', label: 'LIVE' },
  faint: { c: '#5A6B80', label: 'SYSTEM' },
}

export default function Toasts() {
  const toasts = useStore((s) => s.toasts)
  if (!toasts.length) return null
  return (
    <div className="pointer-events-none fixed bottom-12 right-3 z-50 flex w-[380px] max-w-[calc(100vw-1.5rem)] flex-col gap-2">
      {toasts.map((t) => {
        const style = TONE_STYLE[t.tone] || TONE_STYLE.live
        return (
          <div
            key={t.id}
            className="rise-in pointer-events-auto overflow-hidden rounded-lg border bg-glass backdrop-blur-md"
            style={{
              borderColor: `${style.c}66`,
              boxShadow: t.tone === 'crit' ? `0 0 32px ${style.c}44` : '0 10px 30px rgba(0,0,0,.5)',
              borderLeftWidth: 3,
              borderLeftColor: style.c,
            }}
          >
            <div className="flex items-start gap-2.5 p-3">
              <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: style.c }}>
                <span className="block h-1.5 w-1.5 rounded-full" style={{ background: style.c, animation: 'pulse-dot 1.6s infinite' }} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.16em]" style={{ color: style.c }}>
                    {style.label}
                  </span>
                  <span className="truncate text-[11px] font-medium text-ink">{t.title}</span>
                </div>
                <p className="num mt-1 line-clamp-4 text-[10px] leading-relaxed text-muted">{t.body}</p>
              </div>
              <button
                onClick={() => storeActions.dismissToast(t.id)}
                className="rounded px-1 text-[13px] leading-none text-faint transition hover:text-ink"
                aria-label="dismiss"
              >
                ×
              </button>
            </div>
            <div className="h-[2px] w-full bg-white/6">
              <div className="h-full" style={{ background: style.c, opacity: 0.5 }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}
