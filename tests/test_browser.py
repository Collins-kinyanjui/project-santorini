import os
import re
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:18765"


def test_real_browser_guest_booking_and_admin():
    env = os.environ.copy()
    env.update({
        "APP_ENV": "development",
        "DATABASE_URL": "sqlite:///./browser_test_santorini.db",
        "ADMIN_PASSWORD": "browser-test-only-password",
        "SESSION_SECRET": "browser-test-only-secret",
        "PUBLIC_URL": "",
    })
    proc = subprocess.Popen(
        ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "18765"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        import urllib.request
        for _ in range(50):
            try:
                with urllib.request.urlopen(BASE + "/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError(proc.stderr.read().decode()[-3000:])
                time.sleep(.2)
        else:
            raise AssertionError("Test server did not start")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(BASE + "/booking")
            page.locator("#checkin").fill((date.today()+timedelta(days=11)).isoformat())
            page.locator("#checkout").fill((date.today()+timedelta(days=12)).isoformat())
            page.locator("#available button").click()
            page.locator("#availabilityStatus").get_by_text("Single Room: 12 available", exact=False).wait_for(timeout=10000)
            page.locator("#category").select_option("double")
            page.locator("#guest_name").fill("Browser Guest")
            page.locator("#email").fill("browser-guest@example.com")
            page.locator("#phone").fill("0712345678")
            page.locator("#guests").fill("2")

            # Changing the payment method must visibly reveal manual payment instructions.
            page.locator("#payment_method").select_option("mpesa")
            page.locator("#modal").wait_for(state="visible", timeout=5000)
            assert "Alex Waragu Kuriah" in page.locator("#modal").inner_text()
            assert "3340030" in page.locator("#modal").inner_text()
            page.locator("#close").click()
            page.locator("#book button").click()
            page.locator("#receipt").wait_for(state="visible", timeout=10000)
            page.locator("#modal").wait_for(state="visible", timeout=5000)
            code_text = page.locator("#paymentReference").inner_text()
            assert "Booking reference:" in code_text
            assert "Private code:" in code_text
            reference = re.search(r"NS-[0-9A-F]{8}", code_text).group(0)
            assert reference in page.locator("#receiptText").inner_text()
            page.locator("#transaction_code").fill("TXBROWSER123")
            page.locator("#submitCode button").click()
            page.get_by_text("Code received for staff verification").wait_for(timeout=10000)
            assert errors == [], errors

            # Staff sees the same reservation and can review it.
            page.goto(BASE + "/admin")
            page.locator("#password").fill("browser-test-only-password")
            page.locator("#login button[type=submit], #login button.btn").click()
            page.locator("#dashboard").wait_for(state="visible", timeout=10000)
            assert reference in page.locator("#bookings").inner_text()
            assert page.locator("#stkPanel").count()==1
            assert page.locator("#stkBooking").count()==1
            assert page.locator("#stkPhone").count()==1
            assert page.locator("#stkConsent").count()==1
            page.get_by_text("STK is not yet enabled",exact=False).wait_for(timeout=10000)
            assert page.locator("#stkSend").is_disabled()
            assert "TXBROWSER123" in page.locator("#bookings").inner_text()
            assert not errors, errors

            # Homepage dates must automatically trigger availability on the booking page.
            start=(date.today()+timedelta(days=15)).isoformat()
            end=(date.today()+timedelta(days=16)).isoformat()
            page.goto(BASE + "/booking?checkin="+start+"&checkout="+end+"&category=single")
            page.locator("#availabilityStatus").get_by_text("Single Room", exact=False).wait_for(timeout=10000)
            # Pay on arrival should produce a visible booking reference and be visible to staff.
            page.locator("#guest_name").fill("Arrival Test Guest")
            page.locator("#email").fill("arrival-guest@example.com")
            page.locator("#phone").fill("0712000000")
            page.locator("#guests").fill("1")
            page.locator("#payment_method").select_option("arrival")
            page.locator("#book button").click()
            page.locator("#receipt").wait_for(state="visible", timeout=10000)
            text=page.locator("#receiptText").inner_text()
            assert "Private code (keep safe)" in text
            arrival_ref=re.search(r"NS-[0-9A-F]{8}", text).group(0)
            assert "pending staff approval" in text
            page.goto(BASE + "/admin")
            page.locator("#dashboard").wait_for(state="visible", timeout=10000)
            assert arrival_ref in page.locator("#bookings").inner_text()
            assert not errors, errors
            browser.close()
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill()
