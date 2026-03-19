# Changelog

## 21-Mar-2026 04:00:00 IST

- `get_job_details`: when opening `/jobs/view/{id}/` directly (no list card match), wait/extract description using LinkConnect-style selectors (`article.jobs-description__container`, `.jobs-description-content__text`, etc.) instead of search-only `#job-details`; add LinkedIn “See more” button selector. INFO log when using direct navigation.

## 21-Mar-2026 03:25:00 IST

- Job search: DOM fallback now scrapes company/location using the same selector family as `_parse_job_card`; `get_job_details` log omits a dangling `at ` when company is still unknown.

## 20-Mar-2026 23:45:00 IST

- Browser `evaluate`: unwrap agent-browser payloads shaped like `{ origin, result: [...] }` (and `value` / `data` list keys, plus digit-key array-like objects) so job DOM fallback and other evals receive a plain list instead of a wrapper dict.

## 20-Mar-2026 22:00:00 IST

- Align job search with working patterns from sibling project **LinkConnect** (`scripts/linkedin_job_scraper.py`): `job_id_from_jobs_view_href()` for slug URLs (`/jobs/view/title-at-co-123`), DOM fallback JS uses the same dual match, list **priming** via window scroll + scrollable-ancestor stepping for lazy lists, selectors `[data-test-id='job-card']` / `[data-job-id]`.

## 20-Mar-2026 21:30:00 IST

- Browser: configurable viewport via `browser_viewport_width` / `browser_viewport_height` in settings (default **1600×1024**, was hardcoded 1400×900) so LinkedIn Jobs has more vertical space and less “blank band” under content when the OS window is taller than the layout viewport.

## 20-Mar-2026 21:00:00 IST

- Job search parity with `main` (no merge): add `_wait_job_list_main_style()` — `sleep(3)` then `wait_for_selector` 12s per `JOB_LIST_CONTAINER_SELECTORS` like `main`’s `search_jobs`; run after strict `_wait_for_job_list` misses, and once more before giving up on a page when the first extract returns 0 jobs.

## 20-Mar-2026 20:10:00 IST

- Fix DOM job-scrape fallback: wrap eval script in `(() => { ... })()` so the result is the job array (bare arrow function was never invoked → undefined). Normalize `{value: [...]}` / array-like object shapes from agent-browser.

## 20-Mar-2026 19:15:00 IST

- Job search extraction: add `_extract_job_cards_dom_fallback` via `browser.evaluate` (scan `a[href*='/jobs/view/']` in the live DOM) when locator counts are wrong or every `_parse_job_card` returns None; broaden job id/title parsing (all view links in card, `data-entity-urn` jobPosting, `base-search-card__title`).
- `get_job_details`: guard Playwright-only `expect_popup` / `popup.wait_for_*` behind `getattr` (agent-browser `AgentPage` never implemented these — same limitation as `main`).

## 20-Mar-2026 18:30:00 IST

