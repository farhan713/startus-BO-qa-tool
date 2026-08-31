"""Intent parser — turn a free-text run request into a structured plan.

Examples handled:
  "test STRAT-28795 with u2"
  "run customer-search-smoke as demo on staging"
  "test the tax-exempt special-char bug with order WEB-99999"

Returns a `LaunchIntent` with:
  - scenario_id   (resolved from the library)
  - user_alias    (resolved against the directory)
  - env_alias     (resolved against the directory)
  - overrides     ({variable: value}) — anything the user pinned explicitly

Uses Gemini for parsing (user picked full-AI). A regex pre-pass handles the
two most common shapes ("test <id>" / "run <id>") so an offline tool still
works for the simple case without an API key.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from framework import scenario_store


@dataclass
class LaunchIntent:
    scenario_id: str | None = None
    user_alias: str | None = None
    env_alias: str | None = None
    overrides: dict = field(default_factory=dict)
    confidence: float = 0.0
    raw_prompt: str = ""
    notes: list = field(default_factory=list)         # human-readable parse trail
    llm_used: bool = False
    llm_error: str = ""

    def is_runnable(self) -> bool:
        return bool(self.scenario_id)


# ============================================================ Pre-pass (regex)
# Cheap, works offline, handles the 2 dominant shapes:
#   "test <id> ..."        / "run <id> ..."        — exact id match (fast)
#   "test the <words> bug" — title-substring match (fuzzy)

_LAUNCH_VERB = re.compile(r"^\s*(?:test|run|execute|launch|repro(?:duce)?|verify|retest|re-?run)\s+", re.I)


def _fuzzy_match_id(prompt: str, scenarios: list) -> tuple[str | None, float]:
    """Try to find a scenario whose id or title matches the prompt."""
    text = prompt.lower()
    # 1. Exact id appearing anywhere in the prompt (case-insensitive)
    for s in scenarios:
        sid = (s.get("id") or "").lower()
        if not sid: continue
        if re.search(r"\b" + re.escape(sid) + r"\b", text):
            return s["id"], 1.0
    # 2. Strip common ticket prefixes ("STRAT-28795" matches when user typed "28795")
    for s in scenarios:
        sid = (s.get("id") or "").lower()
        m = re.search(r"(\d{3,})", sid)
        if m and re.search(r"\b" + m.group(1) + r"\b", text):
            return s["id"], 0.85
    # 3. Title substring (looser — only if at least 2 distinct words from title appear)
    best = (None, 0.0)
    for s in scenarios:
        title = (s.get("title") or "").lower()
        if not title: continue
        title_words = {w for w in re.findall(r"[a-z0-9]{3,}", title)}
        prompt_words = {w for w in re.findall(r"[a-z0-9]{3,}", text)}
        overlap = title_words & prompt_words
        if len(overlap) >= 2:
            score = len(overlap) / max(len(title_words), 1)
            if score > best[1]:
                best = (s["id"], score * 0.7)   # cap fuzzy match below 0.7
    return best


def _user_lookup(prompt: str, users: dict) -> str | None:
    """Pull a user alias from phrases like 'as u2' / 'with user demo' / 'with u2'."""
    text = " " + prompt.lower() + " "
    for alias in users.keys():
        a = alias.lower()
        if re.search(r"\b(?:as|with(?:\s+user)?|using)\s+" + re.escape(a) + r"\b", text):
            return alias
    return None


def _env_lookup(prompt: str, envs: dict) -> str | None:
    text = " " + prompt.lower() + " "
    for alias in envs.keys():
        a = alias.lower()
        if re.search(r"\bon\s+" + re.escape(a) + r"\b", text):
            return alias
    return None


def _override_lookup(prompt: str, user_aliases: set | None = None,
                     env_aliases: set | None = None) -> dict:
    """Pull explicit "with <var> <value>" / "<var>=<value>" overrides.
    Skips matches that collide with user/env aliases (so "with u1 on staging"
    doesn't pick up u1=on as a bogus override)."""
    out: dict = {}
    user_aliases = user_aliases or set()
    env_aliases = env_aliases or set()
    for m in re.finditer(r"\bwith\s+(\w+)\s+['\"]?([A-Za-z0-9_\-\.]{2,40})['\"]?", prompt, re.I):
        key, val = m.group(1).lower(), m.group(2)
        # Skip the user/env keywords + alias names we already handle above
        if key in ("user", "env", "environment"): continue
        if key in user_aliases or key in env_aliases: continue
        # Don't capture stop-words like "on", "as", "in" as values
        if val.lower() in ("on", "as", "in", "and", "or", "the"): continue
        out[key] = val
    for m in re.finditer(r"\{\{\s*(\w+)\s*\}\}\s*[=:]\s*['\"]?([^'\"\s]{1,60})['\"]?", prompt):
        out[m.group(1).lower()] = m.group(2)
    return out


def parse_regex(prompt: str) -> LaunchIntent:
    """Pure-regex parse. Returns a LaunchIntent with whatever could be
    determined cheaply. Caller decides whether to fall back to Gemini."""
    scenarios = scenario_store.list_scenarios()
    directory = scenario_store.load_directory()
    users = directory.get("users") or {}
    envs  = directory.get("envs") or {}
    sid, conf = _fuzzy_match_id(prompt, scenarios)
    intent = LaunchIntent(
        scenario_id=sid,
        user_alias=_user_lookup(prompt, users),
        env_alias=_env_lookup(prompt, envs),
        overrides=_override_lookup(prompt, set(users), set(envs)),
        confidence=conf,
        raw_prompt=prompt,
    )
    if sid:
        intent.notes.append(f"matched scenario {sid!r} (confidence {conf:.2f})")
    return intent


# ============================================================ Gemini parse

def _gemini_parse(prompt: str) -> dict:
    """Ask Gemini to extract {scenario_id, user_alias, env_alias, overrides}.

    Returns a dict (possibly empty if Gemini couldn't determine anything).
    Never raises — translation errors are returned as `{"_error": "..."}`.
    """
    from framework import llm
    if not llm.available():
        return {"_error": "no language model is configured"}

    scenarios = scenario_store.list_scenarios()
    directory = scenario_store.load_directory()
    user_aliases = list((directory.get("users") or {}).keys())
    env_aliases  = list((directory.get("envs")  or {}).keys())
    scenario_summaries = [
        {"id": s.get("id"), "title": s.get("title", ""), "tags": s.get("tags", []),
         "variables": list((s.get("variables") or {}).keys())}
        for s in scenarios[:200]    # cap for token budget
    ]

    instructions = (
        "You are a router for a QA test runner. The user wrote a free-text "
        "request to run a saved test scenario. Match it to one of the known "
        "scenarios + resolve any user, environment, or variable overrides.\n\n"
        "Reply with ONLY a JSON object — no commentary, no markdown fences:\n"
        '  { "scenario_id": "<exact id from list, or null>",\n'
        '    "user_alias":  "<exact alias from list, or null>",\n'
        '    "env_alias":   "<exact alias from list, or null>",\n'
        '    "overrides":   { "<varname>": "<value>", ... },\n'
        '    "confidence":  0.0 to 1.0,\n'
        '    "explanation": "<one sentence why this match>" }\n\n'
        "Rules:\n"
        "- scenario_id MUST be one of the listed ids exactly, or null if no match.\n"
        "- user_alias / env_alias must be one of the listed aliases, or null.\n"
        "- overrides keys must be in the scenario's `variables` list.\n"
        "- confidence < 0.5 means you're guessing — set scenario_id to null.\n\n"
        f"=== AVAILABLE SCENARIOS ({len(scenarios)}) ===\n"
        f"{json.dumps(scenario_summaries, indent=1)}\n\n"
        f"=== AVAILABLE USERS === {user_aliases}\n"
        f"=== AVAILABLE ENVS  === {env_aliases}\n\n"
        f"=== USER REQUEST ===\n{prompt}\n"
    )
    try:
        text = llm.complete(instructions)
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
        return json.loads(text)
    except llm.LLMError as e:
        return {"_error": f"model call failed: {e}"}
    except json.JSONDecodeError as e:
        return {"_error": f"model returned non-JSON: {e}"}
    except Exception as e:
        return {"_error": f"model call failed: {e}"}


def parse_intent(prompt: str, use_llm: bool = True) -> LaunchIntent:
    """Top-level parse. Tries regex first; if that's confident enough,
    skip the Gemini call. Otherwise use Gemini and merge."""
    intent = parse_regex(prompt)

    # If regex got a confident match (>= 0.85) AND we already know user/env if
    # they were referenced, skip the AI call — saves quota.
    if intent.scenario_id and intent.confidence >= 0.85:
        intent.notes.append("regex parse confident — skipped AI")
        return intent

    if not use_llm:
        return intent

    llm = _gemini_parse(prompt)
    if "_error" in llm:
        intent.llm_error = llm["_error"]
        intent.notes.append(f"AI fallback skipped: {llm['_error']}")
        return intent

    intent.llm_used = True
    sid    = llm.get("scenario_id")
    user_a = llm.get("user_alias")
    env_a  = llm.get("env_alias")
    over   = llm.get("overrides") or {}
    conf   = float(llm.get("confidence") or 0.0)

    # AI wins ties where regex was unsure
    if sid and (not intent.scenario_id or conf > intent.confidence):
        intent.scenario_id = sid
        intent.confidence = conf
        intent.notes.append(f"AI matched scenario {sid!r} (confidence {conf:.2f})")
        if llm.get("explanation"):
            intent.notes.append(f"AI: {llm['explanation']}")
    if user_a and not intent.user_alias:
        intent.user_alias = user_a
        intent.notes.append(f"AI resolved user → {user_a}")
    if env_a and not intent.env_alias:
        intent.env_alias = env_a
        intent.notes.append(f"AI resolved env  → {env_a}")
    if over:
        for k, v in over.items():
            if k not in intent.overrides:
                intent.overrides[k] = v
        intent.notes.append(f"AI added overrides: {list(over.keys())}")
    return intent
