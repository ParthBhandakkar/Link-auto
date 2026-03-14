"""LinkedIn referral outreach automation for external-apply job contacts."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from browser.engine import BrowserEngine
from config import BASE_DIR, RESUME_DIR
from models.schemas import ReferralContact, ReferralReachoutResult, ReferralReachoutStatus
from profile import PROFILE
from utils.helpers import human_delay


MESSAGE_BUTTON_SELECTORS = [
    "button:has-text('Message')",
    "a:has-text('Message')",
    "button[aria-label*='Message']",
    "a[aria-label*='Message']",
]

CONNECT_BUTTON_SELECTORS = [
    "main button:has-text('Connect')",
    "main button[aria-label*='Connect'][aria-label*='to connect']",
    "main button[aria-label*='Invite'][aria-label*='connect']",
]

PENDING_BUTTON_SELECTORS = [
    "main button:has-text('Pending')",
    "main button[aria-label*='Pending']",
    "main button[aria-label*='withdraw invitation']",
    "main button[aria-label*='Withdraw invitation']",
]

MORE_BUTTON_SELECTORS = [
    "main button[aria-label*='More actions']",
    "main button[aria-label='More']",
    "main button:has-text('More')",
]

# Selectors for the Connect item INSIDE the More dropdown menu
CONNECT_MENU_SELECTORS = [
    ".artdeco-dropdown__content[aria-hidden='false'] div[role='button'][aria-label*='Invite'][aria-label*='connect']",
    ".artdeco-dropdown__content[aria-hidden='false'] div[role='button']:has-text('Connect')",
    "div[role='menu'] div[role='button'][aria-label*='Invite'][aria-label*='connect']",
    "div[role='menu'] div[role='button']:has-text('Connect')",
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

ATTACH_BUTTON_SELECTORS = [
    "button[aria-label*='Attach']",
    "button[aria-label*='attachment']",
    "button[aria-label*='Add files']",
    "button[aria-label*='Upload file']",
]


class LinkedInReferralMessenger:
    """Send connection requests and referral messages to employees on LinkedIn."""

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser
        self.resume_path = self._find_resume()

    @staticmethod
    def _unwrap_eval_payload(value):
        if isinstance(value, dict) and "result" in value:
            return value.get("result")
        return value

    async def _evaluate_unwrapped(self, expression: str):
        raw = await self.browser.evaluate(expression)
        return self._unwrap_eval_payload(raw)

    async def reach_out_to_contact(self, contact: ReferralContact) -> ReferralReachoutResult:
        """Open the employee profile and attempt a connect or referral message action."""
        connect_note = self._build_connect_note(contact)
        message_text = self._build_referral_message(contact)

        try:
            logger.info("Opening employee profile: {}", contact.linkedin_profile_url)
            await self.browser.goto(contact.linkedin_profile_url)
            await asyncio.sleep(3)
            await self._dismiss_popups()

            if await self._has_any_selector(PENDING_BUTTON_SELECTORS):
                return ReferralReachoutResult(
                    contact=contact,
                    status=ReferralReachoutStatus.CONNECT_REQUESTED,
                    action_taken="pending_detected",
                    connected_status="pending",
                    notes="Connection request already pending on LinkedIn",
                )

            # If already 1st-degree, prefer messaging immediately.
            if await self._is_first_degree_profile() and await self._has_any_selector(MESSAGE_BUTTON_SELECTORS):
                sent, attach_note = await self._send_message_flow(message_text)
                if sent:
                    return ReferralReachoutResult(
                        contact=contact,
                        status=ReferralReachoutStatus.MESSAGE_SENT,
                        action_taken="message",
                        connected_status="first_degree",
                        message_text=message_text,
                        notes=attach_note or "Referral message sent",
                        sent_at=datetime.now(),
                    )

            # Try Connect first (direct button or via More dropdown).
            # Message button alone is NOT a reliable indicator of 1st-degree connection —
            # LinkedIn shows Message on some 3rd-degree profiles too.
            # Only fall back to messaging when no Connect option exists at all.
            connect_available = await self._has_connect_in_profile_header()
            if not connect_available:
                # Check inside More menu
                connect_available = await self._connect_available_in_more()

            if connect_available:
                connect_status, connect_notes = await self._connect_flow(connect_note)
            elif await self._has_any_selector(MESSAGE_BUTTON_SELECTORS) and not await self._is_third_degree_profile():
                # Only message when there is truly no Connect option (already 1st-degree)
                sent, attach_note = await self._send_message_flow(message_text)
                if sent:
                    return ReferralReachoutResult(
                        contact=contact,
                        status=ReferralReachoutStatus.MESSAGE_SENT,
                        action_taken="message",
                        connected_status="first_degree",
                        message_text=message_text,
                        notes=attach_note or "Referral message sent",
                        sent_at=datetime.now(),
                    )
                connect_status, connect_notes = ReferralReachoutStatus.FAILED, "Message send failed"
            else:
                connect_status, connect_notes = await self._connect_flow(connect_note)

            return ReferralReachoutResult(
                contact=contact,
                status=connect_status,
                action_taken="connect" if connect_status != ReferralReachoutStatus.SKIPPED else "none",
                connected_status=(
                    "first_degree" if connect_status == ReferralReachoutStatus.CONNECTED else
                    "pending" if connect_status == ReferralReachoutStatus.CONNECT_REQUESTED else
                    ""
                ),
                message_text=connect_note,
                notes=connect_notes,
                sent_at=datetime.now() if connect_status in {ReferralReachoutStatus.CONNECTED, ReferralReachoutStatus.CONNECT_REQUESTED} else None,
            )

        except Exception as e:
            logger.warning("Referral reachout failed for '{}': {}", contact.person_name, str(e)[:160])
            return ReferralReachoutResult(
                contact=contact,
                status=ReferralReachoutStatus.FAILED,
                action_taken="error",
                message_text=message_text,
                notes=str(e)[:500],
                errors=[str(e)],
            )

    def _find_resume(self) -> Path | None:
        for directory in (RESUME_DIR, BASE_DIR.parent / "details"):
            if not directory.exists():
                continue
            for ext in ("*.pdf", "*.docx", "*.doc"):
                files = sorted(directory.glob(ext))
                if files:
                    return files[0]
        return None

    def _build_connect_note(self, contact: ReferralContact) -> str:
        first_name = (contact.person_name.split()[0] if contact.person_name else "there").strip()
        note = (
            f"Hi {first_name}, I came across the {contact.job_title} role at {contact.company}. "
            "Your profile stood out and I'd love to connect."
        )
        return note[:300]

    def _build_referral_message(self, contact: ReferralContact) -> str:
        first_name = (contact.person_name.split()[0] if contact.person_name else "there").strip()
        portfolio = PROFILE.get("portfolio_url", "")
        linkedin_url = PROFILE.get("linkedin_url", "")
        github_url = PROFILE.get("github_url", "")
        job_link = contact.job_url or contact.apply_link
        message = (
            f"Hi {first_name}, thanks for connecting. I'm interested in the {contact.job_title} role at {contact.company} "
            "and would really appreciate a referral if you feel my profile is a fit.\n\n"
            f"Job link: {job_link}\n"
            f"Portfolio: {portfolio}\n"
            f"GitHub: {github_url}\n"
            f"LinkedIn: {linkedin_url}\n"
            "I've attached my resume here as well. Thank you!"
        )
        return message[:900]

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

    async def _send_message_flow(self, message_text: str) -> tuple[bool, str]:
        clicked = await self._click_any_selector(MESSAGE_BUTTON_SELECTORS, timeout=4000)
        if not clicked:
            return False, "Message button not available"

        await human_delay(1.5, 2.5)
        input_locator = await self._find_visible_locator(MESSAGE_INPUT_SELECTORS, timeout=15000)
        if input_locator is None:
            return False, "Message composer input not found"

        typed = await self._type_message(input_locator, message_text)
        if not typed:
            return False, "Failed to type referral message"

        attach_note = "Resume attachment skipped"
        attached = await self._attach_resume_if_possible()
        if attached and self.resume_path:
            attach_note = f"Resume attached: {self.resume_path.name}"

        sent = await self._click_send_button()
        if not sent:
            return False, attach_note + "; failed to click send"

        await human_delay(1, 2)
        return True, attach_note

    async def _connect_available_in_more(self) -> bool:
        """Check (without committing) whether a Connect item exists in the More dropdown."""
        try:
            opened = await self._open_profile_more_menu()
            if not opened:
                return False
            found = await self._has_connect_item_in_open_menu()
            await self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true}))")
            await asyncio.sleep(0.4)
            return found
        except Exception:
            return False

    async def _open_profile_more_menu(self) -> bool:
        primary_selector = "main button[aria-label='More actions']"
        try:
            group = self.browser.page.locator(primary_selector)
            count = await group.count()
            for idx in range(min(count, 3)):
                loc = group.nth(idx)
                try:
                    if not await loc.is_visible():
                        continue
                    box = await loc.bounding_box()
                    if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                        x = box["x"] + box["width"] / 2
                        y = box["y"] + box["height"] / 2
                        await self.browser.page.mouse.move(x, y)
                        await self.browser.page.mouse.click(x, y)
                        await asyncio.sleep(0.8)
                        if await self._has_connect_item_in_open_menu():
                            return True
                except Exception:
                    continue
        except Exception:
            pass

        # fallback to previous generic selectors
        clicked = await self._click_any_selector(MORE_BUTTON_SELECTORS, timeout=3500)
        if not clicked:
            return False
        await asyncio.sleep(0.8)
        return await self._has_connect_item_in_open_menu()

    async def _is_third_degree_profile(self) -> bool:
        js = """
