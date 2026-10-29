"""Headless-browser walkthrough of the UI (run inside the official Playwright image, on the compose network).

    docker run --rm --network supply-chain-copilot_default -v "$PWD/e2e/out:/out" -v "$PWD/e2e/ui_check.py:/ui_check.py:ro" \
        mcr.microsoft.com/playwright/python:v1.48.0-jammy \
        sh -c "pip install -q playwright==1.48.0 && python /ui_check.py http://web:8080 http://mailpit:8025"

Journey: sign up (lands as analyst) -> sign in as analyst -> scenario -> interpret (offline) -> ambiguity switch -> run -> every tab -> order drawer
-> emails (approve is disabled for an analyst) -> explorer -> about -> sign in as approver -> approve & send one email
-> it reaches the Mailpit inbox -> sign in as admin -> promote the new account to approver -> phone width. Writes screenshots to /out and prints console errors, failed requests
and mobile overflow. Exits non-zero if any check fails.
"""

import json
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://web:8080"
MAILPIT = sys.argv[2] if len(sys.argv) > 2 else "http://mailpit:8025"
OUT = "/out"
console_errors: list[str] = []
failed_requests: list[str] = []
checks: dict[str, object] = {}


def shot(page, name):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
    print("screenshot", name)


def sign_in(page, email):
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.get_by_role("button", name=email).click()  # demo account shortcut fills the form
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("link", name="New scenario").wait_for(timeout=30_000)


