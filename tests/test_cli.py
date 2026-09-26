import json
from pathlib import Path

import httpx
import pytest

from signalpost.cli import main
from signalpost.net import CountingClient
from signalpost.official import (
    BRREG_ACCOUNT_YEARS,
    BRREG_ACCOUNTS,
    BRREG_ENTITY,
    BRREG_ROLES,
    BRREG_SUBUNITS,
)
from signalpost.store import load_state, save_state

FIXTURES = Path(__file__).parent / "fixtures" / "brreg"
GOOD = "810034882"
BROKEN = "938702675"  # every endpoint 503s for this one


def _fixture_urls(org):
    return {
        BRREG_ENTITY.format(org=org): f"entity_{org}.json",
        BRREG_ROLES.format(org=org): f"roles_{org}.json",
        BRREG_SUBUNITS.format(org=org): f"subunits_{org}.json",
        BRREG_ACCOUNTS.format(org=org): f"accounts_{org}.json",
        BRREG_ACCOUNT_YEARS.format(org=org): f"years_{org}.json",
    }


def client_factory(max_requests):
    urls = _fixture_urls(GOOD)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in urls:
            return httpx.Response(200, content=(FIXTURES / urls[url]).read_bytes(),
                                  headers={"content-type": "application/json"})
        return httpx.Response(503, text="down")

    return CountingClient(transport=httpx.MockTransport(handler), max_requests=max_requests,
                          resolver=lambda h: ["8.8.8.8"])


def write_input(tmp_path, orgs):
    path = tmp_path / "orgs.txt"
    path.write_text("\n".join(orgs) + "\n", encoding="utf-8")
    return path


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- store -----------------------------------------------------------------


def test_state_store_round_trips_and_missing_state_is_empty(tmp_path):
    assert load_state(tmp_path, GOOD) == {}
    save_state(tmp_path, GOOD, {"abc": {"family": "jobs"}})
    assert load_state(tmp_path, GOOD) == {"abc": {"family": "jobs"}}


def test_state_store_with_no_directory_is_a_noop(tmp_path):
    assert load_state(None, GOOD) == {}
    save_state(None, GOOD, {"abc": {}})  # must not raise or write anywhere


# ---- cli -------------------------------------------------------------------


def run_cli(tmp_path, orgs, extra=()):
    out = tmp_path / "out" / "envelopes.jsonl"
    code = main(
        ["--input", str(write_input(tmp_path, orgs)), "--output", str(out), "--run-id", "r1", *extra],
        client_factory=client_factory,
    )
    return code, out


def test_cli_writes_exactly_one_envelope_per_input_including_broken_ones(tmp_path):
    code, out = run_cli(tmp_path, [GOOD, BROKEN])
    envelopes = read_jsonl(out)
    assert code == 0
    assert [e["organisation_number"] for e in envelopes] == [GOOD, BROKEN]
    assert envelopes[0]["run"]["terminal_status"] == "completed"
    assert envelopes[1]["run"]["terminal_status"] == "failed"


def test_cli_writes_a_machine_readable_run_report(tmp_path):
    _, out = run_cli(tmp_path, [GOOD, BROKEN])
    report = json.loads(Path(str(out) + ".report.json").read_text(encoding="utf-8"))
    assert report["expected_count"] == 2
    assert report["emitted_envelopes"] == 2
    assert report["terminal_status_counts"] == {"completed": 1, "failed": 1}
    assert report["requests"] > 0
    assert report["third_party_cost_usd"] == 0.0
    assert report["max_requests"] == 1900


def test_cli_state_dir_makes_second_run_report_no_changes(tmp_path):
    state = tmp_path / "state"
    run_cli(tmp_path, [GOOD], extra=("--state-dir", str(state)))
    first = read_jsonl(tmp_path / "out" / "envelopes.jsonl")[0]
    assert first["changes"]

    run_cli(tmp_path, [GOOD], extra=("--state-dir", str(state)))
    second = read_jsonl(tmp_path / "out" / "envelopes.jsonl")[0]
    assert second["changes"] == []


def test_cli_rejects_input_with_invalid_organisation_number(tmp_path):
    with pytest.raises(ValueError, match="9 digits"):
        run_cli(tmp_path, ["12345"])


# ---- website research flags ----------------------------------------------------


def _claims(path):
    return {c["field"]: c for c in read_jsonl(path)[0]["claims"]}


def test_website_families_are_researched_by_default(tmp_path):
    _, out = run_cli(tmp_path, [GOOD], extra=("--domain-table", str(tmp_path / "missing.json.gz")))
    assert "official_website" in _claims(out)


def test_no_web_flag_skips_website_families_and_site_requests(tmp_path):
    _, out = run_cli(tmp_path, [GOOD], extra=("--no-web",))
    assert "official_website" not in _claims(out)
    report = json.loads(Path(str(out) + ".report.json").read_text(encoding="utf-8"))
    assert report["web_enabled"] is False
    assert report["requests"] == 5


def test_report_says_when_the_domain_table_is_missing_and_warns(tmp_path, capsys):
    _, out = run_cli(tmp_path, [GOOD], extra=("--domain-table", str(tmp_path / "missing.json.gz")))
    report = json.loads(Path(str(out) + ".report.json").read_text(encoding="utf-8"))
    assert report["web_enabled"] is True
    assert report["domain_table_loaded"] is False
    assert "domain table" in capsys.readouterr().err.lower()


def test_report_records_a_loaded_domain_table(tmp_path):
    from signalpost.domains import save_table

    table = tmp_path / "table.json.gz"
    save_table({"shared.no": 3}, table)
    _, out = run_cli(tmp_path, [GOOD], extra=("--domain-table", str(table)))
    report = json.loads(Path(str(out) + ".report.json").read_text(encoding="utf-8"))
    assert report["domain_table_loaded"] is True
    assert report["domain_table_shared_domains"] == 1


def test_default_domain_table_path_is_absolute_and_ships_in_the_repo():
    from signalpost.cli import DEFAULT_DOMAIN_TABLE

    assert Path(DEFAULT_DOMAIN_TABLE).is_absolute()
    assert Path(DEFAULT_DOMAIN_TABLE).exists()


def test_report_records_discovery_state_and_dns_lookups(tmp_path):
    _, out = run_cli(tmp_path, [GOOD], extra=("--domain-table", str(tmp_path / "missing.json.gz")))
    report = json.loads(Path(str(out) + ".report.json").read_text(encoding="utf-8"))
    assert report["discovery_enabled"] is True
    assert report["dns_lookups"] >= 1


def test_no_discovery_flag_is_reported_and_still_researches_registry_candidates(tmp_path):
    _, out = run_cli(tmp_path, [GOOD], extra=("--no-discovery", "--domain-table", str(tmp_path / "missing.json.gz")))
    report = json.loads(Path(str(out) + ".report.json").read_text(encoding="utf-8"))
    assert report["discovery_enabled"] is False
    assert "official_website" in _claims(out)
