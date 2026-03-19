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
        python main.py --job-reachout # Reach out to employees for external-apply jobs
  python main.py --dashboard    # Launch API + dashboard (ports 8081, 8502)
  python main.py --headless     # Run headless (no browser window)
  python main.py --kill-browser # Close agent-browser and cleanup (if browser keeps opening)
  python main.py --max-apps 5   # Limit applications per session
    python main.py --max-leads 25 # Limit investor leads for outreach runs
    python main.py --max-reachouts 10 # Limit investor reachouts per run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import signal
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
        "--job-reachout",
        action="store_true",
        help="Reach out to employees from external-apply job companies on LinkedIn",
    )
    mode.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the Streamlit dashboard (operate bot, view status, job similarity)",
    )
    mode.add_argument(
        "--test-browser",
        action="store_true",
        help="Minimal agent-browser test (open about:blank, no profile) — for debugging",
    )
    mode.add_argument(
        "--kill-browser",
        action="store_true",
        help="Close agent-browser and kill leftover Chrome/daemon (use if browser keeps opening after closing terminal)",
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
        "--port",
        type=int,
        default=None,
        help="API server port (default: 8081)",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=None,
        help="Dashboard port (default: 8502)",
    )
    return parser.parse_args()


_interrupt_cleanup_running = False


def _run_kill_cleanup() -> None:
    """Run kill-browser cleanup once (for interrupt-driven exits)."""
    global _interrupt_cleanup_running
    if _interrupt_cleanup_running:
        return
    _interrupt_cleanup_running = True
    try:
        run_kill_browser()
    except Exception as e:
        logger.warning("Interrupt cleanup failed: {}", e)


def _install_interrupt_handlers() -> None:
    """Install handlers so Ctrl+C / terminate events run cleanup."""

    def _on_interrupt(signum: int, frame) -> None:
        logger.warning("Interrupt signal {} received; running browser cleanup.", signum)
        _run_kill_cleanup()
        raise KeyboardInterrupt()

    try:
        signal.signal(signal.SIGINT, _on_interrupt)
        signal.signal(signal.SIGTERM, _on_interrupt)
        if os.name == "nt" and hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _on_interrupt)
    except Exception as e:
        logger.debug("Failed to install interrupt handler: {}", e)


def run_kill_browser() -> None:
    """Close agent-browser and kill leftover Chrome/daemon/Python processes.
    Use when the browser keeps opening after closing the terminal.
    On Windows: kills agent-browser, Chrome (all instances), and any Python main.py processes.
    """
    from browser.engine import AGENT_BROWSER_CLIENT, get_session_name, _PROJECT_ROOT

    session = get_session_name()
    env = os.environ.copy()
    env["AGENT_BROWSER_SESSION"] = session
    env["AGENT_BROWSER_CONFIRM_INTERACTIVE"] = "0"

    logger.info("Closing agent-browser session '{}'...", session)
    try:
        subprocess.run(
            ["node", str(AGENT_BROWSER_CLIENT), "close"],
            cwd=str(_PROJECT_ROOT),
            env=env,
            timeout=3,
            capture_output=True,
        )
        logger.info("agent-browser close completed.")
    except Exception as e:
        logger.warning("agent-browser close failed: {}", e)

    # Kill leftover processes (Windows) — kill spawners first, then agent-browser, then Chrome
    if os.name == "nt":
        my_pid = os.getpid()

        # 1. Kill node.exe with agent-browser in path (daemon respawns Chrome)
        try:
            ps_script = """
            Get-CimInstance Win32_Process -Filter "name='node.exe'" | 
                Where-Object { $_.CommandLine -like '*agent-browser*' } | 
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            """
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                timeout=10,
            )
            logger.info("Killed node.exe agent-browser daemon processes.")
        except Exception:
            pass

        # 2. Kill Python main.py (script may still be retrying)
        try:
            ps_script = f"""
            Get-CimInstance Win32_Process -Filter "name='python.exe'" | 
                Where-Object {{ $_.ProcessId -ne {my_pid} -and $_.CommandLine -like '*main.py*' }} | 
                ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
            """
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                timeout=10,
            )
            logger.info("Killed Python main.py processes.")
        except Exception:
            pass

        # 3. Kill agent-browser binary and Chrome
        for name in ["agent-browser-win32-x64.exe", "chrome.exe"]:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", name],
                    capture_output=True,
                    timeout=5,
                )
                logger.info("Killed {} processes.", name)
            except Exception:
                pass