- Job search: remove unscoped `base-card` / `jobPosting` urn signals (false \"ready\" → 0 parsed jobs); scope row detection to list `a[href*='/jobs/view/']` + known list-item selectors; after strict polling, reuse main-style visible `JOB_LIST_CONTAINER_SELECTORS` wait for the remaining timeout slice.
- `search_all_keywords` uses `PROFILE['job_search_location']` or `country` for URL/geo (was hardcoded `Remote`); added `job_search_location` in `profile.py` (India); `Job.search_location` metadata follows the same geo string; INFO probes when card count is 0.

## 20-Mar-2026 17:00:00 IST

- Fix job search vs `main`: use `goto(..., force_open=True)` for jobs URLs (and direct job detail navigation) so LinkedIn Jobs SPA loads like the old always-`open` path; tighten `_wait_for_job_list` to require real job-row selectors or jobs-specific empty copy (no early exit on generic scaffolds / footer phrases); add `base-card` / `jobPosting` urn selectors for newer result cards.

## 20-Mar-2026 16:00:00 IST

- Harden LinkedIn job search page detection (`linkedin/search.py`): expand list/scaffold selectors, poll for job rows / empty-state copy / containers instead of a single long `visible` wait on stale selectors; longer budgets; optional `wait --load load` after `goto` via `BrowserEngine.wait_for_load_state`; slightly longer post-navigation settle delay.

## 20-Mar-2026 14:30:00 IST

- Reduce stacked Chrome windows on Windows startup retries: after a failed `open`, temp-profile fallback now calls `stop(try_close=False)` (skip useless `close` RPC), runs `_cleanup_agent_browser_processes` again, waits briefly, then opens; cleanup also runs `taskkill /F /IM chrome.exe /T` and a longer post-kill sleep so child processes die before the next launch.

## 20-Mar-2026 12:00:00 IST

- Fix Windows bogus “cannot find 0;” popups during job-search navigation: stop routing agent-browser through `cmd.exe` on Windows. `eval` args contain JSON-quoted URLs with `&` and `%2C…`; broken cmd quoting split the line so fragments like `start=0` invoked the shell. `_run_json` now always uses `asyncio.create_subprocess_exec` (argv only), same `env` as before.

## 19-Mar-2026 23:52:38 IST

- Improve interrupt hard-cleanup and page navigation stability:
  - run hard cleanup (`run_kill_browser`) immediately from the Ctrl+C signal handler,
  - keep cleanup as the final safety path after `KeyboardInterrupt`,
  - switch navigation to in-page `window.location.href` updates after first load to avoid one-tab-per-navigate “open” calls.

## 19-Mar-2026 23:48:38 IST

- Improve terminal UX with Rich output formatting:
  - switched logger rendering to a Rich-backed sink for colour + structured message prefixes,
  - retained detailed file logging for debugging,
  - ensured Rich logger setup is loaded in both CLI (`main.py`) and server (`server.py`) entrypoints.

## 19-Mar-2026 23:45:33 IST

- Ensure Ctrl+C teardown runs the hard cleanup command as a final step:
  - deferred `agent-browser`/chrome cleanup from signal handler to shutdown handlers,
  - kept existing pipeline `KeyboardInterrupt` stop logic first, then ran `run_kill_browser`,
  - added explicit `python main.py --kill-browser` subprocess invocation so Ctrl+C always executes the same hard kill flow.

## 19-Mar-2026 23:45:00 IST

- Re-guarded agent-browser startup for Windows bind failures:
  - added bind-aware restart loop in `browser/engine.py` to rotate session names (`safe`, `safe-1-*`, ...),
  - cleanup stale session-bound agent-browser/chrome processes between attempts, then retry startup,
  - preserved temporary-profile fallback for timeout/profile errors when bind errors are not involved.

## 19-Mar-2026 23:58:00 IST

- Fixed flaky LinkedIn login selector timeouts in `linkedin/auth.py`:
  - added JavaScript DOM fallback to locate/fill login fields and submit without long
    selector-wait loops,
  - reduced login-form wait timeouts for selector-based strategy to avoid repeated 19s stalls.

## 19-Mar-2026 23:59:00 IST

- Hardened LinkedIn persistence checks to reduce repeated OTP prompts:
  - fixed async login-path bug (`_fill_login_form_via_javascript()` was not awaited),
  - added cookie-based logged-in detection in `linkedin/auth.py` to prefer reused sessions,
  - enabled fixed agent-browser `--session-name` (`link-auto-apply`) so auth state is persisted explicitly while keeping existing bind-safe session fallback logic.

## 20-Mar-2026 00:05:00 IST

- Improved search-page resilience in `linkedin/search.py` for intermittent connection/drop pages:
  - added recovery reset helper to return to feed and retry search context before switching templates,
  - added retry with longer job-list wait for second/recurring attempts,
  - added recovery pauses after navigation/list-render failures to improve continuation before keyword-level fallback.

## 20-Mar-2026 00:15:00 IST

- Hardened browser startup retries in `browser/engine.py` for transient bootstrap failures:
  - added cleanup before each startup retry,
  - treated DevTools-handshake failures (`Chrome exited before providing DevTools URL`, `no stderr output`) as retryable,
  - retried startup using temporary profiles when profile/socket bootstrap fails to reduce hard startup crashes.

## 17-Mar-2026 20:50:00 IST

- Apply-mode resilience added to handle LinkedIn checkpoint/login-wall interruptions:
  - added blocked-page detection and 40-second checkpoint wait/recovery loop in `linkedin/apply.py`,
  - added open-job retries that retry after checkpoint clearance and fall back to card-click reopening,
  - added checkpoint checks inside Easy Apply and external apply flows before and during application actions.

## 15-Mar-2026 18:20:00 IST

- Improve login reuse and search recovery reliability:
  - broadened `linkedin/auth.py` login state checks with multi-pass `_is_already_logged_in()` polling so temporary redirects/slow renders are not misclassified as logout,
  - added search-side resilience in `linkedin/search.py` via blocked/checkpoint detection, retry loops, and fallback URL retry before skipping pages.

## 17-Mar-2026 00:00:00 IST

- Restore browser profile usage on startup (`use_profile=True`) so LinkedIn session/cookies are reused across runs when `browser.engine` can open with profile; still falls back to temporary profile on profile-related startup failures.

## 17-Mar-2026 19:20:00 IST

- Improve scrape stability:
  - strengthened logged-in detection in `linkedin/auth.py` (URL + persistent session UI markers) before forcing login,
  - added broader/fallback LinkedIn job-search URL and more resilient page-load/error handling in `linkedin/search.py` to reduce false failures on unstable pages.

## 16-Mar-2026 00:55:00 IST

- Make `Ctrl+C`/interrupt automatically run browser cleanup by installing signal handlers and calling `--kill-browser` cleanup on `KeyboardInterrupt` across pipeline modes.

## 16-Mar-2026 00:40:00 IST

- Add `python main.py --kill-browser` to close agent-browser and kill leftover daemon (use when browser keeps opening after closing terminal)
- Make --kill-browser more aggressive on Windows: also kill chrome.exe and Python main.py processes (stops respawn loop)

## 16-Mar-2026 00:35:00 IST

- Fix Windows TCP bind 10013 (EACCES): use session "safe" on Windows so agent-browser daemon binds to port 49252 instead of excluded range 50766-50865 (agent-browser issue #132)

## 16-Mar-2026 00:25:00 IST

- Fix Windows "cannot find file 0": quote URL args in shell (cmd.exe treats & as separator; &start=0 was parsed as `start` command)

## 16-Mar-2026 00:20:00 IST

- LinkedIn login: explicit wait for URL change (wait --url "**/feed") after submit; retry Sign in via find role button if still on /login

## 16-Mar-2026 00:15:00 IST

- LinkedIn login: wait for networkidle, try multiple selectors (#username, input[name=session_key]), fallback to semantic find (label "Email or phone", "Password")
- Added credentials check (LINKEDIN_EMAIL, LINKEDIN_PASSWORD) before login attempt

## 15-Mar-2026 23:50:00 IST

- agent-browser timeout fix: redirect stdout/stderr to temp file instead of PIPE (Chrome writes heavily to stderr; pipe buffer filled and caused deadlock)
- First attempt without --profile to avoid profile lock/slow drive hangs
- Added stdin=DEVNULL and AGENT_BROWSER_CONFIRM_INTERACTIVE=0 to prevent process waiting for input
- Use env vars (AGENT_BROWSER_SESSION, AGENT_BROWSER_PROFILE) to avoid shell quoting issues with paths
- Added --test-browser flag for minimal agent-browser debugging (open about:blank, no profile)

## 15-Mar-2026 (ports)

- `python main.py --dashboard` now auto-starts API server in background; single command
- Changed default API server port 8080 -> 8081
- Changed default dashboard port 8501 -> 8502
- Added DASHBOARD_PORT and --dashboard-port for dashboard
- Dashboard passes DASHBOARD_API_URL to match server port

## 15-Mar-2026 (later)

- agent-browser setup: installed via npm in project (node_modules/agent-browser)
- Removed separate daemon startup; native agent-browser CLI handles persistence
- Run `npx agent-browser install` to download Chrome (first time)
- Added node_modules/ to .gitignore

## 15-Mar-2026 12:40:00 IST

- Dashboard and Vector DB implementation (LKC-41 through LKC-50)
- Added ChromaDB + SentenceTransformer for job embeddings
- Added JobSimilarity, SimilarityEngine, JobClustering
- Integrated vector DB with orchestrator and Google Sheets
- Added FastAPI endpoints: /jobs/similar, /clusters, /vector-db/stats
- Added Streamlit dashboard (python main.py --dashboard)
- Added scripts: query_similar_jobs.py, analyze_clusters.py, maintain_vector_db.py
- Added config/similarity_config.json
- Added tests: test_job_similarity, test_vector_db, test_similarity_engine, test_clustering, test_integration
- Added docs: VECTOR_DATABASE_GUIDE.md, SIMILARITY_API.md
