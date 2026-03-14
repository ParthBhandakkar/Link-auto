"""
LinkedIn Authentication — handles login, session persistence, and CAPTCHA detection.
"""
from __future__ import annotations

import asyncio
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
        page = self.browser.page

        # Check if already logged in by visiting the feed
        logger.info("Checking if already logged in…")
        await self.browser.goto(LINKEDIN_FEED_URL)
        await asyncio.sleep(3)

        current_url = await self.browser.get_current_url()
        if "/feed" in current_url:
            logger.info("Already logged in!")
            return True

        # Not logged in — go to login page
        logger.info("Not logged in. Navigating to login page…")
        await self.browser.goto(LINKEDIN_LOGIN_URL)
        await asyncio.sleep(2)

        # Check for email and password fields
        try:
            email_field = await self.browser.wait_for_selector("#username", timeout=10000)
            password_field = await self.browser.wait_for_selector("#password", timeout=5000)
        except Exception:
            logger.error("Cannot find login form fields!")
            screenshot = await self.browser.take_screenshot("login_error")
            # Ask LLM to analyze the page
            await self._analyze_login_state(screenshot)
            return False

        # Type email
        logger.info("Typing email…")
        await self.browser.human_type(email_field, settings.linkedin_email)
        await asyncio.sleep(0.5)

        # Type password
        logger.info("Typing password…")
        await self.browser.human_type(password_field, settings.linkedin_password)
        await asyncio.sleep(0.5)

        # Click the sign-in button
        logger.info("Clicking sign-in button…")
        sign_in_btn = self.browser.page.locator('button[type="submit"]').first
        await self.browser.human_click(sign_in_btn)

        # Wait for navigation
        logger.info("Waiting for login to complete…")
        await asyncio.sleep(5)

        # Check result
        current_url = await self.browser.get_current_url()

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
        await asyncio.sleep(5)
        current_url = await self.browser.get_current_url()
        if "/feed" in current_url:
            logger.info("Login successful (delayed)!")
            return True

        logger.error("Login failed. Current URL: {}", current_url)
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
