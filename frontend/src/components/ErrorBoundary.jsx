import React from 'react'

/**
 * Top-level render safety net: any uncaught throw inside the dashboard
 * (unguarded property access, bad payload shape, ...) renders this panel
 * instead of unmounting the whole app.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Surface in dev tools; the boundary itself stays user-facing.
    console.error('[AquaTrace] render error caught:', error, info)
  }

  render() {
    const { error } = this.state
    if (error) {
      return (
        <div className="flex h-full min-h-[320px] items-center justify-center bg-abyss p-6">
          <div className="w-full max-w-[520px] rounded-lg border border-crit/40 bg-glass p-6 text-center">
            <div className="text-[12px] font-semibold uppercase tracking-[0.22em] text-crit">
              Something glitched in this view
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-muted">
              A render error was caught before it could take down the dashboard. Your live
              telemetry and alerts are unaffected — the stream keeps running underneath.
            </p>
            <pre className="mt-4 max-h-[120px] overflow-auto rounded border border-white/10 bg-abyss/70 p-3 text-left font-mono text-[10px] leading-relaxed text-watch">
              {String(error?.message || error)}
            </pre>
            <button
              onClick={() => window.location.reload()}
              className="mt-5 rounded-md border border-live/50 bg-live/12 px-5 py-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-live transition hover:bg-live/20"
            >
              reload dashboard
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
