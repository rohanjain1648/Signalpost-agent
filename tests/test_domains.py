import csv
import gzip

from signalpost.domains import build_table_from_bulk, load_table, save_table

HEADER = ["organisasjonsnummer", "navn", "hjemmeside", "epostadresse"]


def write_bulk(path, rows):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)


def test_build_table_counts_distinct_orgs_per_domain_across_website_and_email(tmp_path):
    bulk = tmp_path / "bulk.csv.gz"
    write_bulk(bulk, [
        ["1", "A", "https://www.solo.no", "post@solo.no"],           # same org twice on one domain = 1
        ["2", "B", "", "a@styrerommet.no"],
        ["3", "C", "www.styrerommet.no", ""],
        ["4", "D", "", "kari@gmail.com"],                          # freemail never counts
        ["5", "E", "", "x@styrerommet.no"],
    ])
    table = build_table_from_bulk(bulk)
    assert table["styrerommet.no"] == 3


def test_table_keeps_only_shared_domains_so_missing_means_unique(tmp_path):
    bulk = tmp_path / "bulk.csv.gz"
    write_bulk(bulk, [["1", "A", "solo.no", ""], ["2", "B", "", "a@shared.no"], ["3", "C", "", "b@shared.no"]])
    table = build_table_from_bulk(bulk)
    assert "solo.no" not in table
    assert table == {"shared.no": 2}


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "table.json.gz"
    save_table({"shared.no": 2, "bring.com": 30}, path)
    assert load_table(path) == {"shared.no": 2, "bring.com": 30}


def test_missing_table_loads_as_none_so_callers_know_it_is_unchecked(tmp_path):
    assert load_table(tmp_path / "nope.json.gz") is None
