# Prompt for AI Code Assistant (Cursor / Antigravity / Claude Code / Codex)

Copy-paste the prompt below into your AI editor of choice when you open
this project. It tells the AI everything it needs to know.

---

## Recommended prompt

```
I have a Python + Playwright + Flask automation testing tool for the
Stratus BackOffice (a JSP/jQuery/Dust.js SPA enterprise retail app).
The whole tool is in the `qa-automation/` folder.

Start by reading these files in order:
1. SETUP.md                  — overview, structure, all run modes
2. README.md                 — quick intro
3. framework/test_generator.py — how auto-tests are generated per screen type
4. framework/catalog_builder.py — how screens are discovered + cataloged
5. framework/crawl_runner.py   — execution engine + custom step actions
6. framework/single_screen_runner.py — single-screen flow
7. framework/bulk_runner.py    — bulk-across-screens flow
8. web_ui/app.py + web_ui/templates/index.html — UI

Key facts:
- The tool discovered 239 screens in this Stratus install (saved in
  knowledge_base/screens_catalog.json — ~8 MB).
- Auto-tests are 100% catalog-driven. No per-screen hardcoded selectors.
- Different test recipes for list / detail / report / wizard / other.
- Already verified working: customerlist (23/23), postransactionsummarylist
  (11/11), receiptslist (18/18) — all 100% pass.
- Runs entirely locally — no cloud, no API keys, no subscriptions.

To run it:
  cd qa-automation
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  playwright install chromium
  cp .env.example .env  # then edit with real Stratus URL/creds
  ./launch.sh           # opens http://localhost:5050

Always preserve:
- The 100%-catalog-driven test generation (no hardcoded selectors)
- The lenient action executor (optional steps don't fail the test)
- The 4-step wizard UI flow (Connect → Configure → Run → Result)
- The 7 run modes (Full / Read-only / Diagnose / API / Crawl / Catalog /
  Single Screen / Bulk)
- The custom YAML test case format (see crawl_runner.py docstring)

When asked to extend the tool, prefer:
- Adding new screen-type recipes to test_generator.py
- Adding new actions to crawl_runner.py's _execute_custom_steps()
- Adding catalog fields in catalog_builder.py's _DOM_DUMP_JS

When asked to fix tests, first inspect the catalog entry for that screen
to see what selectors actually exist there.
```

---

## What this tool already does (for context)

| Capability | Status |
|---|---|
| Login + machine ID auto-injection | ✅ Working against real Stratus |
| HTTPS self-signed cert handling | ✅ |
| Auto-discover ALL 239 screens from sidebar menu | ✅ |
| Catalog every field/button per screen | ✅ |
| Generate ~10-25 auto-tests per screen based on catalog | ✅ |
| Run a single screen with deep tests | ✅ |
| Run bulk across many screens | ✅ |
| Generic Crawl mode (~5 sec/screen smoke) | ✅ |
| API-only mode (no browser, ~50 sec) | ✅ |
| Custom YAML test case upload | ✅ |
| Live progress streaming via SSE | ✅ |
| Screenshots on every step | ✅ |
| HTML report + Allure integration | ✅ |
| Saved connection profiles | ✅ |
| Test history (last 25 runs) | ✅ |
| Diagnose mode (capture HTML/console for debugging) | ✅ |
| Connection-test button (probes URL before running) | ✅ |
| Step-isolation (failures don't cascade) | ✅ |
| Mock Stratus server (offline demos) | ✅ |
| One-command launcher (./launch.sh) | ✅ |

## What it does NOT do (future work)

| Capability | Estimated effort |
|---|---|
| Parallel test execution (multi-worker) | ~1 day |
| Test trend dashboard (track flakiness over time) | ~1 day |
| Scheduled runs + Slack/email alerts | ~half day |
| Visual regression (compare screenshots to baseline) | ~1 day |
| Database-backed result history (vs JSON) | ~half day |
| Jira / TestRail integration | ~1 day |
| Multi-environment profiles (dev/staging/prod) | ~2 hours |
| Per-screen YAML overrides catalog | ~half day |
| AI-assisted "fix this flaky test" suggestions | ~2 days |
| Token-on-file payment testing (writes to real card processor) | DO NOT BUILD |
