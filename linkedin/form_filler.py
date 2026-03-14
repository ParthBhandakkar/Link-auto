"""
Intelligent Form Filler — uses the LLM to determine the best value for each
form field based on the applicant's profile and the job context.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import Locator

from browser.engine import BrowserEngine
from config import RESUME_DIR
from llm.client import llm_client
from llm.prompts import (
    FORM_FILLER_SYSTEM,
    FORM_FILLER_USER,
    EXACT_FIELD_ANSWER_SYSTEM,
    EXACT_FIELD_ANSWER_USER,
    ERROR_ANALYZER_SYSTEM,
    ERROR_ANALYZER_USER,
    COVER_LETTER_SYSTEM,
    COVER_LETTER_USER,
    OPEN_QUESTION_SYSTEM,
    OPEN_QUESTION_USER,
)
from models.schemas import FormField, FormPage, Job
from profile import PROFILE


class FormFiller:
    """
    Analyses application forms, uses LLM to determine answers,
    and fills them in with human-like keyboard/mouse interactions.
    """

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser
        self._resume_path = self._find_resume()
        self._resume_text = self._load_resume_text()
        self._answer_cache: dict[str, str] = {}

    def _find_resume(self) -> Optional[Path]:
        """Locate the resume file in the data/resume directory."""
        # First check auto_apply/data/resume
        for ext in ("*.pdf", "*.docx", "*.doc"):
            files = list(RESUME_DIR.glob(ext))
            if files:
                logger.info("Found resume: {}", files[0].name)
                return files[0]

        # Check the details folder (original location)
        details_dir = Path(__file__).resolve().parent.parent.parent / "details"
        for ext in ("*.pdf", "*.docx", "*.doc"):
            files = list(details_dir.glob(ext))
            if files:
                logger.info("Found resume in details/: {}", files[0].name)
                return files[0]

        logger.warning("No resume file found!")
        return None

    def _load_resume_text(self) -> str:
        """Extract a compact text summary from the resume for LLM context."""
        if not self._resume_path or not self._resume_path.exists():
            return ""
        try:
            if self._resume_path.suffix.lower() == ".pdf":
                from PyPDF2 import PdfReader

                reader = PdfReader(str(self._resume_path))
                text_parts: list[str] = []
                for page in reader.pages[:3]:
                    text_parts.append(page.extract_text() or "")
                text = "\n".join(text_parts)
            else:
                text = self._resume_path.read_text(encoding="utf-8", errors="ignore")
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                logger.info("Loaded resume text for LLM context ({} chars)", len(text))
            return text[:4000]
        except Exception as e:
            logger.warning("Could not extract resume text: {}", str(e)[:120])
            return ""

    def _build_reference_context(self) -> str:
        """Build extra context for the LLM from resume and reference links."""
        parts = [
            f"LinkedIn: {PROFILE.get('linkedin_url', '')}",
            f"GitHub: {PROFILE.get('github_url', '')}",
            f"Portfolio: {PROFILE.get('portfolio_url', '')}",
        ]
        if self._resume_text:
            parts.append(f"Resume excerpt: {self._resume_text}")
        return "\n".join(p for p in parts if p.strip())

    # ── Main form-filling pipeline ──────────────────────────────────────
    async def fill_current_form(self, job: Job) -> bool:
        """
        Analyse the current form page, get answers from LLM, fill fields.
        Returns True if all fields were filled successfully.
        """
        page = self.browser.page

        # 1. Extract form fields
        form_fields = await self._extract_form_fields()
        if not form_fields:
            logger.info("No form fields detected on this page.")
            return True

        logger.info("Found {} form fields to fill", len(form_fields))

        # 2. Get answers from LLM
        answers = await self._get_llm_answers(form_fields, job)
        if not answers:
            logger.warning("LLM did not return answers — attempting manual fill")
            answers = self._get_fallback_answers(form_fields)

        # 3. Fill each field
        success_count = 0
        required_count = sum(1 for field in form_fields if field.is_required)
        for field in form_fields:
            try:
                answer = answers.get(field.label, "")
                if not answer and field.label:
                    # Try fuzzy match
                    answer = self._fuzzy_match_answer(field.label, answers)
                if not answer and field.label:
                    answer = await self._get_single_field_answer(field, job)
                if not answer and not field.is_required:
                    continue

                filled = await self._fill_single_field(field, answer)
                if filled:
                    if answer and field.label:
                        self._answer_cache[field.label] = answer
                    success_count += 1
            except Exception as e:
                logger.warning("Error filling field '{}': {}", field.label, str(e)[:100])

        logger.info("Filled {}/{} fields", success_count, len(form_fields))
        if required_count:
            return success_count >= required_count
        return success_count == len(form_fields)

    # ── Form field extraction ───────────────────────────────────────────
    async def _extract_form_fields(self) -> list[FormField]:
        """Extract all visible form fields from the current page/modal."""
        page = self.browser.page
        fields: list[FormField] = []

        # --- Text inputs ---
        text_inputs = page.locator(
            "div.jobs-easy-apply-content input[type='text'], "
            "div.jobs-easy-apply-content input[type='email'], "
            "div.jobs-easy-apply-content input[type='tel'], "
            "div.jobs-easy-apply-content input[type='number'], "
            "div.jobs-easy-apply-content input[type='url'], "
            "div.jobs-easy-apply-content input:not([type]), "
            ".jobs-easy-apply-modal input[type='text'], "
            ".jobs-easy-apply-modal input[type='email'], "
            ".jobs-easy-apply-modal input[type='tel'], "
            ".jobs-easy-apply-modal input[type='number'], "
            ".jobs-easy-apply-modal input:not([type])"
        )
        count = await text_inputs.count()
        for i in range(count):
            el = text_inputs.nth(i)
            if not await el.is_visible():
                continue
            field = await self._parse_input_field(el, "text")
            if field:
                fields.append(field)

        # --- Textareas ---
        textareas = page.locator(
            "div.jobs-easy-apply-content textarea, "
            ".jobs-easy-apply-modal textarea"
        )
        count = await textareas.count()
        for i in range(count):
            el = textareas.nth(i)
            if not await el.is_visible():
                continue
            field = await self._parse_input_field(el, "textarea")
            if field:
                fields.append(field)

        # --- Select dropdowns ---
        selects = page.locator(
            "div.jobs-easy-apply-content select, "
            ".jobs-easy-apply-modal select"
        )
        count = await selects.count()
        for i in range(count):
            el = selects.nth(i)
            if not await el.is_visible():
                continue
            field = await self._parse_select_field(el)
            if field:
                fields.append(field)

        # --- Radio buttons (grouped by name) ---
        radio_groups = await self._extract_radio_groups()
        fields.extend(radio_groups)

        # --- Checkboxes ---
        checkboxes = page.locator(
            "div.jobs-easy-apply-content input[type='checkbox'], "
            ".jobs-easy-apply-modal input[type='checkbox']"
        )
        count = await checkboxes.count()
        for i in range(count):
            el = checkboxes.nth(i)
            if not await el.is_visible():
                continue
            field = await self._parse_checkbox_field(el)
            if field:
                fields.append(field)

        # --- File upload ---
        file_inputs = page.locator(
            "div.jobs-easy-apply-content input[type='file'], "
            ".jobs-easy-apply-modal input[type='file']"
        )
        count = await file_inputs.count()
        for i in range(count):
            el = file_inputs.nth(i)
            field = FormField(
                field_type="file",
                label="Resume/CV Upload",
                name=await el.get_attribute("name") or "",
                is_required=True,
            )
            fields.append(field)

        return fields

    async def _parse_input_field(self, el: Locator, field_type: str) -> Optional[FormField]:
        """Parse an input or textarea element into a FormField."""
        try:
            name = await el.get_attribute("name") or ""
            placeholder = await el.get_attribute("placeholder") or ""
            required = await el.get_attribute("required") is not None
            aria_required = await el.get_attribute("aria-required")
            if aria_required == "true":
                required = True
            value = await el.input_value() if field_type != "textarea" else await el.inner_text()

            # Try to find the label
            label = await self._find_label_for_element(el)
            if not label:
                label = placeholder or name

            return FormField(
                field_type=field_type,
                label=label,
                placeholder=placeholder,
                name=name,
                is_required=required,
                current_value=value or "",
            )
        except Exception:
            return None

    async def _parse_select_field(self, el: Locator) -> Optional[FormField]:
        """Parse a <select> element."""
        try:
            name = await el.get_attribute("name") or ""
            required = await el.get_attribute("required") is not None
            label = await self._find_label_for_element(el)
            if not label:
                label = name

            # Get options
            options_els = el.locator("option")
            count = await options_els.count()
            options = []
            for i in range(count):
                opt = options_els.nth(i)
                text = (await opt.inner_text()).strip()
                value = await opt.get_attribute("value") or ""
                if text and text.lower() not in ("select", "select an option", "--", "choose"):
                    options.append(text)

            return FormField(
                field_type="select",
                label=label,
                name=name,
                options=options,
                is_required=required,
            )
        except Exception:
            return None

    async def _extract_radio_groups(self) -> list[FormField]:
        """Extract radio button groups."""
        page = self.browser.page
        fields: list[FormField] = []
        seen_names: set[str] = set()

        # LinkedIn uses fieldset > legend > radio pattern
        fieldsets = page.locator(
            "div.jobs-easy-apply-content fieldset, "
            ".jobs-easy-apply-modal fieldset"
        )
        count = await fieldsets.count()
        for i in range(count):
            fs = fieldsets.nth(i)
            if not await fs.is_visible():
                continue
            try:
                legend = fs.locator("legend, span[class*='label']").first
                label = ""
                if await legend.count() > 0:
                    label = (await legend.inner_text()).strip()

                radios = fs.locator("input[type='radio']")
                radio_count = await radios.count()
                group_name = ""
                options = []
                for j in range(radio_count):
                    radio = radios.nth(j)
                    if not group_name:
                        group_name = await radio.get_attribute("name") or ""
                    # Get the label for this radio
                    radio_id = await radio.get_attribute("id") or ""
                    if radio_id:
                        lbl = page.locator(f"label[for='{radio_id}']").first
                        if await lbl.count() > 0:
                            options.append((await lbl.inner_text()).strip())
                    else:
                        val = await radio.get_attribute("value") or ""
                        options.append(val)

                if label and options:
                    if group_name:
                        seen_names.add(group_name)
                    fields.append(FormField(
                        field_type="radio",
                        label=label,
                        name=group_name,
                        options=options,
                        is_required=True,
                    ))
            except Exception:
                continue

        # Fallback: generic LinkedIn radio groups not wrapped in fieldset
        radios = page.locator(
            "div.jobs-easy-apply-content input[type='radio'], "
            ".jobs-easy-apply-modal input[type='radio']"
        )
        radio_count = await radios.count()
        grouped: dict[str, list[Locator]] = {}
        for i in range(radio_count):
            radio = radios.nth(i)
            try:
                if not await radio.is_visible():
                    continue
            except Exception:
                continue
            name = await radio.get_attribute("name") or ""
            if not name or name in seen_names:
                continue
            grouped.setdefault(name, []).append(radio)

        for name, group_radios in grouped.items():
            try:
                first_radio = group_radios[0]
                label = ""
                section = first_radio.locator(
                    "xpath=ancestor::div[contains(@class, 'jobs-easy-apply-form-section__grouping') or contains(@class, 'fb-dash-form-element')][1]"
                ).first
                if await section.count() > 0:
                    question_el = section.locator(
                        "legend, label, span[class*='label'], span[aria-hidden='true'], .t-14"
                    ).first
                    if await question_el.count() > 0:
                        label = self._sanitize_label(await question_el.inner_text())

                options: list[str] = []
                for radio in group_radios:
                    radio_id = await radio.get_attribute("id") or ""
                    option_text = ""
                    if radio_id:
                        lbl = page.locator(f"label[for='{radio_id}']").first
                        if await lbl.count() > 0:
                            option_text = self._sanitize_label(await lbl.inner_text())
                    if not option_text:
                        parent_label = radio.locator("xpath=ancestor::label[1]").first
                        if await parent_label.count() > 0:
                            option_text = self._sanitize_label(await parent_label.inner_text())
                    if not option_text:
                        option_text = await radio.get_attribute("value") or ""
                    if option_text:
                        options.append(option_text)

                if label and options:
                    fields.append(FormField(
                        field_type="radio",
                        label=label,
                        name=name,
                        options=options,
                        is_required=True,
                    ))
            except Exception:
                continue

        return fields

    async def _parse_checkbox_field(self, el: Locator) -> Optional[FormField]:
        """Parse a checkbox field."""
        try:
            label = await self._find_label_for_element(el)
            name = await el.get_attribute("name") or ""
            if not label:
                label = name
            return FormField(
                field_type="checkbox",
                label=label,
                name=name,
            )
        except Exception:
            return None

    @staticmethod
    def _sanitize_label(text: str) -> str:
        """Collapse newlines/tabs into a single space, deduplicate repeated words
        (e.g. 'City City' -> 'City'), so labels are safe for CSS selectors."""
        import re as _re
        cleaned = _re.sub(r"[\n\r\t]+", " ", text).strip()
        # Deduplicate fully repeated label (e.g. "City City")
        words = cleaned.split()
        if len(words) == 2 and words[0].lower() == words[1].lower():
            return words[0]
        # Deduplicate when first half == second half
        if len(cleaned) >= 4:
            half = len(cleaned) // 2
            first, second = cleaned[:half].strip(), cleaned[half:].strip()
            if first.lower() == second.lower():
                return first
        return cleaned

    async def _find_label_for_element(self, el: Locator) -> str:
        """Find the label text associated with a form element."""
        page = self.browser.page
        try:
            # Method 1: aria-label
            aria = await el.get_attribute("aria-label")
            if aria:
                return self._sanitize_label(aria)

            # Method 2: id → <label for="id">
            el_id = await el.get_attribute("id")
            if el_id:
                label_el = page.locator(f"label[for='{el_id}']").first
                if await label_el.count() > 0:
                    return self._sanitize_label(await label_el.inner_text())

            # Method 3: Closest parent with label class
            # Go up and find a label-like element
            parent = el.locator("xpath=ancestor::div[contains(@class, 'fb-text-selectable__paragraph') or contains(@class, 'jobs-easy-apply-form-section')]")
            if await parent.count() > 0:
                label_el = parent.first.locator("label, span[class*='label'], .t-14").first
                if await label_el.count() > 0:
                    return self._sanitize_label(await label_el.inner_text())

            # Method 4: Previous sibling label
            prev_label = el.locator("xpath=preceding::label[1]")
            if await prev_label.count() > 0:
                return self._sanitize_label(await prev_label.inner_text())

        except Exception:
            pass
        return ""

    # ── LLM-powered answer generation ───────────────────────────────────
    async def _get_llm_answers(self, fields: list[FormField], job: Job) -> dict[str, str]:
        """Ask the LLM what to fill in each field."""
        # Build fields description
        fields_data = []
        for f in fields:
            fd = {
                "label": f.label,
                "type": f.field_type,
                "required": f.is_required,
                "current_value": f.current_value,
            }
            if f.options:
                fd["options"] = f.options
            if f.placeholder:
                fd["placeholder"] = f.placeholder
            fields_data.append(fd)

        user_msg = FORM_FILLER_USER.format(
            profile_json=json.dumps(PROFILE, indent=2, default=str),
            job_title=job.title,
            company=job.company,
            reference_context=self._build_reference_context(),
            fields_json=json.dumps(fields_data, indent=2),
        )

        try:
            logger.info("LLM form prompt for '{}' at '{}': {}", job.title[:60], job.company[:40], user_msg[:600])
            answers = await llm_client.chat_json(
                system_prompt=FORM_FILLER_SYSTEM,
                user_message=user_msg,
            )
            normalized_answers: dict[str, str] = {}
            for field in fields:
                raw_answer = answers.get(field.label, "")
                if raw_answer:
                    normalized_answers[field.label] = self._normalize_answer_for_field(field, raw_answer)
            for key, value in answers.items():
                if key not in normalized_answers:
                    normalized_answers[key] = value
            logger.info("LLM provided answers for {} fields", len(normalized_answers))
            logger.info("LLM form answers: {}", json.dumps(normalized_answers, ensure_ascii=False)[:1200])
            self._answer_cache.update(normalized_answers)
            return normalized_answers
        except Exception as e:
            logger.error("LLM form filling failed: {}", str(e)[:200])
            return {}

    async def _get_single_field_answer(self, field: FormField, job: Job) -> str:
        """Use the LLM for one unresolved field and cache the answer for later forms."""
        if field.label in self._answer_cache:
            return self._answer_cache[field.label]

        try:
            user_msg = EXACT_FIELD_ANSWER_USER.format(
                profile_json=json.dumps(PROFILE, indent=2, default=str),
                job_title=job.title,
                company=job.company,
                reference_context=self._build_reference_context(),
                field_label=field.label,
                field_type=field.field_type,
                field_required=field.is_required,
                current_value=field.current_value,
                placeholder=field.placeholder,
                options=json.dumps(field.options, ensure_ascii=False),
            )
            logger.info("LLM single-field prompt for '{}': {}", field.label[:80], user_msg[:800])
            raw_answer = await llm_client.chat(
                system_prompt=EXACT_FIELD_ANSWER_SYSTEM,
                user_message=user_msg,
                temperature=0.1,
                max_tokens=300,
            )
            logger.info("LLM single-field raw answer for '{}': {}", field.label[:80], raw_answer[:400])
            answer = self._normalize_answer_for_field(field, raw_answer.strip())
            if answer:
                self._answer_cache[field.label] = answer
                logger.info("Cached single-field LLM answer for '{}'", field.label[:60])
            return answer
        except Exception as e:
            logger.warning("Single-field LLM fallback failed for '{}': {}", field.label[:60], str(e)[:120])
            return ""

    def _get_fallback_answers(self, fields: list[FormField]) -> dict[str, str]:
        """Generate answers without LLM using profile data matching."""
        answers = {}
        for field in fields:
            label_lower = field.label.lower()
            answer = ""

            # Check cache first
            if field.label in self._answer_cache:
                answers[field.label] = self._answer_cache[field.label]
                continue

            # Pattern-matching heuristics
            if any(k in label_lower for k in ("first name", "first_name", "fname")):
                answer = PROFILE["first_name"]
            elif any(k in label_lower for k in ("last name", "last_name", "lname", "surname")):
                answer = PROFILE["last_name"]
            elif any(k in label_lower for k in ("full name", "name")):
                answer = PROFILE["full_name"]
            elif "email" in label_lower:
                answer = PROFILE["email"]
            elif "phone" in label_lower or "mobile" in label_lower:
                answer = PROFILE.get("phone", "")
            elif any(k in label_lower for k in ("city", "location")):
                answer = PROFILE["city"]
            elif "country" in label_lower:
                answer = PROFILE["country"]
            elif any(k in label_lower for k in ("linkedin", "profile url")):
                answer = PROFILE["linkedin_url"]
            elif "github" in label_lower:
                answer = PROFILE["github_url"]
            elif any(k in label_lower for k in ("portfolio", "website", "personal site")):
                answer = PROFILE["portfolio_url"]
            elif any(k in label_lower for k in ("current ctc", "current salary", "current compensation")):
                answer = PROFILE["current_ctc"]
            elif any(k in label_lower for k in ("salary expectation", "salary expectations", "expected ctc", "expected salary", "expected compensation")):
                answer = PROFILE["expected_ctc"]
            elif any(k in label_lower for k in ("salary", "ctc", "compensation", "expected")):
                answer = PROFILE["expected_ctc"]
            elif any(k in label_lower for k in ("notice period", "notice")):
                answer = PROFILE["notice_period"]
            elif any(k in label_lower for k in ("experience", "years")):
                answer = PROFILE["years_of_experience"]
            elif any(k in label_lower for k in ("university", "college", "school")):
                answer = PROFILE["university"]
            elif any(k in label_lower for k in ("degree", "education")):
                answer = PROFILE["degree"]
            elif "gpa" in label_lower or "cgpa" in label_lower:
                answer = PROFILE.get("gpa", "")
            elif any(k in label_lower for k in ("relocate", "relocation")):
                answer = "No"
            elif any(k in label_lower for k in ("authorize", "authorization", "work permit", "visa")):
                answer = PROFILE["common_answers"]["legally_authorized"]
            elif any(k in label_lower for k in ("sponsor", "sponsorship")):
                answer = PROFILE["common_answers"]["sponsorship_required"]
            elif "gender" in label_lower:
                answer = PROFILE["common_answers"]["gender"]
            elif "veteran" in label_lower:
                answer = PROFILE["common_answers"]["veteran_status"]
            elif "disability" in label_lower:
                answer = PROFILE["common_answers"]["disability_status"]

            answer = self._normalize_answer_for_field(field, answer)

            # For select/radio — pick closest option
            if field.field_type in ("select", "radio") and field.options and answer:
                answer = self._pick_closest_option(answer, field.options)

            if answer:
                answers[field.label] = answer

        return answers

    def _pick_closest_option(self, target: str, options: list[str]) -> str:
        """Pick the closest matching option from a list."""
        target_lower = target.lower()
        # Exact match
        for opt in options:
            if opt.lower() == target_lower:
                return opt
        # Contains match
        for opt in options:
            if target_lower in opt.lower() or opt.lower() in target_lower:
                return opt
        # First option as fallback
        if options:
            return options[0]
        return target

    def _fuzzy_match_answer(self, label: str, answers: dict[str, str]) -> str:
        """Try to match a field label to an answer key with fuzzy matching."""
        label_lower = label.lower().strip()
        for key, value in answers.items():
            if key.lower().strip() == label_lower:
                return value
            # Check if one contains the other
            if label_lower in key.lower() or key.lower() in label_lower:
                return value
        return ""

    def _looks_numeric_field(self, field: FormField) -> bool:
        """Return True for compensation / numeric amount fields that require plain digits."""
        label = (field.label or "").lower()
        placeholder = (field.placeholder or "").lower()
        text = f"{label} {placeholder}"
        numeric_terms = (
            "compensation", "salary", "ctc", "inr", "amount", "expected",
            "current salary", "current ctc", "current compensation",
        )
        return any(term in text for term in numeric_terms)

    def _normalize_answer_for_field(self, field: FormField, answer: str) -> str:
        """Normalize answers for specific field types, especially numeric salary fields."""
        if not answer:
            return answer

        if self._looks_numeric_field(field):
            digits = re.sub(r"[^0-9.]", "", answer)
            if digits:
                if digits.count(".") > 1:
                    first = digits.find(".")
                    digits = digits[: first + 1] + digits[first + 1 :].replace(".", "")
                if digits.startswith("."):
                    digits = digits[1:]
                if digits.endswith("."):
                    digits = digits[:-1]
                return digits

        return answer

    # ── Field filling actions ───────────────────────────────────────────
    async def _fill_single_field(self, field: FormField, answer: str) -> bool:
        """Fill a single form field with the given answer."""
        page = self.browser.page

        if field.field_type == "file":
            return await self._handle_file_upload(field)

        if field.field_type == "select":
            return await self._handle_select(field, answer)

        if field.field_type == "radio":
            return await self._handle_radio(field, answer)

        if field.field_type == "checkbox":
            return await self._handle_checkbox(field, answer)

        # Text input or textarea
        return await self._handle_text_input(field, answer)

    async def _handle_text_input(self, field: FormField, answer: str) -> bool:
        """Fill a text input or textarea."""
        page = self.browser.page
        answer = self._normalize_answer_for_field(field, answer)

        # Skip if already has correct value
        if field.current_value and field.current_value.strip() == answer.strip():
            logger.debug("Field '{}' already has correct value", field.label)
            return True

        # Don't overwrite a non-empty field with an empty answer
        if not answer.strip() and field.current_value and field.current_value.strip():
            logger.debug("Keeping existing value for '{}' (answer is empty)", field.label)
            return True

        # Find the element by multiple strategies
        el = None
        strategies = []
        if field.name:
            strategies.append(f"[name='{field.name}']")
        if field.label:
            strategies.append(f"[aria-label='{field.label}']")
            strategies.append(f"[aria-label*='{field.label[:20]}']")

        for sel in strategies:
            try:
                locator = page.locator(sel).first
                if await locator.is_visible():
                    el = locator
                    break
            except Exception:
                continue

        if el is None:
            # Try label-based approach
            if field.label:
                try:
                    label_el = page.locator(f"label:text-is('{field.label}')").first
                    if await label_el.count() > 0:
                        for_attr = await label_el.get_attribute("for")
                        if for_attr:
                            el = page.locator(f"#{for_attr}").first
                except Exception:
                    pass

        if el is None and field.label:
            # Try finding input inside the matching form section
            try:
                sections = page.locator(
                    "div.jobs-easy-apply-form-section__grouping, "
                    "div.fb-dash-form-element"
                )
                sec_count = await sections.count()
                for si in range(sec_count):
                    sec = sections.nth(si)
                    sec_text = await sec.inner_text()
                    if field.label.lower() in sec_text.lower():
                        inp = sec.locator("input, textarea").first
                        if await inp.count() and await inp.is_visible():
                            el = inp
                            break
            except Exception:
                pass

        if el is None:
            logger.debug("Could not locate field '{}'", field.label)
            return False

        await self.browser.scroll_to_element(el)
        await self.browser.human_type(el, answer)
        logger.debug("Filled '{}' = '{}'", field.label, answer[:50])
        return True

    async def _handle_select(self, field: FormField, answer: str) -> bool:
        """Handle a select dropdown (native or custom)."""
        page = self.browser.page

        # Normalise the answer — if the LLM echoed back the question, replace
        # with a sensible default for yes/no-style dropdowns.
        answer = self._fix_yes_no_answer(field, answer)

        try:
            # --- Strategy 1: native <select> by name ---
            sel = None
            if field.name:
                sel = page.locator(f"select[name='{field.name}']").first
                if not await sel.count() or not await sel.is_visible():
                    sel = None

            # --- Strategy 2: native <select> via label ---
            if sel is None and field.label:
                clean = self._sanitize_label(field.label)
                for label_sel in [
                    f"label:text-is('{clean}')",
                    f"label:has-text('{clean[:40]}')",
                ]:
                    try:
                        label_el = page.locator(label_sel).first
                        if await label_el.count():
                            for_attr = await label_el.get_attribute("for")
                            if for_attr:
                                candidate = page.locator(f"#{for_attr}").first
                                if await candidate.count() and await candidate.is_visible():
                                    tag = await candidate.evaluate("el => el.tagName")
                                    if tag and tag.upper() == "SELECT":
                                        sel = candidate
                                        break
                    except Exception:
                        continue

            # --- Strategy 3: find <select> inside the same form-section ---
            if sel is None and field.label:
                sections = page.locator(
                    "div.jobs-easy-apply-form-section__grouping, "
                    "div.fb-dash-form-element"
                )
                sec_count = await sections.count()
                for si in range(sec_count):
                    sec = sections.nth(si)
                    try:
                        sec_text = await sec.inner_text()
                        if field.label[:30] in sec_text:
                            inner_sel = sec.locator("select").first
                            if await inner_sel.count() and await inner_sel.is_visible():
                                sel = inner_sel
                                break
                    except Exception:
                        continue

            # If we found a native <select>, try to pick the option
            if sel:
                await self.browser.scroll_to_element(sel)

                # Read available options from the DOM
                opts = sel.locator("option")
                opt_count = await opts.count()
                opt_texts = []
                for oi in range(opt_count):
                    t = (await opts.nth(oi).inner_text()).strip()
                    if t and t.lower() not in ("select an option", "select", "--", "choose", ""):
                        opt_texts.append(t)

                # Pick the best option
                chosen = self._pick_closest_option(answer, opt_texts) if opt_texts else answer

                try:
                    await sel.select_option(label=chosen)
                    logger.debug("Selected '{}' in '{}'", chosen, field.label[:40])
                    return True
                except Exception:
                    # Fall back to value-based selection
                    for oi in range(opt_count):
                        t = (await opts.nth(oi).inner_text()).strip()
                        if answer.lower() in t.lower() or t.lower() in answer.lower():
                            val = await opts.nth(oi).get_attribute("value") or ""
                            if val:
                                await sel.select_option(value=val)
                                logger.debug("Selected by value '{}' in '{}'", val, field.label[:40])
                                return True

            # Fall through to custom dropdown approach
            return await self._handle_custom_dropdown(field, answer)

        except Exception as e:
            logger.debug("Select fill error for '{}': {}", field.label[:40], str(e)[:100])
            return await self._handle_custom_dropdown(field, answer)

    def _fix_yes_no_answer(self, field: FormField, answer: str) -> str:
        """If the field looks like a yes/no question and the LLM echoed back
        the question text, replace with 'Yes' (safe default for most
        application questions)."""
        label_lower = field.label.lower()
        answer_lower = answer.lower().strip()

        # If answer is very long or matches the question, it's probably wrong
        is_echo = len(answer) > 60 or answer_lower.startswith(label_lower[:20].lower())

        yes_no_starters = (
            "are you", "do you", "have you", "can you", "will you",
            "is your", "would you", "did you", "does your", "were you",
        )
        is_yes_no = any(label_lower.startswith(s) for s in yes_no_starters)

        if is_yes_no and (is_echo or not answer.strip()):
            # Default "Yes" for most application questions
            logger.debug("Auto-answering yes/no question '{}' with 'Yes'", field.label[:40])
            return "Yes"

        if field.options:
            # If answer not in options but "Yes" is, pick "Yes"
            opt_lower = [o.lower() for o in field.options]
            if answer_lower not in opt_lower and "yes" in opt_lower:
                return "Yes"

        return answer

    async def _handle_custom_dropdown(self, field: FormField, answer: str) -> bool:
        """Handle LinkedIn's custom dropdown components."""
        page = self.browser.page
        answer = self._fix_yes_no_answer(field, answer)

        try:
            # Multiple strategies to find and open the dropdown
            label_prefix = self._sanitize_label(field.label)[:25]
            trigger_selectors = [
                f"button[aria-label*='{label_prefix}']",
                f"[data-test-text-selectable-option] >> text='{answer}'",
                f"div[data-test-form-builder-radio-button-form-component] button",
            ]

            # Also try finding the dropdown inside its form section
            sections = page.locator(
                "div.jobs-easy-apply-form-section__grouping, "
                "div.fb-dash-form-element"
            )
            sec_count = await sections.count()
            for si in range(sec_count):
                sec = sections.nth(si)
                try:
                    sec_text = await sec.inner_text()
                    if field.label[:25] in sec_text:
                        # Found the right section — try select inside
                        inner_sel = sec.locator("select").first
                        if await inner_sel.count() and await inner_sel.is_visible():
                            await self.browser.scroll_to_element(inner_sel)
                            try:
                                await inner_sel.select_option(label=answer)
                                logger.debug("Custom dropdown (via section): selected '{}' for '{}'",
                                             answer, field.label[:40])
                                return True
                            except Exception:
                                pass

                        # Try button trigger inside section
                        btn = sec.locator("button[role='combobox'], button[aria-haspopup]").first
                        if await btn.count() and await btn.is_visible():
                            trigger_selectors.insert(0, None)  # placeholder
                            # Click it directly
                            await self.browser.human_click(btn)
                            await asyncio.sleep(0.5)
                            option = page.locator(f"[role='option']:text-is('{answer}')").first
                            if await option.count():
                                await self.browser.human_click(option)
                                return True
                            option = page.locator(f"[role='option']:has-text('{answer}')").first
                            if await option.count():
                                await self.browser.human_click(option)
                                return True
                except Exception:
                    continue

            # Original trigger-based approach
            for sel in trigger_selectors:
                if sel is None:
                    continue
                try:
                    locator = page.locator(sel).first
                    if await locator.count() > 0 and await locator.is_visible():
                        await self.browser.human_click(locator)
                        await asyncio.sleep(0.5)
                        # Look for the option
                        option = page.locator(f"[role='option']:text-is('{answer}')").first
                        if await option.count() > 0:
                            await self.browser.human_click(option)
                            return True
                        # Try partial match
                        option = page.locator(f"[role='option']:has-text('{answer}')").first
                        if await option.count() > 0:
                            await self.browser.human_click(option)
                            return True
                except Exception:
                    continue

            return False
        except Exception as e:
            logger.debug("Custom dropdown error: {}", str(e)[:100])
            return False

    async def _handle_radio(self, field: FormField, answer: str) -> bool:
        """Handle radio button selection."""
        page = self.browser.page
        try:
            answer = self._fix_yes_no_answer(field, answer)
            answer_lower = answer.lower().strip()

            async def click_matching_option(scope: Locator) -> bool:
                radio_inputs = scope.locator("input[type='radio']")
                count = await radio_inputs.count()
                for i in range(count):
                    radio = radio_inputs.nth(i)
                    option_text = ""
                    radio_id = await radio.get_attribute("id") or ""
                    label_el = None
                    if radio_id:
                        candidate = page.locator(f"label[for='{radio_id}']").first
                        if await candidate.count() > 0:
                            label_el = candidate
                            option_text = self._sanitize_label(await candidate.inner_text())
                    if not option_text:
                        candidate = radio.locator("xpath=ancestor::label[1]").first
                        if await candidate.count() > 0:
                            label_el = candidate
                            option_text = self._sanitize_label(await candidate.inner_text())
                    if not option_text:
                        option_text = (await radio.get_attribute("value") or "").strip()

                    option_lower = option_text.lower()
                    if option_lower == answer_lower or answer_lower in option_lower or option_lower in answer_lower:
                        if label_el is not None:
                            await self.browser.scroll_to_element(label_el)
                            await self.browser.human_click(label_el)
                        else:
                            await self.browser.scroll_to_element(radio)
                            await self.browser.human_click(radio)
                        logger.debug("Selected radio '{}' for '{}'", option_text[:40], field.label[:40])
                        return True

                if answer_lower in ("yes", "true", "1"):
                    for i in range(count):
                        radio = radio_inputs.nth(i)
                        radio_id = await radio.get_attribute("id") or ""
                        option_text = ""
                        label_el = None
                        if radio_id:
                            candidate = page.locator(f"label[for='{radio_id}']").first
                            if await candidate.count() > 0:
                                label_el = candidate
                                option_text = self._sanitize_label(await candidate.inner_text())
                        if not option_text:
                            candidate = radio.locator("xpath=ancestor::label[1]").first
                            if await candidate.count() > 0:
                                label_el = candidate
                                option_text = self._sanitize_label(await candidate.inner_text())
                        if "yes" in option_text.lower():
                            if label_el is not None:
                                await self.browser.human_click(label_el)
                            else:
                                await self.browser.human_click(radio)
                            logger.debug("Selected yes-radio for '{}'", field.label[:40])
                            return True
                return False

            # Strategy 1: exact radio group by name
            if field.name:
                by_name_scope = page.locator(f"input[type='radio'][name='{field.name}']").locator("xpath=ancestor::*[self::fieldset or self::div][1]").first
                inputs = page.locator(f"input[type='radio'][name='{field.name}']")
                if await inputs.count() > 0:
                    section = inputs.first.locator(
                        "xpath=ancestor::div[contains(@class, 'jobs-easy-apply-form-section__grouping') or contains(@class, 'fb-dash-form-element')][1]"
                    ).first
                    if await section.count() > 0 and await click_matching_option(section):
                        return True
                    if await click_matching_option(page.locator("body")):
                        return True

            # Strategy 2: locate the matching section by question text
            if field.label:
                sections = page.locator(
                    "div.jobs-easy-apply-form-section__grouping, div.fb-dash-form-element, fieldset"
                )
                sec_count = await sections.count()
                for i in range(sec_count):
                    sec = sections.nth(i)
                    try:
                        sec_text = (await sec.inner_text()).lower()
                    except Exception:
                        continue
                    if field.label.lower()[:30] in sec_text:
                        if await click_matching_option(sec):
                            return True

            # Strategy 3: broad fallback across the page
            if await click_matching_option(page.locator("body")):
                return True

            return False
        except Exception as e:
            logger.debug("Radio fill error: {}", str(e)[:100])
            return False

    async def _handle_checkbox(self, field: FormField, answer: str) -> bool:
        """Handle checkbox toggling."""
        page = self.browser.page
        try:
            should_check = answer.lower() in ("true", "yes", "1", "checked")
            # Find checkbox by name or label
            cb = None
            if field.name:
                cb = page.locator(f"input[type='checkbox'][name='{field.name}']").first
            if cb is None or not await cb.is_visible():
                if field.label:
                    label_el = page.locator(f"label:text-is('{field.label}')").first
                    if await label_el.count() > 0:
                        for_attr = await label_el.get_attribute("for")
                        if for_attr:
                            cb = page.locator(f"#{for_attr}").first

            if cb and await cb.is_visible():
                is_checked = await cb.is_checked()
                if is_checked != should_check:
                    await self.browser.human_click(cb)
                return True
            return False
        except Exception as e:
            logger.debug("Checkbox error: {}", str(e)[:100])
            return False

    async def _handle_file_upload(self, field: FormField) -> bool:
        """Upload the resume file."""
        if not self._resume_path or not self._resume_path.exists():
            logger.warning("No resume file available for upload!")
            return False

        page = self.browser.page
        try:
            file_inputs = page.locator("input[type='file']")
            count = await file_inputs.count()
            for i in range(count):
                try:
                    file_input = file_inputs.nth(i)
                    await file_input.set_input_files(str(self._resume_path))
                    logger.info("Uploaded resume: {}", self._resume_path.name)
                    await asyncio.sleep(2)
                    return True
                except Exception:
                    continue

            # Some LinkedIn review states show an upload CTA before exposing the
            # actual file input. Try clicking it, then retry the upload.
            for sel in [
                "button:has-text('Upload resume')",
                "button:has-text('Upload a resume')",
                "label:has-text('Upload resume')",
                "label:has-text('Upload a resume')",
                "text='Be sure to include an updated resume'",
            ]:
                try:
                    if await self.browser.safe_click(sel, timeout=3000):
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue

            file_inputs = page.locator("input[type='file']")
            count = await file_inputs.count()
            for i in range(count):
                try:
                    file_input = file_inputs.nth(i)
                    await file_input.set_input_files(str(self._resume_path))
                    logger.info("Uploaded resume after prompt: {}", self._resume_path.name)
                    await asyncio.sleep(2)
                    return True
                except Exception:
                    continue

            logger.error("Resume upload failed: no usable file input found")
            return False
        except Exception as e:
            logger.error("Resume upload failed: {}", str(e)[:100])
            return False

    async def _open_review_section(self, section_hint: str) -> bool:
        """Open a collapsed review section by clicking its nearby Edit control."""
        page = self.browser.page
        hint = self._sanitize_label(section_hint).lower()
        if not hint:
            return False

        expanded_hints: list[str] = []
        for candidate in [hint, hint.split(" - ")[0].strip(), hint.split(":")[0].strip()]:
            if candidate and candidate not in expanded_hints:
                expanded_hints.append(candidate)

        if "additional question" in hint:
            expanded_hints.extend(["additional questions", "additional question"])
        if any(token in hint for token in ("resume", "cv")):
            expanded_hints.extend(["resume", "resume/cv", "cv"])
        if "contact" in hint:
            expanded_hints.append("contact info")

        section_selectors = [
            ".jobs-easy-apply-modal section",
            ".jobs-easy-apply-modal .jobs-easy-apply-form-section__grouping",
            ".jobs-easy-apply-modal .fb-dash-form-element",
            ".jobs-easy-apply-modal div",
            ".artdeco-modal section",
            ".artdeco-modal .jobs-easy-apply-form-section__grouping",
            ".artdeco-modal .fb-dash-form-element",
        ]
        edit_selectors = [
            "button[aria-label*='Edit']",
            "a[aria-label*='Edit']",
            "button:has-text('Edit')",
            "a:has-text('Edit')",
            "[role='button'][aria-label*='Edit']",
        ]

        for hint_value in expanded_hints:
            for section_selector in section_selectors:
                try:
                    sections = page.locator(section_selector)
                    count = await sections.count()
                    for i in range(count):
                        section = sections.nth(i)
                        if not await section.is_visible():
                            continue
                        try:
                            section_text = self._sanitize_label(await section.inner_text()).lower()
                        except Exception:
                            continue
                        if not section_text or hint_value not in section_text:
                            continue

                        for edit_selector in edit_selectors:
                            try:
                                edit_control = section.locator(edit_selector).first
                                if await edit_control.count() > 0 and await edit_control.is_visible():
                                    await self.browser.scroll_to_element(edit_control)
                                    await self.browser.human_click(edit_control)
                                    logger.info(
                                        "Opened review section '{}' via {}",
                                        hint_value,
                                        edit_selector,
                                    )
                                    await asyncio.sleep(1)
                                    return True
                            except Exception:
                                continue
                except Exception:
                    continue

        for edit_selector in edit_selectors:
            try:
                controls = page.locator(edit_selector)
                count = await controls.count()
                for i in range(count):
                    control = controls.nth(i)
                    if not await control.is_visible():
                        continue
                    container = control.locator("xpath=ancestor::*[self::section or self::div][1]").first
                    container_text = ""
                    try:
                        if await container.count() > 0:
                            container_text = self._sanitize_label(await container.inner_text()).lower()
                    except Exception:
                        container_text = ""
                    if any(h in container_text for h in expanded_hints if h):
                        await self.browser.scroll_to_element(control)
                        await self.browser.human_click(control)
                        logger.info("Opened review section '{}' via fallback edit control", section_hint)
                        await asyncio.sleep(1)
                        return True
            except Exception:
                continue

        return False

    async def _apply_error_recovery(self, job: Job, field_label: str, fix_value: str, action: str) -> bool:
        """Apply targeted recovery for review-page and upload-related errors."""
        label_lower = (field_label or "").lower()
        fix_lower = (fix_value or "").lower()

        if (
            field_label and any(token in label_lower for token in ("resume", "cv"))
        ) or any(token in fix_lower for token in ("resume", "cv", "upload")):
            fixed_field = FormField(
                field_type="file",
                label=field_label or "Resume/CV Upload",
                is_required=True,
            )
            return await self._handle_file_upload(fixed_field)

        if any(token in label_lower for token in ("additional questions", "additional question", "review")):
            if await self._open_review_section(field_label or "Additional Questions"):
                return True

        if action == "click":
            if await self._open_review_section(field_label or fix_value):
                return True
            if fix_value and len(fix_value) <= 80:
                return await self.browser.safe_click(f"text='{fix_value}'")

        return False

    # ── Error analysis and recovery ─────────────────────────────────────
    async def analyze_and_fix_errors(self, job: Job, current_values: dict) -> bool:
        """
        Take a screenshot, send to LLM for error analysis,
        and attempt to fix the errors.
        """
        screenshot_path = await self.browser.take_screenshot("form_error")
        logger.info("Analyzing form errors via LLM…")

        user_msg = ERROR_ANALYZER_USER.format(
            job_title=job.title,
            company=job.company,
            current_values=json.dumps(current_values, indent=2),
        )

        try:
            analysis = await llm_client.chat_json_with_image(
                system_prompt=ERROR_ANALYZER_SYSTEM,
                user_message=user_msg,
                image_path=screenshot_path,
            )
            logger.info("Error analysis: {}", analysis)

            if analysis.get("recommended_action") == "abort":
                logger.warning("LLM recommends aborting this application")
                return False

            # Try to fix each error
            errors = analysis.get("errors", [])
            fixed_any = False
            for error in errors:
                field_label = error.get("field_label", "")
                fix_value = error.get("suggested_fix", "")
                action = error.get("action", "type")

                if action == "skip":
                    continue

                if await self._apply_error_recovery(job, field_label, fix_value, action):
                    fixed_any = True
                    continue

                if action == "type" and field_label and fix_value:
                    # Find and fix the field
                    fixed_field = FormField(
                        field_type="text",
                        label=field_label,
                    )
                    fixed_any = await self._fill_single_field(fixed_field, fix_value) or fixed_any
                elif action == "select" and field_label and fix_value:
                    fixed_field = FormField(
                        field_type="select",
                        label=field_label,
                        options=[fix_value],
                    )
                    fixed_any = await self._fill_single_field(fixed_field, fix_value) or fixed_any
                elif action == "click":
                    if fix_value and len(fix_value) <= 80:
                        fixed_any = await self.browser.safe_click(f"text='{fix_value}'") or fixed_any

            return fixed_any or not errors
        except Exception as e:
            logger.error("Error analysis failed: {}", str(e)[:200])
            return False

    # ── Cover letter generation ─────────────────────────────────────────
    async def generate_cover_letter(self, job: Job) -> str:
        """Generate a tailored cover letter using the LLM."""
        user_msg = COVER_LETTER_USER.format(
            profile_summary=PROFILE["professional_summary"],
            job_title=job.title,
            company=job.company,
            job_description=job.description[:2000] if job.description else "Not available",
        )
        try:
            cover_letter = await llm_client.chat(
                system_prompt=COVER_LETTER_SYSTEM,
                user_message=user_msg,
            )
            return cover_letter
        except Exception as e:
            logger.error("Cover letter generation failed: {}", e)
            return PROFILE["professional_summary"]

    # ── Open-ended question answering ───────────────────────────────────
    async def answer_open_question(self, question: str, job: Job) -> str:
        """Generate an answer for an open-ended application question."""
        # Check cache
        cache_key = f"{job.job_id}:{question[:50]}"
        if cache_key in self._answer_cache:
            return self._answer_cache[cache_key]

        user_msg = OPEN_QUESTION_USER.format(
            profile_summary=PROFILE["professional_summary"],
            job_title=job.title,
            company=job.company,
            question=question,
        )
        try:
            answer = await llm_client.chat(
                system_prompt=OPEN_QUESTION_SYSTEM,
                user_message=user_msg,
            )
            self._answer_cache[cache_key] = answer
            return answer
        except Exception as e:
            logger.error("Open question answering failed: {}", e)
            return PROFILE["professional_summary"][:300]
