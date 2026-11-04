"""Precipitation forecast integration for AquaTrace.

Source: Open-Meteo (https://open-meteo.com) — free, keyless, no account.

    GET https://api.open-meteo.com/v1/forecast
          ?latitude=<zone lat>&longitude=<zone lng>
          &hourly=precipitation,precipitation_probability
          &forecast_days=2&timezone=auto

Contract — same rules as awsx.py (the project's external-integration
pattern): every public function is best-effort and NEVER raises; a failed
fetch keeps the last-known summary, else the zone reports "unavailable";
reads are served from an in-memory TTL cache so the agent loop NEVER blocks
on a network call; ``status()`` reports health for the dashboard pill.

Refresh: one poll per 30 minutes per unique camera zone (the first camera
in the zone supplies the coordinates), executed on a daemon thread started
with the API. No keys, no configuration required.

Configuration (environment):
    AQUATRACE_FORECAST_ENABLED  "1" (default) / "0" — master switch
    AQUATRACE_FORECAST_MODEL    open-meteo ``models=`` value ("" = best match)

Display / fusion of forecast data in the dashboard and the agent are
separate switches — see config.FORECAST_DISPLAY / FORECAST_ADVISORY /
FORECAST_TIGHTEN. Forecasts NEVER create alerts, whatever those flags say.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from . import config

log = logging.getLogger("aquatrace.forecast")

BASE = "https://api.open-meteo.com/v1/forecast"
TTL_S = 30 * 60.0            # one poll per zone per 30 min
FUSION_MAX_AGE_S = 2 * TTL_S  # agent fusion trusts nothing older than 1 h
TIMEOUT_S = 6.0               # per-request budget; a slow feed cannot stall us
STATUS_TTL_S = 60.0           # status() cache, mirrors awsx.status()
SOURCE_DEFAULT = "Open-Meteo precipitation forecast (open-meteo.com, best-match model)"


class ForecastSettings:
    def __init__(self) -> None:
        self.enabled = os.environ.get("AQUATRACE_FORECAST_ENABLED", "1").strip() != "0"
        self.model = os.environ.get("AQUATRACE_FORECAST_MODEL", "").strip()
        # endpoint override for proxies / offline testing (never a key)
        self.url = os.environ.get("AQUATRACE_FORECAST_URL", "").strip() or BASE

    @property
    def source(self) -> str:
        if self.model:
            return f"Open-Meteo precipitation forecast (open-meteo.com, models={self.model})"
        return SOURCE_DEFAULT


settings = ForecastSettings()

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}   # zone -> summary (TTL cache)
_refreshing = False
_stop = threading.Event()
_status_cache: dict[str, Any] = {"at": 0.0, "payload": None}


# ------------------------------------------------------------------- zones


def _zones() -> list[dict[str, Any]]:
    """One entry per unique camera zone (first camera with coordinates wins)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cam in config.CAMERAS:
        zone = str(cam.get("zone") or cam["id"])
        if zone in seen:
            continue
        lat, lng = cam.get("lat"), cam.get("lng")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        seen.add(zone)
        out.append({"zone": zone, "lat": float(lat), "lng": float(lng)})
    return out


def _zone_of(camera_id: str) -> Optional[str]:
    for cam in config.CAMERAS:
        if cam["id"] == camera_id:
            return str(cam.get("zone") or camera_id)
    return None


# ------------------------------------------------------------------- fetch


