# Case Memory — staged implementation plan

*(Read the code first: `framework/testcase_importer.py`, `prompt_engine.py`, `testcase_editor.py`, `intent_parser.py`, `crawl_runner.py`, `scenario_store.py`, `web_ui/app.py`, `web_ui/static/convert.js`, `web_ui/static/app.js`, `knowledge_base/screens_catalog.json`, `config/settings.py`. Numbers below are measured, not quoted.)*

---

## 0. What is actually there right now (verified)

| Claim | Verified |
|---|---|
| 27 regexes / 26 distinct labels in the importer | `len(testcase_importer.RULES) == 27`, `len(set(labels)) == 26` (`assert-no-errors` appears twice) |
| 21 rule entries / 13 distinct builders in the prompt engine | `len(prompt_engine.RULES) == 21`; builders `_r_default_screen` … `_r_only_screen` = 13 |
| 53 `re.compile` in `prompt_engine.py` | includes `_FILLER` + the 30-entry `_SUBS` table at lines 48–81 |
| Gemini is fallback-only | `apply_prompt` line 543–552: `apply_rules()` first, then `if use_llm and result.ignored and _have_gemini()` |
| Gemini returns a **whole rewritten YAML** | `_gemini_call` line 436–477; instruction block at 444–459 |
| Nothing is persisted but a counter | `_load_usage`/`_save_usage` lines 484–493 → `~/.stratus-qa/llm-usage.json` = `{"2026-08-11": 7}` |
| No ML anywhere | no sklearn/torch/pickle in `requirements.txt`; `tests/` is `smoke/`, `demo/`, `features/` only — **zero unit tests for `framework/`** |
| Catalog | 239 screens, 131 with `fields`, **485 distinct field labels**, **303 distinct button texts**, per-field `type/required/max_length/options` |
| `eval/` | **does not exist.** No corpus. The 94 % number is not reproducible today. |
| The best label stream in the codebase | `convert.js:546–572` renders a todo card showing *"Your step said: …"* + a free-text box; `applyFix()` at 576 POSTs the tester's own words to `/api/modify-testcases`; the answer is used once and dropped |
| Second-best, already client-side | `S.yamlBackup = $("techarea").value` at `convert.js:638` — the pre-edit YAML is already in memory when `tsave` (line 650) commits the edited one |
| A latent bug worth fixing en route | `testcase_editor.edit_testcases:263` calls `apply_prompt(..., use_llm)` **before** the catalog-aware pass at 268–276 — so Gemini is billed for lines `_RX_RETARGET`/`_RX_ADD`/`_RX_VALUE` would have handled for free |

---

## 1. The architecture in one paragraph

Every prose→structure decision the tool makes is written back as a **case**: a catalog-grounded *signature* (literals abstracted to slots), the *slot fillers*, and an *output template* that references those slots. New prose is abstracted the same way and answered by lookup, then by nearest-neighbour over the signature space, before any regex or LLM runs. Cases come from four streams — backfill from the existing 27+21 regexes, the tester's answer to a todo card, the tester's YAML edits, and Gemini — and are graded by a fifth: what Playwright actually found at runtime. Gemini is confined to one `Resolver` class from day one and its output contract is flipped from "rewrite this YAML" to "which of these 16 ops, with what arguments" — which is the single change that turns each answer from a disposable document diff into a permanent, replayable, reviewable artifact.

**The mechanism that makes this learning and not a cache** is the output template. A case's `out` is not `{"action":"fill","target":"LastName","value":"QA"}` — it is `{"action":"fill","target":"${FIELD1}","value":"${VAL1}"}` keyed to signature `enter <VAL> in <FIELD>`. So one case answers *"Type 'Smith' into the First Name field"*, which it has never seen, on a screen it has never seen. Generalisation to unseen input is the definition; recall of seen input is the cache.

---

## 2. The case record (the whole data model)

`knowledge_base/case_memory.jsonl`, append-only, one JSON object per line:

