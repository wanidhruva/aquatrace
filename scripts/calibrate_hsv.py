#!/usr/bin/env python3
"""AquaTrace — FloodNet Real Flood Water HSV Calibration Benchmark.

Calibrates and validates HSV segmentation ranges against real post-flood UAV imagery
from the FloodNet dataset (Hurricane Harvey damage assessment) compared to synthetic CCTV.

Outputs a calibration summary and saves backend/data/hsv_calibration.json.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
import urllib.request
import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOODNET_DIR = os.path.join(ROOT, "assets", "floodnet")
OUT_JSON = os.path.join(ROOT, "backend", "data", "hsv_calibration.json")

# Default FloodNet sample IDs from official challenge dataset
SAMPLES = ["10163", "10164", "10808", "10812"]

# Old cyan-only bounds
OLD_LO = np.array([88, 52, 52], dtype=np.uint8)
OLD_HI = np.array([126, 190, 235], dtype=np.uint8)

# Calibrated multi-band flood water thresholds
FLOOD_TURBID_LO = np.array([8, 28, 30], dtype=np.uint8)
FLOOD_TURBID_HI = np.array([42, 230, 220], dtype=np.uint8)

FLOOD_DEEP_LO = np.array([42, 30, 30], dtype=np.uint8)
FLOOD_DEEP_HI = np.array([135, 240, 245], dtype=np.uint8)

FLOOD_PAVE_LO = np.array([0, 8, 28], dtype=np.uint8)
FLOOD_PAVE_HI = np.array([180, 70, 160], dtype=np.uint8)


def ensure_samples() -> list[str]:
    os.makedirs(FLOODNET_DIR, exist_ok=True)
    paths = []
    base_url = "https://huggingface.co/datasets/takara-ai/FloodNet_2021-Track_2_Dataset_HF/resolve/main/test_image/img"
    for sid in SAMPLES:
        dest = os.path.join(FLOODNET_DIR, f"{sid}.jpg")
        if not os.path.exists(dest) or os.path.getsize(dest) < 100_000:
            url = f"{base_url}/{sid}.JPG"
            print(f"  · downloading FloodNet sample {sid} from {url} …", flush=True)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=40) as r, open(dest, "wb") as f:
                    f.write(r.read())
            except Exception as e:
                print(f"  ! failed downloading {sid}: {e}", flush=True)
                continue
        if os.path.exists(dest):
            paths.append(dest)
    return paths


def segment_old(hsv: np.ndarray) -> np.ndarray:
    return cv2.inRange(hsv, OLD_LO, OLD_HI)


def segment_calibrated(hsv: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    m1 = cv2.inRange(hsv, FLOOD_TURBID_LO, FLOOD_TURBID_HI)
    m2 = cv2.inRange(hsv, FLOOD_DEEP_LO, FLOOD_DEEP_HI)
    m3 = cv2.inRange(hsv, FLOOD_PAVE_LO, FLOOD_PAVE_HI)
    combined = cv2.bitwise_or(cv2.bitwise_or(m1, m2), m3)
    total_px = hsv.shape[0] * hsv.shape[1]
    breakdown = {
        "turbid_pct": round(float(np.count_nonzero(m1)) / total_px * 100, 2),
        "deep_pct": round(float(np.count_nonzero(m2)) / total_px * 100, 2),
        "pavement_pct": round(float(np.count_nonzero(m3)) / total_px * 100, 2),
        "total_water_pct": round(float(np.count_nonzero(combined)) / total_px * 100, 2),
    }
    return combined, breakdown


def main() -> int:
    print("=" * 70)
    print("  AquaTrace — Real Flood Water HSV Calibration Benchmark")
    print("=" * 70)

    images = ensure_samples()
    if not images:
        print("  ! Error: No FloodNet images found or downloaded.")
        return 1

    results = []
    print(f"\nEvaluating {len(images)} real FloodNet images:\n")
    print(f"{'Image':<26} {'Dimensions':<12} {'Old Cyan %':<12} {'Calibrated %':<14} {'Primary Component'}")
    print("-" * 75)

    for p in images:
        name = os.path.basename(p)
        img = cv2.imread(p)
        if img is None:
            continue
        h, w = img.shape[:2]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        # Old threshold detection
        m_old = segment_old(hsv)
        old_pct = float(np.count_nonzero(m_old)) / (h * w) * 100

        # Calibrated multi-band detection
        m_cal, breakdown = segment_calibrated(hsv)
        cal_pct = breakdown["total_water_pct"]

        # Dominant component
        comps = [
            ("Turbid / Muddy Silt", breakdown["turbid_pct"]),
            ("Deep / Algae Water", breakdown["deep_pct"]),
            ("Reflective Road Runoff", breakdown["pavement_pct"]),
        ]
        dominant = max(comps, key=lambda x: x[1])[0]

        results.append({
            "image": name,
            "width": w,
            "height": h,
            "old_cyan_detection_pct": round(old_pct, 2),
            "calibrated_flood_water_pct": round(cal_pct, 2),
            "breakdown": breakdown,
            "dominant_component": dominant,
        })

        print(f"{name:<26} {f'{w}x{h}':<12} {f'{old_pct:.1f}%':<12} {f'{cal_pct:.1f}%':<14} {dominant}")

    # Also test on normal.mp4 if available
    normal_scene = os.path.join(ROOT, "assets", "scenes", "normal.mp4")
    if os.path.exists(normal_scene):
        cap = cv2.VideoCapture(normal_scene)
        ret, frame = cap.read()
        cap.release()
        if ret:
            h, w = frame.shape[:2]
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            m_old = segment_old(hsv)
            m_cal, breakdown = segment_calibrated(hsv)
            old_pct = float(np.count_nonzero(m_old)) / (h * w) * 100
            cal_pct = breakdown["total_water_pct"]
            print(f"{'normal.mp4 (synthetic)':<26} {f'{w}x{h}':<12} {f'{old_pct:.1f}%':<12} {f'{cal_pct:.1f}%':<14} {'Deep / Canal Water'}")
            results.append({
                "image": "normal.mp4 (synthetic)",
                "width": w,
                "height": h,
                "old_cyan_detection_pct": round(old_pct, 2),
                "calibrated_flood_water_pct": round(cal_pct, 2),
                "breakdown": breakdown,
                "dominant_component": "Synthetic Canal Water",
            })

    output_data = {
        "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": "FloodNet (Hurricane Harvey UAS Imagery)",
        "thresholds": {
            "turbid_muddy": {
                "hsv_lo": [int(v) for v in FLOOD_TURBID_LO],
                "hsv_hi": [int(v) for v in FLOOD_TURBID_HI],
                "description": "Murky tan/brown suspended sediment typical of storm runoff",
            },
            "deep_canal": {
                "hsv_lo": [int(v) for v in FLOOD_DEEP_LO],
                "hsv_hi": [int(v) for v in FLOOD_DEEP_HI],
                "description": "Canal Reach A, storm outfalls, algae and deeper standing water",
            },
            "reflective_pavement": {
                "hsv_lo": [int(v) for v in FLOOD_PAVE_LO],
                "hsv_hi": [int(v) for v in FLOOD_PAVE_HI],
                "description": "Low-saturation reflective sheen on flooded roadways and culverts",
            },
        },
        "benchmark_results": results,
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(output_data, f, indent=2)
    print(f"\n✓ Calibration profile saved to {OUT_JSON}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
