"""Work out which screen a test scenario belongs to, instead of asking.

Legacy sheets already name the destination in every scenario — "Path: Product
Management > Setup > Product Classification Options". All 238 catalogued screens
carry a human label ("Customer SMS Messaging" -> customersmstemplatelist), so the
breadcrumb can be matched against the catalog directly.

This removes the single most error-prone input on the import screen: a tester
typing an exact screenname. When they typed "productclassificationoptionslist"
(which does not exist) the AI tier ran with no field context at all and had to
guess targets from wording.
"""
from __future__ import annotations

import difflib
import re

_STOP = re.compile(r"\b(?:screen|page|list|tab|menu|setup|option|options)\b", re.I)


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _key(s: str) -> str:
    """Comparison key: lowercase alphanumerics, common screen nouns dropped."""
    return re.sub(r"\s+", " ", _STOP.sub(" ", _norm(s))).strip()


def _candidates(steps: list[dict]) -> list[str]:
    """Breadcrumb-ish text from a scenario's steps, best guess first.

    The destination is the LAST segment of the first navigation step, which is
    also what _r_navigate already reduces a breadcrumb to."""
    out = []
    for st in steps[:3]:
        t = (st.get("target") or "").strip()
        if not t:
            continue
        parts = [p.strip() for p in re.split(r"\s*(?:>>|->|>|→)\s*", t) if p.strip()]
        if parts:
            out.append(parts[-1])
        if len(parts) > 1:
            out.append(parts[-2])
    return out


def resolve(steps: list[dict], catalog: list[dict], default: str) -> tuple[str, float]:
    """Return (screenname, confidence 0-1). Falls back to `default`."""
    if not catalog:
        return default, 0.0
    by_key = {}
    for s in catalog:
        name = (s.get("screenname") or "").strip()
        if not name:
            continue
        for text in (s.get("label"), name):
            k = _key(str(text or ""))
            if k:
                by_key.setdefault(k, name)
    keys = list(by_key)
    for cand in _candidates(steps):
        k = _key(cand)
        if not k:
            continue
        if k in by_key:
            return by_key[k], 1.0
        near = difflib.get_close_matches(k, keys, n=1, cutoff=0.84)
        if near:
            score = difflib.SequenceMatcher(None, k, near[0]).ratio()
            return by_key[near[0]], round(score, 3)
    return default, 0.0
