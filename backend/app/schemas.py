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
    # ad-hoc, this-session equipment overrides (parsed from the prompt or
    # confirmed via the clarify loop):
    exclude_equipment: list[str] = Field(default_factory=list)  # not available this session
    extra_equipment: list[str] = Field(default_factory=list)    # available this session


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


class EquipmentContext(BaseModel):
    """LLM-routed equipment-context classification. The model decides ONLY whether
    to ask the guide about equipment — it never sets the constraint itself. The
    guide's answer re-enters as deterministic equipment overrides, so the graph
    filter stays the sole arbiter of what's feasible (the LLM gathers, the human
    decides). This is why a brittle hardcoded phrase list isn't needed: 'traveling',
    'in a hotel', 'at an Airbnb', 'visiting family' all generalize without
    enumeration."""
    situation: str = Field(
        description="exactly one of: bodyweight_only (clearly no equipment at all — "
        "'bodyweight only', 'no equipment'), ambiguous (an equipment context we "
        "can't resolve — traveling, a hotel, home, a park, away from the gym — where "
        "the kit on hand is unknown), or none (no equipment constraint implied)")
    confidence: float = Field(description="0.0-1.0 confidence in this classification")
    clarify_question: str = Field(
        default="",
        description="when ambiguous, a short friendly question asking what equipment "
        "is available this session")


class Prescription(BaseModel):
    id: str
    name: str
    section: str            # warmup | main | cooldown
    sets: int
    reps: str               # "8-12" or "30s"
    rest_seconds: int


class WorkoutPlan(BaseModel):
    """Assembler's structured output — the LLM fills sections from the safe set."""
    warmup: list[Prescription] = Field(default_factory=list)
    main: list[Prescription] = Field(default_factory=list)
    cooldown: list[Prescription] = Field(default_factory=list)
