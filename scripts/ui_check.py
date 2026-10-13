"""Headless browser UI checks (mobile widths, chat JS, theme, dialogs, a11y basics).

Run against a LOCAL server with in-memory storage (it signs up test users):

    pip install playwright && python -m playwright install chromium
    DATA_BACKEND=memory python wsgi.py           # in another terminal
    python scripts/ui_check.py http://127.0.0.1:5000 ./ui-shots

Checks 360/375/390/430 px for horizontal overflow, sticky composer, 44px
touch targets, mobile vs desktop Enter behaviour, typing dots, URL update
after the first message, theme persistence, delete dialog, reduced motion
and JS console errors; writes screenshots to OUT_DIR.
"""

import os
import secrets
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/")
OUT = sys.argv[2]
os.makedirs(OUT, exist_ok=True)
results = []
console_errors = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def overflow(page):
    """Pixels any element sticks out past the configured viewport width.

    Mobile emulation grows the layout viewport to fit overflowing content
    (innerWidth > configured width), so compare against the configured width.
    """
    width = page.viewport_size["width"]
    doc = page.evaluate("document.documentElement.scrollWidth") - width
    worst = page.evaluate("""(w) => Math.max(0, ...[...document.querySelectorAll('body *')]
        .filter(e => e.offsetParent !== null && getComputedStyle(e).position !== 'fixed')
        .map(e => e.getBoundingClientRect().right - w))""", width)
    return max(doc, round(worst))


def shot(page, name, full=False):
    page.screenshot(path=os.path.join(OUT, f"{name}.png"), full_page=full)