```json
{"id":"c_000412","kind":"step","sig":"enter <VAL> in <FIELD>","sig_hash":"9f2c1a…",
 "raw":"Enter 'QA' in Last Name","slots":{"VAL1":"QA","FIELD1":"Last Name"},
 "ctx":{"screen":"customerlist","layout":"grouped","catalog_fp":"a71b…"},
 "out":{"action":"fill","target":"${FIELD1}","value":"${VAL1}"},
 "provenance":"rule:fill","status":"active",
 "hits":37,"wins":31,"losses":0,"confidence":0.94,
 "created_ts":1754870400.0,"last_used_ts":1754884000.0,"author":"farhan"}
```

* `kind` ∈ `step` (importer) | `edit` (prompt engine) | `intent` (intent parser)
* `provenance` ∈ `rule:<label>` | `human_todo` | `human_yaml` | `gemini` | `exec`
* `status` ∈ `active` | `quarantined` (Gemini-authored, unreviewed) | `retired`
* `catalog_fp` = hash of the screen's `fields`+`buttons` block, so a case is auto-invalidated when the screen changes under it

**Storage decisions, and why:**

1. **JSONL, not pickle, not sqlite.** The learned artifact must be readable in a pull request. This tool decides whether a test passes; an opaque model file is unshippable here. 10 k cases ≈ 4 MB; the in-memory index rebuilds at Flask start in well under a second. Compaction rewrites a deduped snapshot using the exact `tempfile` + `os.replace` pattern already at `scenario_store._write_all:62–68`.
2. **Committed, not gitignored.** `.gitignore` currently splits `knowledge_base/`: `scenarios.json` / `directory.json` / `users.json` are per-user; `screens_catalog.json` is explicitly "the durable, shared Knowledge Base". Case memory goes on the *shared* side. Five testers on separate laptops learning the same lesson five times is the fastest way to make this fail — and with a team this small, data volume is the binding constraint.
3. **Env override `STRATUS_CASES_PATH`**, mirroring `STRATUS_SCENARIOS_PATH` at `scenario_store:22–26`, so the eval harness never writes the real memory.

---

## 3. Stages

Effort is given as **optimistic / realistic**. Realistic is optimistic + 40 %, which is what I'd actually plan against for one engineer who also has BAU. The optimistic column is what it costs if this is the only thing they do.

---

### STAGE 1 — "It remembers, and it already generalises across values and fields"
**Ships this week. 5 / 7 days.**

#### Build

**`framework/case_signature.py` (new, ~180 lines, stdlib only)**

```
build_lexicon(catalog) -> Lexicon      # 485 field labels + 303 button texts + 239 screennames,
                                       # longest-first, from knowledge_base/screens_catalog.json
abstract(prose, entry=None, lexicon=None) -> Signature(sig, slots, tokens)
sig_hash(sig) -> str
```

`abstract()` pipeline, in order:
1. strip enumeration — reuse the exact regex at `testcase_importer.translate_step:210`
2. `prompt_engine.normalize_prompt()` (line 84) — its `_FILLER` + 30-entry `_SUBS` table is already a hand-built canonicaliser; do not rebuild it
3. quoted literals `'…'` / `"…"` → `<VAL>`, captured to `slots.VAL1…n`
4. bare numbers → `<NUM>`
5. **catalog grounding**: when the screen is known, match against `testcase_editor.build_field_index(entry)` (line 46) → `<FIELD>`; otherwise match against the global lexicon → `<FIELD?>` (unverified tier, kept as a separate token so a verified case never collides with an unverified one). Button texts → `<BTN>`, screennames → `<SCREEN>`. Longest-match-first.

**`framework/case_memory.py` (new, ~250 lines)**

