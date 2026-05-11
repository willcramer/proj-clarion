"""IncidentScript — deterministic timeline so demos are reproducible."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(str, Enum):
    LATENCY_SPIKE = "latency_spike"
    ERROR_BURST = "error_burst"
    THROUGHPUT_DROP = "throughput_drop"
    QUEUE_BACK_PRESSURE = "queue_back_pressure"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    AGENT_HALLUCINATION = "agent_hallucination"
    TOKEN_COST_SPIKE = "token_cost_spike"
    BUSINESS_KPI_DROP = "business_kpi_drop"


class IncidentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., pattern=r"^evt-[a-z0-9-]+$")
    offset_seconds: int = Field(
        ..., ge=0, description="Seconds from script start when this event begins"
    )
    target_kind: Literal["service", "business_entity", "agent"]
    target_id: str = Field(..., description="KGNode.node_id of the target")
    event_type: EventType
    magnitude: float = Field(..., gt=0, le=10.0, description="1.0 = baseline, >1 = worse")
    recovery_offset_seconds: int = Field(
        ..., gt=0, description="Seconds from script start when the event resolves"
    )
    expected_alert_id: str | None = Field(None, description="alert_id from AlertSpec, if any")
    narrator_cue: str = Field(
        ..., description="One-line note for the SE: 'this is where you click into the trace'"
    )

    @model_validator(mode="after")
    def recovery_after_start(self) -> IncidentEvent:
        if self.recovery_offset_seconds <= self.offset_seconds:
            raise ValueError("recovery_offset_seconds must be greater than offset_seconds")
        return self


class IncidentScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(..., pattern=r"^scr-[a-z0-9-]+$")
    title: str
    total_duration_minutes: int = Field(..., ge=1, le=60)
    arming_mode: Literal["historical_replay", "live_armed"]
    events: list[IncidentEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def events_in_window(self) -> IncidentScript:
        max_offset = self.total_duration_minutes * 60
        for ev in self.events:
            if ev.recovery_offset_seconds > max_offset:
                raise ValueError(
                    f"event {ev.event_id} recovery extends beyond script duration"
                )
        return self
