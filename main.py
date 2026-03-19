"""
LinkedIn Auto-Apply Bot — Main Entry Point

Usage:
  python main.py                # Start the FastAPI server
  python main.py --scrape       # Scrape jobs → Google Sheets + CSV (no apply)
  python main.py --apply        # Apply to unapplied jobs already in the Sheet
  python main.py --run          # Combined: scrape then apply
    python main.py --people       # Scrape up to 5 people per company already in the Sheet
    python main.py --outreach     # Find investor/VC leads and write them to the VCs sheet
    python main.py --reachout     # Read VCs from the sheet and message/connect on LinkedIn
    python main.py --check-connections # Check pending connections and send follow-up messages
        python main.py --job-reachout # Reach out to employees for external-apply jobs
  python main.py --headless     # Run headless (no browser window)
  python main.py --max-apps 5   # Limit applications per session
    python main.py --max-leads 25 # Limit investor leads for outreach runs
    python main.py --max-reachouts 10 # Limit investor reachouts per run
    python main.py --max-checks 10 # Limit pending connection checks per run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure the project root is on the Python path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loguru import logger
from config import settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LinkedIn Auto-Apply Bot — Automated job applications with LLM intelligence",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--scrape",
        action="store_true",
        help="Scrape-only: search → export to Google Sheets + CSV (no apply)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply-only: read unapplied jobs from the Google Sheet and apply",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="Combined: scrape jobs then apply (full pipeline)",
    )
    mode.add_argument(
        "--people",
        action="store_true",
        help="Read companies from the Google Sheet and scrape up to 5 people per company",
    )
    mode.add_argument(
        "--outreach",
        action="store_true",
        help="Find investors/VCs on LinkedIn and write them to the VCs worksheet",
    )
    mode.add_argument(
        "--reachout",
        action="store_true",
        help="Read investors from the VCs worksheet and message/connect with them on LinkedIn",
    )
    mode.add_argument(
        "--check-connections",
        action="store_true",
        help="Check pending connection requests and send follow-up messages if accepted",
    )
    mode.add_argument(
        "--job-reachout",
        action="store_true",
        help="Reach out to employees from external-apply job companies on LinkedIn",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser in headless mode (no window)",
    )
    parser.add_argument(
        "--max-apps",
        type=int,
        default=None,
        help="Maximum number of applications per session",
    )
    parser.add_argument(
        "--max-leads",
        type=int,
        default=None,
        help="Maximum number of investor leads to collect during outreach runs",
    )
    parser.add_argument(
        "--max-reachouts",
        type=int,
        default=None,
        help="Maximum number of investor reachouts to process during a run",
    )
    parser.add_argument(
        "--max-checks",
        type=int,
        default=None,
        help="Maximum number of pending connections to check during a run",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Server port (default: 8080)",
    )
    return parser.parse_args()


async def run_scrape(headless: bool = False) -> None:
    """Scrape-only: search → details → export to Google Sheets + CSV."""
    from orchestrator import Orchestrator

    if headless:
        settings.headless = True

    orch = Orchestrator()
    try:
        jobs = await orch.run_scrape_pipeline()
        logger.info("Scrape finished — {} jobs collected", len(jobs))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await orch.stop()
    except Exception as e:
        logger.error("Scrape pipeline failed: {}", e)
        await orch.stop()
        raise


async def run_apply(headless: bool = False, max_apps: int | None = None) -> None:
    """Apply-from-sheet: read unapplied jobs from Sheet → apply → update status."""
    from orchestrator import Orchestrator

    if headless:
        settings.headless = True
    if max_apps:
        settings.max_applications_per_session = max_apps

    orch = Orchestrator()
    try:
        stats = await orch.run_apply_from_sheet_pipeline(limit=max_apps)
        logger.info("Apply finished — {} jobs applied", stats.total_applied)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await orch.stop()
    except Exception as e:
        logger.error("Apply pipeline failed: {}", e)
        await orch.stop()
        raise


async def run_combined(headless: bool = False, max_apps: int | None = None) -> None:
    """Combined: scrape then apply."""
    from orchestrator import Orchestrator

    if headless:
        settings.headless = True
    if max_apps:
        settings.max_applications_per_session = max_apps

    orch = Orchestrator()
    try:
        stats = await orch.run_full_pipeline()
        logger.info("Pipeline finished — {} jobs applied", stats.total_applied)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await orch.stop()
    except Exception as e:
        logger.error("Pipeline failed: {}", e)
        await orch.stop()
        raise


async def run_people(headless: bool = False) -> None:
    """Read companies from the sheet and scrape people for them."""
    from orchestrator import Orchestrator

    if headless:
        settings.headless = True

    orch = Orchestrator()
    try:
        results = await orch.run_people_scrape_pipeline(limit_per_company=5)
        logger.info("People scrape finished — {} companies processed", len(results))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await orch.stop()
    except Exception as e:
        logger.error("People pipeline failed: {}", e)
        await orch.stop()
        raise


async def run_outreach(headless: bool = False, max_leads: int | None = None) -> None:
    """Log in to LinkedIn, find investor leads, and store them in the VCs worksheet."""
    from orchestrator import Orchestrator

    if headless:
        settings.headless = True

    orch = Orchestrator()
    try:
        leads = await orch.run_outreach_pipeline(limit=max_leads)
        logger.info("Outreach finished — {} investor leads collected", len(leads))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await orch.stop()
    except Exception as e:
        logger.error("Outreach pipeline failed: {}", e)
        await orch.stop()
        raise


async def run_reachout(headless: bool = False, max_reachouts: int | None = None) -> None:
    """Read VC leads from the sheet and attempt LinkedIn messages/connect requests."""
    from orchestrator import Orchestrator

    if headless:
        settings.headless = True

    orch = Orchestrator()
    try:
        results = await orch.run_vc_reachout_pipeline(limit=max_reachouts)
        logger.info("VC reachout finished — {} investors processed", len(results))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await orch.stop()
    except Exception as e:
        logger.error("VC reachout pipeline failed: {}", e)
        await orch.stop()
        raise


async def run_check_connections(headless: bool = False, max_checks: int | None = None) -> None:
    """Check pending connection requests and send follow-up messages if accepted."""
    from orchestrator import Orchestrator

    if headless:
        settings.headless = True

    orch = Orchestrator()
    try:
        results = await orch.run_vc_connection_followup_pipeline(limit=max_checks)
        logger.info("Connection check finished — {} connections checked", len(results))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await orch.stop()
    except Exception as e:
        logger.error("Connection check pipeline failed: {}", e)
        await orch.stop()
        raise

async def run_job_reachout(headless: bool = False, max_reachouts: int | None = None) -> None:
    """Read external-job contacts from the jobs sheet and attempt referral outreach."""
    from orchestrator import Orchestrator

    if headless:
        settings.headless = True

    orch = Orchestrator()
    try:
        results = await orch.run_referral_reachout_pipeline(limit=max_reachouts)
        logger.info("Job referral reachout finished — {} contacts processed", len(results))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await orch.stop()
    except Exception as e:
        logger.error("Job referral reachout pipeline failed: {}", e)
        await orch.stop()
        raise


def run_server(port: int | None = None) -> None:
    """Start the FastAPI server."""
    import uvicorn

    server_port = port or settings.server_port
    logger.info("Starting server on {}:{}", settings.server_host, server_port)
    uvicorn.run(
        "server:app",
        host=settings.server_host,
        port=server_port,
        reload=False,
        log_level="info",
    )


def main() -> None:
    args = parse_args()

    # Print banner
    print("""
    ╔══════════════════════════════════════════════════╗
    ║                                                  ║
    ║   🤖  LinkedIn Auto-Apply Bot  v1.0              ║
    ║   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━          ║
    ║   Powered by Playwright + Kimi K2.5 LLM          ║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝
    """)

    if args.headless:
        settings.headless = True

    if args.scrape:
        logger.info("Mode: SCRAPE (search → Sheets + CSV)")
        asyncio.run(run_scrape(headless=args.headless))
    elif args.apply:
        logger.info("Mode: APPLY (read Sheet → apply → update status)")
        asyncio.run(run_apply(headless=args.headless, max_apps=args.max_apps))
    elif args.run:
        logger.info("Mode: COMBINED (scrape + apply)")
        asyncio.run(run_combined(headless=args.headless, max_apps=args.max_apps))
    elif args.people:
        logger.info("Mode: PEOPLE (sheet companies → people scrape)")
        asyncio.run(run_people(headless=args.headless))
    elif args.outreach:
        logger.info("Mode: OUTREACH (LinkedIn investors/VCs → VCs worksheet)")
        asyncio.run(run_outreach(headless=args.headless, max_leads=args.max_leads))
    elif args.reachout:
        logger.info("Mode: REACHOUT (VCs worksheet → LinkedIn message/connect)")
        asyncio.run(run_reachout(headless=args.headless, max_reachouts=args.max_reachouts))
    elif args.check_connections:
        logger.info("Mode: CHECK-CONNECTIONS (pending connections → follow-up messages)")
        asyncio.run(run_check_connections(headless=args.headless, max_checks=args.max_checks))
    elif args.job_reachout:
        logger.info("Mode: JOB-REACHOUT (external jobs → employee referral outreach)")
        asyncio.run(run_job_reachout(headless=args.headless, max_reachouts=args.max_reachouts))
    else:
        # Start the API server
        run_server(port=args.port)



if __name__ == "__main__":
    main()