(() => {
  const main = document.querySelector('main');
  if (!main) return false;
  const text = (main.innerText || '').slice(0, 1200).toLowerCase();
  return text.includes('3rd degree connection') || /\b3rd\b/.test(text);
})()
"""
        try:
            return bool(await self._evaluate_unwrapped(js))
        except Exception:
            return False

    async def _is_first_degree_profile(self) -> bool:
        js = """
(() => {
  const main = document.querySelector('main');
  if (!main) return false;
  const text = (main.innerText || '').slice(0, 1500).toLowerCase();
  return text.includes('1st degree connection') || /\b1st\b/.test(text);
})()
"""
        try:
            return bool(await self._evaluate_unwrapped(js))
        except Exception:
            return False

    async def _has_connect_in_profile_header(self) -> bool:
        js = """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const controls = Array.from(document.querySelectorAll('main button, main a, main div[role="button"]')).filter(isVisible);
  for (const el of controls) {
    const r = el.getBoundingClientRect();
    if (r.top > 900) continue;
    const text = (el.textContent || '').trim().toLowerCase();
    const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
    const isConnect =
      text === 'connect' ||
      (aria.includes('invite') && aria.includes('connect')) ||
      (aria.includes('connect') && aria.includes('to connect'));
    if (isConnect) return true;
  }
  return false;
})()
"""
        try:
            return bool(await self._evaluate_unwrapped(js))
        except Exception:
            return False

    async def _has_connect_item_in_open_menu(self) -> bool:
        js = """
