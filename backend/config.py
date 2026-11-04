"""Static configuration: camera registry, thresholds, paths."""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("aquatrace.config")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "backend", "data")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
SCENE_DIR = os.path.join(ROOT, "assets", "scenes")
CONTRACTS_DIR = os.path.join(ROOT, "contracts")
VISION_MODULE = "vision.engine"

# -------------------------------------------------------------------- mode
# AQUATRACE_MODE=demo (default) | live
# live mode: /simulate/* is disabled (403), engines start from the camera
# registry, drill endpoints stay available in both modes.
MODE = os.environ.get("AQUATRACE_MODE", "live").strip().lower() or "live"
IS_LIVE = MODE == "live"

# Identity of this deployment: Flood Prone Uttarakhand — Upper Ganga & Alaknanda Basin
SITE_NAME = "Uttarakhand Flood Early Warning Network — Upper Ganga & Alaknanda Basin"
SITE_CENTER = [30.1030, 78.2970]

_DEFAULT_CAMERAS: list[dict[str, Any]] = [
    {
        "id": "cam-01",
        "name": "Ganga Tapovan & Lakshman Jhula Gorge",
        "zone": "Upper Ganga Gorge",
        "location": "Tapovan / Lakshman Jhula, Rishikesh, Uttarakhand",
        "lat": 30.1250,
        "lng": 78.3180,
        "adjacent": ["cam-02"],
        "source": "sim://floodnet_live",
        "enabled": True,
    },
    {
        "id": "cam-02",
        "name": "Triveni Ghat Flood Gauge & Riverfront",
        "zone": "Rishikesh Floodplain",
        "location": "Triveni Ghat, Mayakund, Rishikesh, Uttarakhand",
        "lat": 30.1030,
        "lng": 78.2970,
        "adjacent": ["cam-01", "cam-03"],
        "source": "sim://bridge-st-culvert",
        "enabled": True,
    },
    {
        "id": "cam-03",
        "name": "Veerbhadra Barrage & Sluice Gates",
        "zone": "Barrage Control Reach",
        "location": "Veerbhadra Barrage, Ganga River, Rishikesh, Uttarakhand",
        "lat": 30.0760,
        "lng": 78.2880,
        "adjacent": ["cam-02", "cam-04"],
        "source": "sim://campus-storm-outfall",
        "enabled": True,
    },
    {
        "id": "cam-04",
        "name": "Chilla Power Canal Intake & Flood Bypass",
        "zone": "Chilla Intake Reach",
        "location": "Chilla Hydroelectric Canal Intake, Ganga Basin, Uttarakhand",
        "lat": 30.0520,
        "lng": 78.2750,
        "adjacent": ["cam-03"],
        "source": "sim://riverside-pump-station",
        "enabled": True,
    },
]


def _load_cameras_yaml() -> "list[dict[str, Any]] | None":
    """Load backend/cameras.yaml; None when missing or unparsable (caller
    falls back to the built-in registry)."""
    path = os.path.join(ROOT, "backend", "cameras.yaml")
    if not os.path.isfile(path):
        return None
    try:
        import yaml  # lazy: only needed when the registry file exists

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        cams = data.get("cameras") if isinstance(data, dict) else None
        if not cams or not isinstance(cams, list):
            log.warning("cameras.yaml has no camera list; using built-in registry")
            return None
        out = []
        for c in cams:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            cam = dict(c)
            cam.setdefault("name", cam["id"])
            cam.setdefault("zone", "")
            cam.setdefault("location", cam["name"])
            cam.setdefault("adjacent", [])
            cam.setdefault("source", "")
            cam.setdefault("enabled", True)
            out.append(cam)
        return out or None
    except Exception:
        log.warning("failed to parse cameras.yaml; using built-in registry",
                    exc_info=True)
        return None


CAMERAS: list[dict[str, Any]] = _load_cameras_yaml() or [dict(c) for c in _DEFAULT_CAMERAS]

CAMERA_IDS = [c["id"] for c in CAMERAS]
ADJACENCY = {c["id"]: list(c["adjacent"]) for c in CAMERAS}


# Map geometry for the console's Leaflet layer: where the camera looks
# (bearing_deg from true north, clockwise) and how far (fov_deg wedge,
# range_m radius). cameras.yaml overrides these per site.
def apply_camera_geometry(cam: dict[str, Any]) -> None:
    cam.setdefault("bearing_deg", 140.0)
    cam.setdefault("fov_deg", 70.0)
    cam.setdefault("range_m", 150.0)


