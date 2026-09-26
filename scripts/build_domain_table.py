"""Build data/domain_multiplicity.json.gz from the public BRREG bulk file.

    curl -L https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv -o brreg-enheter.csv.gz
    uv run python scripts/build_domain_table.py brreg-enheter.csv.gz data/domain_multiplicity.json.gz

The bulk file is open data under NLOD 2.0. The table lists only domains shared by two or
more registry entities; it is used to refuse treating a manager's, accountant's, chain's or
parent's domain as one entity's own site. Declared as a cache in LIMITATIONS.md.
"""
from __future__ import annotations

import sys
import time

from signalpost.domains import build_table_from_bulk, save_table


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    started = time.monotonic()
    table = build_table_from_bulk(argv[1])
    save_table(table, argv[2])
    print(f"{len(table)} shared domains written to {argv[2]} in {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
