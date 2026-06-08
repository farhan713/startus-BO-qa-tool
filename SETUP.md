# Stratus QA Tool — Setup & Run Guide

A complete enterprise-level automation testing tool for the Stratus
BackOffice. Tests **all 239 screens** with **zero per-screen code** — uses
a one-time catalog scan + auto-generated tests driven by what's actually
on each screen.

---

## What this is

A self-contained Python + Playwright + Flask project that:

1. **Logs into** any Stratus BackOffice instance (URL + credentials)
2. **Discovers all screens** from the sidebar menu (~239 in a typical install)
3. **Catalogs every field/button/dropdown** on each screen → `knowledge_base/screens_catalog.json`
4. **Auto-generates comprehensive tests** per screen, recipe varies by screen type:
   - **List screens** — search by each filter, click action menu, print, reset, new
   - **Detail screens** — fill each field, dropdowns, checkboxes, save/cancel
   - **Reports** — fill filters, click Run
5. **Runs tests** through a clean web UI at `http://localhost:5050`
6. **Streams results** live, captures screenshots, produces HTML reports
7. **Supports custom YAML test cases** layered on top of auto-generated ones

**No subscriptions, no cloud, no API costs. Built once with AI; runs forever locally.**

---

## Quick start — 3 commands

### macOS / Linux

```bash
# 1. One-time setup (Python 3.10+, ~5 min)
cd qa-automation
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Configure your Stratus URL + credentials
cp .env.example .env
# Edit .env to set APP_BASE_URL, TEST_USER, TEST_PASSWORD, DB_*

# 3. Launch the tool (auto-opens http://localhost:5050)
./launch.sh
```

### Windows

```cmd
cd qa-automation
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
REM Edit .env in Notepad

REM Then start the two servers in separate terminals:
python mock_server\server.py
python web_ui\app.py
REM Browse to http://localhost:5050
```

---

## The run modes (New run page)

Open **New run** in the sidebar, pick a mode tile, then **Start test**.
The four core modes are shown first; the rest are under "Advanced".

| Mode | What it does | When to use |
|---|---|---|
| 🎯 **One screen** | Pick ONE screen (searchable picker), auto-runs ~10–25 tests | Deep test of one screen |
| 🏭 **Many screens** | Multi-select; run all auto-tests across them | Nightly regression |
| 📚 **Rebuild catalog** | One-time: visit every screen, capture every element | After a Stratus release |
| ⚙️ **API smoke** | Backend test via `/stratus` JSON endpoint, no browser (~5 s) | Fast CI smoke |
| 🌐 **Crawl from menu** | Like Many, but discovers screens from the live menu | Catalog is stale |
| 🔬 **Diagnose** | Login + capture HTML/screenshots, no assertions | Debug what's wrong |
| ⚡ **Full / Read-only** | Legacy Customer-only demo flow | Backward compatibility |

Tip: press **⌘K / Ctrl+K** to open the command palette and jump to any
screen or page instantly.

---

## Project structure

```
qa-automation/
├── README.md                      ← intro
├── SETUP.md                       ← this file
├── HOW-TO-USE.md                  ← user manual
├── DEMO.md                        ← demo walkthrough
│
├── requirements.txt               ← Python deps
├── pytest.ini                     ← test config
├── .env.example                   ← config template (copy to .env)
├── .env                           ← YOUR config (gitignored)
├── launch.sh                      ← one-command launcher
├── conftest.py                    ← pytest fixtures
├── demo.py                        ← standalone CLI demo
│
├── config/
│   └── settings.py                ← config loader
│
├── framework/                     ← the test framework
│   ├── api/
│   │   ├── stratus_api.py         ← direct JSON API client
│   │   └── stratus_client.py      ← HTTP wrapper
│   ├── db/
│   │   └── sqlserver_helper.py    ← pyodbc / SQL Server
│   ├── ui/
│   │   ├── base_page.py
│   │   └── pages/                 ← Customer page objects (legacy/demo)
│   ├── utils/
│   │   └── logger.py
│   ├── demo_runner.py             ← legacy Customer demo
│   ├── api_demo_runner.py         ← API-only runner
│   ├── crawl_runner.py            ← generic crawl runner
│   ├── catalog_builder.py         ← scans + saves catalog
│   ├── catalog_analyzer.py        ← summary stats from catalog
│   ├── test_generator.py          ← auto-generates tests from catalog
│   ├── single_screen_runner.py    ← runs tests for ONE screen
│   └── bulk_runner.py             ← runs across MANY screens
│
├── knowledge_base/
│   └── screens_catalog.json       ← 239 screens cataloged (8 MB JSON)
│
├── mock_server/
│   └── server.py                  ← offline Stratus simulator (port 8080)
│
├── web_ui/                        ← Flask web console
│   ├── app.py                     ← Flask app + REST API
│   ├── templates/
│   │   └── index.html             ← single-page UI
│   └── static/
│       ├── style.css              ← modern UI styles
│       └── app.js                 ← SPA client logic
│
├── tests/
│   ├── demo/
│   │   └── test_customer_demo.py  ← 7 pytest demo tests
│   ├── features/                  ← BDD scenarios (Gherkin)
│   └── smoke/                     ← critical-path checks
│
├── docs/                          ← full design docs
│   ├── architecture.md            ← system architecture + diagrams
│   ├── PITCH.md                   ← elevator pitch
│   ├── HOW-TO-USE.md              ← end-user manual
│   ├── quickstart-for-qa.md       ← non-coder guide
│   ├── glossary.md
│   └── word/                      ← .docx versions of all docs
│
└── reports/                       ← test artifacts (gitignored)
    ├── report.html                ← pytest HTML report
    ├── screenshots/
    └── allure-results/
```

