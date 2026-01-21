"""Persistence helpers for the blue check bot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

DEFAULT_STATE: Dict[str, Any] = {"requests": {}}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"requests": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"requests": {}}
    if not isinstance(data, dict) or "requests" not in data:
        return {"requests": {}}
    if not isinstance(data["requests"], dict):
        data["requests"] = {}
    return data


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(path)
