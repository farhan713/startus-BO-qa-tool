"""Classify the rows that could not become UI actions.

Legacy test sheets put four different kinds of content in one "Testing Steps"
column: actual UI steps, database/config setup, security prerequisites, and
statements of business rules or requirements. Only the first is automatable.

Reporting all of them as "needs your input" is what makes the tool look like it
is handing the work back — a tester reads 217 and assumes the tool did nothing.
Naming the kind turns that into a much smaller, truthful number: these many
steps need you, and these others were never steps.
"""
from __future__ import annotations
import re

STEP     = "step"          # a real UI action that still needs a human
SETUP_DB = "setup_db"      # DB / module / config prerequisite
SETUP_SEC= "setup_sec"     # security right that must be granted first
RULE     = "rule"          # business rule, requirement or expected behaviour

LABELS = {
    STEP:      "Needs input",
    SETUP_DB:  "Setup",
    SETUP_SEC: "Permission",
    RULE:      "Rule to check",
}

_DB = re.compile(
    r"\b(?:db\s*check|tb_[a-z_]+|update\s+\w+\s+set\b|select\s+\*|insert\s+into"
    r"|is_used\s*=|module_name\s*=|turned?\s+(?:on|off)\b|set\s+to\s+[ynYN]\b"
    r"|database|stored\s+proc)", re.I)

_SEC = re.compile(
    r"\b(?:security|permission|user\s+rights?|allow\s+(?:the\s+)?user"
    r"|create/recall|re-?open\s+deal|void\s+deal|access\s+to)\b", re.I)

_RULE = re.compile(
    r"^\s*(?:if\b|when\b|this\s+will\b|it\s+will\b|the\s+\w+\s+will\b"
    r"|add\s+a\b|remove\s+\w+\s+button|include\b|example\s*:|o\s+example"
    r"|note\s*:|make\s+sure\b|nothing\s+gets\b|should\b|must\b)", re.I)

_RULE_MID = re.compile(
    r"\b(?:will\s+(?:be\s+)?(?:display|show|generat|allow)|won['’]?t\s+show"
    r"|does\s+not\s+(?:show|display|say)|gets?\s+saved|is\s+where\s+the\s+user)", re.I)


# Sub-bullets ("o ...", "a. ...") in these sheets are always elaboration of the
# line above — an example, a consequence, or an acceptance note — never a step.
_SUBBULLET = re.compile(r"^\s*(?:o\s+|[a-z][\.\)]\s+|\u25aa\s*|\u2022\s*)", re.I)

# Requirement phrasing: the sheet restating what the mod must do.
_REQUIREMENT = re.compile(
    r"^\s*(?:apply|remove|add|allow|require|alert|automatic|enable|disable|"
    r"support|display|generate)\b.*\b(?:during|when|from|for|in|of|to)\b", re.I)


# A bare ALL_CAPS token is a TB_MODULE constant from a list of modules to switch
# on ("CONSIGNMENT", "LIVE_TAGGING"), i.e. environment setup, not a step.
_MODULE_CONST = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")

# Narrative openers used when the sheet is describing the workflow in prose.
_NARRATIVE = re.compile(
    r"^\s*(?:once\b|at\s+this\s+point\b|periodically\b|then\b|after\b|"
    r"next\s+the\b|meanwhile\b|finally\b|the\s+\w+\s+will\b|"
    r"this\s+(?:is|allows|will)\b|there\s+(?:is|are)\b)", re.I)

# A breadcrumb is navigation however long it runs, so it must never be caught
# by the prose-length heuristic below.
_BREADCRUMB = re.compile(r"(?:>>|->|\s>\s|→)")

# Lowercase openers that ARE instructions, so they survive the fragment check.
_SECTION_HEADING = re.compile(
    r"^\s*(?:buttons?|fields?|grid|grids?\s+results?|search\s+(?:criteria|components?)"
    r"|navigation\s+buttons?|columns?|bottom\s+(?:section|information)"
    r"|top\s+section|customer\s+info|deal\s+grid(?:\s+buttons?)?"
    r"|components?|tabs?|menu\s+options?)\s*:?\s*$", re.I)

_VERB_START = re.compile(
    r"^\s*(?:click|select|choose|enter|put|type|set|open|go|search|save|add|"
    r"check|verify|scan|create|edit|delete|update|close|print|apply|under|"
    r"enable|disable|login|log\s*in|navigate|press|tap|fill)\b", re.I)


def classify(text: str) -> str:
    """Best-effort kind for one untranslated row."""
    t = (text or "").strip()
    if not t:
        return STEP
    if _SUBBULLET.match(t):
        return RULE
    if _MODULE_CONST.match(t) and len(t) >= 4:
        return SETUP_DB
    if _NARRATIVE.match(t):
        return RULE
    # "Search Components:", "Buttons", "Grid Results:" — the sheet listing what a
    # screen contains rather than telling anyone to do something. The rows under
    # such a heading are element names, not instructions.
    if _SECTION_HEADING.match(t):
        return RULE
    # A fragment that starts mid-sentence is the tail of wrapped prose, never
    # an instruction — real steps start with a verb or a proper noun.
    if t[:1].islower() and not _VERB_START.match(t):
        return RULE
    if _DB.search(t):
        return SETUP_DB
    if _SEC.search(t):
        return SETUP_SEC
    if _RULE.match(t) or _RULE_MID.search(t) or _REQUIREMENT.match(t):
        return RULE
    # A long sentence with a verb phrase is prose, not a step. Real leftover
    # steps in these sheets are short fragments ("Custom Tab", "Advance Filter").
    # Long prose with no breadcrumb is description, not an instruction. Steps in
    # these sheets are short fragments; a breadcrumb stays a step at any length.
    if len(t) > 60 and " " in t and not _BREADCRUMB.search(t):
        return RULE
    return STEP


def summarise(kinds: list[str]) -> dict:
    return {k: sum(1 for x in kinds if x == k)
            for k in (STEP, SETUP_DB, SETUP_SEC, RULE)}