async def run_test_browser() -> None:
    """Minimal agent-browser test: open about:blank without profile — for debugging timeouts."""
    from browser.engine import BrowserEngine

    engine = BrowserEngine()
    try:
        logger.info("Testing agent-browser (open about:blank, no profile)...")
        page = await engine.start()
        logger.info("SUCCESS: agent-browser started. URL={}", page.url)
        await engine.stop()
        logger.info("Test complete.")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await engine.stop()
        _run_kill_cleanup()
    except Exception as e:
        logger.error("Test failed: {}", e)
        await engine.stop()
        raise


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
        _run_kill_cleanup()
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
        _run_kill_cleanup()
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
        _run_kill_cleanup()
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
        _run_kill_cleanup()
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
        _run_kill_cleanup()
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
        _run_kill_cleanup()
    except Exception as e:
        logger.error("VC reachout pipeline failed: {}", e)
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
        _run_kill_cleanup()
    except Exception as e:
        logger.error("Job referral reachout pipeline failed: {}", e)
        await orch.stop()
        raise


def _wait_for_api(api_url: str, timeout: float = 15.0) -> bool:
    """Poll API /health until ready or timeout."""
    import time

    try:
        import requests
    except ImportError:
        time.sleep(2)
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{api_url}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def run_dashboard(port: int | None = None, api_port: int | None = None) -> None:
    """Launch the API server (if needed) and Streamlit dashboard."""
    import subprocess

    dashboard_path = Path(__file__).resolve().parent / "dashboard" / "app.py"
    if not dashboard_path.exists():
        logger.error("Dashboard not found: {}", dashboard_path)
        return
    dash_port = port or settings.dashboard_port
    server_port = api_port or settings.server_port
    api_url = f"http://localhost:{server_port}"

    # Start API server in background so dashboard has something to talk to
    logger.info("Starting API server on port {} (background)", server_port)
    api_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            settings.server_host,
            "--port",
            str(server_port),
        ],
        cwd=str(Path(__file__).resolve().parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    try:
        if not _wait_for_api(api_url):
            logger.warning("API server may not be ready yet; dashboard might show errors")
        logger.info("Launching dashboard at http://localhost:{}", dash_port)
        env = os.environ.copy()
        env["DASHBOARD_API_URL"] = api_url
        subprocess.run(
            ["streamlit", "run", str(dashboard_path), "--server.port", str(dash_port)],
            check=True,
            env=env,
        )
    finally:
        api_proc.terminate()
        try:
            api_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api_proc.kill()


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
    _install_interrupt_handlers()

    # Print banner (ASCII-safe for Windows cp1252)
    print("""
    +==================================================+
    |                                                  |
    |   LinkedIn Auto-Apply Bot  v1.0                  |
    |   ---------------------------------------------- |
    |   Powered by agent-browser + Kimi K2.5 LLM       |
    |                                                  |
    +==================================================+
    """)

    if args.headless:
        settings.headless = True

    try:
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
        elif args.job_reachout:
            logger.info("Mode: JOB-REACHOUT (external jobs → employee referral outreach)")
            asyncio.run(run_job_reachout(headless=args.headless, max_reachouts=args.max_reachouts))
        elif args.dashboard:
            logger.info("Mode: DASHBOARD (Streamlit UI)")
            run_dashboard(port=args.dashboard_port, api_port=args.port)
        elif args.test_browser:
            logger.info("Mode: TEST-BROWSER (minimal agent-browser check)")
            asyncio.run(run_test_browser())
        elif args.kill_browser:
            logger.info("Mode: KILL-BROWSER (close agent-browser and cleanup)")
            run_kill_browser()
        else:
            # Start the API server
            run_server(port=args.port)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        _run_kill_cleanup()



if __name__ == "__main__":
    main()
