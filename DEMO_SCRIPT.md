# AquaTrace — 2-minute judge demo script

Run the judging demo in **DEMO mode** (`python3 scripts/start.py`).
The drill also works in LIVE mode — same button, same pipeline.

## The arc (2:00 total)

**0:00–0:20 — The problem, one breath.**
"Urban floods kill because nobody watches the drains at 3am. AquaTrace watches
camera feeds, detects rising water, verifies across adjacent cameras, and gets
a human paged with evidence — before the street becomes a river."

**0:20–0:40 — The console.**
Point at the four-camera mosaic, the sector map, the alert feed. Click the AWS
pill: "S3 evidence vault, SNS paging, CloudWatch metrics — live, not mocked."

**0:40–1:30 — The money moment: Run drill.**
Click **Run drill → gradual rise**. Narrate while it runs:
- "Water area climbing — 5% to 60%."
- "Anomaly spiking — adjacent camera corroborates, that's the verification gate."
- "Critical alert — watch the feed."
- Hold up your phone: **the SNS email/SMS arrives live.** (Confirm a subscriber
  exists before judging day. This is the moment they remember.)

**1:30–1:50 — The evidence.**
Click the critical alert → evidence view: annotated frames, waterline + flow
strip. Hit **Download incident report** → real PDF: timeline, S3 evidence links,
agent reasoning. "This is what the responder gets — not just a ping."

**1:50–2:00 — The closer.**
"Everything you just saw ran through the production pipeline — detection,
verification, alerting, evidence vault. The drill is one input source; a real
RTSP camera is the other. Same code path. And a dead camera pages us too —
signal-loss is a first-class alert, not a grey tile."

## If things go sideways

- Drill slow to alert? Talk over it: point at the rising waterline strip.
- No network for SNS? The outbox queues and retries; say so, show `/api/aws/outbox`.
- Anything crashes? `python3 scripts/start.py` reboots everything in seconds.

## Q&A cheat sheet (honest answers)

- *"Is this semantic segmentation?"* — No. Classical OpenCV/HSV segmentation +
  adjacent-camera verification. Fast, explainable, runs on a CPU.
- *"Does it work at night?"* — Known gap: IR night footage is on the roadmap.
  Daylight-optimized today; we say so openly.
- *"How many cameras?"* — Pilot: one site, a few cameras. Architecture scales
  by adding engine processes; 40-camera city grid is phase 3.
- *"What stops false alarms?"* — Adjacent-camera verification gate, per-camera
  thresholds, operator false-positive feedback loop.
- *"Where's the data?"* — Alerts + decision log in SQLite (pilot), evidence in
  S3, telemetry in CloudWatch. Nothing lives only in RAM.
- *"What does the agent decide?"* — Severity and escalation only. It never
  dispatches responders or acts on the world — notify-only by design.
