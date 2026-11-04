"""Durable SQLite persistence for AquaTrace.

Survives backend restarts: alerts, the agent decision log, operator actions,
and the AWS delivery outbox. sqlite3 stdlib only — no new dependencies.

All functions are best-effort and never raise: a persistence failure must
never take down the alert pipeline. A single shared connection is guarded by
a lock (check_same_thread=False) because alert writes come from the agent
task, the ingest thread pool, and the AWS worker threads.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Optional

from . import config
from .stores import now_iso

log = logging.getLogger("aquatrace.db")

DB_PATH = os.path.join(config.DATA_DIR, "aquatrace.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id          TEXT PRIMARY KEY,
    camera_id   TEXT,
    severity    TEXT,
    type        TEXT,
    title       TEXT,
    detail      TEXT,
    created_at  TEXT,
    acked       INTEGER DEFAULT 0,
    ack_by      TEXT,
    ack_at      TEXT,
    drill       INTEGER DEFAULT 0,
    s3_key      TEXT,
    sns_msg_id  TEXT,
    data_json   TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT,
    agent         TEXT,
    input_summary TEXT,
    decision      TEXT,
    confidence    REAL,
    rationale     TEXT
);
CREATE TABLE IF NOT EXISTS operator_actions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT,
    actor   TEXT,
    action  TEXT,
    target  TEXT,
    note    TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT,
    kind          TEXT,
    payload_json  TEXT,
    attempts      INTEGER DEFAULT 0,
    next_retry_at TEXT,
    status        TEXT DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_alerts_cam ON alerts(camera_id, created_at);
"""

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> Optional[sqlite3.Connection]:
    global _conn
    with _lock:
        if _conn is None:
            try:
                os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
                _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                _conn.row_factory = sqlite3.Row
            except Exception:
                log.exception("sqlite connect failed (%s)", DB_PATH)
                return None
        return _conn


def init_db() -> bool:
    """Create tables if needed. Returns True when the DB is usable."""
    conn = _connect()
    if conn is None:
        return False
    try:
        with _lock:
            conn.executescript(_SCHEMA)
            conn.commit()
        log.info("sqlite persistence ready at %s", DB_PATH)
        return True
    except Exception:
        log.exception("sqlite init failed")
        return False


# ------------------------------------------------------------------- alerts


def _alert_row(alert: dict[str, Any]) -> dict[str, Any]:
    aws = alert.get("aws") or {}
    severity = str(alert.get("severity", ""))
    state = str(alert.get("state", ""))
    camera_id = str(alert.get("camera_id", ""))
    return {
        "id": alert.get("alert_id", ""),
        "camera_id": camera_id,
        "severity": severity,
        "type": alert.get("type") or state,
        "title": f"{severity.upper()} {state} on {camera_id}".strip(),
        "detail": str(alert.get("reasoning", "") or "")[:2000],
        "created_at": alert.get("started_at") or now_iso(),
        "acked": 1 if alert.get("acknowledged_at") else 0,
        "ack_by": alert.get("ack_by"),
        "ack_at": alert.get("acknowledged_at"),
        "drill": 1 if alert.get("drill") else 0,
        "s3_key": aws.get("s3_frame_url") or "",
        "sns_msg_id": aws.get("sns_message_id") or "",
        "data_json": json.dumps(alert, default=str),
    }


def save_alert(alert: dict[str, Any]) -> bool:
    """Write-through for a new or updated alert. Never raises."""
    conn = _connect()
    if conn is None or not alert.get("alert_id"):
        return False
    row = _alert_row(alert)
    try:
        with _lock:
            conn.execute(
                """INSERT OR REPLACE INTO alerts
                   (id, camera_id, severity, type, title, detail, created_at,
                    acked, ack_by, ack_at, drill, s3_key, sns_msg_id, data_json)
                   VALUES (:id, :camera_id, :severity, :type, :title, :detail,
                           :created_at, :acked, :ack_by, :ack_at, :drill,
                           :s3_key, :sns_msg_id, :data_json)""",
                row,
            )
            conn.commit()
        return True
    except Exception:
        log.exception("save_alert failed for %s", row["id"])
        return False


