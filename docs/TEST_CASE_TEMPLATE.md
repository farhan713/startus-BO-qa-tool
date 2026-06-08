# Stratus QA — Test Case Template

A standardized format for writing test cases so the tool can import them
**100% cleanly** (no `todo` markers) and run them with zero edits.

## The 10-column template

Fill in one row per **step**, not one row per scenario. Repeat the TC ID
and Scenario columns on every step row of the same test.

| Column      | Required        | Example                          | Notes |
|-------------|:---------------:|----------------------------------|-------|
| **TC ID**   | ✓               | `TC-CUST-001`                    | Stable ID — survives reorders |
| **Scenario**| ✓               | `Customer search by Last Name`   | Group label (repeated per step) |
| **Screen**  | ✓               | `customerlist`                   | Must match a catalog screenname |
| **Step**    | ✓               | `1`, `2`, `3`                    | Per-scenario step order |
| **Action**  | ✓               | `click`, `fill`, `select`, …     | Pick from the dropdown |
| **Target**  | when applicable | `#Search` · `Last Name` · `Save` | Element id, label, or button text |
| **Value**   | for fill/select | `QA`, `Active`, `2025-01-01`     | Empty for click/assert |
| **Expected**| optional        | `5 results shown`                | Free text — comment only |
| **Priority**| optional        | `P1`, `P2`, `P3`                 | For filtering / reporting |
| **Notes**   | optional        | `Verify TB_ORDER.STATUS manually`| Saved as YAML comment |

## Allowed actions

| Action            | When to use                       | Target                       | Value           |
|-------------------|-----------------------------------|------------------------------|-----------------|
| `click`           | Click a button or link            | `#buttonId` · `"Save"` · `New`| (blank)        |
| `fill`            | Enter text into a field           | `#fieldId` · `"Last Name"`   | the value       |
| `select`          | Pick from a dropdown              | `#fieldId` · label           | option label    |
| `open_search`     | Reveal the search criteria pane   | (blank)                      | (blank)         |
| `wait`            | Pause N seconds                   | `N`                          | (blank)         |
| `assert_visible`  | Element must be visible           | `#element` · `"text"`        | (blank)         |
| `assert_text`     | Page must contain text            | (blank)                      | exact text      |
| `assert_no_errors`| No error words on the page        | (blank)                      | (blank)         |
| `assert_rows_min` | Grid has at least N rows          | (blank)                      | N               |
| `assert_rows_max` | Grid has at most N rows           | (blank)                      | N               |
| `screenshot`      | Capture a screenshot              | filename suffix              | (blank)         |

## Download the template

| Format          | Use                                     | Where |
|-----------------|-----------------------------------------|-------|
| `.xlsx` (Excel) | Direct fill-in. Has data validation.    | **New run → One screen → Add custom test cases → ⬇ Template .xlsx** |
| `.csv`          | Google Sheets, LibreOffice, anything    | **⬇ Template .csv (Sheets)** |

Or hit the endpoints directly:
- `GET /api/template/xlsx`
- `GET /api/template/csv`

## Google Sheets workflow

1. Download the `.csv` template
2. In Google Sheets: **File → Import → Upload** → pick the csv → **Replace spreadsheet**
3. Add data validation manually:
   - Select column **E** (Action) → Data → Data validation → List of items: `click,fill,select,open_search,wait,assert_visible,assert_text,assert_no_errors,assert_rows_min,assert_rows_max,screenshot`
   - Select column **I** (Priority) → same → `P1,P2,P3`
4. Fill in your tests, one row per step
5. **File → Download → Microsoft Excel (.xlsx)** when done
6. Drop the `.xlsx` into the tool

## Bulk import — many screens, one Excel

You don't have to import one screen at a time. The Screen column on every
row determines where the test goes. After import:

- The editor shows the **combined YAML** (all screens in one file) — useful
  when you want to skim everything before running.
- A **⬇ Download ZIP** button appears if the template covers more than one
  screen. The ZIP contains one `<screenname>_tests.yaml` per screen plus a
  `README.txt` index.

## Why this beats free-prose test cases

The legacy importer reads pre-existing Celerant test files (like the
sample STRAT-28795 / Test-Case-26949 / customerNotification ones) and uses
regex rules to translate prose into actions. It gets 5–70% accuracy
depending on writing style.

The template guarantees **100%** because every cell is already an action,
target, or value — no prose translation needed.

| Source                                | Auto-translated |
|---------------------------------------|----------------:|
| Free prose Excel (legacy)             | 5 – 70 %        |
| **Template Excel** (this format)      | **100 %**       |

## Worked example

The template ships with a filled **Example** sheet showing this Customer
test rendered correctly:

| TC ID       | Scenario                       | Screen        | Step | Action            | Target             | Value | Expected           |
|-------------|--------------------------------|---------------|-----:|-------------------|--------------------|-------|--------------------|
| TC-CUST-001 | Customer search by Last Name   | customerlist  | 1    | click             | Customer Management|       | Menu hover         |
| TC-CUST-001 | Customer search by Last Name   | customerlist  | 2    | click             | Customer List      |       |                    |
| TC-CUST-001 | Customer search by Last Name   | customerlist  | 3    | open_search       |                    |       |                    |
| TC-CUST-001 | Customer search by Last Name   | customerlist  | 4    | fill              | Last Name          | Smith |                    |
| TC-CUST-001 | Customer search by Last Name   | customerlist  | 5    | click             | Search             |       |                    |
| TC-CUST-001 | Customer search by Last Name   | customerlist  | 6    | assert_no_errors  |                    |       |                    |

The above becomes this YAML (auto-generated, runnable as-is):

```yaml
tests:
- screen: customerlist
  name: Customer search by Last Name
  steps:
  - action: click
    target: Customer Management
  - action: click
    target: Customer List
  - action: open_search
  - action: fill
    target: Last Name
    value: Smith
  - action: click
    target: Search
  - action: assert_no_errors
```
