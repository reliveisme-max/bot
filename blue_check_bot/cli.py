"""Command-line interface for the blue check bot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bot import BlueCheckBot


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


def _render_result(result: Any, as_json: bool) -> int:
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
