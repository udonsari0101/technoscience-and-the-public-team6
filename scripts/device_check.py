"""Device-matrix UI check: iOS Safari (WebKit) and Android Chrome (Chromium).

Run against a LOCAL server with in-memory storage (it signs up a test user):

    pip install playwright && python -m playwright install chromium webkit
    DATA_BACKEND=memory python wsgi.py
    python scripts/device_check.py http://127.0.0.1:5000 ./device-shots
    python scripts/device_check.py https://askphilo.kro.kr ./device-shots --pace=6

Use a port browsers allow (5000/5070 are fine; 5060/5061 are SIP ports that
Chromium and WebKit refuse). Against a deployment, --pace keeps the chat
under the per-minute limit; delete the printed test user afterwards.

Real on-screen keyboards cannot be driven from a headless browser, so the
keyboard is simulated by shrinking the viewport height (what iOS/Android do
to the visual viewport) after focusing the composer; the check then makes
sure the composer and the newest message are still visible.
"""

from __future__ import annotations

import os
import secrets
import sys

from playwright.sync_api import sync_playwright

import time

args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = args[0].rstrip("/")
OUT = args[1] if len(args) > 1 else "device-shots"
# --pace=N: seconds between chat messages (stay under the per-minute chat limit
# when running against a deployed server).
PACE = next((float(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--pace=")), 0.0)
os.makedirs(OUT, exist_ok=True)

# (label, engine, device descriptor name or custom viewport)
DEVICES = [
    ("iphone-se", "webkit", "iPhone SE"),
    ("iphone-13", "webkit", "iPhone 13"),
    ("iphone-14-pro-max", "webkit", "iPhone 14 Pro Max"),
    ("galaxy-s8-360", "chromium", "Galaxy S8"),
    ("pixel-7", "chromium", "Pixel 7"),
]
REPLY = ".msg-assistant:not([data-static]):not([data-typing])"
results: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def overflow(page) -> int:
    width = page.viewport_size["width"]
    return max(0, round(page.evaluate("""(w) => Math.max(document.documentElement.scrollWidth - w,
        ...[...document.querySelectorAll('body *')]
          .filter(e => e.offsetParent !== null && getComputedStyle(e).position !== 'fixed')
          .map(e => e.getBoundingClientRect().right - w))""", width)))


def in_view(page, selector) -> bool:
    return page.evaluate("""(sel) => { const e = document.querySelector(sel); if (!e) return false;
        const r = e.getBoundingClientRect();
        return r.top >= -1 && r.bottom <= window.innerHeight + 1 && r.height > 0; }""", selector)


username = "dev_" + secrets.token_hex(3)
print(f"DEVICE_CHECK_USER={username}", flush=True)
password = "password123"

with sync_playwright() as p:
    first = True
    for label, engine, device_name in DEVICES:
        device = dict(p.devices[device_name])
        device.pop("default_browser_type", None)
        browser = getattr(p, engine).launch()
        ctx = browser.new_context(**device, locale="ko-KR")
        page = ctx.new_page()
        errors: list[str] = []
        capturing = [False]  # Playwright's WebKit screenshots inject a style our CSP blocks

        def shot(name):
            capturing[0] = True
            page.screenshot(path=os.path.join(OUT, f"{label}-{name}.png"))
            page.wait_for_timeout(100)
            capturing[0] = False

        page.on("pageerror", lambda e: errors.append(str(e)))
        # Safari logs a harmless notice for Chrome's viewport `interactive-widget` key.
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and not capturing[0] and "interactive-widget" not in m.text
                else None)
        w, h = page.viewport_size["width"], page.viewport_size["height"]
        tag = f"[{label} {engine} {w}x{h}]"

        if first:
            page.goto(BASE + "/signup")
            page.fill("#name", "기기")
            page.fill("#username", username)
            page.fill("#password", password)
            page.fill("#password_confirm", password)
            page.click("button[type=submit]")
            first = False
        else:
            page.goto(BASE + "/login")
            page.fill("#username", username)
            page.fill("#password", password)
            page.click("button[type=submit]")
        page.wait_for_url("**/home")
        check(f"{tag} login/signup → home", "님" in page.inner_text(".greeting-title"))

        for path in ("/home", "/new", "/settings", "/history"):
            page.goto(BASE + path)
            check(f"{tag} no overflow {path}", overflow(page) == 0, f"{overflow(page)}px")

        page.goto(BASE + "/new")
        page.wait_for_timeout(400)
        page.click("[data-dialog-open=sheet-epicurus]")
        page.wait_for_timeout(500)
        check(f"{tag} bottom sheet opens", page.evaluate("document.getElementById('sheet-epicurus').open"))
        shot("sheet")
        page.keyboard.press("Escape")

        page.goto(BASE + "/chat/epicurus")
        layout_h = page.evaluate("document.querySelector('.chat-layout').getBoundingClientRect().height")
        check(f"{tag} chat fills viewport (dvh)", abs(layout_h - h) <= 1, f"{layout_h} vs {h}")
        page.fill("#composer-input", "애인 SNS를 자꾸 확인하게 되는 내가 싫어")
        time.sleep(PACE)
        page.click(".send-btn")
        page.locator(REPLY).first.wait_for(timeout=15000)
        for i in range(4):
            page.fill("#composer-input", f"조금 더 이야기할게 {i}. 요즘 연락이 줄어서 불안해")
            time.sleep(PACE)
            page.click(".send-btn")
            page.locator(REPLY).nth(i + 1).wait_for(timeout=15000)
        check(f"{tag} chat round-trips", page.locator(REPLY).count() == 5)
        check(f"{tag} composer at bottom", in_view(page, ".composer"))

        # Simulated on-screen keyboard: focus, then shrink the viewport like iOS/Android do.
        page.focus("#composer-input")
        page.set_viewport_size({"width": w, "height": int(h * 0.55)})
        page.wait_for_timeout(400)
        check(f"{tag} keyboard: composer visible", in_view(page, ".composer"))
        check(f"{tag} keyboard: latest reply visible",
              page.evaluate("""() => { const all = document.querySelectorAll('.msg-assistant');
                  const r = all[all.length - 1].getBoundingClientRect();
                  const c = document.querySelector('.composer').getBoundingClientRect();
                  return r.bottom <= c.top + 2 && r.bottom > 0; }"""))
        check(f"{tag} keyboard: header still visible", in_view(page, ".chat-header"))
        shot("chat-keyboard")
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(300)
        shot("chat")

        check(f"{tag} no JS errors", not errors, "; ".join(errors[:3]))
        ctx.close()
        browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed; screenshots in {OUT}")
sys.exit(1 if failed else 0)
