"""Typed envelope contract: Claim, Evidence, Envelope.

Mirrors OUTPUT_CONTRACT.md from the Signalpost starter kit: one JSON envelope
per organisation number, with typed claims pointing at typed evidence, plus
changes/errors/operations. Availability is never silently converted to zero.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AVAILABILITY_STATES: tuple[str, ...] = (
    "available",
    "not_available",
    "blocked",
    "not_applicable",
    "ambiguous",
    "failed",
)
Availability = Literal[
    "available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"
]


class Claim(BaseModel):
    field: str
    value: Any = None
    availability: Availability
    confidence: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    as_of: str | None = None


class Evidence(BaseModel):
    id: str
    source_url: str
    source_class: str
    retrieved_at: str
    final_url: str | None = None
    effective_at: str | None = None
    content_sha256: str | None = None
    claim_span: str | None = None
    extractor: str | None = None
    retry_count: int = 0


class RunInfo(BaseModel):
    run_id: str
    started_at: str
    completed_at: str
    terminal_status: str


class Operations(BaseModel):
    requests: int = 0
    runtime_ms: int = 0
    third_party_cost_usd: float = 0.0


class Envelope(BaseModel):
    organisation_number: str
    run: RunInfo
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    operations: Operations = Field(default_factory=Operations)

    @field_validator("organisation_number")
    @classmethod
    def _exactly_nine_digits(cls, value: str) -> str:
        if not re.fullmatch(r"\d{9}", value):
            raise ValueError(f"organisation_number must be exactly 9 digits, got {value!r}")
        return value
