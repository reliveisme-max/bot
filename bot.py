"""BOT TÍCH XANH - Telegram bot (single file)."""

from __future__ import annotations

import json
import os
import queue
import random
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, parse, request

BOT_NAME = "BOT TÍCH XANH"

DEFAULT_STATE_PATH = Path.cwd() / "data" / "state.json"
CHECK_INTERVAL_MIN = 30
CHECK_INTERVAL_MAX = 60
WATCH_EXPIRE_DAYS = 7
HTTP_TIMEOUT = 20

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
)

DEFAULT_STATE: Dict[str, Any] = {
    "users": {},
    "cookies": {},
    "watches": {},
}


def now_ts() -> int:
    return int(time.time())


def format_time(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M | %d/%m")


def normalize_command(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.strip().upper().split())


def new_id(prefix: str) -> str:
    return f"{prefix}{int(time.time() * 1000)}{random.randint(100, 999)}"


def extract_url(text: str) -> Optional[str]:
    text = text.strip()
    if not text:
        return None
    if "facebook.com" in text or text.startswith("http"):
        if text.startswith("http"):
            return text
        return "https://" + text
    return None


def normalize_facebook_url(url: str, host: str) -> Optional[str]:
    if "facebook.com" not in url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    parsed = parse.urlparse(url)
    path = parsed.path or "/"
    query = parsed.query
    return parse.urlunparse(("https", host, path, "", query, ""))


def http_get(url: str, cookie_value: str) -> Tuple[Optional[str], Optional[str]]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": cookie_value,
    }
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = resp.read()
            return data.decode("utf-8", errors="ignore"), None
    except error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except error.URLError as exc:
        return None, f"Lỗi mạng: {exc.reason}"


def extract_title(html: str) -> Optional[str]:
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    if " | Facebook" in title:
        title = title.replace(" | Facebook", "").strip()
    return title or None


LOGIN_HINTS = [
    "login.php",
    "log in",
    "đăng nhập",
    "m.facebook.com/login",
    "checkpoint",
]

PAGE_HINTS = [
    "people like this",
    "followers",
    "người theo dõi",
    "thích trang",
    "đang theo dõi",
    "likes",
]


def is_login_wall(html: str) -> bool:
    lower = html.lower()
    return any(hint in lower for hint in LOGIN_HINTS)


def looks_like_page(html: str) -> bool:
    lower = html.lower()
    return any(hint in lower for hint in PAGE_HINTS)


def detect_verified(html: str) -> Optional[bool]:
    if re.search(r'"is_verified"\s*:\s*true', html):
        return True
    if re.search(r'"is_verified"\s*:\s*false', html):
        return False
    if re.search(r'"verification_status"\s*:\s*"verified"', html):
        return True
    if re.search(r'"verification_status"\s*:\s*"(not_verified|unverified)"', html):
        return False
    if re.search(r"verified badge", html, re.IGNORECASE):
        return True
    if re.search(r"(trang|tài khoản)\s+đã\s+xác\s+minh", html, re.IGNORECASE):
        return True
    return None


@dataclass
class CheckResult:
    status: str
    reason: Optional[str] = None
    page_name: Optional[str] = None
    cookie_ok: Optional[bool] = None


