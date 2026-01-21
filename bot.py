"""Blue check bot - single file CLI."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Blue check bot - manage verification requests."
    )
    parser.add_argument(
        "--state",
        default=str(Path.cwd() / "data" / "requests.json"),
        help="Path to the JSON state file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable text.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    request = subparsers.add_parser("request", help="Create a verification request.")
    request.add_argument("user_id", help="User id, e.g. alice")
    request.add_argument("display_name", help="Display name")
    request.add_argument("reason", help="Reason for verification")

    approve = subparsers.add_parser("approve", help="Approve a pending request.")
    approve.add_argument("user_id", help="User id")
    approve.add_argument("--by", required=True, help="Reviewer name")
    approve.add_argument("--reason", default="", help="Review note")

    reject = subparsers.add_parser("reject", help="Reject a pending request.")
    reject.add_argument("user_id", help="User id")
    reject.add_argument("--by", required=True, help="Reviewer name")
    reject.add_argument("--reason", default="", help="Review note")

    status = subparsers.add_parser("status", help="Get status for a user.")
    status.add_argument("user_id", help="User id")

    list_requests = subparsers.add_parser("list", help="List requests.")
    list_requests.add_argument(
        "--status",
        choices=["pending", "approved", "rejected"],
        default=None,
        help="Filter by status.",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    bot = BlueCheckBot(args.state)

    if args.command == "request":
        result = bot.request_verification(args.user_id, args.display_name, args.reason)
        return _render_result(result, args.json)

    if args.command == "approve":
        result = bot.approve(args.user_id, args.by, args.reason)
        return _render_result(result, args.json)

    if args.command == "reject":
        result = bot.reject(args.user_id, args.by, args.reason)
        return _render_result(result, args.json)

    if args.command == "status":
        result = bot.status(args.user_id)
        return _render_result(result, args.json)

    if args.command == "list":
        records = bot.list_requests(args.status)
        return _render_list(records, args.json)

    parser.print_help()
    return 1


def _render_result(result: ActionResult, as_json: bool) -> int:
    if as_json:
        payload = {"ok": result.ok, "message": result.message, "record": result.record}
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(result.message)
        if result.record:
            print(_format_record(result.record))
    return 0 if result.ok else 1


def _render_list(records: List[Dict[str, Any]], as_json: bool) -> int:
    if as_json:
        print(json.dumps(records, indent=2, sort_keys=True))
        return 0
    if not records:
        print("No records.")
        return 0
    for record in records:
        print(_format_record(record))
        print("-" * 40)
    return 0


def _format_record(record: Dict[str, Any]) -> str:
    fields = [
        ("user_id", record.get("user_id")),
        ("display_name", record.get("display_name")),
        ("status", record.get("status")),
        ("reason", record.get("reason")),
        ("requested_at", record.get("requested_at")),
        ("reviewed_at", record.get("reviewed_at")),
        ("reviewed_by", record.get("reviewed_by")),
        ("review_reason", record.get("review_reason")),
    ]
    lines = [f"{label}: {value}" for label, value in fields]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