```
load() / _rebuild_index()              # dict: sig_hash -> [case]
recall(sig, ctx) -> Case | None        # exact tier only in stage 1
remember(case) -> str                  # append + index, dedup on (sig_hash, out-template)
record_outcome(case_id, ok: bool)      # wins/losses, Wilson lower bound -> confidence
instantiate(case, slots, entry) -> dict  # ${FIELD1} -> resolve_field(...)["id"] when the
                                         # screen is known (testcase_editor.resolve_field:62),
                                         # else the literal
compact()                              # atomic snapshot rewrite
```

**Backfill script `eval/backfill.py`** — replay `import_xlsx` over every Excel file the QA team has and write one case per firing rule. `translate_step:211–219` already has `label` in scope and discards it; return it. This is ~202 cases from the 11 measured files on day one and thousands if the back-catalogue is bigger, at zero human cost. **The case base is never empty, so there is no cold start.**

**Capture hooks — three points, all already flowing, all currently discarded:**

| Where | What is captured | Code today |
|---|---|---|
| `convert.js:576 applyFix()` | `(todo prose, tester's own words, resulting step)` — the **highest-quality label in the codebase**, keyed precisely to the residual 6 % | posts to `/api/modify-testcases`, answer discarded |
| `convert.js:650 tsave` | `(generated YAML, hand-edited YAML)` step-level diff — `S.yamlBackup` at line 638 already holds the "before" | discarded on `endEdit()` |
| `prompt_engine.apply_rules:415` | every `ignored` line = the engine's own admission of blindness | returned, rendered in the panel at `app.js:1066 renderPromptPanel`, discarded |

`applyFix` needs *no diff alignment* — the card already knows which prose line it is about (`t.text`, line 579). Send `todo_text` alongside `prompt` in the POST body at line 584; `api_modify_testcases` (app.py:840) writes the case. That is roughly 30 lines of change for the best signal in the system.

**Retrieval, wired in at exactly two call sites:**
* `testcase_importer.translate_step:200` — gains `ctx: dict | None = None`; consults `case_memory.recall()` before the `for rx, builder, label in RULES` loop at 211. Callers at 299–300 and 325–327 pass `{"screen": screen, "layout": layout}`.
* Rules that fire and *are not* already in memory write themselves in as `provenance="rule:<label>"`.

**`eval/harness.py` + `eval/corpus/` (new)** — freeze the 11 real files. **This asset exists nowhere today.** `python -m eval.harness --baseline` prints coverage and writes `eval/baseline.json`. Coverage needs no labels (`import_xlsx:552–553` already computes `n_steps_translated`), so it works on day one. Hand-labelling the 215 expected outputs runs as a background task through stage 1 and gates stage 2, not stage 1.

Also start `eval/corpus/observed/` — every import appends its prose lines (unlabelled). The 11-file corpus is statistically thin; this is how it stops being thin without anyone doing extra work.

#### Files
`framework/case_signature.py` (new) · `framework/case_memory.py` (new) · `eval/harness.py` (new) · `eval/backfill.py` (new) · `eval/corpus/` (new) · `knowledge_base/case_memory.jsonl` (new) · `framework/testcase_importer.py` (MOD: `translate_step:200`, callers 299/325) · `web_ui/app.py` (MOD: `api_modify_testcases:840`, `api_import_testcases:907`) · `web_ui/static/convert.js` (MOD: `applyFix:576`, `tsave:650`) · `.gitignore` (MOD: commit `case_memory.jsonl` on the shared-KB side)

#### Demo to the manager (3 minutes, scripted)
1. `python -m eval.harness --baseline` → **`coverage 202/215 (94.0%)`**, reproducible for the first time.
2. Open `/convert`, upload a real file. A todo card appears: *"Your step said: 'Select the vendor from the Brand drop-down'"*.
3. Type *"it's the Brand dropdown on the product detail screen"*, click **That fixes it**.
4. `tail -1 knowledge_base/case_memory.jsonl` → the new case, human-readable, on screen.
5. Re-upload. **The card is gone.** The transparency panel says *"answered from memory — learned from your correction, 11 Aug"*.
6. `git diff knowledge_base/case_memory.jsonl` → "this is the thing that is learning, and you can read it."

