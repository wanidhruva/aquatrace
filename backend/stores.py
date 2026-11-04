"""Storage adapters.

Two interfaces, both shaped so they swap to managed AWS services 1:1 later:

    TimeSeriesStore  -> Amazon Timestream table (memory here)
    ObjectStorage    -> Amazon S3 bucket   (local disk here)

No AWS calls happen in the sprint build.
"""

from __future__ import annotations

import collections
import json
import os
import threading
import time
from typing import Any, Iterable, Optional

from . import config


def now_iso(ts: Optional[float] = None) -> str:
    t = time.time() if ts is None else ts
    ms = int((t - int(t)) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{ms:03d}Z"


def parse_iso(s: str) -> float:
    from datetime import datetime, timezone

    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# --------------------------------------------------------------- time series


class TimeSeriesStore:
    """In-memory Timestream-shaped table: (camera_id, ts, fields) rows."""

    RETENTION_S = 900.0          # 15 minutes at 5 fps ~ 4500 rows per camera

    def __init__(self) -> None:
        self._rows: dict[str, collections.deque] = {}
        self._lock = threading.Lock()
        for cid in config.CAMERA_IDS:
            self._rows[cid] = collections.deque(maxlen=4600)

    def write(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._rows.setdefault(row["camera_id"], collections.deque(maxlen=4600)).append(row)

    def write_many(self, rows: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            for row in rows:
                self._rows.setdefault(row["camera_id"], collections.deque(maxlen=4600)).append(row)

    def query(self, camera_id: str, window_s: float, valid_only: bool = False) -> list[dict[str, Any]]:
        cutoff = time.time() - window_s
        with self._lock:
            buf = self._rows.get(camera_id)
            rows = list(buf) if buf else []
        out = [r for r in rows if r["_epoch"] >= cutoff]
        if valid_only:
            out = [r for r in out if r.get("detection_valid", True)]
        return out

    def latest(self, camera_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            buf = self._rows.get(camera_id)
            return buf[-1] if buf else None

    def last_seen(self, camera_id: str) -> Optional[float]:
        row = self.latest(camera_id)
        return row["_epoch"] if row else None

    def clear(self, camera_id: str) -> None:
        with self._lock:
            self._rows.setdefault(camera_id, collections.deque(maxlen=4600)).clear()

    def clear_all(self) -> None:
        with self._lock:
            for buf in self._rows.values():
                buf.clear()

    def reset_and_seed(self, camera_id: str, minutes: float = 5.0) -> None:
        """Pre-fill a calm history so the agent has a learned baseline at t=0."""
        self.clear(camera_id)
        seed_row(camera_id, minutes)

    def stats(self, camera_id: str) -> dict[str, Any]:
        rows = self.query(camera_id, config.BASELINE_WINDOW_S)
        return {"rows": len(rows)}


def seed_row(camera_id: str, minutes: float = 5.0, hz: float = 5.0) -> None:
    """Deterministic calm baseline rows (see scripts/seed_baseline.py)."""
    import math

    end = time.time()
    count = int(minutes * 60 * hz)
    step = 1.0 / hz
    rows = []
    for i in range(count):
        t = end - (count - i) * step
        phase = t  # continuous phase across seeds
        ratio = (
            0.070
            + 0.0060 * math.sin(2 * math.pi * phase / 12.0)
            + 0.0022 * math.sin(2 * math.pi * phase / 6.0 + 1.1)
            + 0.0010 * math.sin(2 * math.pi * phase / 4.0 + 2.4)
        )
        rows.append({
            "camera_id": camera_id,
            "ts": now_iso(t),
            "_epoch": t,
            "frame_id": i,
            "water_area_ratio": round(ratio, 5),
            "flow_mean": round(2.05 + 0.25 * math.sin(2 * math.pi * phase / 6.0), 3),
            "flow_std": 0.25,
            "waterline_y": round(332.0 - (ratio - 0.070) * 2300.0, 1),
            "waterline_delta": 0.0,
            "confidence": 0.93,
            "detection_valid": True,
            "water_area_px": round(ratio * 640 * 360, 1),
        })
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _ts_store().write_many(rows)


_TS: Optional[TimeSeriesStore] = None


def _ts_store() -> TimeSeriesStore:
    global _TS
    if _TS is None:
        _TS = TimeSeriesStore()
    return _TS


def timeseries() -> TimeSeriesStore:
    return _TS or _ts_store()


# ------------------------------------------------------------- object storage


class ObjectStorage:
    """S3-shaped object store. Local disk implementation for the sprint."""

    def __init__(self, root: str, public_prefix: str = "/snapshots") -> None:
        self.root = root
        self.public_prefix = public_prefix
        os.makedirs(root, exist_ok=True)

    def put(self, key: str, body: bytes, content_type: str = "image/jpeg") -> str:
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
        return self.presign(key)

    def presign(self, key: str) -> str:
        """Equivalent of S3 presigned GET — local files are served by the API."""
        return f"{self.public_prefix}/{key}"

    def get(self, key: str) -> Optional[bytes]:
        path = os.path.join(self.root, key)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            return fh.read()

    def delete(self, key: str) -> None:
        """Remove an object (used when snapshots are evicted)."""
        path = os.path.join(self.root, key)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


class SnapshotStore:
    """Evidence frames: keeps the most recent annotated frame per camera."""

    def __init__(self, storage: ObjectStorage, keep: int = 30) -> None:
        self.storage = storage
        self.keep = keep
        self._lock = threading.Lock()
        self._latest: dict[str, str] = {}
        self._keys: dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=keep)
        )

    def put_jpeg_b64(self, camera_id: str, ts: str, b64: str) -> str:
        import base64

        key = f"{camera_id}/{ts.replace(':', '').replace('.', '')}.jpg"
        url = self.storage.put(key, base64.b64decode(b64))
        with self._lock:
            keys = self._keys[camera_id]
            # Evict oldest keys beyond `keep` and DELETE the JPEG files so
            # the snapshot directory cannot grow without bound.
            while len(keys) >= self.keep:
                old = keys.popleft()
                self.storage.delete(old)
            keys.append(key)
            self._latest[camera_id] = url
        return url

    def latest_url(self, camera_id: str) -> Optional[str]:
        with self._lock:
            return self._latest.get(camera_id)

    def freeze_latest(self, camera_id: str) -> Optional[str]:
        """Pin the current latest frame as an alert's evidence snapshot."""
        with self._lock:
            return self._latest.get(camera_id)


# ---------------------------------------------------------------- alert store


class AlertStore:
    """Alert records keyed by alert_id, capped at MAX_ALERTS (oldest first)."""

    MAX_ALERTS = 300

    def __init__(self) -> None:
        self._alerts: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
        self._seq = 0
        self._lock = threading.Lock()

    def new_id(self, camera_id: str) -> str:
        with self._lock:
            self._seq += 1
            return f"ALT-{time.strftime('%H%M%S')}-{self._seq:03d}-{camera_id}"

    def add(self, alert: dict) -> None:
        with self._lock:
            self._alerts[alert["alert_id"]] = alert
            while len(self._alerts) > self.MAX_ALERTS:
                self._alerts.popitem(last=False)

    def get(self, alert_id: str) -> Optional[dict]:
        with self._lock:
            return self._alerts.get(alert_id)

    def active_for(self, camera_id: str) -> Optional[dict]:
        with self._lock:
            for a in reversed(self._alerts.values()):
                if a["camera_id"] == camera_id and a["status"] in ("active", "acknowledged"):
                    return a
            return None

    def all(self, limit: int = 60) -> list[dict]:
        with self._lock:
            items = list(self._alerts.values())[::-1]
            return items[:limit]

    def to_json(self) -> str:
        with self._lock:
            return json.dumps({"alerts": list(self._alerts.values())}, default=str)

    def restore(self, items: "Iterable[dict]") -> None:
        """Reload persisted alerts on boot (oldest-first). Bumps the id
        sequence past any restored ids so new ids never collide."""
        import re

        with self._lock:
            for a in items:
                aid = a.get("alert_id")
                if not aid:
                    continue
                self._alerts[aid] = a
                m = re.search(r"-(\d{3})-", aid)
                if m:
                    self._seq = max(self._seq, int(m.group(1)))
            while len(self._alerts) > self.MAX_ALERTS:
                self._alerts.popitem(last=False)