---

## .env template

```ini
APP_BASE_URL=https://YOUR-STRATUS-HOST:8443/backoffice/?mid=100
APP_LOGIN_PATH=/login.jsp
APP_AUTH_SERVLET=/UserAuthenticationServlet.do
APP_DISPATCHER=/stratus
APP_NAME=WRMS

TEST_USER=your_qa_user
TEST_PASSWORD=your_qa_password
TEST_INVALID_USER=does_not_exist
TEST_INVALID_PASSWORD=wrong

BROWSER=chromium
HEADLESS=true
BROWSER_TIMEOUT_MS=30000
SLOW_MO_MS=0
VIDEO_ON_FAILURE=false
SCREENSHOT_ON_FAILURE=true

DB_DRIVER=ODBC Driver 18 for SQL Server
DB_HOST=your-sql-host
DB_PORT=1433
DB_NAME=stratus
DB_USER=sa
DB_PASSWORD=YOUR_DB_PASSWORD
DB_TRUST_SERVER_CERT=yes
DB_ENCRYPT=no

ENV_NAME=DEV
RUN_ID=local
```

---

## Typical workflow on a new machine

### Day 1 (one-time, ~30 min total)

1. `cd qa-automation && python3 -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `playwright install chromium`
4. `cp .env.example .env` → fill in URL + creds
5. `./launch.sh` → browser opens to `http://localhost:5050`
6. **New run → Rebuild catalog → Start test** → wait ~10–15 min (one-time)
   *(A pre-built catalog of 239 screens is already included, so you can skip
   this and go straight to testing — only rebuild after a Stratus release.)*
7. Done — catalog saved, all 239 screens known

### Daily

1. `./launch.sh` (if not already running)
2. **New run** → pick a mode:
   - **🎯 One screen** for one-screen deep test (~5 min)
   - **🏭 Many screens** for full regression (~3–6 hours for all 239)
   - **⚙️ API smoke** for a 5-second backend check
3. Watch the live timeline + screenshots
4. Review the HTML report (report icon top-right, or `reports/last_run.html`)

### When a new screen ships in Stratus

