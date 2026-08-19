# Stratus QA Automation Tool
## Status report and roadmap to an in-house model

**Prepared by:** Farhan Memon
**Audience:** Engineering management
**Subject:** What the tool does today, and the plan to remove the external AI dependency

---

## 1. Executive summary

The Stratus QA tool converts the Excel test cases our testers already write into
automated browser tests against Stratus BackOffice. It is working and in use.

Today it runs on two engines: a **rule engine we own**, which handles the large
majority of the work, and **Google Gemini**, which handles the remainder. Measured
across a representative set of eleven test files, our own engine handles **94% of
steps with the external AI switched off entirely**.

The remaining 6% is the reason we still hold a Gemini API key. This document sets
out a five-stage plan to close that gap with capability we own, and to delete the
key. **Stages 1–4 take 27–38 working days for one engineer.** The realistic date to
remove the external dependency is **end of month 3**.

No GPU, no new servers, no new runtime dependencies are required.

---

## 2. Where we are today

### 2.1 What is built and running

| Component | Status |
|---|---|
| Screen knowledge base | **239 Stratus BackOffice screens** catalogued; 131 with full field detail |
| Field and control vocabulary | **483 distinct field labels**, **303 button and menu labels**, 31 screens with tab structures |
| Test case importer | Reads **4 different Excel layouts** our testers actually use |
| Conversion engine | **48 rules** (27 importer, 21 editor) translating English into test steps |
| Convert page | Live, working, in use |
| Test execution | Playwright driving a real browser against a test server |
| Reporting | Live progress, screenshots, HTML reports |

### 2.2 What the Convert page can do today

This is the tester-facing screen and the heart of the product.

**Getting test cases in — three ways**
- Upload the Excel test-case files testers already write. One file can hold many
  test cases across different screens; the tool splits them correctly.
- Paste steps copied from an email or Word document.
- Describe the test in plain English with no format at all.

**Understanding them**
- Recognises four Excel layouts automatically — no template to learn.
- Detects the ticket number from the file name and fills it in.
- Matches steps against the 239-screen catalogue to find real field and button
  identifiers, not guesses.
- Produces a runnable automated test.

**Handling what it cannot work out — the part that matters most**
- Unclear steps never fail silently. Each becomes a **plain-English question**
  showing the tester's own sentence back to them — for example *"Which screen is
  this dropdown on, and what should we choose?"*
- The tester answers **in their own words**; the system applies it.
- Any question can be skipped; the test still runs and the report lists the skip.
- Counts update live as questions are answered.

**Reviewing and running**
- Every understood step is shown as a readable sentence, so a non-technical tester
  can proofread the automation **without reading code**.
- The technical file is available behind a developer link — view-only by default,
  with explicit edit, save and undo.
- Guards against error: the result is marked stale if inputs change afterwards,
  conversion can be cancelled, work survives a page reload, and a file containing
  no test cases is refused rather than reported as a success.
- Save to the shared library, or run immediately against the test server.

### 2.3 Measured capability — our engine alone, external AI switched off

| Test file | Steps | Handled by our rules | Coverage |
|---|---|---|---|
| Single test case | 5 | 5 | 100% |
| Multiple test cases | 25 | 25 | 100% |
| Mixed realistic file | 17 | 15 | 88% |
| Every question type (adversarial) | 18 | 12 | 67% |
| Unparseable prose (adversarial) | 4 | 0 | 0% |
| Large regression file | 108 | 108 | 100% |
| Ticket-linked file | 8 | 7 | 88% |
| Numbered layout | 11 | 11 | 100% |
| Grouped layout | 8 | 8 | 100% |
| Official template layout | 11 | 11 | 100% |
| **Total** | **215** | **202** | **94%** |

Two files were built deliberately to defeat the rules, which drags the average
down. Every file resembling real tester output scored **100%**.

### 2.4 What the external AI is actually doing

Gemini is a **fallback, not the engine**. The code always runs our rules first and
sends only what is left over. It is used for exactly two things:

1. Free-form answers a tester types that no rule matches.
2. Matching a spoken instruction to a saved test when confidence is low.

### 2.5 The honest gap

Two facts management should have before any figure is quoted externally:

- **The 94% is coverage, not accuracy.** It counts a step as handled if it produced
  an instruction, not if the instruction is right. The first true accuracy
  measurement will be lower. Stage 1 builds the harness that produces that number.
- **Nothing is currently retained from the AI.** Every Gemini answer is used once
  and discarded. There is no store, no reuse, no accumulated capability. Today the
  system does not improve with use — and that is precisely what stages 1 and 2 fix.

---

## 3. The plan

The approach chosen is **Case Memory**: every conversion the tool performs is
retained as a reusable case, with the specific values abstracted out, so that one
lesson answers many future questions it has never seen.

> A case learned from *"Enter 'QA' in Last Name"* correctly answers
> *"Type 'Smith' into the First Name field"* — different wording, different field,
> different screen. Generalising to unseen input is what makes this learning rather
> than a cache.