#### The number
**Cases learned** (`wc -l knowledge_base/case_memory.jsonl`), and **memory hit rate** = % of steps answered from memory before the regexes run. Both from a new `/api/learning/stats`. Baseline coverage 202/215 is frozen and reproducible.

#### Say this, and not more
At stage 1 the system generalises across **literals and field names** (one case covers *"Enter 'QA' in Last Name"* and *"Type 'Smith' into the First Name field"*) but **not across phrasing** (*"Populate the Last Name box with QA"* is still a miss). That is real, it is not nothing, and it is not yet the whole claim. Stage 2 is where phrasing generalisation arrives. Do not let stage 1 be described as more than it is — the credibility spent there is the credibility stage 2 needs.

---

### STAGE 2 — "It answers phrasings nobody ever taught it"
**Weeks 2–3. 8 / 11 days.**

#### Build

**Neighbour tier in `case_memory.py`** — TF-IDF over word 1–2-grams + char 3–5-grams of the *abstracted* signature, cosine similarity, ~80 lines of `collections.Counter`. No sklearn; leave the distance function behind an interface so a local ONNX sentence-embedding channel can be swapped in later **without building it now**.

Retrieval becomes two-tier **with abstention**:
* **Exact tier** — `sig_hash` → O(1), confidence 1.0
* **Neighbour tier** — top-k (k=3); answer only if `top_score ≥ τ` **and all k agree on the same action/op**. Disagreement → **abstain and fall through to the regexes.** A k-NN that always answers is worse than one that knows when it doesn't; abstention is what stops coverage rising while accuracy falls.
* Ties broken by `confidence = f(similarity, provenance_prior, wins/losses)`, priors `human_todo > human_yaml > rule > gemini`.

`τ` and `k` are **fitted from data** by leave-one-file-out cross-validation in the harness — they are hyperparameters, not constants someone picked.

**`eval/harness.py` gains LOFO** — build the case base from 10 files, evaluate on the 11th, rotate. Non-negotiable: a case base evaluated on data already inside it scores ~100 % and means nothing. The hand-labelled goldens (finished during stage 1) land here as `eval/goldens/*.json`, and **accuracy becomes a first-class metric alongside coverage** — never reported without it, because a confidently-wrong step runs and produces a plausible-looking wrong result, whereas a `todo` is visible and gets fixed in ten seconds.

**The metric that keeps this honest** (and that the design as originally scored was missing): report the **memory split** — what fraction of memory hits came from the exact tier versus the neighbour tier. The exact tier *is* a cache. If the neighbour tier's share is near zero, this is a cache with extra steps and the whole thesis has failed. Put that number on the dashboard, not in a footnote.

#### Files
`framework/case_memory.py` (MOD: neighbour tier, abstention) · `framework/case_signature.py` (MOD: vectoriser) · `eval/harness.py` (MOD: LOFO, accuracy, memory split) · `eval/goldens/` (new) · `web_ui/app.py` (new `/api/learning/stats`) · `web_ui/static/app.js` (MOD: provenance line in `renderPromptPanel:1066`)

#### Demo
Teach it *once* with phrasing A. Feed it phrasing B, which is not in the file, not in the memory, and matches none of the 27 regexes. It answers. Then show the **learning curve**: x = cases in base, y = held-out LOFO accuracy. That chart is a literal picture of a model learning, and it is honest precisely because it is held-out.

#### The number
**Held-out LOFO accuracy as a function of case-base size** — the curve — plus **neighbour-tier share of memory hits**.

#### The falsification condition, committed in writing now
**If the curve is flat after two weeks of real usage, this design has failed and should be abandoned.** Building the harness in stage 1 is what buys that answer in two weeks instead of two quarters. Agree it with the manager before the data arrives.

---

### STAGE 3 — "Gemini stops doing the work and starts writing the rules"
**Weeks 3–4. 9 / 13 days. This is the stage that makes deletion possible.**

#### Build