1. Re-run **📚 Build catalog** (it'll pick up the new screen)
2. Single Screen mode → it appears in the dropdown automatically
3. Tests run on it with zero code changes

---

## How the auto-test generation works

The catalog stores, per screen:
- Every visible field (with type, label, options)
- Every visible button (categorized: topnav / form / action menu / other)
- Grid columns, tabs, etc.

The **test generator** then produces tests based on this data:

### For LIST screens
```
Render check
Search with no filters
For each text field: fill QA, click Search
For each dropdown: select first real option, click Search
For each checkbox: toggle, click Search
Click Reset
Open Action menu
Click Print List (if present)
Click New (if present)
```

### For DETAIL screens
```
Render check
For each text field: fill sample value
For each dropdown: try each option
For each checkbox: toggle
Fill ALL fields together
Click Cancel
(In non-safe mode) Click Save with sample data
```

### For REPORTS
```
Render check
For each filter field: fill sample value
Click Run / Generate / Submit
```

**Adding a new screen to Stratus** → catalog rebuild discovers it →
auto-tests generated automatically. Zero per-screen code.

---

## Verified results

- **customerlist (Customer module)** — 23/23 auto-tests PASS (100%)
- **postransactionsummarylist (POS)** — 11/11 PASS
- **receiptslist (Receipts)** — 18/18 PASS
- Full **catalog of 239 screens** built in 17.6 min
- 99% of screens render successfully in the catalog scan

---

## CLI commands (alternative to the UI)

```bash
# Activate venv first
source .venv/bin/activate

# Run the standalone demo (Customer module)
python demo.py
python demo.py --headless --fast
python demo.py --read-only

# Run pytest suite (CI-style)
pytest -m demo
pytest -m smoke

# Programmatically — build catalog
python -c "
import sys; sys.path.insert(0, '.')
from pathlib import Path
from framework.demo_runner import DemoConfig
from framework.catalog_builder import build_catalog
cfg = DemoConfig(
    base_url='https://your-stratus/backoffice/?mid=100',
    user='qa', password='pass', machine_id='100',
    headless=True, reports_dir=Path('reports'),
)
build_catalog(cfg, lambda e: print(e.text))
"

# Programmatically — run Single Screen test
python -c "
import sys; sys.path.insert(0, '.')
from pathlib import Path
from framework.demo_runner import DemoConfig
from framework.single_screen_runner import run_single_screen
cfg = DemoConfig(
    base_url='https://your-stratus/backoffice/?mid=100',
    user='qa', password='pass', machine_id='100',
    headless=True, reports_dir=Path('reports'),
)
r = run_single_screen(cfg, 'customerlist', lambda e: print(e.text), safe_mode=True)
print(f'{r.steps_passed}/{r.steps_total} passed in {r.duration_s:.1f}s')
"

# Show catalog analysis
python -m framework.catalog_analyzer

# Stop everything cleanly
./launch.sh stop
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `pip install` fails | Ensure Python 3.10+ (`python3 --version`); on macOS may need `brew install python` |
| `playwright install chromium` fails | Internet/proxy issue; retry or set `HTTPS_PROXY` |
| `Cannot connect to BackOffice` | VPN not on? Check `APP_BASE_URL` in `.env`; can you reach it in Chrome? |
| `Login failed` | Wrong creds or expired account; try logging in via the real Stratus UI first |
| `Database connection failed` | `pip install pyodbc` requires ODBC driver — `brew install msodbcsql18` on Mac |
| `Single Screen mode says no catalog` | Click **📚 Build catalog** first (one-time, ~18 min) |
| Tests timeout on a specific screen | Screen needs more time to render — increase wait or skip via type filter |
| Browser opens but page is blank | SPA didn't initialize — re-run the post-login bounce; try Diagnose mode |

---

## Tech stack

- **Python 3.10+** — runtime
- **Playwright 1.48** — browser automation (Chromium)
- **Flask 3.0** — web UI server
- **pytest 8.3** + **pytest-bdd** + **pytest-html** — test runner & reports
- **pyodbc + SQLAlchemy** — SQL Server (optional, for DB verification)
- **requests** — direct HTTP for API mode
- **PyYAML** — custom test case parsing
- **No cloud, no subscriptions, no API keys required at runtime.**

---

## Files you can give to Cursor / Claude / Codex to extend the tool

For an AI coding assistant to make changes, point it at:

1. **`framework/test_generator.py`** — auto-test recipes per screen type
2. **`framework/catalog_builder.py`** — what gets captured per screen
3. **`framework/crawl_runner.py`** — generic execution + custom-step actions
4. **`framework/single_screen_runner.py`** — single-screen flow
5. **`framework/bulk_runner.py`** — bulk-across-screens flow
6. **`web_ui/app.py`** + **`web_ui/templates/index.html`** — UI

The catalog at `knowledge_base/screens_catalog.json` is the source-of-truth for everything that's tested. To see what's on any screen:

```bash
python -c "
import json
c = json.load(open('knowledge_base/screens_catalog.json'))
s = next(x for x in c['screens'] if x['screenname'] == 'customerlist')
print('fields:', [f['id'] for f in s['fields']])
print('buttons:', [b['id'] or b['text'] for b in s['topnav_buttons']])
"
```
