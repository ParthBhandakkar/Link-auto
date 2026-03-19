"""
LinkedIn Authentication — handles login, session persistence, and CAPTCHA detection.
"""
from __future__ import annotations

import asyncio
import json
from loguru import logger

from browser.engine import BrowserEngine
from config import settings
from llm.client import llm_client
from llm.prompts import PAGE_STATE_SYSTEM, PAGE_STATE_USER


LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
LINKEDIN_HOME_URL = "https://www.linkedin.com/"


class LinkedInAuth:
    """Manages LinkedIn authentication with human-like interactions."""

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser

    async def login(self) -> bool:
        """
        Log into LinkedIn. Returns True on success.
        Uses persistent browser data, so subsequent runs may already be logged in.
        """
        if not settings.linkedin_email or not settings.linkedin_password:
            logger.error(
                "LinkedIn credentials missing. Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env"
            )
            return False

        # Check if already logged in by visiting the feed and validating logged-in UI
        logger.info("Checking if already logged in…")
        await self.browser.goto(LINKEDIN_FEED_URL)
        if await self._is_already_logged_in(max_checks=4, wait_seconds=2.0):
            logger.info("Already logged in!")
            return True

        # Not logged in — go to login page
        logger.info("Not logged in. Navigating to login page…")
        await self.browser.goto(LINKEDIN_LOGIN_URL, wait_until="networkidle")
        await asyncio.sleep(3)

        # Try multiple strategies to find and fill login form (LinkedIn UI varies)
        if not await self._try_login_form():
            logger.error("Cannot find or fill login form!")
            screenshot = await self.browser.take_screenshot("login_error")
            await self._analyze_login_state(screenshot)
            return False

        logger.info("Waiting for login to complete…")
        try:
            await self.browser.wait_for_url("**/feed", timeout=30000)
            logger.info("Login successful!")
            return True
        except Exception:
            pass
        if await self._is_already_logged_in(max_checks=2, wait_seconds=2.5):
            logger.info("Login successful!")
            return True

        await asyncio.sleep(4)
        current_url = await self.browser.get_current_url()

        # If still on login, form may be filled but button not clicked — try JS submit click
        if "/login" in current_url:
            try:
                clicked = await self.browser.evaluate(
                    "(() => { const btn = document.querySelector('button[type=submit]') || "
                    "document.querySelector('input[type=submit]'); if (btn) { btn.click(); return true; } return false; })()"
                )
                if clicked:
                    logger.info("Retry: clicked submit button via JavaScript")
                    await asyncio.sleep(8)
                    current_url = await self.browser.get_current_url()
            except Exception as e:
                logger.debug("Submit button retry failed: {}", str(e)[:80])

        # Check result (current_url already set above)
        # Handle verification/CAPTCHA
        if "checkpoint" in current_url or "challenge" in current_url:
            logger.warning("LinkedIn security checkpoint detected!")
            return await self._handle_checkpoint()

        if "/feed" in current_url or "mynetwork" in current_url:
            logger.info("Login successful!")
            return True

        # Unknown state — take screenshot and analyze
        logger.warning("Unexpected post-login state: {}", current_url)
        screenshot = await self.browser.take_screenshot("login_unexpected_state")
        page_state = await self._analyze_login_state(screenshot)

        # Check one more time after a delay
        await asyncio.sleep(4)
        current_url = await self.browser.get_current_url()
        if "/feed" in current_url or await self._is_already_logged_in(max_checks=1):
            logger.info("Login successful (delayed)!")
            return True

        logger.error("Login failed. Current URL: {}", current_url)
        return False

    async def _is_already_logged_in(self, max_checks: int = 2, wait_seconds: float = 1.0) -> bool:
        """Check for multiple indicators that LinkedIn session is already authenticated."""
        for _ in range(max_checks):
            # 1) URL-based checks
            current_url = await self.browser.get_current_url()
            if any(path in current_url for path in ("/feed", "/jobs", "/mynetwork", "/in/")):
                return True
            if any(path in current_url for path in ("/login", "/checkpoint", "/challenge", "/uas/login")):
                return False

            # 1b) If a LinkedIn session cookie exists, treat it as authenticated signal.
            if await self._has_linkedin_session_cookie():
                return True

            # 2) UI marker checks that are present only for authenticated users
            logged_in_markers = [
                "nav[aria-label='Global navigation']",
                ".global-nav__me-photo",
                "header img.global-nav__me-photo",
                ".feed-identity-module__actor-count",
                "[data-test-global-nav-link='me']",
                "#ember58",  # common fallback for old/me nav wrappers
            ]
            for marker in logged_in_markers:
                try:
                    if await self.browser.is_element_visible(marker):
                        return True
                except Exception:
                    continue

            # 3) Last fallback: check for login form controls (likely logged out state)
            login_markers = [
                "#username",
                "input[name='session_key']",
                "input[id='username']",
            ]
            for marker in login_markers:
                try:
                    if await self.browser.is_element_visible(marker):
                        return False
                except Exception:
                    continue
            await asyncio.sleep(wait_seconds)

        return False

    async def _has_linkedin_session_cookie(self) -> bool:
        """Check for known LinkedIn auth/session cookies in browser storage."""
        try:
            cookies_payload = await self.browser._run_json(["cookies"])
            payload = cookies_payload.get("data", cookies_payload)
            if isinstance(payload, dict) and "cookies" in payload:
                cookies = payload.get("cookies", [])
            else:
                cookies = payload if isinstance(payload, list) else []

            if not isinstance(cookies, list):
                return False

            auth_cookie_names = {
                "li_at",
                "liap",
                "li_a",
                "lidc",
                "bcookie",
                "JSESSIONID",
                "li_rm",
            }
            for cookie in cookies:
                if not isinstance(cookie, dict):
                    continue
                name = str(cookie.get("name", "")).strip()
                domain = str(cookie.get("domain", "")).lower()
                if name in auth_cookie_names and "linkedin.com" in domain:
                    logger.debug("Detected LinkedIn auth cookie '{}' for domain '{}'.", name, domain)
                    return True
        except Exception as e:
            logger.debug("Cookie-based login check failed: {}", str(e)[:120])
        return False

    async def _try_login_form(self) -> bool:
        """
        Try multiple strategies to find and fill the LinkedIn login form.
        Returns True if form was filled and submit clicked successfully.
        """
        if await self._is_already_logged_in():
            return True

        # Fast path for LinkedIn UI variations that do not render expected selectors quickly.
        if await self._fill_login_form_via_javascript():
            logger.debug("Login form filled via JavaScript fallback.")
            return True

        # Strategy 1: Standard LinkedIn IDs (#username, #password)
        for email_sel, pass_sel in [
            ("#username", "#password"),
            ('input[name="session_key"]', 'input[name="session_password"]'),
            ("input#username", "input#password"),
        ]:
            try:
                email_field = await self.browser.wait_for_selector(email_sel, timeout=8000)
                password_field = await self.browser.wait_for_selector(pass_sel, timeout=8000)
                logger.info("Found login fields with selectors: {} / {}", email_sel, pass_sel)
                await self.browser.human_type(email_field, settings.linkedin_email)
                await asyncio.sleep(0.5)
                await self.browser.human_type(password_field, settings.linkedin_password)
                await asyncio.sleep(0.5)
                sign_in = self.browser.page.locator('button[type="submit"]').first
                await self.browser.human_click(sign_in)
                return True
            except Exception as e:
                logger.debug("Login strategy {} failed: {}", email_sel, str(e)[:80])
                continue

        # Strategy 2: agent-browser semantic find (label, placeholder, text)
        for label_email, label_pass, btn_text in [
            ("Email or phone", "Password", "Sign in"),
            ("Email", "Password", "Sign in"),
            ("Email or phone number", "Password", "Sign in"),
            ("Email or phone", "Password", "Sign In"),
        ]:
            try:
                await self.browser._run_json(
                    ["find", "label", label_email, "fill", settings.linkedin_email]
                )
                await asyncio.sleep(0.5)
                await self.browser._run_json(
                    ["find", "label", label_pass, "fill", settings.linkedin_password]
                )
                await asyncio.sleep(0.5)
                # Use JS to click submit — find text can match wrong element (e.g. header link)
                clicked = await self.browser.evaluate(
                    "(() => { const btn = document.querySelector('button[type=submit]') || "
                    "document.querySelector('input[type=submit]'); if (btn) { btn.click(); return true; } return false; })()"
                )
                if clicked:
                    await asyncio.sleep(1)
                    return True
                await self.browser._run_json(
                    ["find", "text", btn_text, "click"]
                )
                await asyncio.sleep(1)
                return True
            except Exception as e:
                logger.debug("Find strategy '{}' failed: {}", label_email, str(e)[:80])
                continue

        return False

    async def _fill_login_form_via_javascript(self) -> bool:
        """Fill LinkedIn login fields by querying known selectors directly in browser JS."""
        email_selectors = [
            "#username",
            "input#username",
            "input[name='session_key']",
            "input[autocomplete='username']",
            "input[id*='username']",
            "input[type='text']",
        ]
        password_selectors = [
            "#password",
            "input#password",
            "input[name='session_password']",
            "input[autocomplete='current-password']",
            "input[type='password']",
        ]
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button[data-litmus='sign-in-form__submit-button']",
        ]

        js = f"""
(() => {{
  const email = {json.dumps(settings.linkedin_email)};
  const password = {json.dumps(settings.linkedin_password)};
  const emailSelectors = {json.dumps(email_selectors)};
  const passwordSelectors = {json.dumps(password_selectors)};
  const submitSelectors = {json.dumps(submit_selectors)};

  const isVisible = (el) => {{
    if (!el || !(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }};

  const findField = (selectors) => {{
    for (const selector of selectors) {{
      const el = document.querySelector(selector);
      if (el && isVisible(el)) return el;
    }}
    return null;
  }};

  const fillField = (input, value) => {{
    if (!input) return;
    input.focus();
    input.value = value;
    input.dispatchEvent(new Event("input", {{ bubbles: true }}));
    input.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }};

  const emailField = findField(emailSelectors);
  const passwordField = findField(passwordSelectors);
  if (!emailField || !passwordField) {{
    return {{filled: false, reason: "fields_not_found"}};
  }}

  fillField(emailField, email);
  fillField(passwordField, password);

  let submitBtn = null;
  for (const selector of submitSelectors) {{
    const candidate = document.querySelector(selector);
    if (candidate && isVisible(candidate) && candidate instanceof Element) {{
      submitBtn = candidate;
      break;
    }}
  }}

  if (!submitBtn) {{
    const buttons = Array.from(document.querySelectorAll("button, input[type='submit']"));
    for (const btn of buttons) {{
      if (!isVisible(btn)) continue;
      const text = (btn.textContent || btn.value || "").trim().toLowerCase();
      if (text.includes("sign in") || text.includes("log in") || text.includes("continue")) {{
        submitBtn = btn;
        break;
      }}
    }}
  }}

  if (submitBtn) {{
    submitBtn.click();
    return {{filled: true, clickedSubmit: true}};
  }}

  if (passwordField.closest && passwordField.closest("form")) {{
    const form = passwordField.closest("form");
    if (typeof form.requestSubmit === "function") {{
      form.requestSubmit();
    }} else {{
      form.submit();
    }}
    return {{filled: true, clickedSubmit: false}};
  }}

  return {{filled: false, reason: "submit_not_found"}};
}})()
"""

        result = await self.browser.evaluate(js)
        if isinstance(result, dict) and result.get("filled"):
            logger.debug("Filled login form via JavaScript fallback: {}", result)
            return True
        logger.debug(
            "JavaScript login fill fallback failed: {}",
            result.get("reason") if isinstance(result, dict) else type(result).__name__,
        )
        return False

    async def _handle_checkpoint(self) -> bool:
        """
        Handle LinkedIn's security checkpoint / verification.
        This typically requires human intervention (email/phone code, CAPTCHA).
        We'll wait and poll for the user to complete it.
        """
        logger.warning(
            "Security checkpoint detected! This may require manual verification. "
            "Please complete the verification in the browser window."
        )
        await self.browser.take_screenshot("checkpoint")

        # Poll for up to 120 seconds for the checkpoint to be resolved
        for i in range(24):
            await asyncio.sleep(5)
            current_url = await self.browser.get_current_url()
            if "/feed" in current_url or "mynetwork" in current_url:
                logger.info("Checkpoint resolved! Login successful.")
                return True
            logger.debug("Still waiting for checkpoint resolution… ({}s)", (i + 1) * 5)

        logger.error("Checkpoint timeout — could not complete verification in 120s.")
        return False

    async def _analyze_login_state(self, screenshot_path) -> dict:
        """Use LLM to analyze the current page state from a screenshot."""
        try:
            result = await llm_client.chat_json_with_image(
                system_prompt=PAGE_STATE_SYSTEM,
                user_message=PAGE_STATE_USER.format(action_context="log into LinkedIn"),
                image_path=screenshot_path,
            )
            logger.info("LLM page analysis: {}", result)
            return result
        except Exception as e:
            logger.error("LLM page analysis failed: {}", e)
            return {}

    async def is_logged_in(self) -> bool:
        """Check if the current session is still valid."""
        url = await self.browser.get_current_url()
        if "/feed" in url or "/jobs" in url or "/mynetwork" in url:
            return True
        # Try navigating to feed
        await self.browser.goto(LINKEDIN_FEED_URL)
        await asyncio.sleep(3)
        url = await self.browser.get_current_url()
        return "/feed" in url

    async def ensure_logged_in(self) -> bool:
        """Make sure we're logged in, attempt login if not."""
        if await self.is_logged_in():
            return True
        return await self.login()
