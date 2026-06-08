# Stratus QA Tool — Demo

A working end-to-end demo of the Stratus QA automation tool against the
**Customer module** — chosen because it has the complete feature set we
need to show off: search / list / action buttons / new / edit / print /
close.

## What the demo does

1. **Logs in** to Stratus BackOffice
2. **Opens the Customer List** screen
3. **Searches** by last name (`SMITH`) with the search criteria form
4. **Opens the Action dropdown** and verifies Edit / Delete / Print List are present
5. **Creates a new customer** — clicks New, fills the detail form, clicks Save
6. **Edits an existing customer** — selects a row, clicks Edit, modifies, saves
7. **Triggers Print List** — confirms the print job initiates
8. **Closes the screen**
9. **Takes a final screenshot** and writes it to `reports/screenshots/`

The demo runs in a **real browser** in slow-motion so you can watch
every step. Set `--headless` to hide it for CI.

---

## Prerequisites

Before the demo can run, you need:

1. **Stratus BackOffice reachable** — either running locally or via VPN
2. **A test user** with permission to view + create + edit customers
3. **A `SMITH` customer** in the test database (for the edit/print steps).
   If none exist those steps are skipped gracefully.
4. **Python 3.10+ and Playwright Chromium installed** — see the
   [Quickstart](docs/quickstart-for-qa.md) for one-time setup.

---

## One-time setup (5 minutes)

```bash
cd qa-automation
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Edit `.env`:

```
APP_BASE_URL=http://your-stratus-dev.example.com/backoffice
TEST_USER=your_qa_user
TEST_PASSWORD=your_qa_password
```

(The demo only needs URL + login. DB connection is not required for the
UI demo, but fill it in anyway for the smoke tests.)

---

## Run the demo

### Default — visible browser, slow-motion, narrated

```bash
python demo.py
```

You'll see:

```
========================================================================
  Stratus QA Tool — Customer Module Demo
========================================================================
    → target: http://dev-stratus.example.com/backoffice
    → user:   qa_automation
    → mode:   headless=False slow_mo=600ms

[ Step 1 ] Logging into Stratus BackOffice
    → opening http://.../backoffice/login.jsp
    → entering credentials for user 'qa_automation'
    ✓ login submitted

[ Step 2 ] Navigating to Customer List screen
    → opening http://.../backoffice/stratus?screenType=CustomerList
    ✓ Customer List loaded — New, Action and Close buttons visible

[ Step 3 ] Searching for customers with last name 'SMITH'
    → filled Last Name = 'SMITH'
    ✓ search complete — grid has 12 row(s)

[ Step 4 ] Opening the Action dropdown
    ✓ Action menu exposes: Edit, Delete, Print List

[ Step 5 ] Creating a NEW customer via the New button
    → filling form: QA-Demo Tester1234 / Co: StratusQA
    → clicking Save
    ✓ save succeeded — no errors on the page

[ Step 6 ] Editing an existing customer (search → row → Edit action)
    → selected the first matching row
    → changing first name to 'Updated123'
    ✓ edit saved

[ Step 7 ] Triggering Print List from the Action menu
    ✓ print job initiated (popup/report may open)

[ Step 8 ] Closing the Customer List screen
    ✓ Close clicked — back to main
    ✓ final screenshot saved → reports/screenshots/demo_final_1716543210.png

========================================================================
  DEMO COMPLETE
========================================================================

  Total runtime: 42.3s
```

### Variations

```bash
# Headless (no browser window) — for CI / overnight runs
python demo.py --headless

# Fast — 100ms slow-mo instead of 600ms
python demo.py --fast

# Read-only — only login + search + close, no Create / Edit / Print
python demo.py --read-only