def _summarize(zone: str, lat: float, lng: float, data: dict[str, Any]) -> dict[str, Any]:
    """Turn an open-meteo payload into a per-zone summary. Raises on junk
    (callers treat that as a failed fetch and keep the last-known data)."""
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    precip = hourly.get("precipitation") or []
    prob = hourly.get("precipitation_probability") or []
    if not times or not precip or len(times) != len(precip):
        raise ValueError("open-meteo returned no usable hourly series")

    # times are local (timezone=auto); align "now" using the reported offset
    offset = int(data.get("utc_offset_seconds") or 0)
    now_key = datetime.fromtimestamp(time.time() + offset, tz=timezone.utc).strftime("%Y-%m-%dT%H")
    start = next((i for i, t in enumerate(times) if str(t)[:13] >= now_key), 0)

    def _pairs(end: int) -> list[tuple[str, float]]:
        """(hour, value) pairs for a window, skipping null entries."""
        return [
            (str(times[j]), float(precip[j]))
            for j in range(start, min(end, len(times)))
            if isinstance(precip[j], (int, float))
        ]

    def _probs(end: int) -> list[float]:
        return [float(prob[j]) for j in range(start, min(end, len(prob)))
                if isinstance(prob[j], (int, float))]

    pairs6, pairs24 = _pairs(start + 6), _pairs(start + 24)
    prob6_vals, prob24_vals = _probs(start + 6), _probs(start + 24)
    if not pairs24:
        raise ValueError("open-meteo returned no numeric precipitation for the window")

    mm6 = round(sum(v for _, v in pairs6), 1)
    mm24 = round(sum(v for _, v in pairs24), 1)
    prob6 = int(max(prob6_vals)) if prob6_vals else None
    prob24 = int(max(prob24_vals)) if prob24_vals else None
    worst_at, worst_mm = max(pairs24, key=lambda kv: kv[1])
    worst = {"at": worst_at, "mm": round(worst_mm, 1)}

    gate_active = mm6 >= config.FORECAST_GATE_MM6H and (prob6 or 0) >= config.FORECAST_GATE_PROB
    if gate_active:
        advisory = (f"Heavy rain expected — {mm6} mm over the next 6 h "
                    f"({prob6}% peak hourly probability)")
    elif mm24 >= 5.0:
        advisory = f"Rain expected — {mm24} mm over the next 24 h"
    else:
        advisory = None

    return {
        "zone": zone,
        "lat": lat,
        "lng": lng,
        "status": "ok",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mm_6h": mm6,
        "mm_24h": mm24,
        "prob_max_6h": prob6,
        "prob_max_24h": prob24,
        "worst_hour": worst["at"] if worst else None,
        "worst_mm": worst["mm"] if worst else None,
        "source": settings.source,
        "advisory": advisory,
        "gate": {
            "active": gate_active,
            "mm_6h": mm6,
            "prob_6h": prob6,
            "threshold_mm_6h": config.FORECAST_GATE_MM6H,
            "threshold_prob": config.FORECAST_GATE_PROB,
        },
        "_fetched_epoch": time.time(),
    }


def _fetch_one(zone: str, lat: float, lng: float) -> dict[str, Any]:
    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lng,
        "hourly": "precipitation,precipitation_probability",
        "forecast_days": 2,
        "timezone": "auto",
    }
    if settings.model:
        params["models"] = settings.model
    r = httpx.get(settings.url, params=params, timeout=TIMEOUT_S)
    r.raise_for_status()
    return _summarize(zone, lat, lng, r.json())


def _worker(zones: list[dict[str, Any]]) -> None:
    global _refreshing
    try:
        for z in zones:
            if _stop.is_set():
                break
            try:
                summary = _fetch_one(z["zone"], z["lat"], z["lng"])
                with _lock:
                    _cache[z["zone"]] = summary
                log.debug("forecast %s: %s mm/6h prob=%s",
                          z["zone"], summary["mm_6h"], summary["prob_max_6h"])
            except Exception:
                # keep the last-known summary; the zone simply ages into "stale"
                log.warning("forecast fetch failed for zone %s", z["zone"], exc_info=True)
    finally:
        global _refreshing
        with _lock:
            _refreshing = False


def schedule_refresh(force: bool = False) -> bool:
    """Fetch every stale zone on a daemon thread. Never raises, never blocks."""
    if not settings.enabled:
        return False
    global _refreshing
    with _lock:
        if _refreshing:
            return False
        now = time.time()
        stale = [
            z for z in _zones()
            if force or z["zone"] not in _cache
            or now - (_cache[z["zone"]].get("_fetched_epoch") or 0) >= TTL_S
        ]
        if not stale:
            return False
        _refreshing = True
    threading.Thread(target=_worker, args=(stale,), daemon=True,
                     name="aquatrace-forecast").start()
    return True


# ------------------------------------------------------------------ readers


