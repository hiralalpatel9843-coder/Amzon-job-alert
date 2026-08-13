"""
Checks hiring.amazon.ca for jobs matching a postal code, and emails an alert
the moment jobs appear (it won't re-alert every run while jobs stay listed —
only when the status changes from "no jobs" to "jobs found").

Configure via environment variables (set as GitHub Actions secrets):
  POSTAL_CODE   - e.g. "L8P 3G1"
  EMAIL_USER    - Gmail address used to SEND the alert
  EMAIL_PASS    - Gmail App Password (not your normal password)
  ALERT_TO      - email address to RECEIVE the alert
"""

import os
import re
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

from playwright.sync_api import sync_playwright

SEARCH_URL = "https://hiring.amazon.ca/app#/jobSearch"
STATE_FILE = "state.txt"
NO_JOBS_PHRASE = "no jobs available that match your search"

POSTAL_CODE = os.environ.get("POSTAL_CODE", "L8P 3G1")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
ALERT_TO = os.environ.get("ALERT_TO")


def read_last_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return f.read().strip()
    return "no_jobs"


def write_state(state):
    with open(STATE_FILE, "w") as f:
        f.write(state)


def send_email(subject, body, screenshot_path=None):
    if not (EMAIL_USER and EMAIL_PASS and ALERT_TO):
        print("Email credentials not set — skipping send. Set EMAIL_USER, EMAIL_PASS, ALERT_TO secrets.")
        return
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = ALERT_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if screenshot_path and os.path.exists(screenshot_path):
        with open(screenshot_path, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-Disposition", "attachment", filename="results.png")
            msg.attach(img)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, ALERT_TO, msg.as_string())
    print("Alert email sent.")


def find_and_fill_location(page):
    """Try a handful of reasonable selectors, since the site is a JS app
    without a stable public API. Adjust here if the site's markup changes."""
    candidates = [
        lambda: page.get_by_placeholder(re.compile("postal|zip|location|city", re.I)),
        lambda: page.locator("input[type='text']").first,
        lambda: page.locator("input").first,
    ]
    for get_locator in candidates:
        try:
            loc = get_locator()
            loc.wait_for(timeout=8000)
            loc.click()
            loc.fill("")
            loc.type(POSTAL_CODE, delay=50)
            page.keyboard.press("Enter")
            return True
        except Exception:
            continue
    return False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)

        for text in ["Accept", "Allow", "Decline", "Got it", "Close"]:
            try:
                page.get_by_role("button", name=re.compile(text, re.I)).click(timeout=2000)
            except Exception:
                pass

        filled = find_and_fill_location(page)
        if not filled:
            print("WARNING: could not find/fill the location input — selectors may need updating.")

        page.wait_for_timeout(5000)

        body_text = page.inner_text("body").lower()
        no_jobs = NO_JOBS_PHRASE in body_text

        screenshot_path = "results.png"
        page.screenshot(path=screenshot_path, full_page=True)

        browser.close()

    current_state = "no_jobs" if no_jobs else "jobs_found"
    last_state = read_last_state()

    print(f"Last state: {last_state} | Current state: {current_state}")

    if current_state == "jobs_found" and last_state != "jobs_found":
        send_email(
            subject="🚨 Amazon jobs available near you!",
            body=(
                f"Jobs may now be available for postal code {POSTAL_CODE}.\n\n"
                f"Check now: {SEARCH_URL}\n\n"
                "(Screenshot attached)"
            ),
            screenshot_path=screenshot_path,
        )
    elif current_state == "jobs_found":
        print("Jobs still listed — already alerted, not sending again.")
    else:
        print("No jobs currently — nothing to send.")

    write_state(current_state)


if __name__ == "__main__":
    main()
