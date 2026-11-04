"""Pydantic mirrors of the frozen JSON contracts in contracts/.

extra="forbid" makes FastAPI answer 422 automatically for any payload that
does not match the contract byte-for-byte in shape.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_id: str = Field(min_length=1, max_length=64)
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    frame_id: int = Field(ge=0)
    water_bbox: list[float] = Field(min_length=4, max_length=4)
    water_area_px: float = Field(ge=0)
    water_area_ratio: float = Field(ge=0, le=1)
    flow_mean: float = Field(ge=0)
    flow_std: float = Field(ge=0)
    waterline_y: float = Field(ge=0)
    waterline_delta: float
    confidence: float = Field(ge=0, le=1)
    flow_dir_deg: Optional[float] = None
    detection_valid: bool = True
    sample_hz: float = 5.0
    annotated_jpeg_b64: Optional[str] = None
    # Stream health (sent by newer vision engines; absent on older ones).
    frame_hash: Optional[str] = None
    captured_at: Optional[str] = None
    stream_status: Optional[Literal["ok", "down", "reconnecting"]] = None
    reconnect_attempts: Optional[int] = None


class Transition(BaseModel):
    from_: str = Field(alias="from")
    to: str
    at: str
    reason: str

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CorroborationCamera(BaseModel):
    camera_id: str
    delta_pct: float
    state: str


class Corroboration(BaseModel):
    verified: bool
    method: Literal["adjacent_cameras", "high_rate_sampling", "timeout_persistence", "high_confidence"]
    confidence: float
    cameras: list[CorroborationCamera] = []
    note: str = ""


class Alert(BaseModel):
    alert_id: str
    camera_id: str
    severity: Literal["watch", "escalating", "critical"]
    state: Literal["normal", "watch", "escalating", "verifying", "critical"]
    started_at: str
    updated_at: str
    closed_at: Optional[str] = None
    status: Literal["active", "acknowledged", "false_positive", "resolved"] = "active"
    transitions: list[Transition] = []
    evidence_snapshot_url: Optional[str] = None
    telemetry_window: list[dict[str, Any]] = []
    reasoning: str = ""
    corroboration: Optional[Corroboration] = None
    agent_confidence: float = 0.0
    acknowledged_at: Optional[str] = None
    false_positive: bool = False


class AckResponse(BaseModel):
    alert_id: str
    status: str
    acknowledged_at: str
