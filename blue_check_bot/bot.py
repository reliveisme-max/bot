"""Core logic for the blue check bot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .storage import load_state, save_state, utcnow_iso


@dataclass
class ActionResult:
    ok: bool
    message: str
    record: Optional[Dict[str, Any]] = None


class BlueCheckBot:
    def __init__(self, state_path: Union[Path, str]) -> None:
        self.state_path = Path(state_path)

    def request_verification(
        self, user_id: str, display_name: str, reason: str
    ) -> ActionResult:
        normalized = self._normalize_user_id(user_id)
        if not normalized:
            return ActionResult(False, "user_id is required")

        state = load_state(self.state_path)
        existing = state["requests"].get(normalized)
        if existing and existing.get("status") == "pending":
            return ActionResult(True, "request is already pending", existing)
        if existing and existing.get("status") == "approved":
            return ActionResult(True, "user is already verified", existing)

        history = []
        if existing:
            history = list(existing.get("history", []))
            history.append(self._snapshot(existing))

        record = {
            "user_id": normalized,
            "display_name": display_name.strip(),
            "reason": reason.strip(),
            "status": "pending",
            "requested_at": utcnow_iso(),
            "reviewed_at": None,
            "reviewed_by": None,
            "review_reason": None,
            "history": history,
        }
        state["requests"][normalized] = record
        save_state(self.state_path, state)
        return ActionResult(True, "request created", record)

    def approve(self, user_id: str, reviewed_by: str, review_reason: str) -> ActionResult:
        record, error = self._get_pending(user_id)
        if error:
            return ActionResult(False, error)

        record["status"] = "approved"
        record["reviewed_at"] = utcnow_iso()
        record["reviewed_by"] = reviewed_by.strip()
        record["review_reason"] = review_reason.strip() if review_reason else None

        self._save_record(record)
        return ActionResult(True, "request approved", record)

    def reject(self, user_id: str, reviewed_by: str, review_reason: str) -> ActionResult:
        record, error = self._get_pending(user_id)
        if error:
            return ActionResult(False, error)

        record["status"] = "rejected"
        record["reviewed_at"] = utcnow_iso()
        record["reviewed_by"] = reviewed_by.strip()
        record["review_reason"] = review_reason.strip() if review_reason else None

        self._save_record(record)
        return ActionResult(True, "request rejected", record)

    def status(self, user_id: str) -> ActionResult:
        normalized = self._normalize_user_id(user_id)
        if not normalized:
            return ActionResult(False, "user_id is required")

        state = load_state(self.state_path)
        record = state["requests"].get(normalized)
        if not record:
            return ActionResult(False, "user not found")
        return ActionResult(True, "status returned", record)

    def list_requests(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        state = load_state(self.state_path)
        records = list(state["requests"].values())
        if status:
            records = [r for r in records if r.get("status") == status]
        return sorted(records, key=lambda r: r.get("requested_at") or "")

    def _get_pending(self, user_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        normalized = self._normalize_user_id(user_id)
        if not normalized:
            return None, "user_id is required"

        state = load_state(self.state_path)
        record = state["requests"].get(normalized)
        if not record:
            return None, "user not found"
        if record.get("status") != "pending":
            return None, f"request is not pending (status={record.get('status')})"
        return record, None

    def _save_record(self, record: Dict[str, Any]) -> None:
        state = load_state(self.state_path)
        state["requests"][record["user_id"]] = record
        save_state(self.state_path, state)

    def _normalize_user_id(self, user_id: str) -> str:
        return (user_id or "").strip().lower()

    def _snapshot(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": record.get("status"),
            "display_name": record.get("display_name"),
            "reason": record.get("reason"),
            "requested_at": record.get("requested_at"),
            "reviewed_at": record.get("reviewed_at"),
            "reviewed_by": record.get("reviewed_by"),
            "review_reason": record.get("review_reason"),
        }
