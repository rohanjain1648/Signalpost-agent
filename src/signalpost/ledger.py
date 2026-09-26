"""Claim ledger: stable claim IDs, idempotent upserts, typed real-change detection.

Refresh contract (PLAN.md section 7):
  * rerunning an unchanged source snapshot yields zero changes and identical state;
  * only *normalized* value differences count as changes (whitespace never does);
  * an item that disappears from a family that was checked successfully is `closed`,
    with its earlier value and history preserved;
  * a family whose fetch failed marks its claims `stale` and emits no change,
    so a failed refresh never erases or invalidates the last supported value.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

NEW = {
    "jobs": "new_job", "nav_jobs": "new_job", "roles": "new_role", "locations": "new_location",
    "financials": "new_filing", "activity": "new_activity", "website": "new_website",
    "socials": "new_social_profile", "description": "new_description",
}
CHANGED = {
    "jobs": "changed_job", "nav_jobs": "changed_job", "roles": "changed_role", "locations": "changed_location",
    "financials": "changed_financial", "activity": "changed_activity",
    "website": "website_changed", "socials": "changed_social_profile",
    "description": "changed_description",
}
CLOSED = {
    "jobs": "closed_job", "nav_jobs": "closed_job", "roles": "ended_role", "locations": "closed_location",
    "financials": "withdrawn_filing", "activity": "closed_activity",
    "website": "website_removed", "socials": "removed_social_profile",
}
MATERIAL = {
    "new_filing": "high", "changed_financial": "high", "website_changed": "high",
    "website_removed": "high", "new_role": "medium", "ended_role": "medium",
    "new_website": "medium",
}


def claim_id(org: str, family: str, key: str) -> str:
    return hashlib.sha1(f"{org}|{family}|{key}".encode("utf-8")).hexdigest()[:16]


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _hash(value: Any) -> str:
    canonical = json.dumps(_normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _change(org: str, claim: dict[str, Any], kind: str, ts: str, *, old: Any, new: Any,
            old_ev: str | None, new_ev: str | None, reopened: bool = False) -> dict[str, Any]:
    return {
        "organisation_number": org,
        "claim_id": claim["claim_id"],
        "family": claim["family"],
        "key": claim["key"],
        "type": kind,
        "materiality": MATERIAL.get(kind, "low"),
        "observed_at": ts,
        "old_value": old,
        "new_value": new,
        "old_evidence_id": old_ev,
        "new_evidence_id": new_ev,
        "reopened": reopened,
    }


def apply_observations(
    org: str,
    prev: dict[str, dict[str, Any]],
    observations: list[dict[str, Any]],
    *,
    run_ts: str,
    checked_families: set[str],
    failed_families: set[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Merge one run's observations into the previous state. Returns (new_state, changes)."""
    state = copy.deepcopy(prev)
    changes: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in observations:
        family, key = item["family"], str(item["key"])
        cid = claim_id(org, family, key)
        seen.add(cid)
        value = _normalize(item["value"])
        digest = _hash(value)
        evidence_id = item.get("evidence_id")
        entry = {"value": value, "value_hash": digest, "seen_at": run_ts, "evidence_id": evidence_id}
        claim = state.get(cid)

        if claim is None:
            claim = {
                "claim_id": cid, "family": family, "key": key, "status": "active",
                "value": value, "value_hash": digest, "evidence_id": evidence_id,
                "first_seen": run_ts, "last_seen": run_ts, "last_verified": run_ts,
                "history": [entry],
            }
            state[cid] = claim
            changes.append(_change(org, claim, NEW.get(family, f"new_{family}"), run_ts,
                                   old=None, new=value, old_ev=None, new_ev=evidence_id))
            continue

        old_value, old_evidence = claim["value"], claim.get("evidence_id")
        if claim["status"] == "closed":
            claim["history"].append(entry)
            changes.append(_change(org, claim, NEW.get(family, f"new_{family}"), run_ts,
                                   old=old_value, new=value, old_ev=old_evidence,
                                   new_ev=evidence_id, reopened=True))
        elif digest != claim["value_hash"]:
            claim["history"].append(entry)
            changes.append(_change(org, claim, CHANGED.get(family, f"changed_{family}"), run_ts,
                                   old=old_value, new=value, old_ev=old_evidence, new_ev=evidence_id))
        claim.update(status="active", value=value, value_hash=digest, evidence_id=evidence_id,
                     last_seen=run_ts, last_verified=run_ts)

    for cid, claim in state.items():
        if cid in seen or claim["status"] == "closed":
            continue
        family = claim["family"]
        if family in failed_families:
            claim["status"] = "stale"
        elif family in checked_families:
            claim["status"] = "closed"
            claim["closed_at"] = run_ts
            changes.append(_change(org, claim, CLOSED.get(family, f"closed_{family}"), run_ts,
                                   old=claim["value"], new=None,
                                   old_ev=claim.get("evidence_id"), new_ev=None))
    return state, changes
