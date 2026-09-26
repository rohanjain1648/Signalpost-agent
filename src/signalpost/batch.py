"""Batch input parsing and the exactly-N-envelopes-out guarantee.

Per the challenge's own "common traps": if we hand you 100 org numbers and
60 come back, the run cannot be scored. `run_batch` therefore never lets a
single company's exception drop that company from the output — it converts
the exception into a terminal `failed` envelope and keeps going.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import Envelope

Worker = Callable[[str], Envelope]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_org(value: Any) -> str:
    org = value.get("organisation_number") if isinstance(value, dict) else value
    digits = "".join(ch for ch in str(org or "") if ch.isdigit())
    if len(digits) != 9:
        raise ValueError(f"organisation_number must be 9 digits, got {value!r}")
    return digits


def read_organisation_numbers(path: str | Path) -> list[str]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix == ".json":
        body = json.loads(text)
        raw_values = body if isinstance(body, list) else body.get("organisation_numbers", [])
    elif source.suffix == ".jsonl":
        raw_values = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        raw_values = [line.strip() for line in text.splitlines() if line.strip()]

    orgs = [_normalize_org(value) for value in raw_values]
    if len(orgs) != len(set(orgs)):
        seen: set[str] = set()
        dupes = sorted({org for org in orgs if org in seen or seen.add(org)})
        raise ValueError(f"Duplicate organisation numbers in input: {dupes[:10]}")
    return orgs


def _failed_envelope(org: str, run_id: str, started_at: str, exc: Exception) -> Envelope:
    return Envelope(
        organisation_number=org,
        run={
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": _utc_now(),
            "terminal_status": "failed",
        },
        errors=[{"type": type(exc).__name__, "message": str(exc)}],
    )


def run_batch(orgs: list[str], *, worker: Worker, run_id: str) -> list[Envelope]:
    """Call `worker(org)` for every org, in order, and always return len(orgs) envelopes.

    A worker exception never drops the company: it is converted into a
    terminal `failed` envelope carrying the exception type and message.
    """
    started_at = _utc_now()
    envelopes: list[Envelope] = []
    for org in orgs:
        try:
            envelopes.append(worker(org))
        except Exception as exc:  # noqa: BLE001 - intentional: no company may be dropped
            envelopes.append(_failed_envelope(org, run_id, started_at, exc))
    return envelopes
