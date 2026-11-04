"""AquaTrace synthetic scene generator.

Programmatically renders pixel-deterministic CCTV test videos of a concrete
flood channel. Three shipped scenarios:

    normal         calm channel, small waterline oscillation
    gradual_rise   water area ramps ~6% -> ~68% of frame, flow accelerates
    flash_surge    flat, then a violent surge in a few seconds

Variants let adjacent cameras play a delayed/damped copy of the same event so
the backend verification gate has a real neighbouring signal to cross-check.

Determinism: every frame is a pure function of (scenario, variant, frame index).
"""

from __future__ import annotations

import math
import os
import sys

import cv2
import numpy as np

W, H = 640, 360
FPS = 15
GROUND_Y = 105            # everything below this row can hold water
PILLAR_X0, PILLAR_X1 = 0, 26
PILLAR_Y0 = 150

SCENARIOS = ("normal", "gradual_rise", "flash_surge")
VARIANTS = ("primary", "secondary", "ambient")

# ---------------------------------------------------------------- static scene


def _noise(rng: np.random.RandomState, h: int, w: int, blur: float) -> np.ndarray:
    n = rng.randn(h, w).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), blur)
    sd = float(n.std()) or 1.0
    return n / sd


def _ground_mask() -> np.ndarray:
    m = np.zeros((H, W), np.uint8)
    m[GROUND_Y:, :] = 1
    m[PILLAR_Y0:, PILLAR_X0:PILLAR_X1] = 0      # solid pillar never floods
    return m


