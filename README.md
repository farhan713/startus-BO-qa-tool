# Stratus BackOffice — QA Automation Framework

End-to-end test automation for the Stratus BackOffice retail/consignment system.

Built on **Python 3.10+ / pytest / Playwright / pytest-bdd / pyodbc** following the
**Hybrid Pyramid** strategy:

```
        /\        E2E UI       — Playwright + Cucumber-style BDD
       /  \                       (login → intake → tender → receipt)
      /----\
     /      \    API/Service   — requests against /stratus dispatcher
    /        \                    + UserAuthenticationServlet.do
   /----------\
  /            \  DB / State   — pyodbc against SQL Server
 /              \                  (verify Hibernate-mapped tables)
/________________\ Unit          — already in the Java /test/ folder
```

---

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.10+ | `python3 --version` |
| Microsoft ODBC Driver for SQL Server | 18 | required by `pyodbc` |
| The Stratus app | running locally or in DEV | default `http://localhost:8080/backoffice` |
| SQL Server | reachable | with the stratus schema deployed |

### macOS — install ODBC driver

```bash
brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
brew install msodbcsql18 mssql-tools18
```

### Linux (Debian/Ubuntu)

```bash
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list \
  | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev
```

### Windows

Download from <https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>.

---

## 2. Setup

```bash
cd qa-automation

# 1. virtual env
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. python deps
pip install -r requirements.txt

# 3. playwright browser binaries
playwright install chromium

# 4. environment config
cp .env.example .env
#   then edit .env and fill in: APP_BASE_URL, TEST_USER, TEST_PASSWORD,
#   DB_HOST, DB_USER, DB_PASSWORD, etc.
```

---

## 3. Running tests

```bash
# everything
pytest

# only smoke tests (recommended on every commit, < 30s)
pytest -m smoke

# only the API layer
pytest -m api

# only the UI layer (Playwright)
pytest -m ui

# only the DB layer
pytest -m db

# only BDD scenarios
pytest -m bdd

# parallel (4 workers) — UI tests included
pytest -n 4

# single test file
pytest tests/smoke/test_login_smoke.py -v

# headed browser for debugging
HEADLESS=false SLOW_MO_MS=300 pytest -m ui tests/smoke/test_login_smoke.py
```

### Reports

After a run you get:

- `reports/report.html` — pytest HTML report (open in browser)
- `reports/allure-results/` — raw Allure data
- `reports/screenshots/` — auto-capture on failure
- `reports/videos/` — Playwright video on failure

To view Allure:

```bash
allure serve reports/allure-results
```

---

## 4. Project layout

```
qa-automation/
├── README.md                  ← you are here
├── requirements.txt
├── pytest.ini                 ← markers, addopts, log format
├── .env.example               ← copy to .env
├── conftest.py                ← shared fixtures (browser, page, api, db)
│
├── config/
│   └── settings.py            ← typed, immutable config from .env
│
├── framework/                 ← the reusable framework, not the tests
│   ├── api/
│   │   ├── base_client.py     ← requests wrapper + logging
│   │   └── stratus_client.py  ← /stratus + /UserAuthenticationServlet.do
│   ├── db/
│   │   └── sqlserver_helper.py ← pyodbc + cursor ctx-mgr
│   ├── ui/
│   │   ├── base_page.py       ← Page-Object base class
│   │   └── pages/
│   │       └── login_page.py
│   └── utils/
│       └── logger.py
│
├── tests/
│   ├── smoke/                 ← critical-path tests run on every commit
│   │   ├── test_api_smoke.py
│   │   ├── test_db_smoke.py
│   │   └── test_login_smoke.py
│   └── features/              ← Gherkin BDD scenarios
│       ├── login.feature
│       └── step_defs/
│           └── test_login_steps.py
│
└── reports/                   ← HTML, Allure, screenshots, videos (git-ignored)
```

---

## 5. How the framework maps to Stratus' architecture

| Stratus piece | Test approach | Files |
|---|---|---|
| `login.jsp` + `UserAuthenticationServlet.do` | UI (Playwright) + API (requests POST) | `framework/ui/pages/login_page.py`, `framework/api/stratus_client.py::login()` |
| `StratusServlet` (`/stratus`, screenType dispatcher) | API — typed `dispatch(screen_type, payload)` method | `framework/api/stratus_client.py::dispatch()` |
| JSP screens + jQuery / Dust.js / jqGrid | UI via Playwright Page Objects (one per screen) | `framework/ui/pages/` (add as you go) |
| Hibernate entities (Receipt, TradeCard, Sku, …) | DB verification via `SqlServerHelper.fetch_one/all` | `framework/db/sqlserver_helper.py` |
| Servlet-routed reports (`/report/run-sql`, Crystal) | API + DB cross-check | extend `StratusApiClient` |

---

## 6. Next milestones (after this slice is green)

1. **Add a stable selector convention** — agree with devs on `data-test="…"` attributes for the top 20 screens. Brittle JSP selectors are the #1 source of UI test flake.
2. **Map the screenType discriminators** — read `StratusServlet.java` and document every screenType bean in `framework/api/stratus_client.py` as a typed method (`consignment_intake()`, `tender_capture()`, …).
3. **Test-data factory** — `framework/factory/` with builders for `Customer`, `Sku`, `Receipt` so each test owns its data.
4. **CI** — wire `pytest -m smoke` into Jenkins/GitHub Actions on every push; nightly runs the full regression.
5. **Allure trends + Slack alerts** on failure.
6. **Buy/Trade/Consignment regression pack** (STRAT-30194) — the highest-business-value flow.

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `pyodbc.InterfaceError: Data source name not found` | ODBC driver not installed — see §1 |
| `playwright._impl._api_types.Error: Executable doesn't exist` | run `playwright install chromium` |
| `ConnectionRefusedError` on smoke | Stratus app is not running on the URL in `.env` |
| All UI tests time out at login | wrong `APP_BASE_URL` or login page redirect to SSO |
| `test_core_tables_exist` fails | wrong DB or schema not deployed; check `DB_NAME` |
