#!/usr/bin/env python3
"""
auto_cookie.py
Headlessly logs into Claude.ai, extracts sessionKey,
writes it back to .env. Called automatically by backup.py on 401.
"""
import logging
import os

from dotenv import load_dotenv, set_key

load_dotenv()
log = logging.getLogger(__name__)
ENV_FILE = ".env"


def refresh_session_key() -> str:
    from playwright.sync_api import sync_playwright

    email = os.getenv("CLAUDE_EMAIL")
    password = os.getenv("CLAUDE_PASSWORD")
    if not email or not password:
        raise ValueError("CLAUDE_EMAIL and CLAUDE_PASSWORD must be set in .env")
    log.info("Launching headless browser to refresh sessionKey...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://claude.ai/login", wait_until="networkidle")
        page.get_by_label("Email address").fill(email)
        page.get_by_role("button", name="Continue with email").click()
        page.wait_for_timeout(1500)
        pwd = page.query_selector('input[type="password"]')
        if pwd:
            pwd.fill(password)
            page.get_by_role("button", name="Log in").click()
        page.wait_for_url("**/claude.ai/**", timeout=15000)
        page.wait_for_load_state("networkidle")
        cookies = ctx.cookies("https://claude.ai")
        session = next((c["value"] for c in cookies if c["name"] == "sessionKey"), None)
        browser.close()
    if not session:
        raise RuntimeError("sessionKey not found. Check credentials or disable 2FA.")
    set_key(ENV_FILE, "CLAUDE_SESSION", session)
    log.info("sessionKey refreshed and saved to .env")
    return session


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    key = refresh_session_key()
    print(f"Done. sessionKey starts with: {key[:20]}...")