**`framework/ops.py` (new)** — the closed op vocabulary. It already exists, scattered: the 13 `prompt_engine` builders (`force_screen`, `default_screen`, `substitute`, `screenshot_after`, `wait_after`, `assert_rows_after_search`, `skip_priority`, `append_assert_no_errors`, `repeat_each`, `default_value_for`, `uppercase_target`, `lowercase_screens`, `only_screen`) plus the 3 in `testcase_editor` (`retarget_field`, `add_search`, `set_value`). **16 ops. Nothing new to invent.**

**Refactor `apply_rules:393`** so the loop at 404–415 *classifies* a line into `(op, args)` and a separate pass applies ops to the doc. Today `builder(m, doc)` at line 409 mutates in place and returns a description string; every one of the 13 builders changes shape. **This is the most underestimated item in the plan — budget 4 days, not 2.**

The safety net that makes it survivable: `python -m eval.harness --equivalence` runs the corpus × a fixed prompt list through both the old and new paths and asserts **byte-identical YAML**. Build that before touching a single builder. There are no existing unit tests for `framework/` — this harness is the first one.

**Flip the Gemini contract.** `_gemini_call:436` currently sends the instruction block at 444–459 and gets back a whole rewritten YAML. That answer is entangled with one document and therefore **unsavable in reusable form — that, not the absence of a cache, is the actual reason nothing is learned today.** New prompt: *"which of these 16 ops, with what arguments, does this instruction mean?"* → JSON. Three things fall out at once:
* closed-vocabulary classification is far more reliable than free-form YAML generation, so **quality goes up as cost goes down**
* the answer is **verifiable before admission**: apply the op, confirm the YAML still parses and actually changed
* every call becomes capital instead of expense — ask once per phrasing family, forever

`intent_parser._gemini_parse:145` already returns JSON. Just persist it.

**`framework/resolvers.py` (new)** — `Resolver` protocol; `CaseResolver → RuleResolver → CatalogResolver → GeminiResolver → TodoResolver`. **Gemini lives behind exactly one class from this day forward.** Today it is threaded through `prompt_engine`, `intent_parser`, five `use_llm` routes in `app.py` (`/api/nl-to-yaml:756`, `/api/modify-testcases:840`, `/api/import-testcases:907`, `/api/import-testcases/zip:966`, `/api/intent-parse:1146`) and two JS files. Removability is bought here, as an architectural property, not excavated later.

Note the order change: **memory runs ahead of the regexes.** Several rules are deliberately greedy — the force-screen pattern at `prompt_engine.py:325` matches `\b(?:all|every|…)\b.{0,40}?\b(?:on|for|…)` — and will swallow a line a specific human-corrected case would answer better.

**Fix the ordering bug while you are in there.** `testcase_editor.edit_testcases:263` sends lines to Gemini before the catalog pass at 268–276 ever sees them. In the resolver chain, `CatalogResolver` sits ahead of `GeminiResolver`, which is both correct and free.

**Quarantine + review UI.** Gemini-authored cases enter `status: quarantined` — used, but flagged in the transparency panel as *"learned, unreviewed"*. `web_ui/templates/learning.html` + `/api/learning/{queue,approve,retire}` lets a QA lead promote or retire. **If this gets cut for schedule, cut the Gemini write-back too**, or you are shipping an unaudited oracle into a correctness-critical tool.

**`config/settings.py` gains a `LearningConfig`.** Note honestly: the module docstring claims all code reads config through `settings`, but `prompt_engine.py:433` and `intent_parser.py:151` read `os.environ` directly. Fixing that is part of this stage, and it is what makes stage 6 a one-line change.

**Extend `~/.stratus-qa/llm-usage.json`** from `{date: int}` to `{date: {calls, lines_resolved, memory_hits, rule_hits, gemini_hits}}` (`_load_usage:484`, `_save_usage:490`). The one file this system persists today becomes the evidence trail for its own removal.

