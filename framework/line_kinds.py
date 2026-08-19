"""Classify one line of a tester's sheet.

The old model asked "did this line produce a step?" and rendered a warning when
it had not — which put "No steps came from this line" directly above "1 note
from your sheet". Both were computed from different fields, so they could
contradict each other, and on the real sheet the warning was wrong every time.

Here a line has exactly ONE kind. The renderer reads that one field, so the
contradiction is not merely unstyled, it is unrepresentable.
"""
from __future__ import annotations
import re

ACTION   = "action"     # produced steps — no status line, silence is the reward
SETUP    = "setup"      # SQL / module config the tester runs themselves
HEADING  = "heading"    # a label in the sheet; nothing to click
NOTE     = "note"       # a condition or expectation kept beside the test
QUESTION = "question"   # we need one answer from the tester

# Question sub-types drive the wording of the card.
Q_EXPECTED = "expected-result"
Q_BULLET   = "settings-bullets"
Q_UNKNOWN  = "unknown-line"

_SQL = re.compile(r"^\s*(?:update|insert|delete|select|alter|create|drop|db\s*check)\b", re.I)
_EXPECTED = re.compile(
    r"^\s*(?:it\s+will|it\s+should|system\s+should|should\s+(?:display|show)|"
    r"the\s+system\s+displays|expected|make\s+sure|nothing\s+gets)", re.I)
_BULLET = re.compile(r"^\s*[-*••▪]")
_COND = re.compile(r"^\s*[-*•]?\s*if\b", re.I)
_VERB = re.compile(
    r"\b(?:click|enter|select|type|press|go\s+to|open|verify|check|scan|save|"
    r"search|add|edit|put|choose|tick)\b", re.I)
_BREADCRUMB = re.compile(r"(?:>>|->|\s>\s|→)")
_SECURITY = re.compile(r"\b(?:security|permission|user\s+rights?|allow\s+the\s+user)\b", re.I)


def classify_line(text: str, produced_steps: bool) -> tuple[str, str | None]:
    """Return (kind, question_type). question_type is None unless kind is QUESTION."""
    s = (text or "").strip()
    if not s:
        return HEADING, None

    if _SQL.search(s) or _SECURITY.search(s):
        return SETUP, None

    # An expectation is the one case worth asking about: it may be something to
    # check on screen, or just prose. Both readings are common in these sheets.
    if _EXPECTED.match(s):
        return QUESTION, Q_EXPECTED

    if _COND.match(s):
        return NOTE, None

    if produced_steps:
        return ACTION, None

    # A bullet that produced nothing is usually a setting worth checking for.
    if _BULLET.match(s):
        return QUESTION, Q_BULLET

    # Short, verb-less, or a menu path: a label, not an instruction.
    if _BREADCRUMB.search(s) or (len(s) < 60 and not _VERB.search(s)):
        return HEADING, None

    return QUESTION, Q_UNKNOWN
