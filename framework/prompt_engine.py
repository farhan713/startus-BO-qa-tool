"""Prompt engine — apply tester instructions to imported test cases.

Two tiers:
  1. Rule engine  — ~15 deterministic regex transforms. Zero cost, instant.
  2. Gemini LLM   — opt-in (requires GEMINI_API_KEY in env). Handles anything
                    open-ended; runs at BUILD TIME only.

Both produce a `PromptResult` recording every applied/ignored transform so the
UI's transparency panel can show exactly what happened.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PromptResult:
    yaml_text: str                      # the (possibly transformed) YAML
    applied: list[str] = field(default_factory=list)   # human-readable rule descriptions
    ignored: list[str] = field(default_factory=list)   # prompt lines no rule matched
    llm_used: bool = False
    llm_error: str = ""
    llm_changed: bool = False


# ============================================================ Rule engine

# ---------- Prompt normalization ----------------------------------------
# Real testers write things like "pls add screenshots wherever it clicks thx"
# not "Screenshot after every click". We aggressively normalize the prompt
# before running it through the splitter + rules so common variations match.

# Fillers / politeness — stripped wholesale.
_FILLER = re.compile(
    r"\b(?:please|pls|plz|kindly|can you|could you|would you|will you|"
    r"can u|just|simply|maybe|i (?:want|need|would like)|i'd like|"
    r"make sure (?:to|that)?|let'?s|hey|hi |thanks?|thx|ty|cheers?|"
    r"btw|fyi)\b",
    re.I,
)
# Contractions / common typos / synonyms — substituted to a canonical form
# so the regex rules match. Order matters (longer phrases first).
_SUBS = [
    (re.compile(r"\bu\b", re.I),                "you"),
    (re.compile(r"\br\b", re.I),                "are"),
    (re.compile(r"\bn\b", re.I),                "and"),
    (re.compile(r"\bw/", re.I),                 "with"),
    (re.compile(r"\bevrywhere\b", re.I),        "every"),
    (re.compile(r"\bevry\b", re.I),             "every"),
    (re.compile(r"\beveywhere\b", re.I),        "every"),
    (re.compile(r"\bwherever\b", re.I),         "every"),
    (re.compile(r"\bwhenever\b", re.I),         "every"),
    (re.compile(r"\beach (?:and every )?\b", re.I), "every "),
    (re.compile(r"\bsnapshot(s)?\b", re.I),     "screenshot"),
    (re.compile(r"\bscreenshots?\b", re.I),     "screenshot"),
    (re.compile(r"\bscrenshot(s)?\b", re.I),    "screenshot"),
    (re.compile(r"\bss\b", re.I),               "screenshot"),
    (re.compile(r"\bclicks?\b", re.I),          "click"),
    (re.compile(r"\bfills?\b", re.I),           "fill"),
    (re.compile(r"\bselects?\b", re.I),         "select"),
    (re.compile(r"\bsaves?\b", re.I),           "save"),
    (re.compile(r"\bassertions?\b", re.I),      "assert"),
    (re.compile(r"\bvalidations?\b", re.I),     "assert"),
    (re.compile(r"\bchecks?\b", re.I),          "assert"),
    (re.compile(r"\bdrop\b", re.I),             "skip"),
    (re.compile(r"\bremove\b", re.I),           "skip"),
    (re.compile(r"\bexclude\b", re.I),          "skip"),
    (re.compile(r"\bafterward(s)?\b", re.I),    "after"),
    (re.compile(r"\bfollowing\b", re.I),        "after"),
    (re.compile(r"\bwith priority\b", re.I),    "priority"),
    # "all of them are on X" / "everything is on X" -> match "all tests are on"
    (re.compile(r"\b(?:all of them|everything|every test|every one|those)\s+(?:is|are)\s+(?:on|for|run on)\b", re.I),
        "all tests are on"),
    (re.compile(r"\b(?:no errors|error free)\b", re.I), "no errors"),
    (re.compile(r"\bend of every (?:test|scenario)\b", re.I), "end of every test"),
]


def normalize_prompt(prompt: str) -> str:
    """Make a user prompt regex-friendly without changing its meaning.

    - Strip leading politeness and bullet markers
    - Collapse runs of whitespace
    - Replace common synonyms / typos with canonical action vocabulary
    - Convert commas to semicolons so the splitter treats them as separators
    """
    if not prompt: return ""
    t = prompt.strip()
    # Remove leading bullets so " - skip P3" still matches the rule
    lines = []
    for ln in t.splitlines():
        ln = re.sub(r"^\s*[-*•·>]\s+", "", ln)
        lines.append(ln)
    t = "\n".join(lines)
    # Strip filler words
    t = _FILLER.sub(" ", t)
    # Synonym substitutions
    for rx, repl in _SUBS:
        t = rx.sub(repl, t)
    # Comma becomes semicolon so each clause is a separate instruction.
    # But don't touch commas inside quotes (used for values like "Smith, Jr").
    t = re.sub(r",(?=(?:[^'\"]*['\"][^'\"]*['\"])*[^'\"]*$)", ";", t)
    # Collapse repeated whitespace + punctuation
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"[;]{2,}", ";", t)
    return t.strip()


def _split_instructions(prompt: str) -> list[str]:
    """Normalize, then break a multi-line prompt into instruction lines.
    Splits on newlines, semicolons, ' and ', and sentence boundaries."""
    norm = normalize_prompt(prompt)
    if not norm: return []
    raw = re.split(r"[\n;]+|\s+\band\s+(?=[A-Za-z]{2,})|(?<=[a-z])\.\s+(?=[A-Za-z])",
                   norm)
    out = []
    for s in raw:
        s = s.strip().rstrip(".").strip()
        if s and len(s) > 2:
            out.append(s)
    return out


def _walk_steps(doc: dict, fn):
    """Apply `fn(step, test, doc)` to every step. fn may mutate step in place
    or return a list of replacement steps."""
    for test in doc.get("tests", []) or []:
        new_steps = []
        for step in test.get("steps", []) or []:
            ret = fn(step, test, doc)
            if ret is None:
                new_steps.append(step)
            elif isinstance(ret, list):
                new_steps.extend(ret)
            else:
                new_steps.append(ret)
        test["steps"] = new_steps


# --- Individual rules ----------------------------------------------------

def _r_default_screen(m, doc):
    screen = m.group(1)
    n = 0
    for t in doc.get("tests", []) or []:
        if not t.get("screen"):
            t["screen"] = screen; n += 1
    return f"set default screen to '{screen}' on {n} test{'s' if n != 1 else ''}"


def _r_force_screen(m, doc):
    screen = m.group(1)
    n = 0
    for t in doc.get("tests", []) or []:
        if t.get("screen") != screen:
            t["screen"] = screen; n += 1
    return f"forced screen='{screen}' on {n} test{'s' if n != 1 else ''}"


def _r_substitute(m, doc):
    var, value = m.group(2), m.group(1)
    pattern = re.compile(r"\{\{\s*" + re.escape(var) + r"\s*\}\}")
    n = 0
    def fn(step, *_):
        nonlocal n
        for k in ("target", "value"):
            v = step.get(k)
            if isinstance(v, str) and pattern.search(v):
                step[k] = pattern.sub(value, v); n += 1
        return None
    _walk_steps(doc, fn)
    return f"substituted {{{{{var}}}}} -> '{value}' in {n} field{'s' if n != 1 else ''}"


def _r_screenshot_after(m, doc):
    target_action = m.group(1).lower()
    # "save" / "navigate" map to clicks on those button labels (not real actions)
    matches_label = {"save": "save", "navigate": None}
    n = 0
    def fn(step, *_):
        nonlocal n
        act = step.get("action")
        # target may be a non-string (e.g. wait steps use an int like 2) — coerce.
        tgt = str(step.get("target") or "").lower()
        hit = (act == target_action) or (
            target_action in matches_label and act == "click"
            and matches_label[target_action]
            and matches_label[target_action] in tgt)
        if hit:
            n += 1
            return [step, {"action": "screenshot",
                           "target": f"after_{target_action}"}]
        return None
    _walk_steps(doc, fn)
    return f"inserted screenshot after {n} '{target_action}' step{'s' if n != 1 else ''}"


def _r_wait_after(m, doc):
    seconds, target_action = m.group(1), m.group(2).lower()
    n = 0
    def fn(step, *_):
        nonlocal n
        if step.get("action") == target_action:
            n += 1
            return [step, {"action": "wait", "target": seconds}]
        return None
    _walk_steps(doc, fn)
    return f"inserted wait {seconds}s after {n} '{target_action}' step{'s' if n != 1 else ''}"


def _r_assert_rows_after_search(m, doc):
    """For every test that performs a Search, assert the grid came back with
    at least one row — i.e. 'check that data is actually coming for each search'.
    Idempotent: skips tests that already assert row counts."""
    def _is_search_test(t):
        for st in t.get("steps") or []:
            if st.get("action") == "click" and "search" in str(st.get("target") or "").lower():
                return True
            if st.get("action") == "open_search":
                return True
        return False
    n = 0
    for t in doc.get("tests", []) or []:
        steps = t.get("steps") or []
        if _is_search_test(t) and not any(s.get("action") == "assert_rows_min" for s in steps):
            steps.append({"action": "assert_rows_min", "target": 1})
            t["steps"] = steps
            n += 1
    return f"asserted results returned (rows ≥ 1) on {n} search test{'s' if n != 1 else ''}"


def _r_skip_priority(m, doc):
    pri = m.group(1).upper()
    before = len(doc.get("tests", []) or [])
    # Priority is stored as a YAML comment line (notes) when present;
    # we tolerate either an explicit `priority:` field on the test or
    # a substring in `name`/`notes`.
    def keeps(t):
        for k in ("priority", "notes"):
            if str(t.get(k, "")).upper().strip() == pri:
                return False
        return True
    doc["tests"] = [t for t in (doc.get("tests") or []) if keeps(t)]
    removed = before - len(doc["tests"])
    return f"removed {removed} test{'s' if removed != 1 else ''} with priority {pri}"


def _r_append_assert_no_errors(m, doc):
    n = 0
    for t in doc.get("tests", []) or []:
        if t.get("steps") and t["steps"][-1].get("action") != "assert_no_errors":
            t["steps"].append({"action": "assert_no_errors"})
            n += 1
    return f"appended assert_no_errors to {n} test{'s' if n != 1 else ''}"


def _r_repeat_each(m, doc):
    times = int(m.group(1))
    if times < 2 or times > 10: return None
    new_tests = []
    for t in doc.get("tests", []) or []:
        for i in range(1, times + 1):
            copy = json.loads(json.dumps(t))     # deep copy
            copy["name"] = f"{t.get('name', 'test')} (run {i}/{times})"
            new_tests.append(copy)
    n = len(doc.get("tests", []) or [])
    doc["tests"] = new_tests
    return f"duplicated {n} test{'s' if n != 1 else ''} {times} times -> {len(new_tests)} total"


def _r_default_value_for(m, doc):
    target_field, value = m.group(2).strip(), m.group(1).strip()
    n = 0
    def fn(step, *_):
        nonlocal n
        if (step.get("action") == "fill"
                and str(step.get("target") or "").strip().lower() == target_field.lower()
                and not step.get("value")):
            step["value"] = value; n += 1
        return None
    _walk_steps(doc, fn)
    return f"filled default value '{value}' for {n} '{target_field}' fill step{'s' if n != 1 else ''}"


def _r_uppercase_target(_, doc):
    n = 0
    def fn(step, *_):
        nonlocal n
        if isinstance(step.get("target"), str):
            up = step["target"].upper()
            if up != step["target"]:
                step["target"] = up; n += 1
        return None
    _walk_steps(doc, fn)
    return f"uppercased {n} target field{'s' if n != 1 else ''}"


def _r_lowercase_screens(_, doc):
    n = 0
    for t in doc.get("tests", []) or []:
        s = t.get("screen", "")
        if isinstance(s, str) and s.lower() != s:
            t["screen"] = s.lower(); n += 1
    return f"lowercased {n} screen name{'s' if n != 1 else ''}"


def _r_only_screen(m, doc):
    screen = m.group(1)
    before = len(doc.get("tests", []) or [])
    doc["tests"] = [t for t in (doc.get("tests") or []) if t.get("screen") == screen]
    removed = before - len(doc["tests"])
    return f"kept only tests on '{screen}' (removed {removed})"


# (regex, builder)  — first match wins; case-insensitive.
# Rules are intentionally LOOSE to handle real-tester phrasing.
RULES = [
    # ---- Force-screen: "all tests on customerlist" / "make all these run on shipping" / "everything on receiptslist"
    # Tolerate intermediate noise words ("these", "run", "tests").
    (re.compile(r"\b(?:all|every|everything|those|these)\b.{0,40}?\b(?:on|for|run on|use)\s+(?:screen\s+)?['\"]?([a-z][a-z0-9_]+)\b", re.I),
     _r_force_screen),
    (re.compile(r"\bdefault screen (?:is|to|=)\s*['\"]?(\w+)", re.I), _r_default_screen),

    # ---- Variable substitution
    # Note: the prompt normalizer turns 'wherever' / 'whenever' into 'every',
    # so these rules only need to match 'for', 'as', 'every'.
    (re.compile(r"\buse\s+['\"]([^'\"]+)['\"]\s+(?:for|as|every)\s+(?:you see\s+)?\{\{\s*(\w+)\s*\}\}", re.I),
     _r_substitute),
    # No-quote variant: "use john smith every you see {{name}}"  (after normalization)
    (re.compile(r"\buse\s+([A-Za-z][\w .'\-]{0,40}?)\s+(?:for|as|every)\s+(?:you see\s+)?\{\{\s*(\w+)\s*\}\}", re.I),
     _r_substitute),
    (re.compile(r"\breplace\s+\{\{\s*(\w+)\s*\}\}\s+with\s+['\"]([^'\"]+)['\"]", re.I),
        lambda m, doc: _r_substitute(type(m)(m.re, type('M',(object,),{'group':lambda s,n:[None,m.group(2),m.group(1)][n]})()), doc)),
    (re.compile(r"\b\{\{\s*(\w+)\s*\}\}\s*[=:]\s*['\"]?([^'\"]+?)['\"]?$", re.I),
        lambda m, doc: _r_substitute(type(m)(m.re, type('M',(object,),{'group':lambda s,n:[None,m.group(2),m.group(1)][n]})()), doc)),

    # ---- Insert screenshot after some action.  Accept many phrasings:
    #   "screenshot after every click"  /  "screenshot after click"
    #   "ss after every click"          /  "add screenshot after click"
    #   "screenshot every fill"          /  "add screenshot every it click"
    # The action word can be anywhere AFTER "screenshot" within the instruction.
    (re.compile(r"\b(?:add\s+)?screenshot(?:s)?\b.{0,40}?\b(click|fill|select|save|navigate)\b", re.I),
     _r_screenshot_after),

    # ---- Wait after action
    (re.compile(r"\bwait\s+(\d+)\s*(?:s|sec|secs|second|seconds)?\s+(?:after\s+)?(?:every\s+|each\s+)?(click|fill|select|save|navigate)\b", re.I),
     _r_wait_after),

    # ---- Assert search results came back ("check data is coming for each search",
    #      "verify results show up", "make sure rows return").  After the prompt
    #      normalizer, "checks"->"assert" and "wherever/each"->"every".
    (re.compile(r"\b(?:data|results?|rows?|records?)\b.{0,30}?\b(?:com(?:e|ing)|return(?:ed|s)?|show(?:n|s)?|appear|load(?:ed|s)?|back|populat)\w*", re.I),
     _r_assert_rows_after_search),
    (re.compile(r"\b(?:assert|verify)\b.{0,20}?\b(?:data|results?|rows?|records?)\b", re.I),
     _r_assert_rows_after_search),

    # ---- Skip / drop / exclude by priority (P1/P2/P3 — any case).
    # Tolerate words like "the", "tests", "ones" between the verb and the priority.
    (re.compile(r"\b(?:skip|drop|remove|exclude|ignore|don't run|do not run)\b.{0,30}?\b(p\d)\b", re.I),
     _r_skip_priority),
    (re.compile(r"\b(p\d)\b.{0,15}?\b(?:skip|drop|excluded?|removed?|ignored?)\b", re.I),
     _r_skip_priority),

    # ---- Append assert_no_errors — many phrasings
    (re.compile(r"\b(?:append|add|insert|put)\s+assert_no_errors", re.I),         _r_append_assert_no_errors),
    (re.compile(r"\b(?:assert\s+)?no errors?\s+(?:at\s+)?(?:the\s+)?end", re.I), _r_append_assert_no_errors),
    (re.compile(r"\bend\s+(?:every|each)\s+test\s+(?:with\s+)?no errors?", re.I), _r_append_assert_no_errors),
    (re.compile(r"\bassert\s+no errors?\b", re.I),                                _r_append_assert_no_errors),

    # ---- Repeat each test
    (re.compile(r"\b(?:run|repeat|duplicate)\s+(?:each\s+|every\s+)?tests?\s+(\d+)\s*(?:x|times?)\b", re.I),
     _r_repeat_each),

    # ---- Default value
    (re.compile(r"\bdefault value\s+(?:for|of)\s+['\"]?([^'\"]+?)['\"]?\s+(?:is|=)\s+['\"]?([^'\"]+?)['\"]?$", re.I),
        lambda m, doc: _r_default_value_for(type(m)(m.re, type('M',(object,),{'group':lambda s,n:[None,m.group(2),m.group(1)][n]})()), doc)),

    # ---- Uppercase / lowercase
    (re.compile(r"\bupper\s*case\s+(?:all\s+)?target", re.I),                                       _r_uppercase_target),
    (re.compile(r"\blower\s*case\s+(?:all\s+)?screen", re.I),                                       _r_lowercase_screens),

    # ---- Keep only tests on one screen
    (re.compile(r"\b(?:only|just)\s+(?:include\s+|keep\s+)?(?:tests?\s+)?(?:on|for)\s+(?:screen\s+)?['\"]?(\w+)", re.I),
     _r_only_screen),
]


def apply_rules(yaml_text: str, prompt: str) -> PromptResult:
    """Run every rule that matches the prompt, in declaration order.
    Returns the transformed YAML + a list of what was applied/ignored."""
    import yaml
    if not prompt or not prompt.strip():
        return PromptResult(yaml_text=yaml_text)
    doc = yaml.safe_load(yaml_text) or {"tests": []}
    if not isinstance(doc, dict): doc = {"tests": []}

    applied: list[str] = []
    ignored: list[str] = []
    for line in _split_instructions(prompt):
        matched = False
        for rx, builder in RULES:
            m = rx.search(line)
            if m:
                desc = builder(m, doc)
                if desc:
                    applied.append(f'"{line}" — {desc}')
                    matched = True
                    break
        if not matched:
            ignored.append(line)

    # Re-render YAML, preserving any leading comment block from the original
    leading_comments = []
    for ln in yaml_text.splitlines():
        if ln.startswith("#") or not ln.strip():
            leading_comments.append(ln)
        else:
            break
    body = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=100)
    out = ("\n".join(leading_comments) + "\n" if leading_comments else "") + body
    return PromptResult(yaml_text=out, applied=applied, ignored=ignored)


# ============================================================ Gemini path

def _have_gemini() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def _gemini_call(prompt_text: str, yaml_text: str, model: str = "gemini-flash-latest",
                 max_retries: int = 3) -> str:
    """Call Gemini with the prompt + current YAML. Returns the new YAML
    text. Retries with exponential backoff on transient errors."""
    from google import genai
    from google.genai.errors import APIError

    client = genai.Client()
    instr = (
        "You are refining a YAML test plan for a UI test automation tool. "
        "The YAML must remain valid for the parser, which accepts this shape:\n"
        "  tests:\n"
        "    - screen: <screenname>\n"
        "      name: <human label>\n"
        "      steps:\n"
        "        - { action: <click|fill|select|open_search|wait|screenshot|"
        "assert_visible|assert_text|assert_no_errors|assert_rows_min|"
        "assert_rows_max|todo>, target?: <str>, value?: <str> }\n\n"
        "Apply the user's instructions to the YAML below. Output ONLY the "
        "resulting YAML — no markdown fences, no commentary, no explanation.\n"
        "Preserve any leading `# comment` lines verbatim.\n\n"
        f"=== USER INSTRUCTIONS ===\n{prompt_text}\n\n"
        f"=== CURRENT YAML ===\n{yaml_text}\n"
    )
    last_err = None
    for attempt in range(max_retries):
        try:
            r = client.models.generate_content(model=model, contents=instr)
            text = (r.text or "").strip()
            # Strip accidental markdown fences if Gemini added them anyway
            text = re.sub(r"^```(?:yaml)?\s*", "", text)
            text = re.sub(r"\s*```\s*$", "", text)
            return text
        except APIError as e:
            last_err = e
            code = getattr(e, "code", None)
            # Retry only on transient errors
            if code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)        # 1s, 2s, 4s
                continue
            raise
    raise last_err or RuntimeError("gemini call failed")


# Daily-usage guard so the free tier can never be accidentally blown
def _usage_path() -> Path:
    return Path.home() / ".stratus-qa" / "llm-usage.json"

def _load_usage() -> dict:
    p = _usage_path()
    if not p.exists(): return {}
    try: return json.loads(p.read_text())
    except Exception: return {}

def _save_usage(u: dict) -> None:
    p = _usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(u, indent=2))

def _today_key() -> str:
    return time.strftime("%Y-%m-%d")


def llm_refine(yaml_text: str, prompt: str,
               daily_limit: int | None = None) -> PromptResult:
    """Send unmatched prompt + YAML to Gemini. Returns a PromptResult with
    `llm_used=True` and a fresh `yaml_text`. If GEMINI_API_KEY is missing
    OR the daily limit is exhausted, returns the input unchanged with an
    error message."""
    if not _have_gemini():
        return PromptResult(yaml_text=yaml_text, llm_used=False,
                            llm_error="GEMINI_API_KEY not set in environment")
    if daily_limit is None:
        daily_limit = int(os.environ.get("GEMINI_DAILY_REQUEST_LIMIT") or "200")
    usage = _load_usage()
    today = usage.get(_today_key(), 0)
    if today >= daily_limit:
        return PromptResult(yaml_text=yaml_text, llm_used=False,
                            llm_error=f"daily Gemini limit reached "
                                      f"({today}/{daily_limit}). Resets at midnight UTC.")
    try:
        new_yaml = _gemini_call(prompt, yaml_text)
        usage[_today_key()] = today + 1
        _save_usage(usage)
        # Quick sanity — must still parse as YAML
        import yaml
        yaml.safe_load(new_yaml)
        return PromptResult(
            yaml_text=new_yaml, llm_used=True,
            llm_changed=(new_yaml.strip() != yaml_text.strip()),
            applied=[f"Gemini refined the YAML based on: {prompt[:80]}"],
        )
    except Exception as e:
        return PromptResult(yaml_text=yaml_text, llm_used=False,
                            llm_error=str(e)[:200])


# ============================================================ Public API

def apply_prompt(yaml_text: str, prompt: str, use_llm: bool = False) -> PromptResult:
    """Apply the rule engine, optionally followed by Gemini refinement.

    Strategy:
      1. Always run the rule engine first (free, deterministic)
      2. If use_llm=True AND there are ignored instructions, send only those
         to Gemini along with the current (post-rule) YAML
    """
    result = apply_rules(yaml_text, prompt)
    if use_llm and result.ignored and _have_gemini():
        remaining_prompt = "\n".join(result.ignored)
        llm_result = llm_refine(result.yaml_text, remaining_prompt)
        result.yaml_text = llm_result.yaml_text
        result.llm_used = llm_result.llm_used
        result.llm_error = llm_result.llm_error
        result.llm_changed = llm_result.llm_changed
        if llm_result.applied: result.applied.extend(llm_result.applied)
    return result


def llm_available() -> dict:
    """Status for the UI — what does the user have configured?"""
    usage = _load_usage()
    today = usage.get(_today_key(), 0)
    limit = int(os.environ.get("GEMINI_DAILY_REQUEST_LIMIT") or "200")
    return {
        "configured": _have_gemini(),
        "model": os.environ.get("GEMINI_MODEL") or "gemini-flash-latest",
        "used_today": today,
        "daily_limit": limit,
    }
