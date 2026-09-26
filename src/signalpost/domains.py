"""Domain-multiplicity table: which domains are shared by several registry entities.

A domain used by many entities belongs to a manager, accountant, chain or parent, not
to any one of them. The table is built offline from the public BRREG bulk file
(declared cache; NLOD 2.0) and holds only *shared* domains (count >= 2), so a missing
domain means "unique". `load_table` returns None when no table exists, which callers
must treat as "unchecked" rather than "everything is unique".
"""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from typing import Iterator

from .identity import build_domain_multiplicity


def _bulk_rows(path: str | Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {
                "organisation_number": row.get("organisasjonsnummer", ""),
                "website": row.get("hjemmeside", ""),
                "email": row.get("epostadresse", ""),
            }


def build_table_from_bulk(path: str | Path) -> dict[str, int]:
    counts = build_domain_multiplicity(_bulk_rows(path))
    return {domain: count for domain, count in sorted(counts.items()) if count >= 2}


def save_table(table: dict[str, int], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(table, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_table(path: str | Path) -> dict[str, int] | None:
    source = Path(path)
    if not source.exists():
        return None
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        return json.load(handle)
