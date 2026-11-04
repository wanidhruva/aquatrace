/* AquaTrace marketing sections — locked design system, no blue/purple gradients. */

const SectionLabel = ({ children }) => (
  <div className="mb-4 flex items-center gap-3">
    <span className="h-px w-8 bg-live/70" />
    <span className="num text-[10px] font-semibold uppercase tracking-[0.3em] text-live">{children}</span>
  </div>
)

const SectionTitle = ({ children }) => (
  <h2 className="max-w-[720px] text-[28px] font-semibold leading-[1.12] tracking-[-0.02em] text-ink md:text-[38px]">
    {children}
  </h2>
)

const Section = ({ id, children, className = '' }) => (
  <section id={id} className={`border-b border-hairline px-5 py-20 md:px-10 md:py-28 ${className}`}>
    <div className="mx-auto max-w-[1180px]">{children}</div>
  </section>
)

/* ------------------------------------------------------------------- problem */

export function Problem() {
  const pains = [
    ['Telemetry blind spots', 'Float sensors and stage gauges are sparse, fail in debris-laden water, and go dark exactly when a culvert blocks.'],
    ['Alarm storms', 'Threshold alarms on noisy water-level data fire on debris, then get muted — the boy-who-cried-wolf tax on every duty operator.'],
    ['CCTV nobody watches', 'Thousands of municipal cameras stream to a wall of monitors. The signal is already being recorded — nobody is measuring it.'],
  ]
  return (
    <Section>
      <SectionLabel>the gap</SectionLabel>
      <SectionTitle>A flood tells you it is coming for hours. Almost nothing is listening.</SectionTitle>
      <div className="mt-10 grid gap-3 md:grid-cols-3">
        {pains.map(([t, d], i) => (
          <div key={i} className="spotlight rounded-lg border border-hairline bg-panel p-5">
            <div className="num text-[11px] text-crit">{`0${i + 1}`}</div>
            <h3 className="mt-3 text-[15px] font-semibold text-ink">{t}</h3>
            <p className="mt-2 text-[13px] leading-relaxed text-muted">{d}</p>
          </div>
        ))}
      </div>
      <p className="mt-10 max-w-[760px] text-[16px] leading-relaxed text-muted md:text-[17px]">
        AquaTrace measures the <span className="text-ink">waterline</span> — the one physical fact
        that changes in every one of those cameras — and treats the footage as a distributed sensor
        network you already paid for.
      </p>
    </Section>
  )
}

/* ------------------------------------------------------------------ pipeline */

const STEPS = [
  {
    n: '01',
    t: 'Perceive',
    d: 'Background subtraction (MOG2) fused with HSV water segmentation, morphology-cleaned, tracked as a single connected region. Farneback dense optical flow measures how fast the surface is actually moving.',
    k: ['cv2.MOG2', 'HSV fusion', 'Farneback flow', 'sub-pixel waterline'],
  },
  {
    n: '02',
    t: 'Interpret',
    d: 'Each frame becomes telemetry: water-area ratio, slope over a rolling 30 s window, and a baseline learned from the trailing five minutes of that camera — never a hard-coded threshold.',
    k: ['ratio slope', 'learned baseline', 'anomaly ×', '5 Hz stream'],
  },
  {
    n: '03',
    t: 'Verify',
    d: 'An anomaly is not an alert. Contour stability, flow coherence and detection validity are fused into one confidence score; below the gate the agent interrogates adjacent cameras and raises sampling to 10 Hz.',
    k: ['confidence gate', 'adjacent corroboration', '10 Hz boost', '15 s timeout'],
  },
  {
    n: '04',
    t: 'Act',
    d: 'Normal → Watch → Escalating → Verifying → Critical, each with sustain timers and one-step-down recovery. Every transition is written to an immutable log with its reasoning string and a frozen evidence frame.',
    k: ['hysteresis', 'reasoning trace', 'evidence snapshot', 'audit export'],
  },
]