# Quiet — no step narration
python demo.py --no-narrate
```

---

## What this demo proves

| Capability | Demonstrated by |
|---|---|
| **Login automation** | Step 1 |
| **Page object pattern** | `framework/ui/pages/login_page.py`, `customer_list_page.py`, `customer_detail_page.py` |
| **List + grid handling** | Steps 2, 3 — including jqGrid row iteration |
| **Search / filter forms** | Step 3 — handles text fields + dropdowns + date ranges |
| **Action button menus** | Step 4 — opens dropdown, verifies items |
| **CRUD: Create** | Step 5 — New → fill form → Save |
| **CRUD: Read/Search** | Step 3 — Search by criteria |
| **CRUD: Update** | Step 6 — Row select → Edit → modify → Save |
| **Print / Report** | Step 7 |
| **Screen navigation** | Step 8 — Close returns to main |
| **Stable selectors** | All page objects use the real Stratus IDs from JSP templates |
| **Self-narrating output** | Live terminal log of each step |
| **Failure isolation** | Each segment in `try/except`; demo continues if one step fails |
| **Artifact capture** | Final screenshot + per-failure auto-screenshot |

---

## Run it as proper pytest tests

The same coverage runs as 7 isolated pytest cases for CI:

```bash
# All Customer-module demo tests
pytest -m demo

# Only the smoke subset (fastest)
pytest -m smoke

# Single test
pytest tests/demo/test_customer_demo.py::test_create_new_customer -v

# Headed mode for debugging a single test
HEADLESS=false SLOW_MO_MS=400 pytest -m demo -v
```

The pytest version generates a clean HTML report at `reports/report.html`
with screenshots on failure.

---

## Run it as BDD scenarios

Same coverage, written in plain-English Gherkin for stakeholders:

```bash
pytest tests/features/step_defs/test_customer_management_steps.py -v
```

The feature file at `tests/features/customer_management.feature` is
readable by non-technical reviewers — it spells out every scenario in
Given/When/Then format.

---

## What you should see — visually

1. Chrome window opens (unless `--headless`)
2. Login form is filled in field by field
3. Customer List page renders with jqGrid
4. Search criteria expands, fields are typed character-by-character (at slow-mo)
5. Search runs, grid reloads with results
6. Action dropdown opens, menu items appear
7. New button click → detail form opens
8. Form fields fill, Save clicks
9. Page navigates back, search re-runs
10. Row gets clicked, Edit action fires
11. Detail form opens again, first name updates, Save
12. Print List clicks → popup or print preview may appear
13. Close clicks → returns to main
14. Browser closes
15. Terminal summarises pass/fail per step

---

## What changes if Stratus is not reachable?

The demo fails fast with a clear message:

```
[ Step 1 ] Logging into Stratus BackOffice
    → opening http://localhost:8080/backoffice/login.jsp
    ✗ ConnectionRefusedError: [Errno 61] Connection refused

========================================================================
  DEMO ABORTED
========================================================================
    ✗ Stratus is not reachable at http://localhost:8080/backoffice
```

To run the demo against a real Stratus, either:

- Start the local Tomcat with the Stratus WAR deployed, or
- Point `APP_BASE_URL` in `.env` at a reachable dev/staging URL (with VPN if needed)

---

## What this demo does NOT yet do (next-phase work)

- It doesn't talk to SQL Server to verify the new customer actually persisted —
  that's the [DB layer](framework/db/) ready to plug in
- It doesn't auto-discover new screens (Phase 1 Knowledge Base work)
- It doesn't generate test cases on its own (Phase 1 Knowledge Base work)
- It only covers the Customer module; other modules need their own page
  objects (or — better — get auto-discovered)

The point of this demo is to **prove the foundation works end-to-end**
on one real, complete module. From here, scaling to the rest of the
BackOffice is a known, tractable problem.

---

## Files this demo created

```
qa-automation/
├── demo.py                                  ← run this
├── DEMO.md                                  ← you are here
├── framework/ui/pages/
│   ├── customer_list_page.py                ← Customer List page object
│   ├── customer_detail_page.py              ← Customer Detail page object
│   └── login_page.py                        ← (existing)
├── tests/
│   ├── demo/test_customer_demo.py           ← 7 pytest demo tests
│   └── features/
│       ├── customer_management.feature      ← Gherkin scenarios
│       └── step_defs/
│           └── test_customer_management_steps.py
```

---

## Quick-reference card

| Want to… | Run |
|---|---|
| Watch the demo end-to-end | `python demo.py` |
| Run silently for CI | `python demo.py --headless` |
| Just verify login + read-only | `python demo.py --read-only` |
| Run as pytest cases | `pytest -m demo` |
| Run just smoke | `pytest -m smoke` |
| Run BDD scenarios | `pytest tests/features/step_defs/test_customer_management_steps.py` |
| See the HTML report | open `reports/report.html` |
| See the final screenshot | open `reports/screenshots/demo_final_*.png` |
