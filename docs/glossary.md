# Glossary

Plain-language definitions of every term used in this documentation.

| Term | Meaning |
|---|---|
| **Stratus BackOffice** | The Java/Spring/JSP web app under test — retail / consignment / POS back office system |
| **Screen** | One logical user-facing page (e.g. "Customer List", "SKU Detail", "Buy-Trade Intake") |
| **Pattern** | A repeating shape of screen — `list`, `detail`, `wizard`, `modal`, `report` |
| **Pattern Runner** | A piece of generic Python that knows how to test one pattern, regardless of which specific screen |
| **Catalog** | The folder of YAML files (one per screen) that describes every screen to the platform |
| **YAML** | A simple text format for describing a screen. No programming needed — looks like a fill-in-the-blank form |
| **Test data file** | An Excel/CSV file listing the inputs and expected outputs for one screen's tests |
| **Page Object** | A piece of hand-written Python code that represents one screen — used only when YAML isn't enough (the "escape hatch") |
| **Selector** | A short string (like `#userid` or `.action-edit`) that tells Playwright which button or field to click |
| **Playwright** | The browser-automation library that actually clicks buttons and reads pages for us |
| **pytest** | The Python test-runner that executes the tests and produces reports |
| **pytest-bdd** | An add-on that lets us write tests in plain English (Given / When / Then) |
| **Allure** | A test report dashboard with trends, screenshots, and historical pass/fail data |
| **Smoke test** | A tiny, fast test that proves the app is alive — runs on every commit (under 5 minutes) |
| **Regression test** | The full test suite — runs nightly (can take hours) |
| **Self-healing selector** | When the primary selector (e.g. `#userid`) breaks, the framework automatically tries a fallback (e.g. by label or field name) — reduces flake |
| **Flake / flaky test** | A test that sometimes passes and sometimes fails for no good reason — usually a timing or selector problem |
| **CI** | Continuous Integration — Jenkins or GitHub Actions running the tests automatically on every code change |
| **DB verification** | After clicking "Save," looking directly in SQL Server to confirm the row really got created/updated |
| **Recorder** | A future tool — a Chrome extension that watches you click around a screen and writes the YAML for you |
| **MVP** | Minimum Viable Product — the first version that proves the design works end-to-end |