with sync_playwright() as p:
    browser = p.chromium.launch()
    mobile = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                 is_mobile=True, has_touch=True, locale="ko-KR")
    page = mobile.new_page()
    page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

    # ---- landing + signup through the real form
    page.goto(BASE + "/")
    shot(page, "01-landing-390")
    page.click("text=시작하기")
    page.fill("#name", "브라우저")
    username = "ui_" + secrets.token_hex(3)
    page.fill("#username", username)
    page.fill("#password", "password123")
    page.fill("#password_confirm", "password123")
    page.click("[data-reveal=password]")
    check("password reveal toggles type", page.get_attribute("#password", "type") == "text")
    shot(page, "02-signup-390")
    page.click("button[type=submit]")
    page.wait_for_url("**/home")
    check("signup lands on home", "안녕하세요, 브라우저님" in page.content())
    shot(page, "03-home-empty-390")

    # ---- selection + bottom sheet
    page.click("text=새 상담 시작")
    page.wait_for_url("**/new")
    shot(page, "04-new-390", full=True)
    page.click("[data-dialog-open=sheet-nietzsche]")
    page.wait_for_selector("#sheet-nietzsche[open]")
    check("detail bottom sheet opens", page.is_visible("#sheet-nietzsche"))
    shot(page, "05-sheet-nietzsche-390")
    page.keyboard.press("Escape")
    page.wait_for_selector("#sheet-nietzsche:not([open])", state="attached")

    # ---- chat via UI (mobile: Enter inserts newline, send via button)
    page.goto(BASE + "/chat/plato")
    shot(page, "06-chat-empty-390")
    page.click("[data-starter] >> nth=0")
    check("starter fills composer", len(page.input_value("#composer-input")) > 5)
    page.focus("#composer-input")
    page.keyboard.press("Enter")
    check("mobile Enter inserts newline (no send)", page.locator(".msg-user").count() == 0)
    page.click(".send-btn")
    page.wait_for_selector("[data-typing]", timeout=3000)
    check("typing dots shown while waiting", True)
    page.wait_for_selector(".msg-assistant:not([data-static]):not([data-typing])", timeout=15000)
    check("assistant reply rendered", page.locator(".msg-assistant:not([data-static]):not([data-typing])").count() == 1)
    check("URL switched to /c/<id>", "/c/" in page.url, page.url)
    long_text = "아주긴단어" * 40 + " https://example.com/" + "x" * 120
    page.fill("#composer-input", long_text)
    check("textarea auto-resizes", page.evaluate("document.getElementById('composer-input').offsetHeight") > 40)
    page.click(".send-btn")
    page.locator(".msg-assistant:not([data-static]):not([data-typing])").nth(1).wait_for(timeout=15000)
    check("long unbroken message does not overflow (390)", overflow(page) <= 0, f"overflow={overflow(page)}")
    shot(page, "07-chat-390")
    chat_url = page.url

    page.goto(BASE + "/chat/epicurus")
    page.fill("#composer-input", "아주 길고 긴 제목이 될 첫 메시지라서 목록에서 잘려야 하는 고민 이야기")
    page.click(".send-btn")
    page.locator(".msg-assistant:not([data-static]):not([data-typing])").first.wait_for(timeout=15000)

    # ---- widths
    for w in (360, 375, 390, 430):
        page.set_viewport_size({"width": w, "height": 800})
        for path, name in (("/home", "home"), ("/new", "new"), (chat_url.replace(BASE, ""), "chat"),
                           ("/settings", "settings"), ("/history", "history")):
            page.goto(BASE + path)
            ov = overflow(page)
            check(f"no horizontal overflow {name} @{w}", ov <= 0, f"{ov}px")
        page.goto(chat_url)
        composer_bottom = page.evaluate("document.querySelector('.composer').getBoundingClientRect().bottom")
        check(f"composer pinned to bottom @{w}", abs(composer_bottom - 800) <= 1, str(composer_bottom))
        if w in (360, 430):
            shot(page, f"08-chat-{w}")

    # ---- touch target sizes
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(chat_url)
    small = page.evaluate("""() => [...document.querySelectorAll('a, button, input, textarea, [role=button]')]
        .filter(e => e.offsetParent !== null && !e.classList.contains('skip-link'))
        .map(e => { const r = e.getBoundingClientRect(); return {t: (e.getAttribute('aria-label') || e.textContent || e.tagName).trim().slice(0,20), w: Math.round(r.width), h: Math.round(r.height)} })
        .filter(r => r.h < 44 && r.w < 44 || (r.h < 36))""")
    check("touch targets >= 44px on chat", not small, str(small[:5]))

    # ---- theme
    page.goto(BASE + "/settings")
    page.check("input[name=theme][value=dark]")
    page.reload()
    check("dark theme persists", page.get_attribute("html", "data-theme") == "dark")
    shot(page, "09-settings-dark-390")
    page.goto(chat_url)
    shot(page, "10-chat-dark-390")
    page.goto(BASE + "/new")
    shot(page, "11-new-dark-390")
    page.goto(BASE + "/settings")
    page.check("input[name=theme][value=system]")
    page.reload()
    check("system theme clears override", page.get_attribute("html", "data-theme") is None)

    # ---- delete via dialog
    page.goto(chat_url)
    page.click("[data-menu-toggle]")
    page.click("text=대화 삭제")
    page.wait_for_selector("#delete-dialog[open]")
    check("delete dialog copy", "이 대화를 삭제할까요?" in page.inner_text("#delete-dialog")
          and "삭제한 대화는 다시 볼 수 없어요." in page.inner_text("#delete-dialog"))
    shot(page, "12-delete-dialog-390")
    page.click("#delete-dialog button[type=submit]")
    page.wait_for_url("**/history")
    check("deleted → history with toast", "대화를 삭제했어요." in page.content())

    # ---- reduced motion respected
    rm = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    rp = rm.new_page()
    rp.goto(BASE + "/")
    dur = rp.evaluate("getComputedStyle(document.querySelector('main')).animationDuration")
    check("prefers-reduced-motion shortens animation", dur in ("1e-06s", "0.000001s", "0s"), dur)
    rm.close()

    # ---- desktop: sidebar + Enter to send / Shift+Enter newline
    desktop = browser.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR")
    dp = desktop.new_page()
    dp.on("pageerror", lambda e: console_errors.append(f"pageerror(desktop): {e}"))
    dp.goto(BASE + "/login")
    dp.fill("#username", username)
    dp.fill("#password", "password123")
    dp.click("button[type=submit]")
    dp.wait_for_url("**/home")
    dp.goto(BASE + "/chat/epicurus")
    check("desktop sidebar visible", dp.is_visible(".chat-sidebar"))
    dp.fill("#composer-input", "첫 줄")
    dp.focus("#composer-input")
    dp.keyboard.press("Shift+Enter")
    dp.keyboard.type("둘째 줄")
    check("Shift+Enter inserts newline", "\n" in dp.input_value("#composer-input"))
    dp.keyboard.press("Enter")
    dp.wait_for_selector(".msg-assistant:not([data-static]):not([data-typing])", timeout=15000)
    check("Enter sends on desktop", dp.locator(".msg-user").count() == 1)
    dp.screenshot(path=os.path.join(OUT, "13-desktop-chat.png"))
    desktop.close()

    browser.close()

check("no JS console errors", not console_errors, "; ".join(console_errors[:5]))
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed; screenshots in {OUT}")
sys.exit(1 if failed else 0)