def payload(schedule: bool = False) -> dict[str, Any]:
    """Full forecast payload for /api/forecast. Never raises."""
    if not settings.enabled:
        return {"enabled": False, "status": "disabled", "source": settings.source,
                "zones": [], "at": time.time()}
    if schedule:
        try:
            schedule_refresh()
        except Exception:
            log.warning("forecast schedule failed", exc_info=True)
    try:
        now = time.time()
        zones: list[dict[str, Any]] = []
        for z in _zones():
            with _lock:
                raw = _cache.get(z["zone"])
                cur = dict(raw) if raw else None
            if cur is None:
                zones.append({**z, "status": "unavailable", "fetched_at": None,
                              "mm_6h": None, "mm_24h": None, "prob_max_6h": None,
                              "prob_max_24h": None, "worst_hour": None, "worst_mm": None,
                              "source": settings.source, "advisory": None,
                              "gate": {"active": False}})
                continue
            age = now - (cur.pop("_fetched_epoch", now) or now)
            cur["status"] = "ok" if age <= TTL_S else "stale"
            cur["age_s"] = int(age)
            zones.append(cur)
        available = any(z["status"] in ("ok", "stale") for z in zones)
        fetched = [z["fetched_at"] for z in zones if z.get("fetched_at")]
        return {
            "enabled": True,
            "available": available,
            "status": "ok" if available else "unavailable",
            "source": settings.source,
            "model": settings.model or "best_match",
            "fetched_at": max(fetched) if fetched else None,
            "zones": zones,
            "at": time.time(),
        }
    except Exception:
        log.warning("forecast payload failed", exc_info=True)
        return {"enabled": True, "status": "unavailable", "source": settings.source,
                "zones": [], "at": time.time()}


def status() -> dict[str, Any]:
    """Compact block for the dashboard status pill (cached 60 s; never raises)."""
    now = time.time()
    if now - (_status_cache.get("at") or 0) < STATUS_TTL_S and _status_cache.get("payload"):
        return _status_cache["payload"]
    try:
        p = payload(schedule=True)
        zone_probs = [z["prob_max_6h"] for z in p["zones"] if isinstance(z.get("prob_max_6h"), (int, float))]
        zone_mm = [z["mm_6h"] for z in p["zones"] if isinstance(z.get("mm_6h"), (int, float))]
        advisories = [z["advisory"] for z in p["zones"] if z.get("advisory")]
        payload_out = {
            "enabled": p.get("enabled", True),
            "status": p.get("status", "unavailable"),
            "source": p.get("source"),
            "model": p.get("model"),
            "zones": len(p.get("zones") or []),
            "zones_ok": sum(1 for z in p.get("zones") or [] if z.get("status") == "ok"),
            "prob_max_6h": max(zone_probs) if zone_probs else None,
            "mm_6h": round(max(zone_mm), 1) if zone_mm else None,
            "advisory": advisories[0] if advisories else None,
            "fetched_at": p.get("fetched_at"),
            "at": now,
        }
    except Exception:
        log.warning("forecast status failed", exc_info=True)
        payload_out = {"enabled": True, "status": "unavailable",
                       "source": settings.source, "zones": 0, "at": now}
    _status_cache["at"] = now
    _status_cache["payload"] = payload_out
    return payload_out


def fusion_hint(camera_id: str) -> Optional[dict[str, Any]]:
    """Read-only view for the agent: the zone summary when — and only when —
    the heavy-rain gate is open. Memory-only: never fetches, never blocks,
    never raises."""
    if not settings.enabled:
        return None
    try:
        zone = _zone_of(camera_id)
        if not zone:
            return None
        with _lock:
            cur = _cache.get(zone)
        if not cur:
            return None
        if time.time() - (cur.get("_fetched_epoch") or 0) > FUSION_MAX_AGE_S:
            return None
        if not (cur.get("gate") or {}).get("active"):
            return None
        return {
            "zone": zone,
            "mm_6h": cur.get("mm_6h"),
            "prob_6h": cur.get("prob_max_6h"),
            "advisory": cur.get("advisory"),
            "source": cur.get("source") or settings.source,
        }
    except Exception:
        return None


# ------------------------------------------------------------------ control


def start() -> None:
    """Kick an initial fetch and keep the cache warm in the background."""
    if not settings.enabled:
        log.info("forecast integration disabled (AQUATRACE_FORECAST_ENABLED=0)")
        return
    try:
        schedule_refresh(force=True)
    except Exception:
        log.warning("initial forecast fetch failed to start", exc_info=True)

    def _loop() -> None:
        while not _stop.wait(120.0):
            try:
                schedule_refresh()
            except Exception:
                log.warning("forecast refresh loop error", exc_info=True)

    threading.Thread(target=_loop, daemon=True, name="aquatrace-forecast-loop").start()
    log.info("forecast integration started (source: %s)", settings.source)


def stop() -> None:
    _stop.set()
