from __future__ import annotations

import asyncio

from browser.engine import BrowserEngine
from linkedin.form_filler import FormFiller
from models.schemas import FormField, Job


async def main() -> None:
    ff = FormFiller(BrowserEngine())
    job = Job(title="Backend Engineer", company="Calyptus")
    tests = [
        FormField(field_type="text", label="LinkedIn profile URL", is_required=True),
        FormField(field_type="text", label="Portfolio website", is_required=True),
        FormField(field_type="text", label="Why are you a good fit for this role?", is_required=True),
    ]
    for field in tests:
        ans = await ff._get_single_field_answer(field, job)
        print(f"{field.label}: {ans}")


if __name__ == "__main__":
    asyncio.run(main())