def check_page_with_cookie(page_url: str, cookie_value: str) -> CheckResult:
    candidates = []
    for host in ("m.facebook.com", "mbasic.facebook.com"):
        normalized = normalize_facebook_url(page_url, host)
        if normalized:
            candidates.append(normalized)

    best_unknown: Optional[CheckResult] = None
    for candidate in candidates:
        html, err = http_get(candidate, cookie_value)
        if err:
            best_unknown = CheckResult(
                status="unknown",
                reason=err,
                page_name=None,
                cookie_ok=True,
            )
            continue
        if not html:
            best_unknown = CheckResult(
                status="unknown",
                reason="Không thể tải trang",
                page_name=None,
                cookie_ok=True,
            )
            continue

        page_name = extract_title(html)
        if is_login_wall(html):
            return CheckResult(
                status="unknown",
                reason="Login wall / cookie hết hạn",
                page_name=page_name,
                cookie_ok=False,
            )

        verdict = detect_verified(html)
        if verdict is True:
            return CheckResult(
                status="verified",
                page_name=page_name,
                cookie_ok=True,
            )
        if verdict is False:
            return CheckResult(
                status="not_verified",
                page_name=page_name,
                cookie_ok=True,
            )
        if looks_like_page(html):
            return CheckResult(
                status="not_verified",
                page_name=page_name,
                cookie_ok=True,
            )

        best_unknown = CheckResult(
            status="unknown",
            reason="Không đủ dữ liệu",
            page_name=page_name,
            cookie_ok=True,
        )

    return best_unknown or CheckResult(
        status="unknown",
        reason="Không thể tải trang",
        page_name=None,
        cookie_ok=True,
    )