def load_recent_alerts(limit: int = 200) -> list[dict[str, Any]]:
    """Most-recent alerts, oldest-first (so AlertStore.add keeps order)."""
    conn = _connect()
    if conn is None:
        return []
    try:
        with _lock:
            rows = conn.execute(
                "SELECT data_json FROM alerts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in reversed(rows):
            try:
                out.append(json.loads(r["data_json"]))
            except Exception:
                continue
        return out
    except Exception:
        log.exception("load_recent_alerts failed")
        return []


# ----------------------------------------------------------------- decisions


def save_decision(at: str, agent: str, input_summary: str, decision: str,
                  confidence: float, rationale: str) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        with _lock:
            conn.execute(
                """INSERT INTO decisions
                   (at, agent, input_summary, decision, confidence, rationale)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (at, agent, input_summary, decision, float(confidence or 0.0),
                 str(rationale or "")[:2000]),
            )
            conn.commit()
        return True
    except Exception:
        log.exception("save_decision failed")
        return False


def load_recent_decisions(limit: int = 100) -> list[dict[str, Any]]:
    conn = _connect()
    if conn is None:
        return []
    try:
        with _lock:
            rows = conn.execute(
                "SELECT at, agent, input_summary, decision, confidence, rationale"
                " FROM decisions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        log.exception("load_recent_decisions failed")
        return []


# ------------------------------------------------------------------- actions


def save_action(at: str, actor: str, action: str, target: str, note: str = "") -> bool:
    """Operator actions (actor='operator') and automated ones ('system')."""
    conn = _connect()
    if conn is None:
        return False
    try:
        with _lock:
            conn.execute(
                "INSERT INTO operator_actions (at, actor, action, target, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (at, actor, action, target, str(note or "")[:2000]),
            )
            conn.commit()
        return True
    except Exception:
        log.exception("save_action failed")
        return False


# -------------------------------------------------------------------- outbox


def outbox_enqueue(kind: str, payload: dict[str, Any]) -> int:
    """Queue a failed AWS delivery. Returns the outbox row id (0 on failure)."""
    conn = _connect()
    if conn is None:
        return 0
    at = now_iso()
    try:
        with _lock:
            cur = conn.execute(
                """INSERT INTO outbox
                   (created_at, kind, payload_json, attempts, next_retry_at, status)
                   VALUES (?, ?, ?, 0, ?, 'pending')""",
                (at, kind, json.dumps(payload, default=str), at),
            )
            conn.commit()
            row_id = cur.lastrowid or 0
        log.warning("outbox: queued %s delivery #%d", kind, row_id)
        return row_id
    except Exception:
        log.exception("outbox_enqueue failed")
        return 0


def outbox_due(now_epoch: float) -> list[dict[str, Any]]:
    """Pending items whose next_retry_at has passed."""
    conn = _connect()
    if conn is None:
        return []
    try:
        from .stores import parse_iso
        due = []
        with _lock:
            for r in conn.execute(
                "SELECT id, kind, payload_json, attempts, next_retry_at FROM outbox"
                " WHERE status = 'pending' ORDER BY id ASC"
            ).fetchall():
                try:
                    if parse_iso(r["next_retry_at"]) <= now_epoch:
                        due.append(dict(r))
                except Exception:
                    due.append(dict(r))  # unparsable -> retry now
        return due
    except Exception:
        log.exception("outbox_due failed")
        return []


def outbox_update(item_id: int, *, attempts: int, next_retry_at: str,
                  status: str) -> bool:
    conn = _connect()
    if conn is None:
        return False
    try:
        with _lock:
            conn.execute(
                "UPDATE outbox SET attempts = ?, next_retry_at = ?, status = ?"
                " WHERE id = ?",
                (attempts, next_retry_at, status, item_id),
            )
            conn.commit()
        return True
    except Exception:
        log.exception("outbox_update failed for #%d", item_id)
        return False


def outbox_list_pending() -> list[dict[str, Any]]:
    """Dashboard view of queued/failed deliveries."""
    conn = _connect()
    if conn is None:
        return []
    try:
        with _lock:
            rows = conn.execute(
                "SELECT id, created_at, kind, payload_json, attempts,"
                " next_retry_at, status FROM outbox"
                " WHERE status IN ('pending', 'dead') ORDER BY id DESC LIMIT 50",
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                payload = json.loads(d.pop("payload_json") or "{}")
            except Exception:
                payload = {}
            d["subject"] = payload.get("subject", "")
            message = str(payload.get("message", ""))
            d["message_preview"] = message[:200]
            out.append(d)
        return out
    except Exception:
        log.exception("outbox_list_pending failed")
        return []