def sign_out(page):
    page.get_by_role("button", name="Sign out").click()
    page.get_by_role("heading", name="Sign in").wait_for(timeout=10_000)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: failed_requests.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    page.goto(f"{BASE}/", wait_until="networkidle")
    shot(page, "00_sign_in")

    # Self-service sign-up: a new account must land signed in as an analyst.
    new_email = f"e2e-{int(time.time())}@example.com"
    page.get_by_role("button", name="Create one").click()
    page.get_by_label("Full name").fill("Erica Tester")
    page.get_by_label("Email").fill(new_email)
    page.locator('input[autocomplete="new-password"]').first.fill("e2e-password-1")
    page.get_by_label("Confirm password").fill("e2e-password-1")
    shot(page, "00b_sign_up")
    page.get_by_role("button", name="Create account").click()
    page.get_by_role("link", name="New scenario").wait_for(timeout=30_000)
    checks["signup_role"] = page.locator("aside").get_by_text("analyst", exact=True).count() == 1
    sign_out(page)

    sign_in(page, "analyst@example.com")
    checks["analyst_has_no_users_page"] = page.get_by_role("link", name="Users").count() == 0
    shot(page, "01_scenario_empty")

    # Offline interpretation keeps the LLM quota untouched.
    llm_box = page.get_by_label("Use the LLM", exact=False)
    if llm_box.count() and llm_box.is_checked():
        llm_box.uncheck()
    page.get_by_role("button", name="Interpret").click()
    page.get_by_text("2. Check the interpretation").wait_for(timeout=120_000)
    page.wait_for_timeout(1500)
    shot(page, "02_interpretation")

    city = page.get_by_label("(city only)", exact=False)
    if city.count():
        city.first.check()
        page.wait_for_timeout(2500)
        shot(page, "03_ambiguity_city")
        page.get_by_label("state (SP)", exact=False).first.check()
        page.wait_for_timeout(2500)

    page.get_by_role("button", name="Run impact analysis").click()
    page.wait_for_url("**/runs/*", timeout=60_000)
    run_url = page.url
    page.get_by_role("tab", name="Impact summary").wait_for(timeout=240_000)
    page.wait_for_timeout(2500)
    shot(page, "04_run_overview")

    page.get_by_role("tab", name="Orders & decisions").click()
    page.wait_for_timeout(2000)
    shot(page, "05_orders")
    page.locator("tbody tr").first.click()
    page.get_by_text("Reasoning trace").wait_for(timeout=30_000)
    page.wait_for_timeout(1000)
    shot(page, "06_order_drawer")
    page.keyboard.press("Escape")

    page.get_by_role("tab", name="Customer emails").click()
    page.wait_for_timeout(2000)
    checks["analyst_cannot_approve"] = page.get_by_role("button", name="Approve & send", exact=True).is_disabled()
    shot(page, "07_emails_analyst")
    for tab, name in (("Retrieval evidence", "08_evidence"), ("Run details", "09_details")):
        page.get_by_role("tab", name=tab).click()
        page.wait_for_timeout(2000)
        shot(page, name)

    page.goto(f"{BASE}/runs", wait_until="networkidle")
    shot(page, "10_runs")
    page.goto(f"{BASE}/explorer", wait_until="networkidle")
    page.get_by_role("button", name="Search").click()
    page.wait_for_timeout(4000)
    shot(page, "11_explorer")
    page.goto(f"{BASE}/about", wait_until="networkidle")
    shot(page, "12_about")

    # Approver: approve one QA-passing draft and check that it is delivered to the Mailpit inbox.
    sign_out(page)
    sign_in(page, "approver@example.com")
    page.goto(run_url, wait_until="networkidle")
    page.get_by_role("tab", name="Customer emails").click()
    page.get_by_role("button", name="draft", exact=True).click()
    page.wait_for_timeout(1500)
    with page.expect_response(lambda r: r.url.endswith("/approve") and r.request.method == "POST") as approval:
        page.get_by_role("button", name="Approve & send", exact=True).click()
    checks["approve_status"] = approval.value.status
    sent = False  # the approved email leaves the "draft" view; the mail worker moves it to "sent"
    page.get_by_role("button", name="sent", exact=True).click()
    for _ in range(15):  # no reload: the list itself must pick up the delivery
        page.wait_for_timeout(1000)
        if page.get_by_text("Sent to", exact=False).count():
            sent = True
            break
    checks["approved_email_sent"] = sent
    shot(page, "13_emails_sent")
    inbox = page.request.get(f"{MAILPIT}/api/v1/messages").json()
    checks["mailpit_messages"] = inbox.get("messages_count", inbox.get("total", 0))

    # Admin: promote the signed-up account to approver on the Users page.
    sign_out(page)
    sign_in(page, "admin@example.com")
    page.get_by_role("link", name="Users").click()
    role_select = page.get_by_label(f"Role of {new_email}")
    role_select.wait_for(timeout=15_000)
    with page.expect_response(lambda r: "/api/v1/users/" in r.url and r.request.method == "PATCH") as promoted:
        role_select.select_option("approver")
    checks["admin_promote_status"] = promoted.value.status
    page.wait_for_timeout(1000)
    checks["promoted_role"] = role_select.input_value()
    shot(page, "13b_users_admin")

    mobile = ctx.new_page()
    mobile.set_viewport_size({"width": 390, "height": 844})
    overflow = {}
    for path in ["/", "/runs", "/about"]:
        mobile.goto(f"{BASE}{path}", wait_until="networkidle")
        overflow[path] = mobile.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    mobile.goto(f"{BASE}/", wait_until="networkidle")
    mobile.screenshot(path=f"{OUT}/14_mobile_scenario.png", full_page=True)
    browser.close()

result = {"checks": checks, "console_errors": console_errors, "failed_requests": failed_requests,
          "mobile_overflow_px": overflow}
print(json.dumps(result, indent=1))
ok = (checks.get("analyst_cannot_approve") is True and checks.get("approved_email_sent") is True
      and (checks.get("mailpit_messages") or 0) >= 1
      and checks.get("signup_role") is True and checks.get("analyst_has_no_users_page") is True
      and checks.get("promoted_role") == "approver")
sys.exit(1 if console_errors or failed_requests or any(v > 0 for v in overflow.values()) or not ok else 0)