export function Pipeline() {
  return (
    <Section id="pipeline" className="grid-texture">
      <SectionLabel>the loop</SectionLabel>
      <SectionTitle>Four stages, running on every camera, forever.</SectionTitle>
      <div className="mt-10 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {STEPS.map((s) => (
          <div key={s.n} className="group relative flex flex-col rounded-lg border border-hairline bg-glass p-5 backdrop-blur transition-all duration-500 hover:border-live/40">
            <div className="flex items-baseline justify-between">
              <span className="num text-[26px] font-semibold text-live/80 transition group-hover:text-live">{s.n}</span>
              <span className="text-[9px] uppercase tracking-[0.2em] text-faint">stage</span>
            </div>
            <h3 className="mt-3 text-[17px] font-semibold text-ink">{s.t}</h3>
            <p className="mt-2 flex-1 text-[13px] leading-relaxed text-muted">{s.d}</p>
            <div className="mt-4 flex flex-wrap gap-1.5">
              {s.k.map((k) => (
                <span key={k} className="num rounded border border-white/10 px-1.5 py-[3px] text-[9px] text-faint">
                  {k}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Section>
  )
}

/* --------------------------------------------------------------- verification */

function Meter({ value, label, tone }) {
  const pct = Math.round(value * 100)
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="num text-[10px] uppercase tracking-[0.14em] text-faint">{label}</span>
        <span className="num text-[12px] font-semibold" style={{ color: tone }}>
          {value.toFixed(2)}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/8">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: tone, boxShadow: `0 0 12px ${tone}` }}
        />
      </div>
    </div>
  )
}

export function Verification() {
  return (
    <Section id="verification">
      <div className="grid items-start gap-10 lg:grid-cols-2">
        <div>
          <SectionLabel>verification gate</SectionLabel>
          <SectionTitle>Confidence decides whether anyone gets woken up.</SectionTitle>
          <p className="mt-6 max-w-[560px] text-[15px] leading-relaxed text-muted">
            Three signals are fused into a single score on every frame: how stable the tracked
            contour is, how coherent the optical flow is inside the water region, and what fraction
            of recent frames produced a detection that agreed with itself.
          </p>
          <div className="mt-6 rounded-lg border border-hairline bg-panel p-5">
            <div className="num text-[12px] leading-relaxed text-muted">
              <span className="text-live">conf</span> = 0.45·stability + 0.20·flow_coherence + 0.35·valid_frac
            </div>
            <div className="num mt-3 border-t border-hairline pt-3 text-[12px] leading-relaxed text-muted">
              <span className="text-live">agent</span> = 0.85·mean_10s(conf) + 0.15·valid_frac
            </div>
          </div>
          <ul className="mt-6 space-y-3">
            {[
              ['≥ 0.80', 'Credible in one feed, stable frame to frame — Critical is declared immediately, with the reasoning string recorded.', '#10B981'],
              ['< 0.80', 'Something is degrading the sensor. The agent enters Verifying: adjacent cameras must corroborate, sampling is raised to 10 Hz, and a 15 s timeout stands in as the last resort.', '#F97316'],
            ].map(([k, d, c]) => (
              <li key={k} className="flex gap-3">
                <span className="num mt-[2px] shrink-0 rounded border px-2 py-1 text-[11px] font-semibold" style={{ color: c, borderColor: `${c}55`, background: `${c}12` }}>
                  {k}
                </span>
                <span className="text-[13px] leading-relaxed text-muted">{d}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-xl border border-hairline bg-glass p-5 backdrop-blur">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-[0.2em] text-faint">same anomaly, two paths</span>
            <span className="num text-[10px] text-faint">gate 0.80</span>
          </div>

          <div className="mt-5 rounded-lg border border-live/30 bg-live/5 p-4">
            <div className="flex items-center justify-between">
              <span className="num text-[11px] text-live">cam-04 · flash surge</span>
              <span className="rounded border border-live/40 px-1.5 py-[2px] text-[9px] uppercase tracking-[0.14em] text-live">immediate</span>
            </div>
            <Meter value={0.91} label="agent confidence" tone="#10B981" />
            <div className="num mt-4 space-y-1 text-[11px] text-muted">
              <div>› anomaly 18.4× · slope 0.019 ratio/s</div>
              <div>› clean contour, coherent flow</div>
              <div className="text-live">› gate passed → CRITICAL in 11 s</div>
            </div>
          </div>

          <div className="mt-3 rounded-lg border border-escal/30 bg-escal/5 p-4">
            <div className="flex items-center justify-between">
              <span className="num text-[11px] text-escal">cam-01 · gradual rise under rain</span>
              <span className="rounded border border-escal/40 px-1.5 py-[2px] text-[9px] uppercase tracking-[0.14em] text-escal">verifying</span>
            </div>
            <Meter value={0.64} label="agent confidence" tone="#F97316" />
            <div className="num mt-4 space-y-1 text-[11px] text-muted">
              <div>› anomaly 6.2× · droplets destabilise tracking</div>
              <div>› confidence below gate → hold Critical</div>
              <div className="text-escal">› adjacent cam-02 +27.5% / 30 s → corroborated</div>
            </div>
          </div>

          <div className="mt-4 flex items-center justify-between border-t border-hairline pt-3">
            <span className="num text-[10px] text-faint">both paths end in one alert, one evidence frame, one reasoning string</span>
          </div>
        </div>
      </div>
    </Section>
  )
}

/* ------------------------------------------------------------------ evidence */

export function Evidence() {
  const beats = [
    ['Frozen frame', 'The annotated overlay captured at the moment of escalation — bbox, sub-pixel waterline, measured ratio and flow, state chip, timestamp.'],
    ['Transition timeline', 'Every state change with the exact UTC time and the sentence the agent used to justify it. Nothing is implicit.'],
    ['Reasoning trace', 'Why this call, why now — anomaly multiple, learned baseline, confidence components, corroboration results.'],
    ['One-click export', 'The whole incident as a printable report: summary, verification method, transition table, telemetry window, snapshot pointer.'],
  ]
  return (
    <Section id="evidence">
      <SectionLabel>evidence, not vibes</SectionLabel>
      <SectionTitle>Every alert arrives with the proof that produced it.</SectionTitle>
      <div className="mt-10 grid items-start gap-6 lg:grid-cols-[1.15fr_1fr]">
        <figure className="overflow-hidden rounded-xl border border-hairline bg-abyss shadow-[0_24px_70px_rgba(0,0,0,.6)]">
          <img src="/evidence-sample.jpg" alt="AquaTrace annotated evidence frame" className="block w-full" loading="lazy" />
          <figcaption className="num flex items-center justify-between border-t border-hairline px-4 py-2.5 text-[10px] text-faint">
            <span>cam-01 · cam-01/gradual_rise.mp4 · annotated by the vision engine</span>
            <span className="text-live">live overlay</span>
          </figcaption>
        </figure>
        <div className="space-y-3">
          {beats.map(([t, d], i) => (
            <div key={t} className="spotlight rounded-lg border border-hairline bg-panel p-4">
              <div className="flex items-center gap-3">
                <span className="num text-[11px] text-live">{`0${i + 1}`}</span>
                <h3 className="text-[14px] font-semibold text-ink">{t}</h3>
              </div>
              <p className="mt-2 text-[13px] leading-relaxed text-muted">{d}</p>
            </div>
          ))}
          <a
            href="http://43.205.127.250/"
            className="mt-2 flex items-center justify-between rounded-lg border border-live/40 bg-live/10 px-4 py-3 text-[12px] font-semibold uppercase tracking-[0.14em] text-live transition hover:bg-live/20"
          >
            open the operator console
            <span aria-hidden>→</span>
          </a>
        </div>
      </div>
    </Section>
  )
}

/* --------------------------------------------------------------------- stack */

const STACK = [
  ['OpenCV 4.13', 'MOG2 background subtraction, HSV water segmentation, morphological cleanup, connected-component tracking, Farneback dense optical flow — all CPU, no GPU required.'],
  ['FastAPI + WebSocket', 'Strict JSON-Schema-validated ingest, sub-millisecond fan-out of telemetry ticks to every connected operator screen, zero-loss reconnect.'],
  ['Agentic decision loop', 'Learned per-camera baselines, sustain timers, a three-way corroboration strategy and a false-positive feedback loop that raises the baseline after each dismissal.'],
  ['React 18 console', 'Live camera wall, ratio charts, site map, severity-ordered alert feed, evidence view with printable report — every numeral monospaced, every state colour-coded.'],
  ['Frozen contracts', 'Telemetry, alert and state-change schemas live in /contracts and are shared by the Python engine, the API and the UI. A breaking change fails the 422 handler.'],
  ['Deterministic synth', 'Sixty seconds of flood can be generated identically on any laptop: the whole demo — calm, gradual rise, flash surge — is reproducible and CI-testable.'],
]

export function Stack() {
  return (
    <Section id="stack">
      <SectionLabel>under the hood</SectionLabel>
      <SectionTitle>Small stack. Every layer explainable.</SectionTitle>
      <div className="mt-10 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {STACK.map(([t, d]) => (
          <div key={t} className="spotlight rounded-lg border border-hairline bg-panel p-5 transition-colors duration-300 hover:border-live/30">
            <div className="num text-[10px] uppercase tracking-[0.16em] text-live">{t}</div>
            <p className="mt-3 text-[13px] leading-relaxed text-muted">{d}</p>
          </div>
        ))}
      </div>
    </Section>
  )
}

/* ---------------------------------------------------------------------- CTA */

export function CTA() {
  return (
    <section className="relative overflow-hidden border-b border-hairline px-5 py-20 md:px-10 md:py-28">
      <div className="grid-texture absolute inset-0 opacity-50" />
      <div
        className="absolute inset-0"
        style={{ background: 'radial-gradient(600px circle at 50% 40%, rgba(34,211,238,.10), transparent 70%)' }}
      />
      <div className="relative mx-auto max-w-[760px] text-center">
        <span className="num text-[10px] font-semibold uppercase tracking-[0.3em] text-live">run it now</span>
        <h2 className="mt-4 text-[30px] font-semibold leading-[1.1] tracking-[-0.02em] text-ink md:text-[44px]">
          One command. Four cameras. Sixty seconds to a verified flood alert.
        </h2>
        <div className="num mt-7 inline-block rounded-lg border border-hairline bg-abyss px-5 py-3 text-left text-[13px] leading-relaxed text-muted">
          <div><span className="text-live">$</span> python3 scripts/start.py</div>
          <div className="text-faint">→ backend :8000 · console :5173 · site :5174</div>
        </div>
        <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
          <a
            href="http://43.205.127.250/"
            className="rounded-md border border-live/60 bg-live px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-abyss transition hover:brightness-110"
            style={{ boxShadow: '0 0 36px rgba(34,211,238,.35)' }}
          >
            launch console
          </a>
          <a
            href="#pipeline"
            className="rounded-md border border-white/14 px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted transition hover:border-white/35 hover:text-ink"
          >
            read the loop again
          </a>
        </div>
      </div>
    </section>
  )
}

export function Footer() {
  return (
    <footer className="px-5 py-8 md:px-10">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-2.5">
          <span className="grid h-6 w-6 place-items-center rounded border border-live/40 bg-live/10">
            <svg width="13" height="13" viewBox="0 0 32 32" aria-hidden>
              <path d="M4 18c4-4 8 4 12 0s8-4 12 0" stroke="#22D3EE" strokeWidth="2.8" fill="none" />
              <path d="M4 24c4-4 8 4 12 0s8-4 12 0" stroke="#22D3EE" strokeWidth="2.2" fill="none" opacity=".45" />
            </svg>
          </span>
          <span className="text-[11px] font-semibold uppercase tracking-[0.28em] text-ink">
            Aqua<span className="text-live">Trace</span>
          </span>
        </div>
        <p className="num text-[10px] leading-relaxed text-faint">
          visual intelligence for flood detection · metro water network — sector 4 · built for the
          cameras you already have
        </p>
        <div className="flex gap-5 text-[10px] uppercase tracking-[0.16em] text-muted">
          <a className="transition hover:text-live" href="http://43.205.127.250/">console</a>
          <a className="transition hover:text-live" href="http://43.205.127.250/docs">api docs</a>
          <a className="transition hover:text-live" href="#pipeline">loop</a>
        </div>
      </div>
    </footer>
  )
}