(() => {
  const menus = Array.from(document.querySelectorAll('.artdeco-dropdown__content, div[role="menu"]'));
  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const openMenus = menus.filter(isVisible);
  for (const menu of openMenus) {
    const items = menu.querySelectorAll('button, [role="menuitem"], li, span, div');
    for (const item of items) {
      const text = (item.textContent || '').trim().toLowerCase();
      if (text === 'connect' || text.startsWith('connect\\n') || text.includes(' connect')) {
        if (isVisible(item)) return true;
      }
    }
  }
  return false;
})()
"""
        try:
            return bool(await self._evaluate_unwrapped(js))
        except Exception:
            return False

    async def _click_connect_in_open_menu(self) -> bool:
        js = """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const menus = Array.from(document.querySelectorAll('.artdeco-dropdown__content, div[role="menu"]')).filter(isVisible);
  for (const menu of menus) {
    const candidates = menu.querySelectorAll('div[role="button"][aria-label*="Invite"][aria-label*="connect"], div[role="button"]');
    for (const el of candidates) {
      if (!isVisible(el)) continue;
      const text = (el.textContent || '').trim().toLowerCase();
      const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
      if (!(text === 'connect' || (aria.includes('invite') && aria.includes('connect')))) continue;
      el.focus();
      el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
      el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
      return true;
    }
  }
  return false;
})()
"""
        try:
            clicked = bool(await self._evaluate_unwrapped(js))
            if not clicked:
                return False
            await asyncio.sleep(1.0)
            if await self._wait_for_pending_state(timeout_seconds=2.5):
                return True
            if await self._has_any_selector(ADD_NOTE_SELECTORS) or await self._has_any_selector(SEND_INVITE_SELECTORS):
                return True
            return not await self._has_connect_item_in_open_menu()
        except Exception:
            return False

    async def _mouse_click_connect_in_open_menu(self) -> bool:
        js = """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const menus = Array.from(document.querySelectorAll('.artdeco-dropdown__content, div[role="menu"]')).filter(isVisible);
  for (const menu of menus) {
    const candidates = menu.querySelectorAll('div[role="button"], button, li, span');
    for (const el of candidates) {
      if (!isVisible(el)) continue;
      const text = (el.textContent || '').trim().toLowerCase();
      const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
      const looksLikeConnect = text === 'connect' || text.startsWith('connect\\n') || (aria.includes('invite') && aria.includes('connect'));
      if (!looksLikeConnect) continue;

      const target = el.closest('div[role="button"], button, li') || el;
      if (!isVisible(target)) continue;
      const r = target.getBoundingClientRect();
      return { x: r.left + (r.width / 2), y: r.top + (r.height / 2) };
    }
  }
  return null;
})()
"""
        try:
            point = await self._evaluate_unwrapped(js)
            if not point or not isinstance(point, dict):
                return False

            x = float(point.get("x", 0))
            y = float(point.get("y", 0))
            if x <= 0 or y <= 0:
                return False

            await self.browser.page.mouse.move(x, y)
            await self.browser.page.mouse.click(x, y)
            await asyncio.sleep(1.0)

            if await self._wait_for_pending_state(timeout_seconds=2.5):
                return True
            if await self._has_any_selector(ADD_NOTE_SELECTORS) or await self._has_any_selector(SEND_INVITE_SELECTORS):
                return True
            return not await self._has_connect_item_in_open_menu()
        except Exception:
            return False

    async def _wait_for_pending_state(self, timeout_seconds: float = 6.0) -> bool:
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            if await self._has_any_selector(PENDING_BUTTON_SELECTORS):
                return True
            await asyncio.sleep(0.5)
        return False

    async def _connect_flow(self, connect_note: str) -> tuple[ReferralReachoutStatus, str]:
        # 1. Try the direct top-level Connect button
        connected = await self._click_any_selector(CONNECT_BUTTON_SELECTORS, timeout=3500)

        if not connected:
            # 2. Open More dropdown and click Connect inside it
            logger.debug("Direct Connect not found — trying More > Connect")
            more_clicked = await self._open_profile_more_menu()
            if more_clicked:
                await asyncio.sleep(1.0)  # wait for dropdown animation
                connected = await self._mouse_click_connect_in_open_menu()
                if not connected:
                    connected = await self._click_connect_in_open_menu()
                if not connected:
                    # Dismiss the open menu before reporting failure
                    await self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true}))")
                    logger.warning("More menu opened but Connect item not clickable")

        if not connected:
            return ReferralReachoutStatus.SKIPPED, "Connect button not available (neither direct nor in More menu)"

        await human_delay(1.0, 1.8)

        if await self._wait_for_pending_state(timeout_seconds=4.0):
            return ReferralReachoutStatus.CONNECT_REQUESTED, "Connection request sent directly"

        if await self._click_any_selector(ADD_NOTE_SELECTORS, timeout=3000):
            await self._set_composer_text(connect_note, ["textarea[name='message']", "textarea", "#custom-message"])

        sent = await self._click_any_selector(SEND_INVITE_SELECTORS, timeout=4000)
        if not sent:
            if await self._has_any_selector(PENDING_BUTTON_SELECTORS):
                return ReferralReachoutStatus.CONNECT_REQUESTED, "Connection request appears pending"
            return ReferralReachoutStatus.FAILED, "Could not submit connection request"

        await human_delay(1.2, 2.0)
        if await self._wait_for_pending_state(timeout_seconds=6.0):
            return ReferralReachoutStatus.CONNECT_REQUESTED, "Connection request sent"
        return ReferralReachoutStatus.FAILED, "Connect flow did not reach pending state"

    async def _attach_resume_if_possible(self) -> bool:
        if not self.resume_path or not self.resume_path.exists():
            return False

        try:
            await self._click_any_selector(ATTACH_BUTTON_SELECTORS, timeout=2000)
        except Exception:
            pass

        try:
            file_inputs = self.browser.page.locator("input[type='file']")
            count = await file_inputs.count()
            for idx in range(count):
                try:
                    await file_inputs.nth(idx).set_input_files(str(self.resume_path))
                    await human_delay(1.5, 2.5)
                    return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug("Resume attachment failed: {}", str(e)[:120])
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
            result = await self._evaluate_unwrapped(js)
            return bool(result)
        except Exception:
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
        except Exception:
            return await self._set_composer_text(message_text, MESSAGE_INPUT_SELECTORS)

    async def _click_send_button(self) -> bool:
        locator = await self._find_visible_locator(SEND_MESSAGE_SELECTORS, timeout=10000)
        if locator is None:
            return False

        try:
            disabled = await locator.get_attribute("disabled")
            aria_disabled = await locator.get_attribute("aria-disabled")
            if disabled is not None or str(aria_disabled).lower() == "true":
                return False
        except Exception:
            pass

        try:
            await self.browser.human_click(locator)
            return True
        except Exception:
            pass

        for selector in SEND_MESSAGE_SELECTORS:
            try:
                if await self.browser.safe_click(selector, timeout=3000):
                    return True
            except Exception:
                continue
        return False
