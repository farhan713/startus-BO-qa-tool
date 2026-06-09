# Prompt rules — what the YAML converter understands

The YAML Converter has a "Conversion instructions" textarea. Type one
instruction per line (or separate with `;`). The tool runs the rule engine
first (free, instant). Anything the engine can't handle gets sent to
Gemini if you enable ✨ AI.

## Tier 1 — deterministic rules (no API key needed)

| Pattern (case-insensitive) | What it does | Example |
|---|---|---|
| `all tests are on screen X` | Force every test's `screen:` to X | `all tests are on screen customerlist` |
| `default screen is X` | Fill blank screens with X | `default screen is receiptslist` |
| `use 'V' for {{var}}` | Replace placeholder in every target/value | `use 'John Smith' for {{customer}}` |
| `{{var}} = 'V'` | Same as above, shorthand | `{{customer}} = 'John Smith'` |
| `screenshot after every X` | Insert screenshot step after each `X` action | `screenshot after every click` |
| `wait N seconds after every X` | Insert wait step after each `X` action | `wait 2 seconds after every fill` |
| `skip priority PN` | Drop tests with priority N | `skip priority P3` |
| `append assert_no_errors` | Add assert_no_errors as last step of every test | `append assert_no_errors` |
| `assert no errors at the end of every test` | Same as above | |
| `run each test N times` | Duplicate every test N times | `run each test 3 times` |
| `default value for X is 'V'` | Fill blank Value cells where Target=X | `default value for Last Name is 'QA'` |
| `uppercase target` | UPPERCASE every target string | `uppercase target` |
| `lowercase screens` | lowercase every screen name | `lowercase screens` |
| `only tests on screen X` | Drop tests on other screens | `only tests on screen customerlist` |

Multiple rules per submission are applied in order. The result panel
shows every rule that fired and what it changed.

## Tier 2 — AI refinement (opt-in)

Anything the rule engine can't match becomes an "ignored" line. If you
tick **✨ Use AI (Gemini)**, those lines get sent to Gemini along with
the current YAML, and Gemini returns a refined YAML. Examples that
benefit from AI:

- `Make these tests more thorough`
- `Add negative test cases for invalid input`
- `Generate edge cases for the date field`
- `Combine duplicate steps`
- `Add a test for SQL injection in Last Name`

### Setup (one time per laptop)

1. Get a free key — https://aistudio.google.com/apikey (no credit card)
2. Edit `qa-automation/.env`:
   ```
   GEMINI_API_KEY=your_key_here
   GEMINI_MODEL=gemini-flash-latest
   GEMINI_DAILY_REQUEST_LIMIT=200
   ```
3. Restart the tool (`./launch.sh stop && ./launch.sh`)
4. The YAML Converter shows **"AI ready · gemini-flash-latest · N requests left today"**
5. Tick **✨ Use AI** before dropping the Excel — that's it

### Cost and safety

- **Free tier**: 1,500 requests/day, no card required.
- **Daily limit**: hardcoded ceiling (`GEMINI_DAILY_REQUEST_LIMIT`) so you
  can never accidentally blow past free quota. Default 200/day.
- **Build-time only**: the resulting YAML has zero runtime LLM dependency.
  Once you save it, it runs forever offline.
- **Data privacy**: on the free tier, Google may use your prompts to
  train models. Don't send real customer data — use synthetic values.

## Where can I add instructions?

| Place | When to use |
|---|---|
| YAML Converter → Conversion instructions textarea | Most common; per-import |
| `PROMPT:` rows at the top of the Excel file (above headers) | Bake instructions into a test file you share with the team |

A QA can author once and the same instructions apply every time anyone
re-imports.

## Transparency

Every conversion shows a "Prompt applied" panel:
- ✓ rules that fired (and what they changed — e.g. "substituted in 8 fields")
- ⊘ rules that were ignored (with the original prompt text)
- ✨ AI status — was it called, did it change anything, any error

If something doesn't fire as expected, the panel tells you why.
