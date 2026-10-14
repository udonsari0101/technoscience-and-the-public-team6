"""End-to-end HTTP smoke test against a running AskPhilo instance.

    python scripts/smoke_test.py https://askphilo.kro.kr [--cleanup]

Signs up a throw-away user `zz_smoke_<random>`, walks the main flow
(home → philosopher selection/detail → chat → reopen → history →
delete → settings → logout → login) and prints a pass/fail line per step.
It only sends chat messages, so with AI_MODE=mock it costs nothing; it
refuses to send chat messages when /health reports ai_mode=anthropic
unless --allow-live is given.

--cleanup removes the throw-away user from Firestore afterwards using the
local Admin SDK credentials (FIREBASE_CREDENTIALS_PATH in .env).
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CSRF_META = re.compile(r'name="csrf-token" content="([^"]+)"')
CSRF_FIELD = re.compile(r'name="csrf_token" value="([^"]+)"')


class Smoke:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "AskPhilo-smoke/1.0"
        self.results: list[tuple[str, bool, str]] = []

    def url(self, path: str) -> str:
        return self.base + path

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")
        return ok

    def get(self, path, **kw):
        return self.s.get(self.url(path), timeout=60, allow_redirects=kw.pop("allow_redirects", True), **kw)

    def csrf(self, html: str) -> str:
        m = CSRF_FIELD.search(html) or CSRF_META.search(html)
        return m.group(1) if m else ""


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    base, flags = argv[0], set(argv[1:])
    t = Smoke(base)
    username = f"zz_smoke_{secrets.token_hex(3)}"
    password = secrets.token_urlsafe(12)

    health = t.get("/health")
    info = health.json() if health.ok else {}
    t.check("/health 200", health.status_code == 200, json.dumps(info, ensure_ascii=False))
    if info.get("ai_mode") != "mock" and "--allow-live" not in flags:
        print("ai_mode is not mock; refusing to send chat messages (use --allow-live)")
        return 1

    r = t.get("/")
    t.check("landing", r.ok and "AskPhilo" in r.text)
    for asset in ("/static/css/app.css", "/static/js/app.js", "/static/js/chat.js",
                  "/static/img/logo.svg", "/favicon.ico", "/assets/philosophers.css",
                  "/static/img/philosophers/plato.svg"):
        r = t.get(asset)
        t.check(f"asset {asset}", r.status_code == 200, r.headers.get("Content-Type", ""))
    r = t.get("/home", allow_redirects=False)
    t.check("home requires login", r.status_code == 302 and "/login" in r.headers.get("Location", ""))

    page = t.get("/signup").text
    r = t.s.post(t.url("/signup"), data={"csrf_token": t.csrf(page), "name": "스모크", "username": username,
                                         "password": password, "password_confirm": password}, timeout=60)
    t.check("signup → home", r.ok and "스모크님, 안녕하세요" in r.text, username)
    cookie = next((c for c in t.s.cookies if c.name == "askphilo_session"), None)
    if base.startswith("https"):
        t.check("session cookie Secure+HttpOnly", bool(cookie and cookie.secure and
                                                       cookie.has_nonstandard_attr("HttpOnly")))

    r = t.get("/new")
    t.check("philosopher selection", "누구와 이야기해볼까요?" in r.text and r.text.count("ph-card ") == 4)
    r = t.get("/philosophers/epicurus")
    t.check("philosopher detail", "이런 질문을 던져요" in r.text)
    r = t.get("/chat/aristotle")
    token = t.csrf(r.text)
    t.check("empty chat page", "메시지 입력" in r.text and bool(token))

    hdr = {"X-CSRF-Token": token}
    started = time.perf_counter()
    r = t.s.post(t.url("/api/conversations"), json={"philosopher_id": "aristotle",
                                                    "content": "남친이 답장을 세 시간째 안 해. 나 무시하는 거지?"},
                 headers=hdr, timeout=90)
    data = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
    ok = r.status_code == 201 and data.get("ok")
    t.check("first message creates conversation + reply", bool(ok),
            f"{round((time.perf_counter() - started) * 1000)} ms")
    cid = data.get("conversation_id")
    if not cid:
        return summary(t)
    reply = data["assistant_message"]["content"]
    t.check("reply does not assert partner's intent", "무시하는 거야" not in reply, reply[:60])
    r = t.s.post(t.url(f"/api/conversations/{cid}/messages"),
                 json={"content": "그냥 네가 정해줘. 헤어질까 말까?"}, headers=hdr, timeout=90)
    d2 = r.json()
    t.check("follow-up ordered 2/3", r.ok and d2["user_message"]["index"] == 2
            and d2["assistant_message"]["index"] == 3)
    r = t.get(f"/c/{cid}")
    t.check("reopen conversation", r.ok and r.text.count('data-index="') == 4)
    r = t.get("/history")
    t.check("history lists conversation", f"/c/{cid}" in r.text)
    r = t.get("/home")
    t.check("home recent list", f"/c/{cid}" in r.text)
    r = t.s.post(t.url(f"/api/conversations/{cid}/messages"), json={"content": "x"}, timeout=60)
    t.check("API rejects missing CSRF", r.status_code == 400)
    r = t.s.post(t.url(f"/c/{cid}/delete"), data={"csrf_token": token}, timeout=60)
    t.check("delete conversation", r.ok and f"/c/{cid}" not in r.text)
    t.check("deleted conversation 404", t.get(f"/c/{cid}").status_code == 404)
    r = t.get("/settings")
    t.check("settings", username in r.text and 'value="dark"' in r.text)
    r = t.s.post(t.url("/logout"), data={"csrf_token": t.csrf(r.text)}, timeout=60)
    t.check("logout", "/login" in r.url)
    page = t.get("/login").text
    r = t.s.post(t.url("/login"), data={"csrf_token": t.csrf(page), "username": username.upper(),
                                        "password": password}, timeout=60)
    t.check("login (case-insensitive)", "스모크님, 안녕하세요" in r.text)

    if "--cleanup" in flags:
        cleanup(username)
    return summary(t)


def cleanup(username: str) -> None:
    import os

    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from askphilo.config import resolve_credentials_path
    from askphilo.data.firestore_repo import FirestoreRepository

    creds = resolve_credentials_path(os.environ.get("FIREBASE_CREDENTIALS_PATH", ""))
    repo = FirestoreRepository(str(creds))
    user = repo.get_user_by_username(username)
    if user:
        repo.client.recursive_delete(repo.client.collection("users").document(user["id"]))
    repo.client.collection("username_index").document(username).delete()
    print(f"cleanup: removed {username} from Firestore")


def summary(t: Smoke) -> int:
    failed = [n for n, ok, _ in t.results if not ok]
    print(f"\n{len(t.results) - len(failed)}/{len(t.results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