#### Files
`framework/ops.py` (new) · `framework/resolvers.py` (new) · `web_ui/templates/learning.html` (new) · `framework/prompt_engine.py` (MOD: 393, 409, 436, 499, 535, 484–493) · `framework/testcase_editor.py` (MOD: 263 ordering; register 3 ops) · `framework/intent_parser.py` (MOD: 145, 208) · `config/settings.py` (MOD) · `web_ui/app.py` (MOD: five `use_llm` routes → resolver chain; `/api/learning/*`) · `eval/harness.py` (MOD: `--equivalence`)

#### Demo
Side-by-side counters: **coverage rising while Gemini calls per 100 lines falls.** Then open the review queue: *"here is what the AI taught us this week — approve or throw away."* The manager sees knowledge being harvested from a vendor rather than rented from one.

#### The number
**Gemini calls per 100 lines converted**, from the extended usage file. This is the migration metric and it must trend to zero.

---

### STAGE 4 — "It grades itself against the real application"
**Week 5. 5 / 7 days.**

#### Build

`crawl_runner._execute_custom_steps:496` already determines whether every target was found — it just formats prose. Lines 529, 535, 553, 559, 567 are structured facts wearing string clothes. Emit `(case_id, action, target, found: bool)` alongside the note, feed to `case_memory.record_outcome()` → wins/losses → confidence. **Free reinforcement against the live application, with no human in the loop.**

Be honest about signal quality, because it differs by action type and this is easy to overclaim: `_find_field:623` only tries `#id`, `[name=]`, `input[id=]`, `select[id=]`, `textarea[id=]` — so *"field not found"* on a `fill` frequently means the step used a human label where a field id was needed, which is a **target-resolution** failure, not a translation failure. `_try_click_any:642` does text matching and is a cleaner signal. Weight `click` outcomes higher than `fill` outcomes, and say why.

**Catalog fingerprint invalidation** — a case whose `ctx.catalog_fp` no longer matches the screen's current field block drops to `quarantined` on the next catalog rebuild. Screens change; stale training data is worse than none.

**Retire a whole Gemini use case by construction.** `docs/PROMPT_RULES.md:30–42` lists *"add negative test cases"*, *"generate edge cases for the date field"*, *"add a test for SQL injection in Last Name"* as Tier-2 AI work. The catalog already carries `type`, `required`, `max_length`, `options` per field. Boundary and invalid-input cases are **better** produced by a deterministic generator in `framework/test_generator.py` (which already has `generate_for_list:64` / `generate_for_detail:229` / `generate_for_report:347` / `generate_for_wizard:394`) — more complete, reproducible, and free. Build `generate_edge_cases(entry)`. This removes a dependency rather than learning around it.

#### Files
`framework/crawl_runner.py` (MOD: 496–620) · `framework/case_memory.py` (MOD: outcome scoring) · `framework/catalog_builder.py` (MOD: fingerprint) · `framework/test_generator.py` (MOD: edge-case generator) · `docs/PROMPT_RULES.md` (MOD: rewrite Tier 2)

#### Demo
*"Nobody told it this was wrong. It ran the test, Playwright couldn't find the field, and it demoted the case on its own."*

#### The number
**Execution-grounded correctness** — % of produced `click`/`fill` steps whose target Playwright actually located at runtime. Objective, free, and not a self-report, which is what makes it the most credible number in the whole programme.

---

### STAGE 5 — "It writes its own rules" *(optional; do not start before the stage-2 curve is proven non-flat)*
**Weeks 6–8. 8 / 11 days.**

Grafted from the runner-up. When ≥3 cases share an output-op shape and a token skeleton, anti-unify to the least general generalisation and emit a **regex bound to an existing builder** — never new Python. `knowledge_base/learned_rules.json` is loaded by `framework/learned_rules.py` and appended *after* the hand-written `RULES` lists in both engines, so first-match-wins keeps hand rules authoritative and every induced rule is strictly additive.