class StateStore:
    def __init__(self, path: Path, admin_ids: List[int]) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()
        self.state = self._load_state()
        self._ensure_schema()
        self._ensure_admins(admin_ids)
        self._save_locked()

    def _load_state(self) -> Dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_STATE))
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return json.loads(json.dumps(DEFAULT_STATE))
        if not isinstance(data, dict):
            return json.loads(json.dumps(DEFAULT_STATE))
        return data

    def _ensure_schema(self) -> None:
        self.state.setdefault("users", {})
        self.state.setdefault("cookies", {})
        self.state.setdefault("watches", {})

    def _ensure_admins(self, admin_ids: List[int]) -> None:
        now = now_ts()
        for admin_id in admin_ids:
            key = str(admin_id)
            if key not in self.state["users"]:
                self.state["users"][key] = {
                    "role": "admin",
                    "username": None,
                    "name": None,
                    "chat_id": None,
                    "added_at": now,
                }
            else:
                self.state["users"][key]["role"] = "admin"

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self.state, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def save(self) -> None:
        with self.lock:
            self._save_locked()

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            user = self.state["users"].get(str(user_id))
            return dict(user) if user else None

    def is_admin(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return bool(user and user.get("role") == "admin")

    def upsert_user(self, user_id: int, username: Optional[str], name: str) -> None:
        with self.lock:
            user = self.state["users"].get(str(user_id))
            if not user:
                return
            user["username"] = username
            user["name"] = name
            self._save_locked()

    def set_chat_id(self, user_id: int, chat_id: int) -> None:
        with self.lock:
            user = self.state["users"].get(str(user_id))
            if not user:
                return
            user["chat_id"] = chat_id
            self._save_locked()

    def add_user(self, user_id: int, role: str = "user") -> None:
        with self.lock:
            self.state["users"][str(user_id)] = {
                "role": role,
                "username": None,
                "name": None,
                "chat_id": None,
                "added_at": now_ts(),
            }
            self._save_locked()

    def remove_user(self, user_id: int) -> bool:
        with self.lock:
            key = str(user_id)
            if key not in self.state["users"]:
                return False
            del self.state["users"][key]
            self._save_locked()
            return True

    def list_users(self) -> List[Tuple[str, Dict[str, Any]]]:
        with self.lock:
            return sorted(self.state["users"].items(), key=lambda x: x[0])

    def add_cookie(self, name: str, value: str) -> str:
        cookie_id = new_id("CK")
        with self.lock:
            self.state["cookies"][cookie_id] = {
                "name": name,
                "value": value,
                "status": "active",
                "fail_count": 0,
                "last_failed_at": None,
                "last_ok_at": None,
                "last_used_at": None,
            }
            self._save_locked()
        return cookie_id

    def list_cookies(self) -> List[Tuple[str, Dict[str, Any]]]:
        with self.lock:
            return sorted(self.state["cookies"].items(), key=lambda x: x[0])

    def set_cookie_status(self, cookie_id: str, status: str) -> bool:
        with self.lock:
            cookie = self.state["cookies"].get(cookie_id)
            if not cookie:
                return False
            cookie["status"] = status
            self._save_locked()
            return True

    def mark_cookie_dead(self, cookie_id: str) -> None:
        with self.lock:
            cookie = self.state["cookies"].get(cookie_id)
            if not cookie:
                return
            cookie["status"] = "dead"
            cookie["fail_count"] = cookie.get("fail_count", 0) + 1
            cookie["last_failed_at"] = now_ts()
            self._save_locked()

    def mark_cookie_ok(self, cookie_id: str) -> None:
        with self.lock:
            cookie = self.state["cookies"].get(cookie_id)
            if not cookie:
                return
            cookie["last_ok_at"] = now_ts()
            cookie["last_used_at"] = now_ts()
            self._save_locked()

    def get_active_cookies(self) -> List[Tuple[str, Dict[str, Any]]]:
        with self.lock:
            items = [
                (cookie_id, cookie)
                for cookie_id, cookie in self.state["cookies"].items()
                if cookie.get("status") == "active"
            ]
            items.sort(
                key=lambda item: (
                    item[1].get("fail_count", 0),
                    item[1].get("last_used_at") or 0,
                )
            )
            return [(cid, dict(c)) for cid, c in items]

    def add_watch(
        self,
        user_id: int,
        page_url: str,
        page_name: Optional[str],
        deal_owner: str,
        price: str,
    ) -> str:
        watch_id = new_id("W")
        now = now_ts()
        with self.lock:
            self.state["watches"][watch_id] = {
                "id": watch_id,
                "user_id": user_id,
                "page_url": page_url,
                "page_name": page_name,
                "deal_owner": deal_owner,
                "price": price,
                "status": "watching",
                "created_at": now,
                "expires_at": now + WATCH_EXPIRE_DAYS * 24 * 3600,
                "last_checked_at": None,
                "next_check_at": now + random.randint(
                    CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX
                ),
                "verified_at": None,
                "last_result": None,
                "last_error": None,
                "error_notified_at": None,
                "cookie_id": None,
            }
            self._save_locked()
        return watch_id

    def list_watches(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.lock:
            watches = list(self.state["watches"].values())
            if user_id is not None:
                watches = [w for w in watches if w.get("user_id") == user_id]
            watches.sort(key=lambda w: w.get("created_at") or 0, reverse=True)
            return [dict(w) for w in watches]

    def update_watch(self, watch_id: str, updates: Dict[str, Any]) -> bool:
        with self.lock:
            watch = self.state["watches"].get(watch_id)
            if not watch:
                return False
            watch.update(updates)
            self._save_locked()
            return True

    def get_watch(self, watch_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            watch = self.state["watches"].get(watch_id)
            return dict(watch) if watch else None


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.lock = threading.Lock()

    def _request(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.base_url + method,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with self.lock:
            with request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                return json.loads(body)

    def get_updates(self, offset: int, timeout: int = 10) -> List[Dict[str, Any]]:
        payload = {"offset": offset, "timeout": timeout}
        data = self._request("getUpdates", payload)
        if not data.get("ok"):
            return []
        return data.get("result", [])

    def send_message(
        self, chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None
    ) -> None:
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._request("sendMessage", payload)


@dataclass
class CheckTask:
    kind: str
    user_id: int
    chat_id: int
    page_url: str
    watch_id: Optional[str] = None


def build_main_keyboard(is_admin: bool) -> Dict[str, Any]:
    rows = [
        ["⚡ CHECK NHANH", "➕ THEO DÕI"],
        ["🗂️ DANH SÁCH & HỦY"],
    ]
    if is_admin:
        rows.append(["⚙️ QUẢN LÝ HỆ THỐNG"])
    return {"keyboard": rows, "resize_keyboard": True}


def build_admin_keyboard() -> Dict[str, Any]:
    rows = [
        ["👥 QUẢN LÝ USER", "🍪 QUẢN LÝ COOKIES"],
        ["⬅️ QUAY LẠI"],
    ]
    return {"keyboard": rows, "resize_keyboard": True}


def build_user_admin_keyboard() -> Dict[str, Any]:
    rows = [
        ["➕ THÊM USER", "📋 DANH SÁCH USER"],
        ["🗑️ XÓA USER"],
        ["⬅️ QUAY LẠI"],
    ]
    return {"keyboard": rows, "resize_keyboard": True}


def build_cookie_admin_keyboard() -> Dict[str, Any]:
    rows = [
        ["➕ THÊM COOKIE", "📋 DANH SÁCH COOKIE"],
        ["⛔ VÔ HIỆU", "✅ KÍCH HOẠT"],
        ["⬅️ QUAY LẠI"],
    ]
    return {"keyboard": rows, "resize_keyboard": True}


def format_quick_result(
    result: CheckResult, page_name: Optional[str], page_url: str
) -> str:
    name = page_name or result.page_name or "Không rõ"
    time_str = format_time(now_ts())
    if result.status == "verified":
        return (
            "✅ KẾT QUẢ KIỂM TRA\n"
            f"🏷️ Trang: {name}\n"
            "📌 Trạng thái: ĐÃ XÁC MINH (tích xanh)\n"
            f"🕒 Thời gian: {time_str}\n"
            f"🔗 Link: {page_url}"
        )
    if result.status == "not_verified":
        return (
            "❌ KẾT QUẢ KIỂM TRA\n"
            f"🏷️ Trang: {name}\n"
            "📌 Trạng thái: CHƯA XÁC MINH\n"
            f"🕒 Thời gian: {time_str}\n"
            f"🔗 Link: {page_url}"
        )
    reason = result.reason or "Không thể xác minh"
    return (
        "⚠️ KHÔNG THỂ XÁC MINH\n"
        f"🏷️ Trang: {name}\n"
        f"❗ Lý do: {reason}\n"
        "👉 Vui lòng liên hệ admin để xử lý."
    )


def format_watch_started(watch: Dict[str, Any]) -> str:
    name = watch.get("page_name") or "Không rõ"
    return (
        "🟡 THEO DÕI ĐÃ BẬT\n"
        f"👤 Kèo của: {watch.get('deal_owner')}\n"
        f"💰 Giá: {watch.get('price')}\n"
        f"🏷️ Trang: {name}\n"
        "🔁 Tần suất: 30–60 giây/lần\n"
        "📌 Trạng thái hiện tại: CHƯA XÁC MINH\n"
        f"🔗 Link: {watch.get('page_url')}"
    )


def format_watch_verified(watch: Dict[str, Any]) -> str:
    name = watch.get("page_name") or "Không rõ"
    time_str = format_time(watch.get("verified_at", now_ts()))
    return (
        "✅ TRANG ĐÃ CÓ TÍCH XANH\n"
        f"👤 Kèo của: {watch.get('deal_owner')}\n"
        f"💰 Giá: {watch.get('price')}\n"
        f"🏷️ Trang: {name}\n"
        f"🕒 Thời gian phát hiện: {time_str}\n"
        f"🔗 Link: {watch.get('page_url')}\n"
        "👉 Theo dõi đã tự tắt."
    )


def format_watch_expired(watch: Dict[str, Any]) -> str:
    name = watch.get("page_name") or "Không rõ"
    return (
        "⏳ THEO DÕI ĐÃ HẾT HẠN (7 NGÀY)\n"
        f"👤 Kèo của: {watch.get('deal_owner')}\n"
        f"💰 Giá: {watch.get('price')}\n"
        f"🏷️ Trang: {name}\n"
        f"🔗 Link: {watch.get('page_url')}"
    )


def format_watch_list(watches: List[Dict[str, Any]], is_admin: bool) -> str:
    if not watches:
        return "📋 DANH SÁCH THEO DÕI\nKhông có kèo nào."
    lines = [f"📋 DANH SÁCH THEO DÕI ({len(watches)})"]
    for idx, watch in enumerate(watches[:10], start=1):
        name = watch.get("page_name") or "Không rõ"
        next_check = watch.get("next_check_at")
        next_str = format_time(next_check) if next_check else "Chưa rõ"
        lines.append(f"{idx}) 🟡 {name}")
        if is_admin:
            lines.append(f"   👤 User ID: {watch.get('user_id')}")
            lines.append(f"   🍪 Cookie: {watch.get('cookie_id') or 'Auto'}")
        lines.append(f"   👤 Kèo của: {watch.get('deal_owner')}")
        lines.append(f"   💰 Giá: {watch.get('price')}")
        lines.append(f"   🆔 Mã: {watch.get('id')}")
        lines.append(f"   ⏭ Check tiếp: {next_str}")
        lines.append(f"   🔗 {watch.get('page_url')}")
    if len(watches) > 10:
        lines.append("... (còn nữa, hãy lọc hoặc hủy bớt)")
    lines.append("👉 Gõ: HUY <Mã> để hủy theo dõi.")
    return "\n".join(lines)


def format_user_list(users: List[Tuple[str, Dict[str, Any]]]) -> str:
    if not users:
        return "👥 DANH SÁCH USER\nKhông có user."
    lines = ["👥 DANH SÁCH USER"]
    for user_id, info in users:
        role = info.get("role")
        username = info.get("username") or "-"
        name = info.get("name") or "-"
        lines.append(f"• {user_id} | {role} | @{username} | {name}")
    return "\n".join(lines)


def format_cookie_list(cookies: List[Tuple[str, Dict[str, Any]]]) -> str:
    if not cookies:
        return "🍪 DANH SÁCH COOKIE\nKhông có cookie."
    lines = ["🍪 DANH SÁCH COOKIE"]
    for cookie_id, info in cookies:
        status = info.get("status")
        name = info.get("name") or "-"
        fail = info.get("fail_count", 0)
        lines.append(f"• {cookie_id} | {name} | {status} | fail={fail}")
    return "\n".join(lines)


def select_cookie(store: StateStore) -> List[Tuple[str, Dict[str, Any]]]:
    return store.get_active_cookies()


def run_check_with_rotation(
    store: StateStore, page_url: str
) -> Tuple[CheckResult, Optional[str]]:
    cookies = select_cookie(store)
    if not cookies:
        return (
            CheckResult(
                status="unknown",
                reason="Cookie die / hệ thống chưa sẵn sàng",
                cookie_ok=False,
            ),
            None,
        )

    for cookie_id, cookie in cookies:
        result = check_page_with_cookie(page_url, cookie.get("value", ""))
        if result.cookie_ok is False:
            store.mark_cookie_dead(cookie_id)
            continue
        store.mark_cookie_ok(cookie_id)
        return result, cookie_id
    return (
        CheckResult(
            status="unknown",
            reason="Cookie die / hệ thống chưa sẵn sàng",
            cookie_ok=False,
        ),
        None,
    )


def check_worker(
    store: StateStore,
    client: TelegramClient,
    task_queue: queue.Queue,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            task: CheckTask = task_queue.get(timeout=1)
        except queue.Empty:
            continue

        result, cookie_id = run_check_with_rotation(store, task.page_url)

        if task.kind == "quick":
            message = format_quick_result(result, None, task.page_url)
            client.send_message(task.chat_id, message)
            continue

        if task.kind == "watch" and task.watch_id:
            watch = store.get_watch(task.watch_id)
            if not watch:
                continue
            updates = {
                "last_checked_at": now_ts(),
                "last_result": result.status,
                "last_error": result.reason,
                "cookie_id": cookie_id,
            }
            if result.page_name and not watch.get("page_name"):
                updates["page_name"] = result.page_name
            if result.status == "verified":
                updates["status"] = "verified"
                updates["verified_at"] = now_ts()
                store.update_watch(task.watch_id, updates)
                watch.update(updates)
                client.send_message(task.chat_id, format_watch_verified(watch))
                continue

            if result.status == "unknown" and result.cookie_ok is False:
                if not watch.get("error_notified_at"):
                    updates["error_notified_at"] = now_ts()
                    store.update_watch(task.watch_id, updates)
                    watch.update(updates)
                    client.send_message(
                        task.chat_id,
                        "⚠️ KHÔNG THỂ XÁC MINH\n"
                        "Lý do: Cookie die / hệ thống chưa sẵn sàng\n"
                        "👉 Vui lòng liên hệ admin để xử lý.",
                    )
                else:
                    store.update_watch(task.watch_id, updates)
                continue

            store.update_watch(task.watch_id, updates)


def handle_expired_watches(
    store: StateStore, client: TelegramClient
) -> None:
    now = now_ts()
    watches = store.list_watches()
    for watch in watches:
        if watch.get("status") != "watching":
            continue
        expires_at = watch.get("expires_at") or 0
        if now >= expires_at:
            store.update_watch(watch["id"], {"status": "expired"})
            user = store.get_user(watch["user_id"])
            chat_id = user.get("chat_id") if user else None
            if chat_id:
                client.send_message(chat_id, format_watch_expired(watch))


def enqueue_due_watches(
    store: StateStore,
    task_queue: queue.Queue,
) -> None:
    now = now_ts()
    watches = store.list_watches()
    for watch in watches:
        if watch.get("status") != "watching":
            continue
        next_check = watch.get("next_check_at") or 0
        if now < next_check:
            continue
        user = store.get_user(watch["user_id"])
        chat_id = user.get("chat_id") if user else None
        if not chat_id:
            continue
        task_queue.put(
            CheckTask(
                kind="watch",
                user_id=watch["user_id"],
                chat_id=chat_id,
                page_url=watch["page_url"],
                watch_id=watch["id"],
            )
        )
        store.update_watch(
            watch["id"],
            {
                "next_check_at": now + random.randint(
                    CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX
                )
            },
        )


def handle_message(
    message: Dict[str, Any],
    client: TelegramClient,
    store: StateStore,
    sessions: Dict[int, Dict[str, Any]],
    task_queue: queue.Queue,
) -> None:
    user = message.get("from", {})
    user_id = user.get("id")
    chat_id = message.get("chat", {}).get("id")
    if not user_id or not chat_id:
        return
    text = (message.get("text") or "").strip()
    if not text:
        return

    stored_user = store.get_user(user_id)
    if not stored_user:
        client.send_message(
            chat_id,
            "⚠️ Bạn chưa được cấp quyền sử dụng bot.\n👉 Vui lòng liên hệ admin.",
        )
        return

    store.upsert_user(user_id, user.get("username"), user.get("first_name", ""))
    store.set_chat_id(user_id, chat_id)

    normalized = normalize_command(text)

    if normalized in {"/START", "MENU"}:
        client.send_message(
            chat_id,
            f"👋 Xin chào! Bạn đang dùng {BOT_NAME}.",
            reply_markup=build_main_keyboard(store.is_admin(user_id)),
        )
        sessions.pop(user_id, None)
        return

    session = sessions.get(user_id)
    if session:
        step = session.get("step")
        if step == "quick_link":
            url = extract_url(text)
            if not url:
                client.send_message(chat_id, "❗ Vui lòng gửi link page Facebook.")
                return
            task_queue.put(
                CheckTask(
                    kind="quick",
                    user_id=user_id,
                    chat_id=chat_id,
                    page_url=url,
                )
            )
            client.send_message(chat_id, "✅ Đã nhận link, đang kiểm tra...")
            sessions.pop(user_id, None)
            return
        if step == "watch_link":
            url = extract_url(text)
            if not url:
                client.send_message(chat_id, "❗ Vui lòng gửi link page Facebook.")
                return
            session["page_url"] = url
            session["step"] = "watch_owner"
            client.send_message(chat_id, "👤 Kèo của ai vậy bạn?")
            return
        if step == "watch_owner":
            session["deal_owner"] = text.strip()
            session["step"] = "watch_price"
            client.send_message(chat_id, "💰 Giá bao nhiêu? (vd: 2tr5 / 200k)")
            return
        if step == "watch_price":
            price = text.strip()
            watch_id = store.add_watch(
                user_id=user_id,
                page_url=session["page_url"],
                page_name=None,
                deal_owner=session.get("deal_owner", "-"),
                price=price,
            )
            watch = store.get_watch(watch_id)
            if watch:
                client.send_message(chat_id, format_watch_started(watch))
            sessions.pop(user_id, None)
            return
        if step == "cancel_watch":
            watch_id = text.strip()
            watch = store.get_watch(watch_id)
            if not watch or watch.get("user_id") != user_id:
                client.send_message(chat_id, "❗ Không tìm thấy mã theo dõi.")
                return
            store.update_watch(watch_id, {"status": "cancelled"})
            client.send_message(chat_id, f"✅ Đã hủy theo dõi: {watch_id}")
            sessions.pop(user_id, None)
            return
        if step == "admin_add_user":
            try:
                new_user_id = int(text.strip())
            except ValueError:
                client.send_message(chat_id, "❗ Vui lòng nhập Telegram ID dạng số.")
                return
            store.add_user(new_user_id, role="user")
            client.send_message(chat_id, f"✅ Đã thêm user {new_user_id}.")
            sessions.pop(user_id, None)
            return
        if step == "admin_remove_user":
            try:
                remove_id = int(text.strip())
            except ValueError:
                client.send_message(chat_id, "❗ Vui lòng nhập Telegram ID dạng số.")
                return
            if store.remove_user(remove_id):
                client.send_message(chat_id, f"✅ Đã xóa user {remove_id}.")
            else:
                client.send_message(chat_id, "❗ User không tồn tại.")
            sessions.pop(user_id, None)
            return
        if step == "admin_cookie_name":
            session["cookie_name"] = text.strip()
            session["step"] = "admin_cookie_value"
            client.send_message(chat_id, "🍪 Gửi cookie (raw string) để lưu.")
            return
        if step == "admin_cookie_value":
            cookie_name = session.get("cookie_name", "FB")
            cookie_value = text.strip()
            cookie_id = store.add_cookie(cookie_name, cookie_value)
            client.send_message(chat_id, f"✅ Đã lưu cookie: {cookie_id}")
            sessions.pop(user_id, None)
            return
        if step == "admin_cookie_disable":
            cookie_id = text.strip()
            if store.set_cookie_status(cookie_id, "disabled"):
                client.send_message(chat_id, f"✅ Đã vô hiệu cookie: {cookie_id}")
            else:
                client.send_message(chat_id, "❗ Không tìm thấy cookie.")
            sessions.pop(user_id, None)
            return
        if step == "admin_cookie_enable":
            cookie_id = text.strip()
            if store.set_cookie_status(cookie_id, "active"):
                client.send_message(chat_id, f"✅ Đã kích hoạt cookie: {cookie_id}")
            else:
                client.send_message(chat_id, "❗ Không tìm thấy cookie.")
            sessions.pop(user_id, None)
            return

    if normalized in {"⚡ CHECK NHANH", "CHECK NHANH"}:
        sessions[user_id] = {"step": "quick_link"}
        client.send_message(chat_id, "🔗 Gửi link page Facebook cần kiểm tra.")
        return

    if normalized in {"➕ THEO DÕI", "THEO DOI"}:
        sessions[user_id] = {"step": "watch_link"}
        client.send_message(chat_id, "🔗 Gửi link page Facebook cần theo dõi.")
        return

    if normalized in {"🗂️ DANH SÁCH & HỦY", "DANH SACH & HUY", "DANH SACH"}:
        watches = store.list_watches(user_id)
        client.send_message(chat_id, format_watch_list(watches, False))
        return

    if normalized.startswith("HUY ") or normalized.startswith("HỦY "):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            client.send_message(chat_id, "❗ Vui lòng nhập: HUY <Mã>")
            return
        watch_id = parts[1].strip()
        watch = store.get_watch(watch_id)
        if not watch or watch.get("user_id") != user_id:
            client.send_message(chat_id, "❗ Không tìm thấy mã theo dõi.")
            return
        store.update_watch(watch_id, {"status": "cancelled"})
        client.send_message(chat_id, f"✅ Đã hủy theo dõi: {watch_id}")
        return

    if normalized == "⚙️ QUẢN LÝ HỆ THỐNG":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        client.send_message(
            chat_id, "⚙️ QUẢN LÝ HỆ THỐNG", reply_markup=build_admin_keyboard()
        )
        return

    if normalized == "👥 QUẢN LÝ USER":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        client.send_message(
            chat_id, "👥 QUẢN LÝ USER", reply_markup=build_user_admin_keyboard()
        )
        return

    if normalized == "🍪 QUẢN LÝ COOKIES":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        client.send_message(
            chat_id, "🍪 QUẢN LÝ COOKIES", reply_markup=build_cookie_admin_keyboard()
        )
        return

    if normalized == "⬅️ QUAY LẠI":
        client.send_message(
            chat_id,
            "🔙 Quay lại menu chính.",
            reply_markup=build_main_keyboard(store.is_admin(user_id)),
        )
        return

    if normalized == "➕ THÊM USER":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        sessions[user_id] = {"step": "admin_add_user"}
        client.send_message(chat_id, "👤 Nhập Telegram ID của user cần thêm.")
        return

    if normalized == "🗑️ XÓA USER":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        sessions[user_id] = {"step": "admin_remove_user"}
        client.send_message(chat_id, "🗑️ Nhập Telegram ID cần xóa.")
        return

    if normalized == "📋 DANH SÁCH USER":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        users = store.list_users()
        client.send_message(chat_id, format_user_list(users))
        return

    if normalized == "➕ THÊM COOKIE":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        sessions[user_id] = {"step": "admin_cookie_name"}
        client.send_message(chat_id, "🍪 Đặt tên cho cookie (vd: FB_01).")
        return

    if normalized == "📋 DANH SÁCH COOKIE":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        cookies = store.list_cookies()
        client.send_message(chat_id, format_cookie_list(cookies))
        return

    if normalized == "⛔ VÔ HIỆU":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        sessions[user_id] = {"step": "admin_cookie_disable"}
        client.send_message(chat_id, "⛔ Nhập cookie ID cần vô hiệu.")
        return

    if normalized == "✅ KÍCH HOẠT":
        if not store.is_admin(user_id):
            client.send_message(chat_id, "❗ Bạn không có quyền admin.")
            return
        sessions[user_id] = {"step": "admin_cookie_enable"}
        client.send_message(chat_id, "✅ Nhập cookie ID cần kích hoạt.")
        return

    client.send_message(
        chat_id,
        "❓ Không hiểu lệnh. Gõ /start để xem menu.",
    )


def parse_admin_ids(raw: str) -> List[int]:
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Missing TELEGRAM_BOT_TOKEN environment variable.")
        return 1

    state_path = Path(os.environ.get("BOT_STATE_PATH", str(DEFAULT_STATE_PATH)))
    admin_ids = parse_admin_ids(os.environ.get("BOT_ADMIN_IDS", ""))
    if not admin_ids:
        print("Warning: BOT_ADMIN_IDS is empty. No admin user configured.")

    store = StateStore(state_path, admin_ids)
    client = TelegramClient(token)
    sessions: Dict[int, Dict[str, Any]] = {}
    task_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    worker = threading.Thread(
        target=check_worker, args=(store, client, task_queue, stop_event), daemon=True
    )
    worker.start()

    offset = 0
    print(f"{BOT_NAME} is running...")

    try:
        while True:
            updates = client.get_updates(offset=offset, timeout=10)
            for update in updates:
                offset = max(offset, update.get("update_id", 0) + 1)
                message = update.get("message")
                if message:
                    handle_message(message, client, store, sessions, task_queue)

            handle_expired_watches(store, client)
            enqueue_due_watches(store, task_queue)
    except KeyboardInterrupt:
        stop_event.set()
        print("Stopping bot...")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
