"""LinkedIn outreach automation for investor messaging and connection requests."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from loguru import logger

from browser.engine import BrowserEngine
from llm.client import llm_client
from models.schemas import InvestorLead, InvestorOutreachResult, InvestorOutreachStatus
from utils.helpers import human_delay

from Outreach.startup_profile import STARTUP_PROFILE


MESSAGE_BUTTON_SELECTORS = [
    "button:has-text('Message')",
    "a:has-text('Message')",
    "button[aria-label*='Message']",
    "a[aria-label*='Message']",
]

CONNECT_BUTTON_SELECTORS = [
    "button:has-text('Connect')",
    "button[aria-label*='Connect']",
    "button[aria-label*='Invite']",
]

PENDING_BUTTON_SELECTORS = [
    "button:has-text('Pending')",
    "button[aria-label*='Pending']",
]

MORE_BUTTON_SELECTORS = [
    "button:has-text('More')",
    "button[aria-label*='More actions']",
    "button[aria-label*='More']",
]

CONNECT_MENU_SELECTORS = [
    "div[role='menu'] *:has-text('Connect')",
    "li:has-text('Connect')",
    "button:has-text('Connect')",
    "div[role='button']:has-text('Connect')",
]

SEND_INVITE_SELECTORS = [
    "button:has-text('Send')",
    "button:has-text('Done')",
    "button[aria-label*='Send invitation']",
]

ADD_NOTE_SELECTORS = [
    "button:has-text('Add a note')",
    "button:has-text('Add note')",
]

MESSAGE_INPUT_SELECTORS = [
    ".msg-form__contenteditable[contenteditable='true']",
    "div.msg-form__contenteditable",
    "div[role='textbox'][contenteditable='true']",
    "textarea[name='message']",
    "textarea",
]

SEND_MESSAGE_SELECTORS = [
    "button.msg-form__send-button",
    "button:has-text('Send')",
    "button[aria-label*='Send message']",
    "button[aria-label='Send now']",
]

FIRST_DEGREE_SELECTORS = [
    "span.dist-value:has-text('1st')",
    "span:has-text('1st')",
    "li-icon[type='1st-degree']",
]


class LinkedInInvestorMessenger:
    """Send investor outreach messages or connection requests on LinkedIn."""

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser
        self.startup = STARTUP_PROFILE

    async def reach_out_to_investor(self, investor: InvestorLead) -> InvestorOutreachResult:
        """Open the investor profile and attempt a message or connection request."""
        pitch = await self._build_pitch(investor)
        message_text = pitch["message"]
        connect_note = pitch["connect_note"]

        try:
            logger.info("Opening investor profile: {}", investor.linkedin_profile_url)
            await self.browser.goto(investor.linkedin_profile_url)
            await asyncio.sleep(3)
            await self._dismiss_popups()

            if await self._has_any_selector(PENDING_BUTTON_SELECTORS):
                return InvestorOutreachResult(
                    investor=investor,
                    status=InvestorOutreachStatus.CONNECT_REQUESTED,
                    action_taken="pending_detected",
                    connected_status="pending",
                    notes="Connection request already pending on LinkedIn",
                )

            if await self._has_any_selector(MESSAGE_BUTTON_SELECTORS):
                sent = await self._send_message_flow(message_text)
                if sent:
                    return InvestorOutreachResult(
                        investor=investor,
                        status=InvestorOutreachStatus.MESSAGE_SENT,
                        action_taken="message",
                        connected_status="first_degree",
                        message_text=message_text,
                        notes="Direct LinkedIn message sent",
                        sent_at=datetime.now(),
                    )

            connect_status, connect_notes = await self._connect_flow(connect_note)
            if connect_status == InvestorOutreachStatus.CONNECTED:
                return InvestorOutreachResult(
                    investor=investor,
                    status=InvestorOutreachStatus.CONNECTED,
                    action_taken="connect",
                    connected_status="first_degree",
                    message_text=connect_note,
                    notes=connect_notes,
                    sent_at=datetime.now(),
                )
            if connect_status == InvestorOutreachStatus.CONNECT_REQUESTED:
                return InvestorOutreachResult(
                    investor=investor,
                    status=InvestorOutreachStatus.CONNECT_REQUESTED,
                    action_taken="connect",
                    connected_status="pending",
                    message_text=connect_note,
                    notes=connect_notes,
                    sent_at=datetime.now(),
                )
            if connect_status == InvestorOutreachStatus.ALREADY_CONTACTED:
                return InvestorOutreachResult(
                    investor=investor,
                    status=InvestorOutreachStatus.CONNECT_REQUESTED,
                    action_taken="connect_check",
                    connected_status="pending",
                    notes=connect_notes,
                )

            return InvestorOutreachResult(
                investor=investor,
                status=InvestorOutreachStatus.SKIPPED,
                action_taken="none",
                notes="Neither Message nor Connect action was available on the profile",
            )

        except Exception as e:
            logger.warning("Investor reachout failed for '{}': {}", investor.investor_name, str(e)[:160])
            return InvestorOutreachResult(
                investor=investor,
                status=InvestorOutreachStatus.FAILED,
                action_taken="error",
                message_text=message_text,
                notes=str(e)[:500],
                errors=[str(e)],
            )

    async def _build_pitch(self, investor: InvestorLead) -> dict[str, str]:
        first_name = investor.investor_name.split()[0] if investor.investor_name else "there"
        message = self.startup["outreach_message_template"].format(first_name=first_name)
        connect_note = self.startup["outreach_connect_note_template"].format(first_name=first_name)
        return {
            "message": message[:600],
            "connect_note": connect_note[:300],
        }

    async def _has_any_selector(self, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                locator = self.browser.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _click_any_selector(self, selectors: list[str], timeout: int = 5000) -> bool:
        for selector in selectors:
            try:
                if await self.browser.safe_click(selector, timeout=timeout):
                    await human_delay(0.8, 1.6)
                    return True
            except Exception:
                continue
        return False

    async def _dismiss_popups(self) -> None:
        for selector in (
            "button:has-text('Not now')",
            "button:has-text('Dismiss')",
            "button[aria-label*='Dismiss']",
            "button[aria-label*='Close']",
        ):
            try:
                await self.browser.safe_click(selector, timeout=1500)
            except Exception:
                continue

    async def _send_message_flow(self, message_text: str) -> bool:
        clicked = await self._click_any_selector(MESSAGE_BUTTON_SELECTORS, timeout=4000)
        if not clicked:
            return False

        await human_delay(1.5, 2.5)
        await self._dismiss_popups()
        await self.browser.take_screenshot("vc_message_composer_open")

        input_locator = await self._find_visible_locator(MESSAGE_INPUT_SELECTORS, timeout=15000)
        if input_locator is None:
            logger.debug("Message composer input was not found after clicking Message")
            return False

        typed = await self._type_message(input_locator, message_text)
        if not typed:
            await self._dismiss_popups()
            await human_delay(0.8, 1.5)
            input_locator = await self._find_visible_locator(MESSAGE_INPUT_SELECTORS, timeout=5000)
            if input_locator is not None:
                typed = await self._type_message(input_locator, message_text)

        if not typed:
            logger.debug("Failed to type outreach message into the LinkedIn composer")
            return False

        await human_delay(1, 1.8)
        await self._dismiss_popups()
        await self.browser.take_screenshot("vc_message_before_send")

        sent = await self._click_send_button()
        if not sent:
            await self._dismiss_popups()
            await human_delay(0.6, 1.2)
            sent = await self._click_send_button()

        if not sent:
            logger.debug("Failed to click an enabled send button in the LinkedIn composer")
            await self.browser.take_screenshot("vc_message_send_failed")
            return False

        await human_delay(1, 2)
        await self.browser.take_screenshot("vc_message_sent_result")
        return True

    async def _connect_flow(self, connect_note: str) -> tuple[InvestorOutreachStatus, str]:
        if await self._has_any_selector(PENDING_BUTTON_SELECTORS):
            return InvestorOutreachStatus.ALREADY_CONTACTED, "Connection request already pending"

        connected = await self._click_any_selector(CONNECT_BUTTON_SELECTORS, timeout=3500)
        if not connected:
            more_clicked = await self._click_any_selector(MORE_BUTTON_SELECTORS, timeout=3000)
            if more_clicked:
                connected = await self._click_any_selector(CONNECT_MENU_SELECTORS, timeout=3000)

        if not connected:
            return InvestorOutreachStatus.SKIPPED, "Connect button not available"

        await human_delay(1, 2)

        if await self._click_any_selector(ADD_NOTE_SELECTORS, timeout=3000):
            await self._set_composer_text(connect_note, ["textarea[name='message']", "textarea", "#custom-message"])

        sent = await self._click_any_selector(SEND_INVITE_SELECTORS, timeout=4000)
        if not sent:
            if await self._has_any_selector(PENDING_BUTTON_SELECTORS):
                return InvestorOutreachStatus.CONNECT_REQUESTED, "Connection request appears pending"
            return InvestorOutreachStatus.FAILED, "Could not submit connection request"

        await human_delay(1.5, 2.5)
        if await self._has_any_selector(PENDING_BUTTON_SELECTORS):
            return InvestorOutreachStatus.CONNECT_REQUESTED, "Connection request sent"
        if await self._has_any_selector(MESSAGE_BUTTON_SELECTORS):
            return InvestorOutreachStatus.CONNECTED, "Already connected / first-degree access available"
        return InvestorOutreachStatus.FAILED, "Connection request submission could not be confirmed"

    async def _is_first_degree_connection(self) -> bool:
        if await self._has_any_selector(FIRST_DEGREE_SELECTORS):
            return True

        try:
            main_text = (await self.browser.page.locator("main").first.inner_text()).lower()
            if " 1st" in f" {main_text}" or "1st\n" in main_text:
                return True
        except Exception:
            pass
        return False

    async def _set_composer_text(self, text: str, selectors: list[str]) -> bool:
        js = f"""
