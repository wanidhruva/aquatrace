"""Incident report PDF for GET /api/alerts/{id}/report.

One to two pages: alert details, camera, timeline of transitions, reasoning,
linked S3 evidence keys, and recent agent decision-log excerpts. fpdf2 only.
"""

from __future__ import annotations

from typing import Any

from fpdf import FPDF


def _text(v: Any) -> str:
    s = "" if v is None else str(v)
    # fpdf core fonts are latin-1; strip anything outside it.
    return s.encode("latin-1", "replace").decode("latin-1")


class _Report(FPDF):
    def header(self) -> None:
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(30, 60, 110)
        self.cell(0, 8, "AquaTrace  |  Flood Telemetry Incident Report",
                  new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(30, 60, 110)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")


def _kv(pdf: _Report, key: str, value: Any) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(40, 40, 40)
    pdf.cell(44, 7, _text(key) + ":")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 7, _text(value), new_x="LMARGIN", new_y="NEXT")


def _section(pdf: _Report, title: str) -> None:
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 60, 110)
    pdf.cell(0, 8, _text(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


def build_report(alert: dict[str, Any], camera: dict[str, Any],
                 decisions: list[dict[str, Any]], generated_at: str) -> bytes:
    pdf = _Report(format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()

    sev = str(alert.get("severity", "")).upper()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(140, 30, 30) if sev in ("CRITICAL", "HIGH") else pdf.set_text_color(30, 60, 110)
    pdf.cell(0, 11, f"[{sev}] {alert.get('alert_id', '')}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    _section(pdf, "Alert")
    _kv(pdf, "Type", alert.get("type") or alert.get("state"))
    _kv(pdf, "State", alert.get("state"))
    _kv(pdf, "Status", alert.get("status"))
    _kv(pdf, "Started", alert.get("started_at"))
    _kv(pdf, "Updated", alert.get("updated_at"))
    if alert.get("closed_at"):
        _kv(pdf, "Closed", alert.get("closed_at"))
    if alert.get("acknowledged_at"):
        _kv(pdf, "Acknowledged", f"{alert.get('acknowledged_at')} by {alert.get('ack_by') or 'operator'}")
    _kv(pdf, "Drill", "yes" if alert.get("drill") else "no")
    _kv(pdf, "Agent confidence", alert.get("agent_confidence"))

    _section(pdf, "Camera")
    _kv(pdf, "Name", f"{camera.get('name')} ({camera.get('id')})")
    _kv(pdf, "Zone", camera.get("zone"))
    _kv(pdf, "Location", camera.get("location"))

    _section(pdf, "Reasoning")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, _text(alert.get("reasoning") or "—"))

    _section(pdf, "Timeline")
    for t in alert.get("transitions") or []:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(36, 6, _text(t.get("at")))
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 6,
                       f"{t.get('from')} -> {t.get('to')}: {_text(t.get('reason'))}",
                       new_x="LMARGIN", new_y="NEXT")

    _section(pdf, "Evidence")
    aws = alert.get("aws") or {}
    keys = []
    if aws.get("s3_frame_url"):
        keys.append(("Frame", aws["s3_frame_url"]))
    if aws.get("s3_alert_url"):
        keys.append(("Alert bundle", aws["s3_alert_url"]))
    snap = alert.get("evidence_snapshot_url")
    if snap:
        keys.append(("Local snapshot", snap))
    if not keys:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 7, "No evidence artefacts linked.", new_x="LMARGIN", new_y="NEXT")
    for label, url in keys:
        _kv(pdf, label, url)

    _section(pdf, "Agent decisions (recent)")
    if not decisions:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 7, "No decision-log entries for this camera.", new_x="LMARGIN", new_y="NEXT")
    for d in decisions[:10]:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(36, 6, _text(d.get("at")))
        pdf.set_font("Helvetica", "", 9)
        summary = d.get("input_summary") or f"{d.get('from')} -> {d.get('to')}"
        pdf.multi_cell(0, 6,
                       f"{_text(summary)} (conf {_text(d.get('confidence'))})",
                       new_x="LMARGIN", new_y="NEXT")
        if d.get("rationale") or d.get("reasoning"):
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(80, 80, 80)
            pdf.multi_cell(0, 5, _text(d.get("rationale") or d.get("reasoning")),
                           new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 7, f"Generated {_text(generated_at)} by AquaTrace.",
             new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