Three architectures were designed and independently scored against time-to-value,
ability to remove the dependency, realism for a small team, and defensibility of
the claim. Case Memory scored highest.

### Stage 1 — It retains every correction
**5–7 days**

Builds the memory store and the capture points. From this stage, every tester
correction and every conversion is retained in a readable, reviewable file.

*Demonstrable:* correct a step once; re-import the same file; the question is gone.
*Measure:* cases learned, and the share of steps answered from memory.

### Stage 2 — It answers wording nobody taught it
**8–11 days**

Adds nearest-neighbour matching with the ability to abstain when unsure, plus a
proper evaluation harness using held-out data.

*Demonstrable:* teach one phrasing, then feed a different phrasing that matches no
rule and is not in memory — it answers.
*Measure:* accuracy on held-out data plotted against memory size. **This curve is
the evidence that the system is learning.**

### Stage 3 — The external AI stops doing the work
**9–13 days**

The AI's job changes from writing test files to classifying an instruction into one
of 16 known operations. Its answers become reusable and reviewable instead of
disposable, and it is confined to a single replaceable component.

*Demonstrable:* coverage rising while AI calls fall, side by side. A review queue
showing what was learned this week, to approve or discard.
*Measure:* AI calls per 100 lines converted — must trend toward zero.

### Stage 4 — It grades itself against the real application
**5–7 days**

When a test runs, Playwright already knows whether each field was found. That
signal is fed back automatically to promote or demote cases — no human involved.

*Demonstrable:* a case demoted automatically because the test failed to find the field.
*Measure:* percentage of generated steps whose target the browser actually located.

### Stage 5 — It writes its own rules *(optional)*
**8–11 days**

Where several cases share a shape, the system proposes a new rule for human
approval. Only started if the Stage 2 curve proves the approach is working.

---

## 4. Timeline and effort

| Stage | Optimistic | Realistic | Cumulative (realistic) |
|---|---|---|---|
| 1 — Retains corrections | 5 d | 7 d | 7 d |
| 2 — Generalises to new wording | 8 d | 11 d | 18 d |
| 3 — AI demoted and contained | 9 d | 13 d | 31 d |
| 4 — Self-grading from test runs | 5 d | 7 d | 38 d |
| **Stages 1–4** | **27 d** | **38 d** | **≈ 8 working weeks** |
| 5 — Rule induction (optional) | 8 d | 11 d | 49 d |

One engineer. "Realistic" assumes normal business-as-usual alongside.
**Target date to remove the external AI: end of month 3.**

---

## 5. When the API key can be deleted

Removal does **not** require 100% automation. When nothing resolves, the tool asks
the tester a question — which is already shipped, working behaviour, and arguably
the correct behaviour for a tool driving a live back office. The bar is that the
remainder is small enough that a question is an acceptable answer.

Four conditions, agreed in advance and not moved:

1. Accuracy without the AI is within 1 point of accuracy with it.
2. AI calls per 100 lines below 2, sustained for four consecutive weeks.
3. Every remaining unresolved line has been reviewed and judged genuinely ambiguous.
4. No regression in the execution-grounded correctness measure from Stage 4.

If condition 2 will not come down, the honest conclusion is *"not yet"* rather than
a quietly lowered threshold.

---

## 6. What can be claimed, and when

Stated plainly so that nothing said externally has to be walked back.

**Accurate from Stage 2 onward:** this is instance-based supervised learning. It has
a training set, it generalises beyond the examples it was given, its parameters are
fitted from data by cross-validation, and its accuracy on held-out data improves
measurably as data accumulates.

**Not accurate, and should not be said:** that it is a neural network; that it
understands English; any accuracy figure measured on data already inside its own
memory; or that it improves entirely on its own — it improves when testers correct
it and someone reviews the result. That is still learning, but the distinction
matters.

A machine-learning specialist would reasonably describe this as *a well-engineered
retrieval system with feedback* rather than *a model*. The claim that holds up under
scrutiny is narrower and sufficient: **it learns from examples, generalises to input
it has not seen, improves measurably on held-out data, and removes the external
dependency.**

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Small data volume — 215 steps today, thin evidence early | Real usage feeds the corpus from week 1; no decision on a single measurement |
| Wording with no shared vocabulary will not match | Expected plateau; a local embedding model can be added later without redesign |
| A wrong correction teaches a wrong lesson | Validation on entry, quarantine of unreviewed cases, automatic demotion from test failures, and a review screen |
| Coverage rising while accuracy falls — the dangerous failure | Accuracy is a release gate; the system is designed to abstain rather than guess |
| Review effort | Approximately 2 hours per week of a QA lead. This is the highest-quality input the system receives |

---

## 8. Recommendation

Approve Stages 1 and 2 — **18 working days** — as a single block. That is the point
at which the learning behaviour is real, measurable and demonstrable, and the
evidence exists to decide whether Stages 3 to 5 are worth continuing.

Stage 1 should start immediately for one reason: **until it is built, every tester
correction is discarded.** That data is the asset the whole plan depends on, and it
is being lost daily.
