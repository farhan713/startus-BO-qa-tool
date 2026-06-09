# Scenarios — saved test plans with natural-language recall

A **scenario** is a named, reusable test plan. Save one once → recall it
later by typing what you want in plain English:

> *"test STRAT-28795 with u1 on staging"*

…and the tool finds it, fills in variables, and runs.

## Why this matters

Today, when a tester wants to re-verify a bug:
- Opens Jira, digs up the steps
- Translates them into YAML or assembles them in the UI
- Runs the test
- 20 minutes per repro, every time

With scenarios:
- Type *"test STRAT-28795"* → done in 5 seconds

It becomes a **shared institutional memory** of every bug and edge case
your team has ever verified.

## Saving a scenario

After any import in the YAML Converter:
1. Drop the Excel
2. Verify the YAML preview is correct
3. Click **💾 Save as scenario**
4. Fill in:
   - **ID** — stable handle (`STRAT-28795`, `customer-search-smoke`)
   - **Title** — short description
   - **Description** — optional notes
   - **Tags** — e.g. `bug, regression, shipping`
   - **Variables** — `name=defaultvalue` per line, e.g. `order_number=WEB-12345`

Saved scenarios live in `knowledge_base/scenarios.json` (atomic writes; safe
to edit by hand if needed).

## Recalling a scenario

Three ways:

### 1. Dashboard launcher (primary)
Big search bar at the top of **Overview**:

> `test STRAT-28795 with u1 on staging`

The intent parser figures out:
- Which scenario you meant (regex + Gemini)
- Which user (from `directory.json`)
- Which environment
- Any variable overrides (`with order WEB-99999`)

Shows a confirmation card → **▶ Run now** → password prompt → goes.

### 2. Scenarios page (sidebar)
Browse the library as a table — id, title, screen, tags, run count, last run.
Click **▶ Run** on any row.

### 3. ⌘K command palette
Type the first few characters of a scenario id → Enter → jumps to Scenarios.

## How the launcher understands you

Two layers:

### Layer 1 — Regex pre-pass (free, instant, offline)
Catches the common shapes:
- Exact id appearing in the prompt (`STRAT-28795`)
- Numeric id stripped of prefix (`28795`)
- Title-word overlap (≥ 2 distinct words from the title)
- `with <var> <value>` / `{{var}}=<value>` overrides
- `as <user_alias>` / `with user <alias>`
- `on <env_alias>`

If regex matches with confidence ≥ 0.85, we skip the AI call. Saves quota.

### Layer 2 — Gemini fallback (opt-in, free tier)
When regex is uncertain:
- Gemini sees the full scenario library + user/env aliases
- Returns a JSON `{scenario_id, user_alias, env_alias, overrides, confidence, explanation}`
- The result merges with the regex pass — AI wins ties only if its
  confidence is higher

Requires `GEMINI_API_KEY` in `.env`. Without it, only the regex layer runs.

## Variables

Scenarios can have **variables** with default values:

```yaml
variables:
  order_number: WEB-12345
  customer_name: Smith
```

Steps reference them with `{{var}}`:

```yaml
steps:
  - { action: fill, target: Search, value: "{{order_number}}" }
```

Override at run time:
- From the launcher: *"…with order WEB-99999"*
- From the run modal: edit the field
- From the URL: `/api/scenarios/STRAT-28795/yaml?vars=order_number=WEB-99999`

## Directory (`knowledge_base/directory.json`)

A small JSON file mapping aliases to real values:

```json
{
  "users": {
    "u1":   { "username": "qauser1", "password": "", "role": "admin" },
    "demo": { "username": "demo",    "password": "", "role": "viewer" }
  },
  "envs": {
    "local":   { "url": "http://localhost:8080/...", "machine_id": "100" },
    "staging": { "url": "https://...",                "machine_id": "100" },
    "prod":    { "url": "https://...",                "machine_id": "100" }
  },
  "data": { "customer.smith": "Smith", "order.web1": "WEB-12345" }
}
```

**Passwords are intentionally blank** in the directory. The tool prompts
for them at run time (never written to disk). If you put a password in the
file, the launcher will use it — **don't do this for production accounts.**

## REST API

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/scenarios` | List all + return directory |
| `POST` | `/api/scenarios` | Save (body: id, title, steps OR yaml, ...) |
| `GET`  | `/api/scenarios/<id>` | Get one |
| `DELETE` | `/api/scenarios/<id>` | Delete |
| `GET`  | `/api/scenarios/<id>/yaml?vars=k=v,k=v` | Render runnable YAML |
| `POST` | `/api/scenarios/<id>/run` | Launch the runner |
| `POST` | `/api/intent-parse` | Parse free-text → `{scenario_id, user_alias, ...}` |

## Sharing across teammates

`scenarios.json` is a plain text file. Commit it to git and the whole team
sees every saved scenario. New teammate joins → clones the repo → has
instant access to *every test scenario the team has ever curated.*

You and Aniket can each push your own scenarios on your branches; they
merge cleanly because the file is a flat keyed-by-id dict.
