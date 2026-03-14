"""Agent Browser Engine — compatibility wrapper around `agent-browser`."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import settings, BROWSER_DATA_DIR, SCREENSHOT_DIR

AGENT_BROWSER_PROFILE_DIR = BROWSER_DATA_DIR / "agent_browser_profile"
AGENT_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
AGENT_BROWSER_ROOT = Path(__file__).resolve().parents[2] / "agent-browser" / "agent-browser"
AGENT_BROWSER_CLIENT = AGENT_BROWSER_ROOT / "bin" / "agent-browser.js"
AGENT_BROWSER_DAEMON = AGENT_BROWSER_ROOT / "dist" / "daemon.js"


def _selector_nth(selector: str, index: int) -> str:
    return f"{selector} >> nth={index}"


class AgentKeyboard:
    def __init__(self, engine: "BrowserEngine") -> None:
        self.engine = engine

    async def press(self, key: str) -> None:
        await self.engine._run_json(["press", key])

    async def type(self, text: str, delay: int | None = None) -> None:
        await self.engine._run_json(["keyboard", "type", text])


class AgentMouse:
    def __init__(self, engine: "BrowserEngine") -> None:
        self.engine = engine

    async def move(self, x: float, y: float, steps: int | None = None) -> None:
        await self.engine._run_json(["mouse", "move", str(int(x)), str(int(y))])

    async def wheel(self, dx: float, dy: float) -> None:
        await self.engine._run_json(["mouse", "wheel", str(int(dy)), str(int(dx))])

    async def click(self, x: float, y: float) -> None:
        await self.move(x, y)
        await self.engine._run_json(["mouse", "down"])
        await self.engine._run_json(["mouse", "up"])


class AgentLocator:
    def __init__(self, page: "AgentPage", selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> "AgentLocator":
        return AgentLocator(self.page, _selector_nth(self.selector, 0))

    def nth(self, index: int) -> "AgentLocator":
        return AgentLocator(self.page, _selector_nth(self.selector, index))

    def locator(self, selector: str) -> "AgentLocator":
        return AgentLocator(self.page, f"{self.selector} >> {selector}")

    async def count(self) -> int:
        data = await self.page.engine._run_json(["get", "count", self.selector])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("count", value.get("value", 0))
        return int(value or 0)

    async def is_visible(self) -> bool:
        data = await self.page.engine._run_json(["is", "visible", self.selector])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("visible", value.get("value", False))
        return bool(value)

    async def is_checked(self) -> bool:
        data = await self.page.engine._run_json(["is", "checked", self.selector])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("checked", value.get("value", False))
        return bool(value)

    async def inner_text(self) -> str:
        data = await self.page.engine._run_json(["get", "text", self.selector])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("text", value.get("value", ""))
        return str(value or "")

    async def inner_html(self) -> str:
        data = await self.page.engine._run_json(["get", "html", self.selector])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("html", value.get("value", ""))
        return str(value or "")

    async def get_attribute(self, name: str) -> str | None:
        data = await self.page.engine._run_json(["get", "attr", self.selector, name])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("attr", value.get("value"))
        return None if value is None else str(value)

    async def input_value(self) -> str:
        data = await self.page.engine._run_json(["get", "value", self.selector])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("value", "")
        return str(value or "")

    async def wait_for(self, timeout: int = 15000, state: str = "visible") -> "AgentLocator":
        if state == "visible":
            await self.page.engine._run_json(["wait", self.selector], timeout=timeout + 5000)
            return self
        deadline = asyncio.get_event_loop().time() + (timeout / 1000)
        while asyncio.get_event_loop().time() < deadline:
            visible = await self.is_visible()
            if state == "hidden" and not visible:
                return self
            if state == "attached" and await self.count() > 0:
                return self
            await asyncio.sleep(0.2)
        raise TimeoutError(f"Locator.wait_for timeout for {self.selector}")

    async def click(self) -> None:
        await self.page.engine._run_json(["click", self.selector])

    async def dispatch_event(self, event_type: str) -> None:
        await self.page.engine._run_json(["eval", self.page.engine._wrap_locator_eval(
            self.selector,
            f"el => el.dispatchEvent(new Event({json.dumps(event_type)}, {{ bubbles: true }}))",
        )])

    async def scroll_into_view_if_needed(self) -> None:
        await self.page.engine._run_json(["scrollintoview", self.selector])

    async def select_option(self, label: str | None = None, value: str | None = None) -> None:
        selected = label if label is not None else value
        if selected is None:
            raise ValueError("select_option requires label or value")
        await self.page.engine._run_json(["select", self.selector, selected])

    async def set_input_files(self, files: str | list[str]) -> None:
        file_list = [files] if isinstance(files, str) else files
        await self.page.engine._run_json(["upload", self.selector, *file_list])

    async def screenshot(self, path: str) -> None:
        await self.page.engine._run_json(["screenshot", path])

    async def bounding_box(self) -> dict[str, float] | None:
        data = await self.page.engine._run_json(["get", "box", self.selector])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("box", value.get("value", value))
        if not isinstance(value, dict):
            return None
        return {
            "x": float(value.get("x", 0)),
            "y": float(value.get("y", 0)),
            "width": float(value.get("width", 0)),
            "height": float(value.get("height", 0)),
        }

    async def evaluate(self, expression: str) -> Any:
        js = self.page.engine._wrap_locator_eval(self.selector, expression)
        data = await self.page.engine._run_json(["eval", js])
        return data.get("data")


class AgentPage:
    def __init__(self, engine: "BrowserEngine") -> None:
        self.engine = engine
        self.mouse = AgentMouse(engine)
        self.keyboard = AgentKeyboard(engine)

    @property
    def url(self) -> str:
        return self.engine._last_url

    def set_default_timeout(self, timeout: int) -> None:
        self.engine.default_timeout = timeout

    async def add_init_script(self, script: str) -> None:
        logger.debug("agent-browser add_init_script skipped")

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        await self.engine.goto(url, wait_until=wait_until)

    def locator(self, selector: str) -> AgentLocator:
        return AgentLocator(self, selector)

    async def wait_for_selector(self, selector: str, timeout: int = 15000) -> AgentLocator:
        return await self.engine.wait_for_selector(selector, timeout=timeout)

    async def wait_for_url(self, url_pattern: str, timeout: int = 30000) -> None:
        await self.engine._run_json(["wait", "--url", url_pattern], timeout=timeout + 5000)

    async def inner_text(self, selector: str) -> str:
        return await self.locator(selector).inner_text()

    async def screenshot(self, path: str, full_page: bool = False) -> None:
        args = ["screenshot", path]
        if full_page:
            args.insert(1, "--full")
        await self.engine._run_json(args)


class AgentContext:
    def __init__(self, engine: "BrowserEngine") -> None:
        self.engine = engine

    @property
    def pages(self) -> list[AgentPage]:
        return [self.engine.page]


class BrowserEngine:
    """Compatibility layer backed by `npx agent-browser`."""

    def __init__(self) -> None:
        self._page: Optional[AgentPage] = None
        self._context: Optional[AgentContext] = None
        self._screenshot_counter = 0
        self._last_url = ""
        self.default_timeout = settings.browser_timeout
        self.session_name = f"auto-apply-{os.getpid()}"
        self.profile_dir = AGENT_BROWSER_PROFILE_DIR
        self._daemon_process: asyncio.subprocess.Process | None = None
        self._daemon_log_task: asyncio.Task[None] | None = None
        self._owns_temp_profile = False

    # ── Lifecycle ───────────────────────────────────────────────────────
    async def start(self) -> AgentPage:
        logger.info("Starting agent-browser engine…")
        await self._start_daemon()
        self._page = AgentPage(self)
        self._context = AgentContext(self)
        try:
            await self._run_json(["open", "about:blank"])
        except Exception as exc:
            if not self._should_retry_with_temp_profile(exc):
                raise

            logger.warning(
                "Persistent browser profile failed to launch; retrying with a temporary profile: {}",
                str(exc)[:200],
            )
            await self.stop()
            self.profile_dir = Path(tempfile.mkdtemp(prefix="agent_browser_profile_", dir=str(BROWSER_DATA_DIR)))
            self._owns_temp_profile = True
            await self._start_daemon()
            self._page = AgentPage(self)
            self._context = AgentContext(self)
            await self._run_json(["open", "about:blank"])

        try:
            await self._run_json(["set", "viewport", "1400", "900"])
        except Exception as e:
            logger.debug("Viewport setup skipped: {}", str(e)[:120])
        logger.info("Agent-browser engine started (headless={})", settings.headless)
        return self._page

    async def stop(self) -> None:
        """Gracefully close the browser."""
        logger.info("Stopping agent-browser engine…")
        try:
            await self._run_json(["close"], timeout=5000)
        except Exception:
            pass
        if self._daemon_process is not None:
            try:
                if self._daemon_process.returncode is None:
                    self._daemon_process.terminate()
                    await asyncio.wait_for(self._daemon_process.wait(), timeout=5)
            except Exception:
                try:
                    self._daemon_process.kill()
                except Exception:
                    pass
            self._daemon_process = None
        if self._daemon_log_task is not None:
            self._daemon_log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._daemon_log_task
            self._daemon_log_task = None
        self._page = None
        self._context = None
        if self._owns_temp_profile and self.profile_dir.exists():
            with contextlib.suppress(Exception):
                shutil.rmtree(self.profile_dir, ignore_errors=True)
            self.profile_dir = AGENT_BROWSER_PROFILE_DIR
            self._owns_temp_profile = False
        logger.info("Agent-browser engine stopped.")

    @property
    def page(self) -> AgentPage:
        if self._page is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    @property
    def context(self) -> AgentContext:
        if self._context is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._context

    async def _run_json(self, command_args: list[str], timeout: int | None = None) -> dict[str, Any]:
        if not AGENT_BROWSER_CLIENT.exists():
            raise RuntimeError(f"agent-browser client not found: {AGENT_BROWSER_CLIENT}")

        args = [
            "node",
            str(AGENT_BROWSER_CLIENT),
            "--json",
            "--session",
            self.session_name,
            "--profile",
            str(self.profile_dir),
        ]
        if not settings.headless:
            args.append("--headed")
        args.extend(command_args)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(AGENT_BROWSER_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=(timeout or self.default_timeout) / 1000,
        )
        out_text = stdout.decode("utf-8", errors="ignore").strip()
        err_text = stderr.decode("utf-8", errors="ignore").strip()
        if err_text:
            logger.debug("agent-browser stderr: {}", err_text[:500])

        json_line = None
        for line in reversed([ln for ln in out_text.splitlines() if ln.strip()]):
            if line.strip().startswith("{") and line.strip().endswith("}"):
                json_line = line.strip()
                break
        if json_line is None:
            raise RuntimeError(f"agent-browser returned no JSON: {out_text[:500]}")

        data = json.loads(json_line)
        if not data.get("success", False):
            raise RuntimeError(data.get("error") or err_text or out_text or "agent-browser command failed")
        return data

    def _should_retry_with_temp_profile(self, exc: Exception) -> bool:
        message = str(exc).lower()
        retry_markers = (
            "launchpersistentcontext",
            "target page, context or browser has been closed",
            "browser has been closed",
            "singletonlock",
            "profile",
        )
        return any(marker in message for marker in retry_markers)

    async def _start_daemon(self) -> None:
        if not AGENT_BROWSER_DAEMON.exists():
            raise RuntimeError(
                f"agent-browser daemon not found: {AGENT_BROWSER_DAEMON}. Build the local checkout first."
            )
        if self._daemon_process is not None and self._daemon_process.returncode is None:
            return

        base_session = self.session_name
        for attempt in range(8):
            candidate_session = base_session if attempt == 0 else f"{base_session}-{attempt}"
            process, log_task = await self._launch_daemon_process(candidate_session)
            port = self._session_port(candidate_session)

            try:
                await self._wait_for_daemon_port(process, port, timeout=10.0)
                self.session_name = candidate_session
                self._daemon_process = process
                self._daemon_log_task = log_task
                logger.info(
                    "Agent-browser daemon ready for session {} on port {}",
                    self.session_name,
                    port,
                )
                return
            except Exception as exc:
                logger.debug(
                    "agent-browser daemon startup failed for session {} on port {}: {}",
                    candidate_session,
                    port,
                    str(exc)[:200],
                )
                if log_task is not None:
                    log_task.cancel()
                try:
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

        raise RuntimeError("agent-browser daemon could not bind to a usable Windows session port")

    async def _launch_daemon_process(
        self,
        session_name: str,
    ) -> tuple[asyncio.subprocess.Process, asyncio.Task[None] | None]:
        env = os.environ.copy()
        env["AGENT_BROWSER_DAEMON"] = "1"
        env["AGENT_BROWSER_SESSION"] = session_name
        env.setdefault("AGENT_BROWSER_DEBUG", "1")

        process = await asyncio.create_subprocess_exec(
            "node",
            str(AGENT_BROWSER_DAEMON),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(AGENT_BROWSER_ROOT),
            env=env,
        )
        log_task = None
        if process.stderr is not None:
            log_task = asyncio.create_task(self._drain_daemon_logs(process.stderr))
        return process, log_task

    async def _wait_for_daemon_port(
        self,
        process: asyncio.subprocess.Process,
        port: int,
        timeout: float,
    ) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if process.returncode is not None:
                raise RuntimeError(f"daemon exited with code {process.returncode}")
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port),
                    timeout=0.5,
                )
                writer.close()
                await writer.wait_closed()
                return
            except Exception:
                await asyncio.sleep(0.2)
        raise RuntimeError(f"timed out waiting for daemon port {port}")

    def _session_port(self, session_name: str) -> int:
        hash_value = 0
        for char in session_name:
            hash_value = ((hash_value << 5) - hash_value + ord(char)) & 0xFFFFFFFF
        signed_hash = hash_value if hash_value < 0x80000000 else hash_value - 0x100000000
        return 49152 + (abs(signed_hash) % 16383)

    async def _drain_daemon_logs(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="ignore").strip()
                if text:
                    logger.debug("agent-browser daemon: {}", text[:500])
        except asyncio.CancelledError:
            return

    def _wrap_locator_eval(self, selector: str, expression: str) -> str:
        safe_selector = json.dumps(selector)
        if expression.strip().startswith("el =>"):
            body = expression.strip()[len("el =>"):].strip()
            return (
                "(() => {"
                f"const el = document.querySelector({safe_selector});"
                "if (!el) return null;"
                f"return ({body});"
                "})()"
            )
        return expression

    # ── Navigation ──────────────────────────────────────────────────────
    async def goto(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        retries: int = 2,
    ) -> None:
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                logger.info("Navigating to {} (attempt {})", url, attempt)
                await self._run_json(["open", url], timeout=self.default_timeout)
                if wait_until:
                    try:
                        await self._run_json(["wait", "--load", wait_until], timeout=self.default_timeout)
                    except Exception:
                        pass
                self._last_url = url
                await self._random_pause(1.0, 2.5)
                return
            except Exception as e:
                last_err = e
                await asyncio.sleep(2 * attempt)
        raise last_err or RuntimeError(f"Failed to navigate to {url}")

    async def wait_for_url(self, url_pattern: str, timeout: int = 30000) -> None:
        await self._run_json(["wait", "--url", url_pattern], timeout=timeout + 5000)

    async def wait_for_selector(self, selector: str, timeout: int = 15000, state: str = "visible") -> AgentLocator:
        locator = self.page.locator(selector).first
        await locator.wait_for(timeout=timeout, state=state)
        return locator

    async def wait_for_any_selector(self, selectors: list[str], timeout: int = 15000) -> Optional[AgentLocator]:
        """Wait for any one of multiple selectors to appear. Returns the first match."""
        for _ in range(int(timeout / 500)):
            for sel in selectors:
                try:
                    locator = self.page.locator(sel).first
                    if await locator.is_visible():
                        return locator
                except Exception:
                    continue
            await asyncio.sleep(0.5)
        return None

    # ── Mouse & Keyboard Actions ────────────────────────────────────────
    async def human_click(self, locator: AgentLocator) -> None:
        await locator.click()
        await self._random_pause(0.3, 0.8)

    async def human_type(self, locator: AgentLocator, text: str, clear_first: bool = True) -> None:
        if clear_first:
            await self.human_click(locator)
            await self.page.keyboard.press("Control+a")
            await asyncio.sleep(0.1)
            await self.page.keyboard.press("Backspace")
            await asyncio.sleep(0.2)
        await self._run_json(["keyboard", "type", text])

    async def human_type_in_field(self, selector: str, text: str) -> None:
        locator = self.page.locator(selector).first
        await self.human_type(locator, text, clear_first=True)

    async def press_key(self, key: str) -> None:
        """Press a keyboard key."""
        await self.page.keyboard.press(key)
        await self._random_pause(0.1, 0.3)

    async def scroll_down(self, amount: int = 400) -> None:
        jitter = random.randint(-80, 80)
        await self._run_json(["scroll", "down", str(amount + jitter)])
        await self._random_pause(0.3, 0.8)

    async def scroll_up(self, amount: int = 400) -> None:
        await self._run_json(["scroll", "up", str(amount + random.randint(-80, 80))])

    async def scroll_element(self, selector: str, amount: int = 500) -> None:
        try:
            await self._run_json(["scroll", "down", str(amount), "--selector", selector])
            await self._random_pause(0.5, 1.2)
            return
        except Exception as e:
            logger.debug("scroll_element fallback for {}: {}", selector, str(e)[:80])
        await self.scroll_down(amount)
        await self._random_pause(0.3, 0.8)

    async def scroll_to_element(self, locator: AgentLocator) -> None:
        await locator.scroll_into_view_if_needed()
        await self._random_pause(0.3, 0.6)

    # ── Screenshots ─────────────────────────────────────────────────────
    async def take_screenshot(self, name: str = "") -> Path:
        self._screenshot_counter += 1
        if not name:
            name = f"screenshot_{self._screenshot_counter:04d}"
        path = SCREENSHOT_DIR / f"{name}.png"
        await self._run_json(["screenshot", str(path)])
        logger.debug("Screenshot saved: {}", path.name)
        return path

    async def take_element_screenshot(self, locator: AgentLocator, name: str = "") -> Path:
        return await self.take_screenshot(name or "element")

    # ── Page Analysis ───────────────────────────────────────────────────
    async def get_page_text(self) -> str:
        return await self.page.inner_text("body")

    async def evaluate(self, expression: str) -> Any:
        """Evaluate arbitrary JavaScript in the active page context."""
        data = await self._run_json(["eval", expression])
        return data.get("data")

    async def get_current_url(self) -> str:
        data = await self._run_json(["get", "url"])
        value = data.get("data")
        if isinstance(value, dict):
            value = value.get("url", value.get("value", ""))
        self._last_url = str(value or "")
        return self._last_url

    async def is_element_visible(self, selector: str) -> bool:
        try:
            return await self.page.locator(selector).first.is_visible()
        except Exception:
            return False

    async def count_elements(self, selector: str) -> int:
        return await self.page.locator(selector).count()

    # ── Helpers ─────────────────────────────────────────────────────────
    async def _random_pause(self, min_sec: float = 0.5, max_sec: float = 2.0) -> None:
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def safe_click(self, selector: str, timeout: int = 5000) -> bool:
        candidates = []
        try:
            group = self.page.locator(selector)
            count = await group.count()
            candidates = [group.nth(idx) for idx in range(min(count, 6))]
            if not candidates:
                candidates = [group.first]
        except Exception:
            candidates = [self.page.locator(selector).first]

        try:
            for locator in candidates:
                try:
                    await locator.wait_for(timeout=timeout, state="attached")
                    await locator.scroll_into_view_if_needed()
                    await locator.wait_for(timeout=max(1200, timeout // 2), state="visible")
                    await self.human_click(locator)
                    return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug("safe_click primary click failed for {}: {}", selector, str(e)[:140])

        try:
            for locator in candidates:
                try:
                    await locator.wait_for(timeout=timeout, state="attached")
                    await locator.scroll_into_view_if_needed()
                    await locator.dispatch_event("click")
                    await self._random_pause(0.2, 0.5)
                    logger.debug("safe_click dispatch fallback succeeded for {}", selector)
                    return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug("safe_click dispatch fallback failed for {}: {}", selector, str(e)[:140])

        try:
            for locator in candidates:
                try:
                    await locator.wait_for(timeout=timeout, state="attached")
                    await locator.scroll_into_view_if_needed()
                    box = await locator.bounding_box()
                    if not box:
                        continue
                    x = box["x"] + (box["width"] / 2)
                    y = box["y"] + (box["height"] / 2)
                    await self.page.mouse.move(x, y)
                    await self.page.mouse.click(x, y)
                    await self._random_pause(0.2, 0.5)
                    logger.debug("safe_click mouse fallback succeeded for {}", selector)
                    return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug("safe_click mouse fallback failed for {}: {}", selector, str(e)[:140])

        logger.debug("safe_click failed for {}", selector)
        return False

    async def safe_fill(self, selector: str, text: str, timeout: int = 5000) -> bool:
        try:
            locator = self.page.locator(selector).first
            await locator.wait_for(timeout=timeout, state="visible")
            await self.human_type(locator, text)
            return True
        except Exception as e:
            logger.debug("safe_fill failed for {}: {}", selector, str(e)[:100])
            return False