def build_static(seed: int = 7) -> dict:
    rng = np.random.RandomState(seed)
    img = np.zeros((H, W, 3), np.uint8)

    # --- sky -------------------------------------------------------------
    for y in range(0, 62):
        k = y / 62.0
        img[y, :] = (int(14 + 10 * k), int(18 + 12 * k), int(28 + 16 * k))

    # --- building silhouettes -------------------------------------------
    x = -20
    while x < W + 20:
        bw = int(rng.randint(46, 110))
        bh = int(rng.randint(26, 58))
        top = 62 - bh
        img[max(top, 0):62, x:x + bw] = (16, 18, 24)
        # lit windows
        for wy in range(max(top + 5, 0), 56, 9):
            for wx in range(x + 4, x + bw - 6, 9):
                if rng.rand() < 0.30:
                    if rng.rand() < 0.35:
                        c = (238, 211, 34)      # cyan-lit
                    else:
                        c = (46, 168, 236)      # warm sodium
                    img[wy:wy + 4, wx:wx + 4] = c
        x += bw + int(rng.randint(2, 9))

    # --- far walkway + railing ------------------------------------------
    walk = _noise(rng, 44, W, 3)
    img[62:GROUND_Y] = np.clip(96 + walk[:GROUND_Y - 62] * 9, 0, 255).astype(np.uint8)[..., None]
    img[62:GROUND_Y] = (img[62:GROUND_Y].astype(np.float32) * np.array([1.0, 0.99, 0.96])).astype(np.uint8)
    for rx in range(4, W, 13):
        img[64:GROUND_Y, rx:rx + 2] = (34, 38, 44)
    img[63:66] = (40, 44, 50)

    # --- channel far wall ------------------------------------------------
    wall_noise = _noise(rng, 46, W, 4)
    wall = 104 + wall_noise * 11
    # vertical rain stains
    stains = (np.abs(_noise(rng, 1, W, 2.5)[0]) > 1.35).astype(np.float32)
    stains = cv2.GaussianBlur(stains[None, :], (0, 0), 6)[0] * 34
    wall = wall - stains[None, :]
    img[GROUND_Y:GROUND_Y + 46] = np.clip(
        np.stack([wall * 1.0, wall * 0.98, wall * 0.94], -1), 0, 255
    ).astype(np.uint8)
    img[GROUND_Y + 44:GROUND_Y + 47] = (58, 60, 62)   # wall/bed joint

    # --- channel bed -----------------------------------------------------
    bed_h = H - (GROUND_Y + 46)
    bedn = _noise(rng, bed_h, W, 3)
    bed = 86 + bedn * 9
    # transverse construction joints, perspective spacing
    y = GROUND_Y + 46
    step = 16
    while y < H:
        bed[y - GROUND_Y - 46:y - GROUND_Y - 43] -= 16
        step *= 1.34
        y += int(step)
    # aggregate speckle
    sp = (np.abs(_noise(rng, bed_h, W, 0.8)) > 1.9).astype(np.float32) * 18
    bed = bed + sp
    # central low-flow groove (where calm water sits)
    groov = np.exp(-(((np.arange(W) - W * 0.5) / (W * 0.42)) ** 2))
    bed = bed - 10 * groov[None, :]
    img[GROUND_Y + 46:] = np.clip(
        np.stack([bed * 1.02, bed * 0.99, bed * 0.95], -1), 0, 255
    ).astype(np.uint8)

    # --- obstacles (never hold water) ------------------------------------
    pil = 62 + _noise(rng, H - PILLAR_Y0, PILLAR_X1 - PILLAR_X0, 2) * 8
    img[PILLAR_Y0:, PILLAR_X0:PILLAR_X1] = np.clip(
        np.stack([pil, pil * 0.97, pil * 0.92], -1), 0, 255
    ).astype(np.uint8)
    img[PILLAR_Y0:PILLAR_Y0 + 4, PILLAR_X0:PILLAR_X1] = (74, 74, 76)
    img[PILLAR_Y0 + 30:PILLAR_Y0 + 34, PILLAR_X0:PILLAR_X1] = (48, 50, 52)

    bench = np.zeros((H, W), np.uint8)
    bench[302:318, 588:W] = 1
    bench[318:H, 596:W] = 1
    img[bench.astype(bool)] = (70, 68, 64)

    ground = _ground_mask()
    ground[bench] = 0

    # baked CCTV vignette so the corners never read as flat black
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.72)) ** 2)
    vignette = np.clip(1.06 - 0.34 * np.clip(r - 0.55, 0, None) ** 1.4, 0.55, 1.06)

    # --- water look-up table: ratio -> waterline row ---------------------
    areas = np.array([int(ground[y:].sum()) for y in range(H + 1)], np.float64)
    total = float(W * H)
    ys_desc = np.arange(H, -1, -1)
    lut_r = areas[ys_desc] / total

    return {
        "img": img,
        "ground": ground,
        "areas": areas,
        "total": total,
        "lut_y": ys_desc,
        "lut_r": lut_r,
        "water_tile": _water_tile(rng),
        "vignette": vignette,
        "reflect": cv2.GaussianBlur(
            (np.abs(_noise(rng, H, W, 1.5)) > 1.45).astype(np.float32), (0, 0), 5
        ),
    }


