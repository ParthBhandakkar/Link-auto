from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