Promotion gates, all hard, all pre-human: support ≥3 lines from ≥2 distinct testers/documents; slot diversity ≥2 distinct fillers per generalised slot (a slot that only ever saw "Last Name" stays literal); zero contradictions across the whole case base; **zero regressions on the frozen benchmark**; ≥1 human-verified case per cluster, so a hallucination can never be distilled into a permanent rule. Then a human reads a plain-English gloss and clicks Approve. `enabled: false` kills a bad rule with no deploy; rollback is `git revert`.

**Why this is stage 5 and not stage 1:** it converts memory into speed and reviewability, not into capability. It is worth doing only once the case base is large enough to cluster, and the gates above mean the backfill alone can promote nothing.

**The number:** **learned-rule share** — % of resolved steps handled by induced rules rather than hand-written ones. The literal answer to "prove it learned something."

---

## 4. The exact point `GEMINI_API_KEY` can be deleted

### The reframe that makes this achievable — say this to the manager explicitly

**Removal does not require 100 %.** The fallback when nothing resolves is not "call an LLM" — it is `{"action": "todo", "target": …}`, which is already shipped product behaviour (`testcase_importer.py:223`, logged at `crawl_runner.py:609`) with a purpose-built resolution UI (`convert.js:537 paintQuestions`) and a per-todo question generator (`convert.js:144 questionFor`). A QA tool driving a live back office **ought** to ask rather than guess. The removal bar is not perfection; it is *"the residual is small enough that a question card is a fine answer."* At 94 % today, with the tail being the rarest and hardest phrasings, that bar is plausibly already within reach. This is the difference between an engineering project and a research project.

### The gate — agree these numbers in advance, in writing, and do not move them

Deletion happens the first week all four hold simultaneously:

1. **Accuracy**: LOFO held-out accuracy with `GeminiResolver` disabled ≥ accuracy with it enabled − 1 pt, on the frozen corpus.
2. **Volume**: Gemini calls per 100 lines < 2, **sustained 4 consecutive weeks**, evidenced by `~/.stratus-qa/llm-usage.json`.
3. **Residual**: every remaining unresolved line has been reviewed and judged genuinely ambiguous — i.e. it *should* be a question card.
4. **No regression**: execution-grounded correctness (stage 4) has not fallen against its own baseline.

If (2) plateaus above threshold, the honest outcome is *"we cannot remove it yet"* — not a quietly lowered threshold.

### The deletion, once the gate holds (half a day)

