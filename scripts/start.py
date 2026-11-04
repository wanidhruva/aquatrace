#!/usr/bin/env python3
"""AquaTrace — one command boots everything.

    python3 scripts/start.py            # backend + operator console + site
    python3 scripts/start.py --no-site  # backend + console only
    python3 scripts/start.py --demo gradual_rise   # then start a scenario

Ports:  API 8000 · console 5173 · site 5174
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "http://127.0.0.1:8000"
CONSOLE = "http://localhost:5173"
SITE = "http://localhost:5174"

def banner(site: str = SITE, disabled: bool = False) -> str:
    inner = 57  # content width between the box rules
    lines = [
        "  ┌" + "─" * 59 + "┐",
        "  │  AquaTrace  ·  visual intelligence for flood detection    │",
        "  ├" + "─" * 59 + "┤",
    ]
    for label, url in (("operator console", CONSOLE),
                       ("marketing site", "(disabled)" if disabled else site),
                       ("api + websocket", API),
                       ("api docs", f"{API}/docs")):
        rows = f"{label:<20}{url}"
        lines.append(f"  │  {rows:<{inner}}│")
    lines.append("  └" + "─" * 59 + "┘")
    return "\n" + "\n".join(lines) + "\n"


def log(msg: str) -> None:
    print(f"  · {msg}", flush=True)


def free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_up(url: str, proc: subprocess.Popen, name: str, timeout: float = 45.0) -> str:
    """Poll `url` until it answers, the child `proc` dies, or `timeout` expires.

    Returns one of: "ok", "timeout", "died (exit N)". Checking the child on
    every poll means a dead service fails fast instead of burning the timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return f"died (exit {proc.returncode})"
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return "ok"
        except Exception:
            time.sleep(0.4)
    return "timeout"


def load_aws_env() -> None:
    """Load backend/data/aws.env (written by scripts/aws_setup.py) so the API
    inherits the AWS configuration without manual exports. Pre-existing
    environment variables win."""
    env_file = os.path.join(ROOT, "backend", "data", "aws.env")
    if not os.path.isfile(env_file):
        return
    loaded = 0
    with open(env_file) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key and key not in os.environ:
                os.environ[key.strip()] = value.strip()
                loaded += 1
    if loaded:
        log(f"loaded {loaded} AWS setting(s) from backend/data/aws.env")


def ensure_scenes() -> None:
    """Render the synthetic clips on first run (~60 s of CPU)."""
    scenes = os.path.join(ROOT, "assets", "scenes")
    needed = ["normal.mp4", "gradual_rise.mp4", "gradual_rise_secondary.mp4", "flash_surge.mp4"]
    missing = [f for f in needed if not os.path.exists(os.path.join(scenes, f)) or
               os.path.getsize(os.path.join(scenes, f)) < 40_000]
    if not missing:
        return
    log(f"rendering {len(missing)} synthetic scene(s) — first run only …")
    proc = subprocess.run([sys.executable, "-c",
                           "import sys; sys.path.insert(0, sys.argv[1]);"
                           "from vision.synth import render_all; render_all(quiet=True)",
                           str(ROOT)],
                          cwd=ROOT)
    if proc.returncode != 0:
        log("scene rendering failed — run `python3 -m vision.synth all` to retry")


def ensure_npm(name: str, do_install: bool = True) -> bool:
    if not shutil.which("npm"):
        log(f"npm not found — skipping {name}")
        return False
    pkg = os.path.join(ROOT, name, "node_modules")
    if not os.path.isdir(pkg):
        if not do_install:
            log(f"{name}: dependencies not installed — run `cd {name} && npm install`")
            return False
        log(f"{name}: installing dependencies …")
        rc = subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                            cwd=os.path.join(ROOT, name)).returncode
        if rc != 0:
            log(f"{name}: npm install failed — skipping")
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Boot AquaTrace")
    ap.add_argument("--no-site", action="store_true", help="do not start the marketing site")
    ap.add_argument("--demo", choices=["normal", "gradual_rise", "flash_surge"],
                    help="scenario to start once the API is up")
    ap.add_argument("--no-install", action="store_true", help="never run npm install")
    args = ap.parse_args()

    kids: list[subprocess.Popen] = []

    def shutdown(*_):
        log("shutting down …")
        for p in kids:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass
        time.sleep(0.8)
        for p in kids:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    for port, what in ((8000, "API"), (5173, "console"), (5174, "site")):
        if not free(port):
            log(f"port {port} is already in use ({what}) — stop the other process first")
            return 1

    ensure_scenes()

    load_aws_env()
    log("starting API on :8000")
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
        cwd=ROOT, start_new_session=True)
    kids.append(api_proc)

    api_state = wait_up(f"{API}/health", api_proc, "API", 45)
    if api_state != "ok":
        if api_state.startswith("died"):
            log(f"API process {api_state} — see the uvicorn output above")
        else:
            log("API did not come up within 45 s — see the output above")
        shutdown()
    log("API healthy")

    if ensure_npm("frontend", do_install=not args.no_install):
        log("starting operator console on :5173")
        console_proc = subprocess.Popen(["npm", "run", "dev"],
                                        cwd=os.path.join(ROOT, "frontend"),
                                        start_new_session=True)
        kids.append(console_proc)
        console_state = wait_up(f"{CONSOLE}/", console_proc, "console", 45)
        if console_state == "ok":
            log("console ready")
        elif console_state.startswith("died"):
            log(f"console npm process {console_state} — continuing without console")
        else:
            log("console did not answer within 45 s — continuing without it")

    if not args.no_site and ensure_npm("site", do_install=not args.no_install):
        log("starting marketing site on :5174")
        site_proc = subprocess.Popen(["npm", "run", "dev"],
                                     cwd=os.path.join(ROOT, "site"),
                                     start_new_session=True)
        kids.append(site_proc)
        site_state = wait_up(f"{SITE}/", site_proc, "site", 45)
        if site_state == "ok":
            log("site ready")
        elif site_state.startswith("died"):
            log(f"site npm process {site_state} — continuing without site")
        else:
            log("site did not answer within 45 s — continuing without it")

    if args.demo:
        req = urllib.request.Request(f"{API}/simulate/{args.demo}", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body = json.loads(r.read())
            log(f"scenario {body.get('scenario')} running")
        except Exception as exc:
            log(f"scenario start failed: {exc}")

    print(banner(disabled=args.no_site), flush=True)
    log("press Ctrl-C to stop everything")

    while True:
        time.sleep(1.0)
        for p in kids:
            if p.poll() is not None:
                log(f"a service exited with code {p.returncode} — stopping")
                shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
