const HEADLINE = ['Water', 'rises', 'before', 'the', 'alarm', 'does.']

export default function HeroSection() {
  return (
    <header className="relative flex min-h-[92vh] flex-col overflow-hidden border-b border-hairline">
      {/* --- cinematic backdrop ------------------------------------------ */}
      <div className="absolute inset-0">
        <video
          className="h-full w-full object-cover"
          autoPlay
          muted
          loop
          playsInline
          poster="/aquatrace-hero-poster.jpg"
          src="/aquatrace-hero.mp4"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-abyss/80 via-abyss/55 to-abyss" />
        <div className="absolute inset-0 bg-gradient-to-r from-abyss via-abyss/40 to-transparent" />
        <div className="grid-texture absolute inset-0 opacity-60" />
        <div
          className="absolute inset-x-0 bottom-0 h-40"
          style={{ background: 'linear-gradient(to top, #05080d, transparent)' }}
        />
      </div>

      {/* --- nav ---------------------------------------------------------- */}
      <nav className="relative z-10 flex items-center justify-between px-5 py-4 md:px-10">
        <div className="flex items-center gap-2.5">
          <span className="grid h-7 w-7 place-items-center rounded-md border border-live/40 bg-live/10">
            <svg width="15" height="15" viewBox="0 0 32 32" aria-hidden>
              <path d="M4 18c4-4 8 4 12 0s8-4 12 0" stroke="#22D3EE" strokeWidth="2.6" fill="none" />
              <path d="M4 24c4-4 8 4 12 0s8-4 12 0" stroke="#22D3EE" strokeWidth="2" fill="none" opacity=".45" />
            </svg>
          </span>
          <span className="text-[13px] font-semibold tracking-[0.3em] uppercase text-ink">
            Aqua<span className="text-live">Trace</span>
          </span>
        </div>
        <div className="hidden items-center gap-6 text-[11px] uppercase tracking-[0.18em] text-muted md:flex">
          <a className="transition hover:text-live" href="#pipeline">Pipeline</a>
          <a className="transition hover:text-live" href="#verification">Verification</a>
          <a className="transition hover:text-live" href="#evidence">Evidence</a>
          <a className="transition hover:text-live" href="#stack">Stack</a>
        </div>
        <a
          href="/"
          className="rounded-md border border-live/50 bg-live/12 px-3.5 py-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-live transition hover:bg-live/20"
        >
          open dashboard
        </a>
      </nav>

      {/* --- copy --------------------------------------------------------- */}
      <div className="relative z-10 flex flex-1 flex-col justify-center px-5 pb-10 pt-14 md:px-10">
        <div className="max-w-[880px]">
          <div className="mb-6 flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1.5 rounded border border-live/40 bg-live/10 px-2.5 py-1 text-[9px] font-semibold uppercase tracking-[0.2em] text-live">
              <span className="relative inline-block h-1.5 w-1.5 rounded-full bg-live">
                <span className="absolute inset-0 rounded-full bg-live" style={{ animation: 'pulse-dot 1.6s infinite' }} />
              </span>
              vision engine online
            </span>
            <span className="num rounded border border-white/10 bg-glass px-2.5 py-1 text-[9px] uppercase tracking-[0.16em] text-muted">
              4 cameras · 5 Hz telemetry · zero new hardware
            </span>
          </div>

          <h1 className="text-[40px] font-semibold leading-[1.02] tracking-[-0.03em] text-ink sm:text-[56px] lg:text-[72px]">
            {HEADLINE.map((w, i) => (
              <span className="reveal-mask mr-[0.28em]" key={i}>
                <span className="reveal-word" style={{ animationDelay: `${0.12 + i * 0.075}s` }}>
                  {i === 0 ? <span className="text-live">{w}</span> : w}
                </span>
              </span>
            ))}
          </h1>

          <p className="mt-6 max-w-[560px] text-[15px] leading-relaxed text-muted md:text-[17px]">
            AquaTrace watches the waterline in the CCTV feeds you already own. Computer vision
            measures it frame by frame; an agentic decision loop corroborates every anomaly against
            adjacent cameras before it wakes anyone up — and hands operators the annotated evidence
            behind the call.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <a
              href="http://43.205.127.250/"
              className="spotlight rounded-md border border-live/60 bg-live px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-abyss transition hover:brightness-110"
              style={{ boxShadow: '0 0 36px rgba(34,211,238,.35)' }}
            >
              launch operator console
            </a>
            <a
              href="#pipeline"
              className="rounded-md border border-white/14 px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted transition hover:border-white/35 hover:text-ink"
            >
              see how it works
            </a>
            <span className="num flex items-center gap-2 text-[10px] text-faint">
              <span className="text-live">›</span> python3 scripts/start.py
            </span>
          </div>
        </div>
      </div>

      {/* --- stat rail ---------------------------------------------------- */}
      <div className="relative z-10 grid grid-cols-2 border-t border-hairline bg-glass backdrop-blur md:grid-cols-4">
        {[
          ['60–180 s', 'median time from rise to verified alert'],
          ['0', 'new sensors installed — existing CCTV only'],
          ['3', 'escalation paths, each gated on confidence'],
          ['100 %', 'alerts carry a frozen evidence snapshot'],
        ].map(([v, l], i) => (
          <div
            key={i}
            className="border-r border-hairline px-5 py-4 last:border-r-0 md:px-8"
            style={{ borderBottomWidth: i < 2 ? 1 : 0 }}
          >
            <div className="num text-[20px] font-semibold text-live md:text-[24px]">{v}</div>
            <div className="mt-1 text-[10px] leading-snug uppercase tracking-[0.12em] text-faint">{l}</div>
          </div>
        ))}
      </div>
    </header>
  )
}