(() => {{
  const selectors = {json.dumps(selectors)};
  const text = {json.dumps(text)};
  for (const selector of selectors) {{
    const el = document.querySelector(selector);
    if (!el) continue;
    el.focus();
    if ('value' in el) {{
      el.value = text;
      el.dispatchEvent(new Event('input', {{ bubbles: true }}));
      el.dispatchEvent(new Event('change', {{ bubbles: true }}));
      return true;
    }}
    if (el.isContentEditable) {{
      el.innerHTML = '';
      el.textContent = text;
      el.dispatchEvent(new InputEvent('input', {{ bubbles: true, data: text, inputType: 'insertText' }}));
      return true;
    }}
  }}
  return false;
}})()
"""
        try:
            result = await self.browser.evaluate(js)
            return bool(result)
        except Exception as e:
            logger.debug("Failed to set composer text: {}", str(e)[:120])
            return False

    async def _find_visible_locator(self, selectors: list[str], timeout: int = 8000):
        deadline = asyncio.get_event_loop().time() + (timeout / 1000)
        while asyncio.get_event_loop().time() < deadline:
            for selector in selectors:
                try:
                    group = self.browser.page.locator(selector)
                    count = await group.count()
                    for idx in range(min(count, 5)):
                        locator = group.nth(idx)
                        if await locator.is_visible():
                            return locator
                except Exception:
                    continue
            await asyncio.sleep(0.4)
        return None

    async def _type_message(self, locator, message_text: str) -> bool:
        try:
            await self.browser.human_type(locator, message_text, clear_first=True)
            return True
        except Exception as e:
            logger.debug("Keyboard typing into message composer failed: {}", str(e)[:120])

        selector_candidates = [
            ".msg-form__contenteditable[contenteditable='true']",
            "div.msg-form__contenteditable",
            "div[role='textbox'][contenteditable='true']",
            "textarea",
        ]
        return await self._set_composer_text(message_text, selector_candidates)

    async def _click_send_button(self) -> bool:
        locator = await self._find_visible_locator(SEND_MESSAGE_SELECTORS, timeout=10000)
        if locator is None:
            return False

        try:
            disabled = await locator.get_attribute("disabled")
            aria_disabled = await locator.get_attribute("aria-disabled")
            if disabled is not None or str(aria_disabled).lower() == "true":
                logger.debug("LinkedIn send button is still disabled after typing")
                return False
        except Exception:
            pass

        try:
            await self.browser.human_click(locator)
            return True
        except Exception as e:
            logger.debug("Direct click on send button failed: {}", str(e)[:120])

        for selector in SEND_MESSAGE_SELECTORS:
            try:
                if await self.browser.safe_click(selector, timeout=3000):
                    return True
            except Exception:
                continue
        return False

    async def check_and_message_pending_connection(self, investor: "InvestorLead") -> InvestorOutreachResult:
        """Check if a pending connection was accepted and message them if so."""
        try:
            profile_url = investor.linkedin_profile_url
            logger.info("Checking connection status for {}", investor.investor_name)

            # Navigate to their profile
            await self.browser.goto(profile_url)
            await human_delay(2, 3)
            await self._dismiss_popups()
            await self.browser.take_screenshot("connection_check_profile_loaded")

            if await self._has_any_selector(PENDING_BUTTON_SELECTORS):
                logger.info("Connection still pending for {} (Pending button visible)", investor.investor_name)
                return InvestorOutreachResult(
                    investor=investor,
                    status=InvestorOutreachStatus.CONNECT_REQUESTED,
                    action_taken="connection_check",
                    connected_status="pending",
                    notes="Connection request still pending (Pending button visible)",
                )

            connect_still_available = await self._has_any_selector(CONNECT_BUTTON_SELECTORS)
            is_first_degree = await self._is_first_degree_connection()

            if not is_first_degree and connect_still_available:
                logger.info("Connection still pending for {} (Connect available / not first-degree)", investor.investor_name)
                return InvestorOutreachResult(
                    investor=investor,
                    status=InvestorOutreachStatus.SKIPPED,
                    action_taken="connection_check",
                    connected_status="",
                    notes="No active pending request found (Connect still available / not first-degree)",
                )

            # Do not use Message button alone for acceptance; it can appear for 2nd-degree profiles.
            if not is_first_degree and not await self._has_any_selector(MESSAGE_BUTTON_SELECTORS):
                logger.info("Connection still pending for {}", investor.investor_name)
                return InvestorOutreachResult(
                    investor=investor,
                    status=InvestorOutreachStatus.CONNECT_REQUESTED,
                    action_taken="connection_check",
                    connected_status="pending",
                    notes="Connection request not accepted yet",
                )

            # Connection accepted! Now send the follow-up message
            logger.info("Connection accepted for {}! Sending follow-up message...", investor.investor_name)
            
            # Build the message
            pitch_data = await self._build_pitch(investor)
            message_text = pitch_data["message"]

            # Send the message
            sent = await self._send_message_flow(message_text)
            if sent:
                return InvestorOutreachResult(
                    investor=investor,
                    status=InvestorOutreachStatus.MESSAGE_SENT,
                    action_taken="follow_up_message",
                    connected_status="first_degree",
                    message_text=message_text,
                    notes="Connection accepted and follow-up message sent",
                    sent_at=datetime.now(),
                )

            # Message flow failed
            logger.warning("Failed to send follow-up message to {}", investor.investor_name)
            return InvestorOutreachResult(
                investor=investor,
                status=InvestorOutreachStatus.CONNECTED,
                action_taken="message_failed",
                connected_status="first_degree",
                notes="Connection accepted but follow-up message sending failed",
                sent_at=datetime.now(),
            )

        except Exception as e:
            logger.warning("Connection check failed for '{}': {}", investor.investor_name, str(e)[:160])
            return InvestorOutreachResult(
                investor=investor,
                status=InvestorOutreachStatus.FAILED,
                action_taken="connection_check_error",
                notes=str(e)[:500],
                errors=[str(e)],
            )