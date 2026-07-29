# Convert Test Cases — screen design

The main screen of the tool: where a tester turns manual test cases into an
automatic test. Both files are self-contained HTML — just open them in a
browser, no server or build step needed.

| File | What it is |
|---|---|
| `Stratus-QA-Convert-Screen-DEMO-clickable.html` | **Interactive prototype.** Every control works. Use the "Jump to" buttons in the blue bar to see all three stages (Start / Converting / Review), and the "Show explanations" toggle to see what each field does and why. |
| `Stratus-QA-Convert-Screen-DESIGN-document.html` | **Design document.** The three states annotated field by field (27 numbered notes), the 6 rules that keep the screen usable by non-technical testers, and an element-to-code map showing which existing API backs each part. |

## Design goals

The screen is built so a **non-technical tester** can use it without training:

1. Plain words only — no "YAML", "parse", "selector" on the main path
2. One primary button per stage
3. Nothing required except the test-case file
4. Safe by default — practice run pre-selected, test server only
5. Problems appear as plain-English questions, never error codes
6. Nothing can be lost — autosave, undo, and skip everywhere

## How it maps to this repo

Most of the screen is served by endpoints that already exist:

- Upload → `POST /api/import-testcases` (`framework/testcase_importer.py`)
- "We understood 19 of 22 steps" → `n_steps_translated` / `n_steps_total` from that response
- Example file → `GET /api/template/xlsx` (`framework/template_builder.py`)
- Describe in English → `POST /api/nl-to-yaml`
- Fix a step in your own words → `POST /api/modify-testcases` (`framework/prompt_engine.py`)
- Practice run → `safe_mode` / `read_only` flags
- Server picker → `GET /api/profiles` + `POST /api/test-connection`
- Save / Run → `POST /api/scenarios`, `POST /api/run` + `/api/events`

See the "element-to-code map" section in the design document for the full table.
