from signalpost.ledger import apply_observations, claim_id

ORG = "810034882"
T1 = "2026-10-01T00:00:00Z"
T2 = "2026-10-02T00:00:00Z"


def obs(family, key, value, evidence_id="ev-1"):
    return {"family": family, "key": key, "value": value, "evidence_id": evidence_id}


def run(prev, observations, ts, checked=("jobs", "roles", "financials"), failed=()):
    return apply_observations(
        ORG, prev, observations, run_ts=ts, checked_families=set(checked), failed_families=set(failed)
    )


def test_claim_id_is_stable_and_scoped_to_org_family_key():
    assert claim_id(ORG, "jobs", "https://x.no/j/1") == claim_id(ORG, "jobs", "https://x.no/j/1")
    assert claim_id(ORG, "jobs", "a") != claim_id(ORG, "jobs", "b")
    assert claim_id(ORG, "jobs", "a") != claim_id("923609016", "jobs", "a")


def test_first_run_records_every_observation_as_a_typed_new_change():
    state, changes = run({}, [obs("jobs", "j1", {"title": "Elektriker"}), obs("roles", "DAGL:Ola", {"name": "Ola"})], T1)
    assert {c["type"] for c in changes} == {"new_job", "new_role"}
    claim = state[claim_id(ORG, "jobs", "j1")]
    assert claim["status"] == "active"
    assert claim["first_seen"] == claim["last_seen"] == T1


def test_rerun_of_identical_snapshot_is_idempotent():
    observations = [obs("jobs", "j1", {"title": "Elektriker"})]
    state1, _ = run({}, observations, T1)
    state2, changes = run(state1, observations, T1)
    assert changes == []
    assert state2 == state1


def test_rerun_later_with_same_values_reports_no_change_but_advances_last_seen():
    observations = [obs("jobs", "j1", {"title": "Elektriker"})]
    state1, _ = run({}, observations, T1)
    state2, changes = run(state1, observations, T2)
    claim = state2[claim_id(ORG, "jobs", "j1")]
    assert changes == []
    assert claim["first_seen"] == T1
    assert claim["last_seen"] == T2
    assert len(claim["history"]) == 1


def test_whitespace_only_difference_is_not_a_change():
    state1, _ = run({}, [obs("jobs", "j1", {"title": "Elektriker  i Sandnes"})], T1)
    _, changes = run(state1, [obs("jobs", "j1", {"title": " Elektriker i   Sandnes "})], T2)
    assert changes == []


def test_changed_value_emits_change_with_both_sides_and_keeps_history():
    state1, _ = run({}, [obs("financials", "2025:revenue", 100, "ev-old")], T1)
    state2, changes = run(state1, [obs("financials", "2025:revenue", 120, "ev-new")], T2)
    assert len(changes) == 1
    change = changes[0]
    assert change["type"] == "changed_financial"
    assert (change["old_value"], change["new_value"]) == (100, 120)
    assert (change["old_evidence_id"], change["new_evidence_id"]) == ("ev-old", "ev-new")
    assert [h["value"] for h in state2[claim_id(ORG, "financials", "2025:revenue")]["history"]] == [100, 120]


def test_item_missing_from_a_successfully_checked_family_is_closed():
    state1, _ = run({}, [obs("jobs", "j1", {"title": "Elektriker"})], T1)
    state2, changes = run(state1, [], T2)
    assert [c["type"] for c in changes] == ["closed_job"]
    claim = state2[claim_id(ORG, "jobs", "j1")]
    assert claim["status"] == "closed"
    assert claim["value"] == {"title": "Elektriker"}  # earlier evidence preserved


def test_closed_item_stays_closed_without_repeating_the_change():
    state1, _ = run({}, [obs("jobs", "j1", {"title": "Elektriker"})], T1)
    state2, _ = run(state1, [], T2)
    state3, changes = run(state2, [], "2026-10-03T00:00:00Z")
    assert changes == []
    assert state3[claim_id(ORG, "jobs", "j1")]["status"] == "closed"


def test_failed_family_marks_claims_stale_and_emits_no_change():
    state1, _ = run({}, [obs("jobs", "j1", {"title": "Elektriker"})], T1)
    state2, changes = run(state1, [], T2, checked=(), failed=("jobs",))
    claim = state2[claim_id(ORG, "jobs", "j1")]
    assert changes == []
    assert claim["status"] == "stale"
    assert claim["value"] == {"title": "Elektriker"}


def test_family_not_checked_this_run_leaves_claims_untouched():
    state1, _ = run({}, [obs("jobs", "j1", {"title": "Elektriker"})], T1)
    state2, changes = run(state1, [], T2, checked=("roles",))
    assert changes == []
    assert state2[claim_id(ORG, "jobs", "j1")]["status"] == "active"


def test_stale_claim_recovers_silently_when_next_run_sees_the_same_value():
    state1, _ = run({}, [obs("jobs", "j1", {"title": "Elektriker"})], T1)
    state2, _ = run(state1, [], T2, checked=(), failed=("jobs",))
    state3, changes = run(state2, [obs("jobs", "j1", {"title": "Elektriker"})], "2026-10-03T00:00:00Z")
    assert changes == []
    assert state3[claim_id(ORG, "jobs", "j1")]["status"] == "active"


def test_closed_item_that_reappears_is_reported_as_new_again():
    state1, _ = run({}, [obs("jobs", "j1", {"title": "Elektriker"})], T1)
    state2, _ = run(state1, [], T2)
    state3, changes = run(state2, [obs("jobs", "j1", {"title": "Elektriker"})], "2026-10-03T00:00:00Z")
    assert [c["type"] for c in changes] == ["new_job"]
    assert changes[0]["reopened"] is True
    assert state3[claim_id(ORG, "jobs", "j1")]["status"] == "active"


def test_input_state_is_not_mutated():
    state1, _ = run({}, [obs("jobs", "j1", {"title": "A"})], T1)
    snapshot = repr(state1)
    run(state1, [obs("jobs", "j1", {"title": "B"})], T2)
    assert repr(state1) == snapshot