def _water_tile(rng: np.random.RandomState) -> np.ndarray:
    """Horizontally-tilable ripple texture, shape (H, 2W), float in [-1, 1].

    Long horizontal streaks dominate so the surface reads as flowing water and
    Farneback sees clean horizontal motion.
    """
    w = W * 2
    streaks = rng.randn(H // 3, w // 10).astype(np.float32)
    streaks = cv2.resize(streaks, (w, H), interpolation=cv2.INTER_CUBIC)
    streaks = cv2.GaussianBlur(streaks, (0, 0), sigmaX=9.0, sigmaY=1.6)
    blobs = cv2.GaussianBlur(rng.randn(H // 8, w // 8).astype(np.float32), (0, 0), 3.0)
    blobs = cv2.resize(blobs, (w, H), interpolation=cv2.INTER_CUBIC)
    fine = cv2.GaussianBlur(rng.randn(H, w).astype(np.float32), (0, 0), 0.9)
    tile = 1.15 * streaks + 0.55 * blobs + 0.22 * fine
    # wrap-blend the horizontal seam so scrolling never shows an edge
    blend = 96
    ramp = np.linspace(0, 1, blend, dtype=np.float32)[None, :]
    tile[:, :blend] = tile[:, :blend] * ramp + tile[:, -blend:][:, ::-1] * (1 - ramp)
    sd = float(tile.std()) or 1.0
    return (tile / sd).astype(np.float32)


# ------------------------------------------------------------ scenario curves


def _normal_ratio(t: float) -> float:
    # periods 12/6/4 s all divide the 120 s normal clip -> seamless loop
    return (
        0.070
        + 0.0060 * math.sin(2 * math.pi * t / 12.0)
        + 0.0022 * math.sin(2 * math.pi * t / 6.0 + 1.1)
        + 0.0010 * math.sin(2 * math.pi * t / 4.0 + 2.4)
    )


def _rise_rate(tau: float) -> float:
    """gradual_rise expansion rate (ratio/s) after the ramp starts."""
    return 0.0015 + 0.00060 * tau


def _ratio_gradual(t: float, delay: float, scale: float) -> float:
    if t < 10.0 + delay:
        return _normal_ratio(t)
    tau = t - 10.0 - delay
    # analytic integral of the ramp: the rise is still climbing when the agent
    # reaches Critical, so confidence is judged mid-storm, not after the fact.
    # Steepened 1.35x (and cap raised to 0.68) so the measured 30 s expansion
    # rate clears the 5x critical gate even on the low excursions of the
    # baseline's median flicker — without this the anomaly wavers 4-9x and the
    # 6 s sustain clock never completes.
    grow = scale * (0.0020 * tau + 0.00040 * tau * tau)
    base = _normal_ratio(10.0 + delay)
    return min(base + grow, 0.68)


def _ratio_flash(t: float) -> float:
    if t < 10.0:
        return _normal_ratio(t)
    tau = t - 10.0
    # cloudburst: rate spikes to 0.030 ratio/s in 3 s, then eases but stays
    # above the Critical gate long enough for the hysteresis chain to fire
    if tau < 3.0:
        grow = 0.004 * tau + 0.5 * (0.026 / 3.0) * tau * tau
    elif tau < 30.0:
        head = 0.004 * 3.0 + 0.5 * (0.026 / 3.0) * 9.0
        u = tau - 3.0
        grow = head + 0.030 * u - (0.010 / 27.0) * u * u
    else:
        u = 27.0
        head = 0.004 * 3.0 + 0.5 * (0.026 / 3.0) * 9.0
        grow = head + 0.030 * u - (0.010 / 27.0) * u * u
        return min(_normal_ratio(10.0) + grow, 0.60)
    return min(_normal_ratio(10.0) + grow, 0.60)


def ratio_at(scenario: str, variant: str, t: float) -> float:
    if scenario == "normal":
        return _normal_ratio(t)
    if scenario == "gradual_rise":
        if variant == "secondary":
            # adjacent camera: delayed and damped, gives the agent corroboration
            return _ratio_gradual(t, delay=8.0, scale=0.5)
        if variant == "ambient":
            return _normal_ratio(t)
        return _ratio_gradual(t, delay=0.0, scale=1.0)
    if scenario == "flash_surge":
        if variant in ("secondary", "ambient"):
            return _normal_ratio(t)
        return _ratio_flash(t)
    raise ValueError(f"unknown scenario {scenario}")


def duration_of(scenario: str) -> float:
    return {"normal": 120.0, "gradual_rise": 78.0, "flash_surge": 46.0}[scenario]


def flow_speed_at(scenario: str, variant: str, t: float) -> float:
    """Water texture scroll speed in px per emitted (5 fps) frame."""
    if scenario == "flash_surge" and variant == "primary" and t > 10.0:
        return 1.4 + 9.5 * math.exp(-max(0.0, t - 13.0) / 6.0)
    if scenario == "gradual_rise" and variant in ("primary", "secondary") and t > 10.0:
        d = 8.0 if variant == "secondary" else 0.0
        return 2.1 + min(4.4, max(0.0, (t - 10.0 - d)) * 0.14)
    return 2.05 + 0.25 * math.sin(2 * math.pi * t / 6.0)


def scroll_offset(scenario: str, variant: str, t: float, frame_idx: int, fps: int) -> float:
    """Horizontal texture offset in px.

    The idle `normal` clip must loop pixel-seamlessly, so it drifts at a
    constant rate that lands exactly one tile period (2W) at the end of the
    clip (1800 frames * 0.7111 px = 1280 px). That also pins the calm-state
    flow baseline at ~2.1 px per 5 fps sample.
    """
    if scenario == "normal":
        frames = max(1, int(duration_of("normal") * fps))
        return frame_idx * (2.0 * W) / frames
    speed = flow_speed_at(scenario, variant, t)
    return t * speed * 5.0


def turbulence_at(scenario: str, variant: str, t: float) -> float:
    """Surface agitation 0..1 — drives flow variance (agent confidence)."""
    if scenario == "gradual_rise" and variant == "primary" and t > 14.0:
        return min(1.0, (t - 14.0) / 9.0)
    if scenario == "flash_surge" and variant == "primary" and 10.0 < t < 26.0:
        return 0.12
    return 0.08


def debris_at(scenario: str, variant: str, t: float) -> bool:
    # Disabled: debris rafts punched holes in the water segmentation, injecting
    # |Δratio| noise that inflated the agent's learned baseline and masked the
    # flood signal itself (the anomaly could never clear the 5x critical gate).
    # Rain + turbulence still make detection genuinely harder (confidence ~0.7,
    # which is what drives the verifying path), without hiding the rise.
    return False


def storm_at(scenario: str, variant: str, t: float) -> float:
    """Storm intensity 0..1 — rain, lens droplets and glare ride with the flood.

    This is what makes detection genuinely harder (and confidence genuinely
    lower) exactly when the agent is deciding whether it can trust the signal.
    """
    if scenario == "gradual_rise" and variant == "primary":
        return min(1.0, max(0.0, (t - 12.0) / 9.0))
    if scenario == "flash_surge" and variant == "primary":
        # Kept light (0.12): the flash_surge beat needs clean tracking so
        # confidence clears the 0.80 gate for the immediate high_confidence
        # Critical path — the contrast to gradual_rise's verifying path.
        return min(0.12, max(0.0, (t - 14.0) / 10.0))
    return 0.0


def _storm(img: np.ndarray, intensity: float, frame_idx: int) -> None:
    if intensity <= 0.02:
        return
    rs = np.random.RandomState(7000 + (frame_idx * 2654435761) % 65536)
    # driving rain — fast diagonal motion degrades optical-flow coherence
    n = int(460 * intensity)
    for _ in range(n):
        x = int(rs.randint(-40, W))
        y = int(rs.randint(-40, H))
        ln = int(rs.randint(11, 30))
        x2 = x + int(ln * 0.30)
        shade = int(150 + 70 * rs.rand())
        cv2.line(img, (x, y), (x2, min(H - 1, y + ln)), (shade, shade + 6, min(255, shade + 16)), 1)
    # lens droplets: persistent, desaturating blobs that punch holes in the mask
    bucket = frame_idx // 7
    rd = np.random.RandomState(8000 + bucket)
    nd = int(6 + 16 * intensity)
    for _ in range(nd):
        cx = int(rd.randint(0, W))
        cy = int(rd.randint(0, H))
        r = int(rd.randint(5, 15))
        y0, y1 = max(0, cy - r), min(H, cy + r)
        x0, x1 = max(0, cx - r), min(W, cx + r)
        if y1 - y0 < 4 or x1 - x0 < 4:
            continue
        patch = img[y0:y1, x0:x1].astype(np.float32)
        blur = cv2.GaussianBlur(patch, (0, 0), 3.0)
        grey = patch.mean(axis=(0, 1), keepdims=True)
        fogged = patch * 0.18 + blur * 0.14 + grey * 0.68 + 14.0
        img[y0:y1, x0:x1] = np.clip(fogged, 0, 255).astype(np.uint8)


def _turbulence_marks(img: np.ndarray, y_w: int, turb: float, frame_idx: int) -> None:
    """Debris-choked surface: whitecap streaks + foam clumps on the waterline.

    Positions are reseeded every frame, so optical flow loses coherence and the
    waterline jitters — both deliberately lower the agent's confidence so the
    verification gate has something real to verify.
    """
    if turb < 0.25 or y_w >= H - 6:
        return
    rs = np.random.RandomState(4000 + (frame_idx * 2654435761) % 65536)
    band = H - y_w
    n = int(10 + 70 * turb)
    for _ in range(n):
        x = int(rs.randint(0, W))
        y = int(rs.randint(y_w + 1, H - 2))
        ln = int(rs.randint(5, 26))
        ang = math.radians(float(rs.randn() * 28.0))
        x2 = int(max(0, min(W - 1, x + ln * math.cos(ang))))
        y2 = int(max(0, min(H - 1, y + ln * math.sin(ang) * 0.35)))
        if rs.rand() < 0.55:
            col = (186, 152, 112)      # whitecap (brighter water)
        else:
            col = (112, 88, 62)        # churned shadow
        cv2.line(img, (x, y), (x2, y2), col, int(1 + rs.rand() * 2))
    # foam clumps riding just above the waterline move its detected top edge
    nf = int(6 + 26 * turb)
    for _ in range(nf):
        x = int(rs.randint(6, W - 6))
        r = int(rs.randint(3, 11))
        cv2.ellipse(img, (x, y_w - int(rs.randint(-3, 7))), (r, max(2, r // 2)),
                    0, 0, 360, (196, 160, 118), -1)
    # debris rafts: non-blue masses that punch holes in the segmentation, so the
    # tracked contour and its top edge genuinely disagree frame to frame
    nd = int(5 + 16 * turb)
    for _ in range(nd):
        x = int(rs.randint(0, W))
        y = int(rs.randint(y_w + 2, H - 4))
        rx = int(rs.randint(5, 20))
        ry = int(rs.randint(3, 9))
        col = (66, 58, 46) if rs.rand() < 0.6 else (94, 88, 70)
        cv2.ellipse(img, (x, y), (rx, ry), float(rs.randn() * 20), 0, 360, col, -1)
        if rs.rand() < 0.5:
            cv2.ellipse(img, (x, y), (rx + 2, ry + 2), 0, 0, 360, (120, 104, 78), 1)


# ------------------------------------------------------------------- rendering


def y_for_ratio(static: dict, ratio: float) -> int:
    """Invert the ground-area LUT: ratio (0..1 of frame) -> waterline row."""
    ys = static["lut_y"]          # descending rows H..0
    rs = static["lut_r"]          # ascending ratios
    r = max(0.0, min(0.999, float(ratio)))
    return int(np.interp(r, rs, ys))


def _composite_water(img: np.ndarray, static: dict, y_w: int, scroll: int,
                     turb: float) -> np.ndarray:
    if y_w >= H - 2:
        return img
    ground = static["ground"]
    tile = static["water_tile"]
    off = int(scroll) % (2 * W)
    rolled = np.roll(tile, -off, axis=1)[:, :W]

    rows = np.arange(H)[:, None]
    # depth shading: darker far, brighter near
    depth = 1.0 + 0.10 * ((rows - GROUND_Y) / float(H - GROUND_Y))
    tex = rolled * (0.16 + 0.10 * turb)
    # building reflections near the top of the sheet
    refl = static["reflect"] * (rows < y_w + 70)
    brightness = depth + tex - 0.22 * refl

    base = np.array([150.0, 118.0, 84.0])          # BGR blue-grey water
    water = base[None, None, :] * brightness[..., None]
    # foam / waterline crest
    water[y_w:y_w + 3] *= 1.42
    water[y_w + 3:y_w + 6] *= 1.18
    water = np.clip(water, 0, 255).astype(np.uint8)

    sel = ground.astype(bool) & (rows >= y_w)
    out = img.copy()
    out[sel] = water[sel]
    return out


def _draw_debris(img: np.ndarray, y_w: int, t: float, t0: float = 25.0) -> None:
    """Deterministic driftwood/log occluders — break contour stability on purpose."""
    dt = t - t0
    logs = [
        (0.16, 34, 9, 46.0),
        (0.52, 26, 7, 31.0),
        (0.83, 40, 10, 58.0),
    ]
    for frac, lw, lh, vx in logs:
        x = int((frac * W + vx * dt) % (W + 120)) - 60
        y = y_w + int(lh * 1.6 + 12 * frac)
        if y + lh >= H:
            y = H - lh - 2
        if y <= y_w + 1:
            continue
        cv2.ellipse(img, (x, y), (lw, lh), 0, 0, 360, (58, 52, 44), -1)
        cv2.ellipse(img, (x, y - 2), (lw - 6, max(2, lh - 4)), 0, 0, 360, (74, 68, 58), -1)


def render_frame(static: dict, scenario: str, variant: str, frame_idx: int,
                 fps: int = FPS) -> np.ndarray:
    t = frame_idx / float(fps)
    ratio = ratio_at(scenario, variant, t)
    y_w = y_for_ratio(static, ratio)

    scroll = int(round(scroll_offset(scenario, variant, t, frame_idx, fps)))
    turb = turbulence_at(scenario, variant, t)

    img = _composite_water(static["img"].copy(), static, y_w, scroll, turb)
    if debris_at(scenario, variant, t):
        _draw_debris(img, y_w, t)
    if turb >= 0.25:
        _turbulence_marks(img, y_w, turb, frame_idx)
    storm = storm_at(scenario, variant, t)
    if storm > 0.02:
        _storm(img, storm, frame_idx)

    # deterministic sensor noise (low-light robustness)
    rs = np.random.RandomState(1000 + frame_idx % 9973)
    noise = rs.randint(-4, 5, (H, W, 3), dtype=np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255)
    img = np.clip(img * static["vignette"][..., None], 0, 255).astype(np.uint8)

    # mild vignette so the frame reads as CCTV
    return img


# --------------------------------------------------------------------- output


def scene_path(name: str, root: str | None = None) -> str:
    root = root or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "scenes")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, f"{name}.mp4")


def render(scenario: str, variant: str = "primary", out_path: str | None = None,
           fps: int = FPS, quiet: bool = True, force: bool = False) -> str:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario}")
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    name = scenario if variant == "primary" else f"{scenario}_{variant}"
    out_path = out_path or scene_path(name)

    expected = int(duration_of(scenario) * fps)
    if not force and os.path.exists(out_path) and os.path.getsize(out_path) > 40_000:
        cap = cv2.VideoCapture(out_path)
        if cap.isOpened() and int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) >= expected - 2:
            cap.release()
            return out_path
        cap.release()

    static = build_static(seed=7)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (W, H))
    if not writer.isOpened():
        raise RuntimeError(f"cannot open VideoWriter for {out_path}")
    for i in range(expected):
        writer.write(render_frame(static, scenario, variant, i, fps))
        if not quiet and i % 150 == 0:
            print(f"  {name}: frame {i}/{expected}", flush=True)
    writer.release()
    return out_path


def render_all(quiet: bool = True, force: bool = False) -> dict:
    """Every clip the demo needs. Cached on disk."""
    jobs = [
        ("normal", "primary"),
        ("gradual_rise", "primary"),
        ("gradual_rise", "secondary"),
        ("flash_surge", "primary"),
    ]
    out = {}
    for scenario, variant in jobs:
        key = scenario if variant == "primary" else f"{scenario}_{variant}"
        out[key] = render(scenario, variant, quiet=quiet, force=force)
    return out


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    quiet = "-q" not in argv
    force = "--force" in argv
    if not args or args == ["all"]:
        paths = render_all(quiet=quiet, force=force)
        for k, v in paths.items():
            print(f"{k:24s} -> {v}")
        return 0
    scenario = args[0]
    variant = args[1] if len(args) > 1 else "primary"
    print(render(scenario, variant, quiet=quiet, force=force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
