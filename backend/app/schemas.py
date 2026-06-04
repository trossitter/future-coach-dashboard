"""Typed request/response contracts (Pydantic) for the agentic runtime + API.

Kept to structured-output-friendly types (no min/max constraints — the API's
structured-output schema subset doesn't support them).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# --- Surface A: workout generation ---

class GenerateRequest(BaseModel):
    member_id: str
    prompt: str = Field(description="Coach's free-text request")
    time_minutes: int = 45
    exclude_terms: list[str] = Field(default_factory=list)


# --- Surface B: copilot ---

class CopilotRequest(BaseModel):
    member_id: str
    question: str


class Intent(BaseModel):
    """Planner output — the coach's prompt resolved onto graph concepts."""
    target_muscles: list[str] = Field(default_factory=list)
    target_patterns: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    emphasis: str = ""
    summary: str = ""


class Prescription(BaseModel):
    id: str
    name: str
    section: str            # warmup | main | cooldown
    sets: int
    reps: str               # "8-12" or "30s"
    rest_seconds: int


class WorkoutPlan(BaseModel):
    warmup: list[Prescription] = Field(default_factory=list)
    main: list[Prescription] = Field(default_factory=list)
    cooldown: list[Prescription] = Field(default_factory=list)


class ProvenanceEntry(BaseModel):
    """PROV-O-style trace for one selected exercise."""
    exercise_id: str
    name: str
    chosen_because: list[str]       # graph paths / intent matches
    safe_because: list[str]         # what safety checks it passed


class GenerationResult(BaseModel):
    member_id: str
    intent: Intent
    plan: WorkoutPlan
    provenance: list[ProvenanceEntry]
    filtered_out: list[dict]        # what was excluded for safety + alternatives
    journey_stage: str
    narration: str = ""             # LLM phrasing (empty when no key)
    degraded: bool = False          # true when running without the LLM
