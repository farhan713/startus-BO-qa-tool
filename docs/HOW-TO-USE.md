# How to Use the Stratus QA Tool

> A complete walkthrough for anyone on the team. No coding required.
> If you can paste text and click a button, you can use this tool.

---

## What you'll be able to do after reading this

- Set up the tool on your laptop (5 minutes, one time only)
- Run a full test of the entire BackOffice with one command
- Run a quick smoke test in under 5 minutes
- Test only a specific area (just consignment, just payment, etc.)
- Read the test report and find bugs with screenshots and video
- File a bug into Jira with copy-paste from the report

---

## Table of contents

1. [One-time setup (5 minutes)](#1-one-time-setup)
2. [The simplest possible run](#2-the-simplest-possible-run)
3. [Running a full BackOffice test](#3-running-a-full-backoffice-test)
4. [Running a scoped test (faster)](#4-running-a-scoped-test)
5. [Running a smoke test (fastest)](#5-running-a-smoke-test)
6. [Reading the report](#6-reading-the-report)
7. [What to do when you find a bug](#7-what-to-do-when-you-find-a-bug)
8. [Adding business rules](#8-adding-business-rules)
9. [Common questions](#9-common-questions)
10. [When something goes wrong](#10-when-something-goes-wrong)
11. [When to run the tool](#11-when-to-run-the-tool)
12. [Quick reference card](#12-quick-reference-card)

---

## 1. One-time setup

> Skip this section if someone on the team has already set up the tool on
> your laptop.

**On a Mac:** open the **Terminal** app (press ⌘+Space and type "Terminal").
**On Windows:** open **Command Prompt** (press Windows+R and type "cmd").

Then run these commands one by one — copy and paste them in:

```bash
cd qa-automation

# Create a private Python environment for the tool
python3 -m venv .venv

# Activate it (Mac/Linux)
source .venv/bin/activate

# Activate it (Windows — different command)
# .venv\Scripts\activate

# Install all the tool's dependencies
pip install -r requirements.txt

# Install the browser the tool uses
playwright install chromium

# Make your local config file
cp .env.example .env
```

Now open the **`.env`** file in any text editor — Notepad, TextEdit, VS Code,
even Excel will open it. Fill in these values:

```
APP_BASE_URL=http://dev-stratus.company.com/backoffice
TEST_USER=your_qa_username
TEST_PASSWORD=your_qa_password
DB_HOST=your_sql_server_host
DB_USER=sa
DB_PASSWORD=your_db_password
DB_NAME=stratus_dev
```

Save the file. Done. **You will never have to do this setup again on this
computer.**

---

## 2. The simplest possible run

The single command to test the entire BackOffice:

```bash
stratus-qa test
```

Walk away. Get coffee. Come back in 30–60 minutes. Open
`reports/report.html` in your browser. Done.

The rest of this document explains the variations and what to do with
the results.

---

## 3. Running a full BackOffice test

```bash
stratus-qa test
```

When you run this, here is exactly what the tool does, in order:

| Step | What happens | About how long |
|---|---|---|
| 1. **Log in** | The tool opens a browser, goes to your BackOffice URL, types your username and password, and logs in. | 10 seconds |
| 2. **Discover** | It walks through every menu and finds every screen — even ones you might not know exist. | ~10 minutes |
| 3. **Classify** | For each screen, it figures out what kind it is — a list, a form, a wizard, a popup, a report. | A few seconds per screen |
| 4. **Generate tests** | It comes up with about 25 test cases per screen — happy path, empty fields, negative numbers, huge values, special characters, duplicates, and more. | A few seconds per screen |
| 5. **Run tests** | It actually clicks the buttons, fills the forms, submits, and watches what happens. | 10–20 seconds per test |
| 6. **Check database** | After every Save, it checks SQL Server directly to confirm the data was really stored. | 1 second per check |
| 7. **Take screenshots** | Whenever something fails, it captures a screenshot at the moment of failure. | Instant |
| 8. **Build report** | At the end, it writes an HTML report with everything. | A few seconds |

Total time on a typical Stratus dev environment: **30 to 60 minutes**.

Run this before every release, or overnight, or whenever you want. There
is no cost and no limit.

---

## 4. Running a scoped test

When you don't need the full hour, you can ask the tool to only test
part of the app:

```bash
stratus-qa test --scope consignment
stratus-qa test --scope payment
stratus-qa test --scope reports
stratus-qa test --scope customers
```

Use plain English. The tool figures out which screens you mean and only
tests those. A scoped run usually takes 5–15 minutes.

You can also write longer descriptions:

```bash
stratus-qa test --scope "all screens that have a price field"
stratus-qa test --scope "buy/trade intake from start to finish"
stratus-qa test --scope "anything that touches the customer table"
```

---

## 5. Running a smoke test

The fastest possible run — under 5 minutes. Tests only the ~20 most
critical screens (login, main menu, POS, payment, receipt, customer
list, SKU list, the things that absolutely cannot be broken).

```bash
stratus-qa test --smoke
```

Run this any time. After lunch. After a coworker pushes a change. Before
you go home. It's cheap and fast.

---

## 6. Reading the report

When a test run finishes, the tool prints something like:

```
============================================================
  Stratus QA Run Complete
  ----------------------------------------
  Screens tested:        187
  Total test cases:      4,612
  Passed:                4,597
  Failed:                12
  Skipped:               3
  Duration:              42 minutes
  Report:                reports/report.html
============================================================
```

**Open `reports/report.html` in your browser.** That's it — no special
viewer needed. The report is just an HTML file.

At the top you see the same summary. Below that, every failure is listed
with these pieces of information:

| Section in report | What it shows |
|---|---|
| **Screen** | The screen name and its URL — click to jump there in BackOffice |
| **Test case** | What the tool was trying to do, in plain English ("Save a customer with empty last name") |
| **Expected** | What should have happened ("Form should show error: 'Last name is required'") |
| **Actually got** | What did happen ("Form was saved successfully — no error shown") |
| **Screenshot** | A picture of the screen at the moment of failure |
| **Video** | A short video of the whole test, from login to failure |
| **Database evidence** | The SQL query the tool ran, and the row it found (or didn't find) |
| **Plain-English bug description** | Copy-paste-ready text you can put straight into Jira |

For trend charts and history across multiple runs over time, also run:

```bash
allure serve reports/allure-results
```

This opens a dashboard with: pass/fail trends, which screens are flakiest,
which tests have been failing longest, and so on.

---

## 7. What to do when you find a bug

Three steps:

**Step 1 — Reproduce manually**

Open the URL from the report. Click through the steps yourself. If it
fails the same way → it's a real bug. If it works fine for you → the
test is flaky. Note which one.

**Step 2 — File the bug**

Open Jira. Create a new issue. **Copy the plain-English bug description
from the report** and paste it into the Description field. **Attach the
screenshot** from the report. Done in 2 minutes.

**Step 3 — Mark the flaky test (if applicable)**

If the test passed manually but failed in the tool, that means the test
itself is wrong (timing issue, selector issue, etc.). Tell the automation
engineer — they'll tune it. Don't keep filing the same flake as a bug.

---

## 8. Adding business rules

Sometimes the tool will report a "bug" that isn't actually a bug — it
just doesn't know your business rules. For example, it might try to save
a SKU with a negative price and report "the form saved successfully —
why didn't it error?"

To teach the tool the rule, open the file **`business_rules.py`** and
add a line. You don't need to know Python — just copy the format of
existing rules. Examples:

```python
# Add rules like these — anyone can edit this file
rules = [
    "Negative prices should be rejected with the message 'Price must be ≥ 0'",
    "Consignor must be 18 years or older",
    "Receipts cannot be deleted, only voided",
    "SKU IDs must be unique within a store",
    "Crystal Reports may take up to 30 seconds to render",
]
```

Save the file. The next test run will read this and stop reporting
false positives for these cases.

This is the **one** place where the tool needs the team's domain
knowledge. Everything else it figures out on its own.

---

## 9. Common questions

**Does it test the database too?**
Yes. After every Save it checks SQL Server directly to confirm the data
was really stored. The report shows the exact SQL query it ran.

**What if I want to test a screen the tool doesn't know about yet?**
Just run `stratus-qa test --rediscover`. The tool will re-crawl the app,
find any new screens, and start testing them. No setup needed.

**Will it test payment and tender screens?**
Those are protected — they have hand-written specs in `money_flows/`. The
auto-discovery tool is not allowed to mess with money flows. It runs the
specs and confirms they still pass, but doesn't invent its own tests for
money.

**Can two people run it at the same time?**
Yes, if you have different test users. The tool isolates its data so two
runs don't pollute each other.

**Just want to see what screens exist, not actually test?**
```bash
stratus-qa crawl --list
```
Prints every screen the tool discovered with its URL and what type it
classified each as.

**Does the tool delete data?**
No. By default it only creates new test records (with unique IDs).
Delete actions are flagged for human review unless you explicitly run
`stratus-qa test --allow-delete`.

**Can I run it in headed mode (so I can watch it)?**
```bash
HEADLESS=false stratus-qa test --smoke
```
Browser windows pop up and you can watch the tool work. Useful the first
time you run it.

**Does it work in production?**
By default, no. The tool refuses to run against URLs that aren't on the
allowed list in `.env`. This prevents accidents.

**Will it find every bug?**
No automated tool can. It catches the kind of bugs a manual tester would
catch on the surface — crashes, error messages, missing fields, wrong
totals, screens that don't save. Subtle business-logic bugs still need
human review. The tool buys you time to focus on the hard stuff.

**Is it slower than a manual tester?**
For one screen, similar. For 200 screens, the tool is roughly 100x
faster — and it doesn't get tired or distracted.

---

## 10. When something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| "Cannot connect to BackOffice" | The URL is wrong or you're not on VPN | Check `APP_BASE_URL` in `.env`, check VPN |
| "Login failed" | Wrong credentials | Check `TEST_USER` and `TEST_PASSWORD` in `.env` |
| "Database connection failed" | Wrong DB host/credentials or no VPN | Check the `DB_*` lines in `.env` |
| "playwright not found" | Browser not installed | Run `playwright install chromium` |
| "ModuleNotFoundError" | Dependencies missing | Run `pip install -r requirements.txt` again |
| The tool hangs forever | Something is stuck | Press Ctrl+C, then run `stratus-qa doctor` |
| Same test passes and fails randomly | Flaky test | Flag to the automation engineer — don't file as a bug |
| Tool says "no screens discovered" | Login may have failed silently | Run with `HEADLESS=false` and watch what happens |
| Report file is empty | Run was killed before finishing | Re-run with `--resume` to pick up where it left off |

When in doubt:

```bash
stratus-qa doctor
```

This runs a health check on your setup and tells you exactly what's
wrong (missing browser, bad DB credentials, unreachable BackOffice, etc.).

---

## 11. When to run the tool

| Situation | Command | Time |
|---|---|---|
| Before every release | `stratus-qa test` | 30–60 min |
| Every morning (automated) | (runs overnight, report by 8 AM) | overnight |
| After someone pushes a big change | `stratus-qa test --scope <area>` | 5–15 min |
| Before lunch / coffee break | `stratus-qa test --smoke` | <5 min |
| When a specific screen got reported broken | `stratus-qa test --scope "<screen name>"` | <2 min |
| When you have a quiet hour | `stratus-qa test --scope "anything you want"` | varies |

There is no per-run cost. Run it as often as you like.

---

## 12. Quick reference card

**Run everything:**
```
stratus-qa test
```

**Run only one area:**
```
stratus-qa test --scope consignment
stratus-qa test --scope payment
stratus-qa test --scope "any plain-English description"
```

**Run only smoke:**
```
stratus-qa test --smoke
```

**See what screens exist:**
```
stratus-qa crawl --list
```

**Find what's wrong with my setup:**
```
stratus-qa doctor
```

**Re-discover newly added screens:**
```
stratus-qa test --rediscover
```

**Watch the tool work (headed mode):**
```
HEADLESS=false stratus-qa test --smoke
```

**See the report:**
```
open reports/report.html         (Mac)
start reports/report.html        (Windows)
```

**See trend dashboard:**
```
allure serve reports/allure-results
```

---

That's the whole tool.

For the design and architecture, see [architecture.md](architecture.md).
For the short pitch, see [PITCH.md](PITCH.md).
For terms in plain English, see [glossary.md](glossary.md).
