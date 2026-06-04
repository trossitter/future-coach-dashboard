"""Typed request/response contracts (Pydantic) for the agentic runtime + API.

Kept to structured-output-friendly types (no min/max constraints — the API's
structured-output schema subset doesn't support them).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# --- Surface A: workout generation ---

class GenerateRequest(BaseModel):
    member_id: str
    # max_length is a request-side guard (rejected with 422 before any token is
    # spent on the LLM); the no-min/max note in the module docstring is about the
    # structured-OUTPUT schemas below, not inbound request bodies.
    prompt: str = Field(description="Coach's free-text request", max_length=600)
    time_minutes: int = 45
    exclude_terms: list[str] = Field(default_factory=list)
    # ad-hoc, this-session constraints resolved via the clarify loop:
    avoid_joints: list[str] = Field(default_factory=list)   # confirmed avoid
    ignore_joints: list[str] = Field(default_factory=list)  # coach said it's fine


# --- Surface B: copilot ---

class CopilotRequest(BaseModel):
    member_id: str
    question: str = Field(min_length=1, max_length=500)
    # prior turns ({role, text}) so the copilot can resolve follow-ups; used for
    # context only — answers stay grounded in the retrieved member slice.
    history: list[dict] = Field(default_factory=list)


class RouteDecision(BaseModel):
    """Copilot router output — which KG2 slice to retrieve, with a confidence the
    router can fall below to force a clarifying question instead of guessing."""
    intent: str = Field(
        description="exactly one of: brief, sleep, adherence, churn, what_changed, general")
    confidence: float = Field(description="0.0–1.0 confidence in this routing")
    clarify_question: str = Field(
        default="",
        description="if the question is ambiguous, a short either/or question for the coach")


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
