"""Real AWS integrations for AquaTrace.

    S3   — evidence vault: alert bundles (annotated frame + alert JSON)
    SNS  — critical-alert delivery to subscribed operators
    CloudWatch — telemetry metrics (namespace "AquaTrace")

Every public function is best-effort and NEVER raises: when AWS is
unconfigured or unreachable, the system keeps running on local storage and
``status()`` reports the degradation for the dashboard. The hackathon demo
must survive dead venue Wi-Fi.

Configuration (environment):
    AQUATRACE_AWS_ENABLED    "1" (default) / "0" — master kill switch
    AQUATRACE_AWS_PROFILE    CLI profile name (default "divesh")
    AQUATRACE_AWS_REGION     default "ap-south-1"
    AQUATRACE_S3_BUCKET      evidence bucket (empty = S3 disabled)
    AQUATRACE_SNS_TOPIC_ARN  critical-alert topic (empty = SNS disabled)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger("aquatrace.aws")

NAMESPACE = "AquaTrace"
METRIC_THROTTLE_S = 20.0
STATUS_TTL_S = 60.0
PRESIGN_TTL_S = 7 * 24 * 3600  # evidence links valid 7 days


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class AWSSettings:
    def __init__(self) -> None:
        self.enabled = _env("AQUATRACE_AWS_ENABLED", "1") == "1"
        self.profile = _env("AQUATRACE_AWS_PROFILE", "divesh")
        self.region = _env("AQUATRACE_AWS_REGION", "ap-south-1")
        self.bucket = _env("AQUATRACE_S3_BUCKET", "")
        self.topic_arn = _env("AQUATRACE_SNS_TOPIC_ARN", "")

    @property
    def s3_on(self) -> bool:
        return self.enabled and bool(self.bucket)

    @property
    def sns_on(self) -> bool:
        return self.enabled and bool(self.topic_arn)

    @property
    def metrics_on(self) -> bool:
        # Metrics need nothing but credentials + region; they ride along
        # whenever the integration is enabled.
        return self.enabled


settings = AWSSettings()

_clients: dict[str, Any] = {}
_clients_lock = threading.Lock()


def _boto3():
    try:
        import boto3  # lazy: backend must import without boto3 installed
        return boto3
    except Exception:
        return None


def _client(service: str) -> Optional[Any]:
    """Cached low-level client with short timeouts; None when unusable."""
    b3 = _boto3()
    if b3 is None or not settings.enabled:
        return None
    with _clients_lock:
        if service in _clients:
            return _clients[service]
        try:
            from botocore.config import Config
            cfg = Config(connect_timeout=3, read_timeout=5,
                         retries={"max_attempts": 1})
            session_kwargs: dict[str, str] = {"region_name": settings.region}
            if settings.profile:
                session_kwargs["profile_name"] = settings.profile
            session = b3.Session(**session_kwargs)
            client_kwargs: dict[str, Any] = {"config": cfg}
            if service == "s3":
                # Sign against the regional endpoint. The global
                # bucket.s3.amazonaws.com host answers 307 to
                # bucket.s3.<region>.amazonaws.com, and the redirect carries
                # the signature into a different Host header -> 403 on every
                # dashboard evidence link.
                client_kwargs["endpoint_url"] = f"https://s3.{settings.region}.amazonaws.com"
                client_kwargs["config"] = Config(
                    connect_timeout=3, read_timeout=5,
                    retries={"max_attempts": 1},
                    s3={"addressing_style": "virtual"})
            client = session.client(service, **client_kwargs)
            _clients[service] = client
            return client
        except Exception:
            log.warning("aws %s client unavailable", service, exc_info=True)
            return None


def drop_clients() -> None:
    """Forget cached clients (used by tests to force re-resolution)."""
    with _clients_lock:
        _clients.clear()


# ------------------------------------------------------------------- S3


def s3_put(key: str, body: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
    """Upload an evidence object; return a presigned GET URL, or None."""
    if not settings.s3_on:
        return None
    s3 = _client("s3")
    if s3 is None:
        return None
    try:
        s3.put_object(Bucket=settings.bucket, Key=key, Body=body,
                      ContentType=content_type)
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.bucket, "Key": key},
            ExpiresIn=PRESIGN_TTL_S,
        )
        log.info("s3 put s3://%s/%s", settings.bucket, key)
        return url
    except Exception:
        log.warning("s3 put failed for %s", key, exc_info=True)
        return None


# ------------------------------------------------------------------- SNS


def sns_publish(subject: str, message: str) -> Optional[str]:
    """Publish to the critical-alert topic; return MessageId, or None."""
    if not settings.sns_on:
        return None
    sns = _client("sns")
    if sns is None:
        return None
    try:
        resp = sns.publish(TopicArn=settings.topic_arn,
                           Subject=subject[:100], Message=message)
        mid = resp.get("MessageId")
        log.info("sns published %s", mid)
        return mid
    except Exception:
        log.warning("sns publish failed", exc_info=True)
        return None


def format_critical_message(alert: dict[str, Any], camera_name: str,
                            evidence_url: Optional[str]) -> tuple[str, str]:
    sev = str(alert.get("severity", "critical")).upper()
    cid = alert.get("camera_id", "?")
    subject = f"[AquaTrace {sev}] {camera_name} ({cid})"
    lines = [
        f"AquaTrace {sev} alert {alert.get('alert_id')}",
        f"Camera: {camera_name} ({cid})",
        f"State: {alert.get('state')}  Confidence: {alert.get('agent_confidence')}",
        f"Started: {alert.get('started_at')}",
        f"Reasoning: {alert.get('reasoning')}",
    ]
    if evidence_url:
        lines.append(f"Evidence: {evidence_url}")
    lines.append("Reply in the operator console to acknowledge.")
    return subject, "\n".join(lines)


# -------------------------------------------------------------- CloudWatch


_metric_last: dict[str, float] = {}
_metric_lock = threading.Lock()


def put_metrics(camera_id: str, fields: dict[str, float]) -> bool:
    """Throttled put_metric_data; True if sent (or throttled), False if down."""
    if not settings.metrics_on:
        return False
    now = time.time()
    with _metric_lock:
        if now - _metric_last.get(camera_id, 0.0) < METRIC_THROTTLE_S:
            return True  # throttled, not an error
        _metric_last[camera_id] = now
    cw = _client("cloudwatch")
    if cw is None:
        return False
    try:
        datum = [
            {"MetricName": name, "Dimensions": [{"Name": "CameraId", "Value": camera_id}],
             "Value": float(value), "Unit": "None"}
            for name, value in fields.items()
        ]
        cw.put_metric_data(Namespace=NAMESPACE, MetricData=datum)
        return True
    except Exception:
        log.warning("cloudwatch put failed for %s", camera_id, exc_info=True)
        return False


# ----------------------------------------------------------------- status


_status_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_topic_name_cache: Optional[str] = None


def _check_s3() -> str:
    if not settings.s3_on:
        return "unconfigured"
    s3 = _client("s3")
    if s3 is None:
        return "error"
    try:
        s3.head_bucket(Bucket=settings.bucket)
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


def _check_sns() -> str:
    if not settings.sns_on:
        return "unconfigured"
    sns = _client("sns")
    if sns is None:
        return "error"
    try:
        sns.get_topic_attributes(TopicArn=settings.topic_arn)
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


def topic_name() -> str:
    global _topic_name_cache
    if _topic_name_cache is None:
        _topic_name_cache = settings.topic_arn.split(":")[-1] if settings.topic_arn else ""
    return _topic_name_cache


def status() -> dict[str, Any]:
    """Dashboard-facing AWS health (cached 60 s; never raises)."""
    now = time.time()
    if now - _status_cache["at"] < STATUS_TTL_S and _status_cache["payload"]:
        return _status_cache["payload"]
    try:
        payload = {
            "enabled": settings.enabled,
            "profile": settings.profile,
            "region": settings.region,
            "bucket": settings.bucket,
            "topic": topic_name(),
            "s3": _check_s3(),
            "sns": _check_sns(),
            "cloudwatch": "ok" if settings.metrics_on and _client("cloudwatch") else (
                "unconfigured" if not settings.metrics_on else "error"),
            "at": now,
        }
    except Exception:
        log.warning("aws status failed", exc_info=True)
        payload = {"enabled": False, "s3": "error", "sns": "error",
                   "cloudwatch": "error", "at": now}
    _status_cache["at"] = now
    _status_cache["payload"] = payload
    return payload


def describe() -> str:
    s = status()
    parts = []
    parts.append(f"S3:{s['s3']}")
    parts.append(f"SNS:{s['sns']}")
    parts.append(f"CloudWatch:{s['cloudwatch']}")
    return " ".join(parts)
