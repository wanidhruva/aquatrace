"""SNS delivery outbox with exponential-backoff retry.

sns_publish() into the void (zero subscribers, dead venue Wi-Fi) used to fail
silently and return None. This module wraps every critical SNS publish:

    publish_sns(subject, message) -> MessageId | None

On failure the delivery is queued in SQLite (db.outbox) and a background
thread retries every 30 s with exponential backoff (30 s * 2^attempts, capped
at 1 h). After 10 attempts the item is marked dead for operator inspection.
Each failure increments the CloudWatch metric AwsPublishFailures.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from . import awsx, db
from .stores import now_iso

log = logging.getLogger("aquatrace.outbox")

MAX_ATTEMPTS = 10
RETRY_INTERVAL_S = 30.0
MAX_BACKOFF_S = 3600.0


def _backoff_s(attempts: int) -> float:
    return min(MAX_BACKOFF_S, RETRY_INTERVAL_S * (2 ** max(0, attempts)))


def _metric_failure() -> None:
    """Best-effort CloudWatch counter; throttled inside awsx, never raises."""
    try:
        awsx.put_metrics("outbox", {"AwsPublishFailures": 1.0})
    except Exception:
        pass


def publish_sns(subject: str, message: str) -> Optional[str]:
    """Try SNS once; on failure queue to the outbox and return None."""
    try:
        mid = awsx.sns_publish(subject, message)
    except Exception:
        log.exception("sns publish raised")
        mid = None
    if mid:
        return mid
    _metric_failure()
    db.outbox_enqueue("sns", {"subject": subject, "message": message})
    return None


def _retry_once(now: float) -> None:
    for item in db.outbox_due(now):
        try:
            payload = json.loads(item.get("payload_json") or "{}")
        except Exception:
            payload = {}
        subject = str(payload.get("subject", ""))
        message = str(payload.get("message", ""))
        try:
            mid = awsx.sns_publish(subject, message)
        except Exception:
            log.exception("outbox #%d retry raised", item["id"])
            mid = None
        if mid:
            db.outbox_update(item["id"], attempts=item["attempts"] + 1,
                             next_retry_at=now_iso(now), status="sent")
            log.info("outbox #%d delivered on retry (MessageId %s)", item["id"], mid)
            continue
        _metric_failure()
        attempts = int(item.get("attempts") or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            db.outbox_update(item["id"], attempts=attempts,
                             next_retry_at=now_iso(now), status="dead")
            log.error("outbox #%d dead after %d attempts", item["id"], attempts)
        else:
            nxt = now + _backoff_s(attempts)
            db.outbox_update(item["id"], attempts=attempts,
                             next_retry_at=now_iso(nxt), status="pending")
            log.warning("outbox #%d retry %d failed; next try in %.0f s",
                        item["id"], attempts, nxt - now)


def _loop(stop: threading.Event) -> None:
    log.info("outbox retry thread started (every %.0f s)", RETRY_INTERVAL_S)
    while not stop.wait(RETRY_INTERVAL_S):
        try:
            _retry_once(time.time())
        except Exception:
            log.exception("outbox retry loop failed")


_stop: Optional[threading.Event] = None
_thread: Optional[threading.Thread] = None


def start_retry_thread() -> None:
    global _stop, _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop = threading.Event()
    _thread = threading.Thread(target=_loop, args=(_stop,), daemon=True,
                               name="aquatrace-outbox")
    _thread.start()


def stop_retry_thread() -> None:
    if _stop is not None:
        _stop.set()


def pending() -> list[dict[str, Any]]:
    """Dashboard view: queued and dead deliveries."""
    return db.outbox_list_pending()
