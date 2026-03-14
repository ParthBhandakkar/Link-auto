"""
Retry connecting with Qazi Atif using the fixed More > Connect flow.
Run from: D:\WorkZera\Projects\AgentBot\auto_apply
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

profile_spec = importlib.util.spec_from_file_location("profile", PROJECT_ROOT / "profile.py")
if profile_spec and profile_spec.loader:
    profile_module = importlib.util.module_from_spec(profile_spec)
    profile_spec.loader.exec_module(profile_module)
    sys.modules["profile"] = profile_module

from orchestrator import Orchestrator
from models.schemas import ReferralContact
from Outreach.referral_messenger import LinkedInReferralMessenger


async def main():
    contact = ReferralContact(
        job_url="https://www.linkedin.com/jobs/view/4382041930/",
        apply_link="https://outsourced.com/apply",
        job_title="Lead Fullstack Developer (Remote)",
        company="Outsourced",
        location="",
        person_index=1,
        person_name="Qazi Atif",
        person_designation="Human Resources Specialist at Outsourced",
        linkedin_profile_url="https://www.linkedin.com/in/qazi-atif-3aab01324/",
    )

    print(f"Target  : {contact.person_name}")
    print(f"Profile : {contact.linkedin_profile_url}")
    print(f"Job     : {contact.job_title} @ {contact.company}")

    orch = Orchestrator()
    try:
        await orch.start()
        if not await orch.auth.login():
            print("ERROR: LinkedIn login failed")
            return

        messenger = LinkedInReferralMessenger(orch.browser)
        result = await messenger.reach_out_to_contact(contact)

        print(f"\n{'='*55}")
        print(f"Status  : {result.status}")
        print(f"Action  : {result.action_taken}")
        print(f"Notes   : {result.notes}")
        print(f"Sent at : {result.sent_at}")
        print(f"{'='*55}")
    finally:
        await orch.stop()


if __name__ == "__main__":
    asyncio.run(main())
