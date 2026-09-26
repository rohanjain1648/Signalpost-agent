import json

import pytest

from signalpost.batch import read_organisation_numbers, run_batch
from signalpost.models import Envelope


# ---- read_organisation_numbers -------------------------------------------------


def test_read_organisation_numbers_from_txt(tmp_path):
    path = tmp_path / "orgs.txt"
    path.write_text("810034882\n938702675\n", encoding="utf-8")
    assert read_organisation_numbers(path) == ["810034882", "938702675"]


def test_read_organisation_numbers_from_json_list(tmp_path):
    path = tmp_path / "orgs.json"
    path.write_text(json.dumps(["810034882", "938702675"]), encoding="utf-8")
    assert read_organisation_numbers(path) == ["810034882", "938702675"]


def test_read_organisation_numbers_from_jsonl_objects(tmp_path):
    path = tmp_path / "orgs.jsonl"
    path.write_text(
        '{"organisation_number": "810034882"}\n{"organisation_number": "938702675"}\n',
        encoding="utf-8",
    )
    assert read_organisation_numbers(path) == ["810034882", "938702675"]


def test_read_organisation_numbers_rejects_wrong_length():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "orgs.txt"
        path.write_text("12345\n", encoding="utf-8")
        with pytest.raises(ValueError, match="9 digits"):
            read_organisation_numbers(path)


def test_read_organisation_numbers_rejects_duplicates():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "orgs.txt"
        path.write_text("810034882\n810034882\n", encoding="utf-8")
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            read_organisation_numbers(path)


# ---- run_batch: the exactly-N-envelopes guarantee -------------------------------


def _envelope_for(org: str) -> Envelope:
    return Envelope(
        organisation_number=org,
        run={
            "run_id": "r1",
            "started_at": "2026-09-26T00:00:00Z",
            "completed_at": "2026-09-26T00:00:01Z",
            "terminal_status": "completed",
        },
    )


def test_run_batch_returns_one_envelope_per_input_in_order():
    orgs = ["810034882", "938702675", "923609016"]
    envelopes = run_batch(orgs, worker=_envelope_for, run_id="r1")
    assert [e.organisation_number for e in envelopes] == orgs


def test_run_batch_emits_failed_envelope_when_worker_raises():
    def flaky_worker(org: str) -> Envelope:
        if org == "938702675":
            raise RuntimeError("site fetch blew up")
        return _envelope_for(org)

    orgs = ["810034882", "938702675", "923609016"]
    envelopes = run_batch(orgs, worker=flaky_worker, run_id="r1")

    assert len(envelopes) == 3
    failed = next(e for e in envelopes if e.organisation_number == "938702675")
    assert failed.run.terminal_status == "failed"
    assert failed.errors
    assert "RuntimeError" in failed.errors[0]["type"]
    assert "site fetch blew up" in failed.errors[0]["message"]


def test_run_batch_still_returns_n_envelopes_when_every_worker_call_raises():
    def always_fails(org: str) -> Envelope:
        raise TimeoutError("budget exhausted")

    orgs = ["810034882", "938702675"]
    envelopes = run_batch(orgs, worker=always_fails, run_id="r1")

    assert len(envelopes) == 2
    assert all(e.run.terminal_status == "failed" for e in envelopes)


# ---- concurrency and the time budget ----------------------------------------------

import threading  # noqa: E402
import time  # noqa: E402


def test_concurrent_run_preserves_input_order_and_returns_every_company():
    orgs = [f"81003488{i}" for i in range(10)]

    def slow_worker(org: str) -> Envelope:
        time.sleep(0.03 if int(org[-1]) % 2 == 0 else 0.001)  # finish out of order
        return _envelope_for(org)

    envelopes = run_batch(orgs, worker=slow_worker, run_id="r1", workers=4)
    assert [e.organisation_number for e in envelopes] == orgs


def test_concurrent_run_really_runs_workers_in_parallel():
    active, peak, lock = 0, 0, threading.Lock()

    def worker(org: str) -> Envelope:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return _envelope_for(org)

    run_batch([f"81003488{i}" for i in range(8)], worker=worker, run_id="r1", workers=4)
    assert peak > 1


def test_concurrent_run_still_converts_exceptions_into_failed_envelopes():
    def worker(org: str) -> Envelope:
        if org.endswith("3"):
            raise RuntimeError("boom")
        return _envelope_for(org)

    orgs = [f"81003488{i}" for i in range(6)]
    envelopes = run_batch(orgs, worker=worker, run_id="r1", workers=3)
    assert [e.run.terminal_status for e in envelopes] == ["completed"] * 3 + ["failed"] + ["completed"] * 2


def test_companies_not_started_before_the_hard_deadline_become_failed_envelopes_without_work():
    now = {"t": 0.0}
    started = []

    def worker(org: str) -> Envelope:
        started.append(org)
        now["t"] += 10.0  # each company "takes" 10 seconds
        return _envelope_for(org)

    orgs = [f"81003488{i}" for i in range(5)]
    envelopes = run_batch(orgs, worker=worker, run_id="r1", hard_deadline=25.0, clock=lambda: now["t"])
    assert len(envelopes) == 5
    assert started == orgs[:3]  # t=0,10,20 start; t=30 is past the deadline
    late = envelopes[3:]
    assert all(e.run.terminal_status == "failed" for e in late)
    assert late[0].errors[0]["type"] == "TimeBudgetExhausted"