for _c in CAMERAS:
    apply_camera_geometry(_c)



def register_camera(cam: dict[str, Any]) -> None:
    """Runtime registration (drill cameras). Idempotent."""
    if cam["id"] in CAMERA_IDS:
        return
    apply_camera_geometry(cam)
    CAMERAS.append(cam)
    CAMERA_IDS.append(cam["id"])
    ADJACENCY[cam["id"]] = list(cam.get("adjacent", []))
    log.info("registered camera %s", cam["id"])


def unregister_camera(camera_id: str) -> None:
    """Runtime removal (drill cameras on drill stop)."""
    if camera_id not in CAMERA_IDS:
        return
    CAMERAS[:] = [c for c in CAMERAS if c["id"] != camera_id]
    CAMERA_IDS[:] = [cid for cid in CAMERA_IDS if cid != camera_id]
    ADJACENCY.pop(camera_id, None)
    log.info("unregistered camera %s", camera_id)

# ---------------------------------------------------------------- agent tuning
BASELINE_WINDOW_S = 300.0        # learned "normal" = trailing 5 minutes
BASELINE_STEP_S = 2.0            # increments are taken 2 s apart (noise-robust)
BASELINE_FLOOR = 0.0004          # ratio/s — never divide by a near-zero normal
RATE_WINDOW_S = 30.0             # expansion rate = slope over 30 s
RATE_MIN_POINTS = 25
TELEMETRY_STALE_S = 6.0          # no samples for this long -> signal_lost
TICK_HZ = 1.0

WATCH_THRESHOLD = 2.0            # anomaly > 2x ...
ESCALATE_THRESHOLD = 3.0         # anomaly > 3x ...
CRITICAL_THRESHOLD = 5.0         # anomaly > 5x ...
NORMAL_THRESHOLD = 1.5           # ... and below this counts as "normal again"
SUSTAIN_WATCH_S = 10.0
SUSTAIN_ESCALATE_S = 10.0
SUSTAIN_CRITICAL_S = 6.0
RECOVER_S = 60.0                 # hysteresis: step down only after 60 s calm
WATERLINE_ESCALATE_PX = 12.0     # persistent waterline displacement (falling)
VERIFY_MIN_CONF = 0.80           # below this we must verify before Critical
VERIFY_TIMEOUT_S = 15.0
VERIFY_MIN_HOLD_S = 5.0        # hold Verifying at least this long before acting
CORROBORATION_MIN_PCT = 10.0     # neighbour water area must grow this much
CORROBORATION_MIN_ANOMALY = 1.5
FP_BOOST = 1.7                   # false-positive feedback: raise the baseline
FP_BOOST_TTL_S = 600.0

DEFAULT_SAMPLE_HZ = 5.0
BOOST_SAMPLE_HZ = 10.0


def camera(id_: str) -> dict[str, Any]:
    for c in CAMERAS:
        if c["id"] == id_:
            return c
    raise KeyError(id_)


def parse_window(window: str) -> float:
    """'5m' -> 300.0 seconds (supports s/m/h suffix; bare numbers are seconds)."""
    w = (window or "5m").strip().lower()
    unit = w[-1] if w[-1] in "smh" else "s"
    try:
        value = float(w[:-1]) if w[-1] in "smh" else float(w)
    except ValueError:
        value = 5.0
    return value * {"s": 1.0, "m": 60.0, "h": 3600.0}[unit]


# ----------------------------------------------------- precipitation forecast
# Display and agent-fusion are INDEPENDENT switches: FORECAST_DISPLAY governs
# /api/forecast and the dashboard pills; FORECAST_ADVISORY / FORECAST_TIGHTEN
# govern whether the agent may reason about forecast data at all. Forecasts
# NEVER create alerts whatever these flags say — they can only label advisory
# text or shrink the verification window by FORECAST_TIGHTEN_PCT (<= 20%).
FORECAST_DISPLAY = os.environ.get("AQUATRACE_FORECAST_DISPLAY", "1").strip() != "0"
FORECAST_ADVISORY = os.environ.get("AQUATRACE_FORECAST_ADVISORY", "1").strip() != "0"
FORECAST_TIGHTEN = os.environ.get("AQUATRACE_FORECAST_TIGHTEN", "1").strip() != "0"
FORECAST_TIGHTEN_PCT = 0.20        # hard cap: verification window shrinks by <= 20%
FORECAST_GATE_MM6H = 20.0          # gate opens at >= 20 mm expected over 6 h
FORECAST_GATE_PROB = 70.0          # ...and >= 70 % peak hourly precipitation probability
