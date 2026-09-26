"""Per-organisation ledger state on disk (one JSON file per org, written atomically)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

State = dict[str, dict[str, Any]]


def _path(directory: str | Path, org: str) -> Path:
    return Path(directory) / f"{org}.json"


def load_state(directory: str | Path | None, org: str) -> State:
    if directory is None:
        return {}
    path = _path(directory, org)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(directory: str | Path | None, org: str, state: State) -> None:
    if directory is None:
        return
    Path(directory).mkdir(parents=True, exist_ok=True)
    path = _path(directory, org)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)
