"""Automated accessibility audit (axe-core) of the main AskPhilo screens.

    pip install playwright axe-playwright-python && python -m playwright install chromium
    DATA_BACKEND=memory python wsgi.py
    python scripts/a11y_check.py http://127.0.0.1:5000

Audits light and dark themes at 390px. Exit code 1 if any violation of
impact "serious" or "critical" remains (moderate/minor are reported).
"""

from __future__ import annotations

import secrets
import sys

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/")
REPLY = ".msg-assistant:not([data-static]):not([data-typing])"
axe = Axe()
blocking = 0


def audit(page, name):
    global blocking
    result = axe.run(page)
    violations = result.response["violations"]
    serious = [v for v in violations if v.get("impact") in ("serious", "critical")]
    blocking += len(serious)
    status = "PASS" if not serious else "FAIL"
    print(f"{status} {name}: {len(violations)} violation(s)")
    for v in violations:
        nodes = ", ".join(n["target"][0] if isinstance(n["target"][0], str) else str(n["target"][0])
                          for n in v["nodes"][:4])
        print(f"    [{v.get('impact')}] {v['id']}: {v['help']}  → {nodes}")


with sync_playwright() as p:
    browser = p.chromium.launch()
    for scheme in ("light", "dark"):
        # reduced motion: audit the settled page, not a frame of a fade-in animation
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme,
                                  reduced_motion="reduce", is_mobile=True, has_touch=True,
                                  locale="ko-KR")
        page = ctx.new_page()
        page.goto(BASE + "/")
        audit(page, f"[{scheme}] landing")
        page.goto(BASE + "/signup")
        page.click("button[type=submit]")  # empty form → server-side errors
        page.wait_for_load_state()
        audit(page, f"[{scheme}] signup with errors")
        page.goto(BASE + "/login")
        audit(page, f"[{scheme}] login")
        page.goto(BASE + "/signup")
        page.fill("#name", "접근성")
        page.fill("#username", "a11y_" + secrets.token_hex(3))
        page.fill("#password", "password123")
        page.fill("#password_confirm", "password123")
        page.click("button[type=submit]")
        page.wait_for_url("**/home")
        audit(page, f"[{scheme}] home (empty)")
        page.goto(BASE + "/new")
        audit(page, f"[{scheme}] philosopher selection")
        page.click("[data-dialog-open=sheet-plato]")
        page.wait_for_timeout(400)
        audit(page, f"[{scheme}] philosopher sheet")
        page.goto(BASE + "/philosophers/nietzsche")
        audit(page, f"[{scheme}] philosopher detail page")
        page.goto(BASE + "/chat/aristotle")
        audit(page, f"[{scheme}] empty chat")
        page.fill("#composer-input", "같은 문제로 자꾸 싸워")
        page.click(".send-btn")
        page.locator(REPLY).first.wait_for(timeout=15000)
        page.wait_for_timeout(400)
        audit(page, f"[{scheme}] chat with messages")
        page.click("[data-menu-toggle]")
        audit(page, f"[{scheme}] chat menu open")
        page.goto(BASE + "/home")
        audit(page, f"[{scheme}] home (with conversation)")
        page.goto(BASE + "/history")
        audit(page, f"[{scheme}] history")
        page.goto(BASE + "/settings")
        audit(page, f"[{scheme}] settings")
        page.goto(BASE + "/does-not-exist")
        audit(page, f"[{scheme}] 404")
        ctx.close()
    browser.close()

print(f"\nblocking (serious/critical) violations: {blocking}")
sys.exit(1 if blocking else 0)