| File | Delete |
|---|---|
| `framework/prompt_engine.py` | `_have_gemini:432`, `_gemini_call:436–477`, `_usage_path/_load_usage/_save_usage/_today_key:481–496`, `llm_refine:499–530`, `llm_available:555–565`, the `use_llm` branch of `apply_prompt:544–551` |
| `framework/intent_parser.py` | `_gemini_parse:145–205`, the merge block `222–252`, the `use_llm` param on `parse_intent:208` |
| `framework/resolvers.py` | the `GeminiResolver` class — **one class** |
| `framework/testcase_editor.py` | `use_llm` on `edit_testcases:252` |
| `web_ui/app.py` | `/api/llm-status:959–963`; `use_llm` in `api_nl_to_yaml:777`, `api_modify_testcases:860`, `api_import_testcases:927`, `api_import_testcases_zip:984`, `api_intent_parse:1160` |
| `web_ui/static/convert.js` | `S.aiOn:30`, the `/api/llm-status` fetch at 768–770, `use_llm` at 429/435/584, the AI-not-configured branch at 605 |
| `web_ui/static/app.js` | the `/api/llm-status` block at 979–993, `useLlm` at 1158/1232/1319/1394, the `llm_used`/`llm_error` branches in `renderPromptPanel:1066` |
| `web_ui/templates/index.html` | the two AI checkboxes at 627 and 654, `#conv-llm-status:641` |
| `requirements.txt` | `google-genai>=0.5.0` (last line) |
| `.env` / `.env.example` | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_DAILY_REQUEST_LIMIT` |
| `~/.stratus-qa/llm-usage.json` | delete the file — after screenshotting it as the evidence trail |
| `docs/PROMPT_RULES.md` | rewrite §Tier 2 (lines 30–42) as Case Memory + the learning curve |

**Realistic date: end of month 3** if stages 1–4 run clean. Month 4 if stage 5 is needed to close the gap.

---

## 5. The honesty ledger

**True from stage 2, and defensible to a skeptical engineer:** this is instance-based supervised learning (k-NN, the Cover & Hart family). It has a training set, a hypothesis space, a generalisation mechanism, hyperparameters (`k`, `τ`, feature weights, provenance priors) fitted from data by leave-one-file-out cross-validation, and credit assignment from runtime outcomes. The decision function changes as a function of accumulated data, it generalises past strings it has seen, no programmer writes the new behaviour, and its accuracy on **held-out** data improves measurably as cases accumulate — here is the curve.

**Sentences that would be lies, and must never be said:** *"it's a neural network"* · *"it understands English"* · any accuracy number measured on data already inside the memory · *"it gets smarter on its own"* (it gets smarter when testers correct it and someone reviews the result — that is still learning; overclaiming autonomy is what would make it a lie) · *"the 94 % is accuracy"* (it is coverage; `import_xlsx:552–553` counts anything not in `(todo, manual, note)` as translated regardless of whether the target is right, so a confidently-wrong mapping scores as a success today, and **the first honest accuracy reading will be lower than 94 %** — report it anyway).

**A skeptical ML person will call this "a well-engineered retrieval system with feedback" rather than "a model."** They are not wrong, and the framing should not be defended past that point. The claim that survives scrutiny is narrow and sufficient: *it learns from examples, generalises to unseen input, improves measurably on held-out data, and removes the external dependency.* That is exactly what was promised. There is no need to overclaim, and overclaiming is the only way to lose this argument.

### Risks I am not hiding

1. **Sample size.** 11 files, 215 steps, and only **13 steps of headroom**. An 11-fold LOFO curve has thin statistical power, and a 2-point move may be noise. The mitigation is `eval/corpus/observed/` growing from real usage from week 1, and a rule that no decision is made on a single LOFO run.
2. **Lexical ceiling.** TF-IDF cosine cannot bridge paraphrase with no shared vocabulary. Slot abstraction plus the `_SUBS` table push the wall back; they do not remove it. Expect a plateau. The upgrade — a small local ONNX embedding model as a *second* similarity channel, inference-only, no training — is a drop-in swap **if the distance function is an interface**. Do not build it in v1; it is precisely the thing that breaches the "small team, no ML infra" constraint, and you will not know if you need it until the curve plateaus.
3. **Memory poisoning**, made worse by memory running ahead of the regexes. Mitigated by validate-on-admission, quarantine, wins/losses demotion, a human-readable store, and the review UI. The review UI is genuinely not optional.
4. **Silent-wrong beats loud-missing.** Coverage rising while accuracy falls is the dangerous failure mode. Hence accuracy as a release gate and abstention as a designed-in feature rather than a defect.
5. **The todo-card signal is noisier than it looks.** `convert.js:593` flips the card to `fixed` on `if (!stillTodo || changed)` — i.e. if the YAML changed *at all*, including from an unrelated edit. Tighten that condition when adding the capture, or the best label stream admits noise.
6. **Tester error is inherited.** Someone who "fixes" a step wrongly teaches a wrong case. Execution outcomes are the independent second opinion; `status: retired` is the kill switch.
7. **Recurring cost, named rather than hidden:** ~2 hours/week of a QA lead working the review queue. That is not overhead — it is the highest-quality label stream in the system.

### Total effort
Stages 1–4: **27 / 38 days** for one engineer. Stage 5 adds 8 / 11. No GPU, no new services, no new runtime dependencies — stdlib `re`/`json`/`hashlib`/`collections` throughout; sklearn optional and not required. Value ships at the end of week 1 and every week after, and every stage is independently useful if the next one is cancelled.