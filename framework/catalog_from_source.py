"""Build the screen catalogue from the BackOffice SOURCE tree.

The original builder (``catalog_builder.py``) drives Playwright over a running
Stratus install. That works, but it needs a login, a reachable server, and it
only ever sees the screens that render without error — the shipped catalogue
covered 239 screens and carried no grid columns at all.

This module reads the same information out of the source instead:

    menu.html                    screen list, labels, data-href, enterprise gate
    templates/{list,detail}screens/<screen>/*components*.html   fields, tabs
    templates/{list,detail}screens/<screen>/*buttons*.html      buttons
    defaultconfig/listscreens/screens/<SCREEN>/<SCREEN>Grid.xml grid columns

Output is byte-compatible with ``knowledge_base/screens_catalog.json``.

Two keys the crawl produces are deliberately not reproduced: ``rendered`` and
``error_words``. Neither affects a test run — ``assert_no_errors`` re-scans the
live page against ``crawl_runner.ERROR_WORDS``, not against the catalogue. Their
only consumer is a counter in ``catalog_analyzer``. We emit ``rendered: True``
and ``error_words: []`` so that counter still reads sensibly.

Usage:
    python -m framework.catalog_from_source <path-to-backoffice> [-o out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

# ── screen type, matching crawl_runner._classify exactly ───────────────────
_TYPE_BY_PATH = [
    ("/listscreen/", "list"),
    ("/detailscreen/", "detail"),
    ("/dtlscreen/", "detail"),
    ("/wizardscreen/", "wizard"),
    ("/reportscreen/", "report"),
]

_FIELD_TAGS = {"input", "select", "textarea"}
_NON_FIELD_INPUT_TYPES = {"hidden", "button", "submit", "reset", "image"}


def _classify(href: str) -> str:
    low = (href or "").lower()
    for frag, kind in _TYPE_BY_PATH:
        if frag in low:
            return kind
    return "other"


def _txt(s: str) -> str:
    """Collapse whitespace the way normalize-space() would."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _truthy(v) -> bool:
    return str(v or "").strip().upper() in ("Y", "YES", "TRUE", "1")


# ──────────────────────────────────────────────────────────── menu parsing
class _MenuParser(HTMLParser):
    """Pull every screen anchor out of menu.html.

    Attribute spelling is inconsistent in this file (``screenname`` and
    ``screenName`` both occur), which is why the runtime lowercases before
    comparing — we do the same.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries: list[dict] = []
        self._cur: dict | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        href = (a.get("data-href") or "").strip()
        if not href or "@" in href:
            # "@" means an unresolved server token (e.g. @MINFILTER) — not a real route.
            return

        # The screen name is the ROUTE segment, not the screenname= attribute.
        # They disagree on 7 anchors, and because three of those attribute values
        # collide with other screens, the old crawler de-duplicated them away and
        # lost frequentbuyerlist, benmooresalesexportdtl and distroratiolist.
        m = re.search(r"#/(?:listScreen|detailScreen|dtlScreen|wizardScreen|reportScreen)/([^/?#]+)",
                      href, re.I)
        if not m:
            return

        self._cur = {
            "screenname": m.group(1).strip().lower(),
            "data_href": href,                  # verbatim — passed straight to page.goto()
            "type": _classify(href),
            "enterprise": (a.get("data-display-enterprise") or "").strip(),
            # kept for reference: this is what the security servlet is asked about
            "security_screenname": (a.get("screenname") or "").strip().lower(),
        }
        self._buf = []

    def handle_data(self, data):
        if self._cur is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self._cur["label"] = _txt("".join(self._buf))
            self.entries.append(self._cur)
            self._cur, self._buf = None, []


# ─────────────────────────────────────────────────── component/field parsing
class _ComponentParser(HTMLParser):
    """Collect fields, buttons, tabs and labels from a template file.

    A real parser is required rather than a regex: a number of ids in these
    templates are written unquoted.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields: list[dict] = []
        self.buttons: list[dict] = []
        self.tabs: list[str] = []
        self.labels: dict[str, str] = {}     # for -> visible text
        self._label_for: str | None = None
        self._label_buf: list[str] = []
        self._btn: dict | None = None
        self._btn_buf: list[str] = []
        self._select: dict | None = None
        self._opt_buf: list[str] | None = None

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _a(attrs):
        return {k.lower(): (v if v is not None else "") for k, v in attrs}

    def handle_starttag(self, tag, attrs):
        a = self._a(attrs)

        # tabs: <div class="tabPanel" data-title="Basic">
        if "tabpanel" in (a.get("class") or "").lower() and a.get("data-title"):
            t = _txt(a["data-title"])
            if t and t not in self.tabs:
                self.tabs.append(t)

        if tag == "label":
            self._label_for = (a.get("for") or "").strip()
            self._label_buf = []
            return

        if tag == "button":
            self._btn = {"id": (a.get("id") or "").strip(),
                         "name": (a.get("name") or "").strip(),
                         "attrs": a}
            self._btn_buf = []
            return

        if tag == "option" and self._select is not None:
            self._opt_buf = []
            return

        if tag not in _FIELD_TAGS:
            return

        itype = (a.get("type") or "").strip().lower()

        # buttons wearing an <input> costume belong with the buttons
        if tag == "input" and itype in _NON_FIELD_INPUT_TYPES:
            if itype in ("button", "submit", "reset", "image"):
                self.buttons.append({
                    "id": (a.get("id") or "").strip(),
                    "name": (a.get("name") or "").strip(),
                    "text": _txt(a.get("value") or a.get("name") or a.get("id")),
                    "attrs": a,
                })
            return

        if _truthy(a.get("data-ishidden")):
            return

        ftype = "select" if tag == "select" else "textarea" if tag == "textarea" else (itype or "text")
        if ftype == "text" and (a.get("data-componentype") == "DynDates"
                                or "date" in (a.get("data-mask") or "").lower()):
            ftype = "date"

        # Only a real maxlength counts. data-sizetype is a layout width class
        # (its whole range is 1|4|5|6), not a character limit — using it would
        # make the generator produce truncated values. Unknown stays 0.
        try:
            maxlen = int(str(a.get("maxlength") or "").strip())
        except (TypeError, ValueError):
            maxlen = 0

        f = {
            "id": (a.get("id") or "").strip(),
            "name": (a.get("name") or "").strip(),
            "label": "",                       # filled in after parsing
            "type": ftype,
            "required": (_truthy(a.get("data-required")) or _truthy(a.get("req"))
                         or "notempty" in (a.get("class") or "").lower()),
            "placeholder": (a.get("placeholder") or "").strip(),
            "max_length": maxlen,
            "options": [],
        }
        self.fields.append(f)
        if tag == "select":
            self._select = f

    def handle_data(self, data):
        if self._label_for is not None:
            self._label_buf.append(data)
        if self._btn is not None:
            self._btn_buf.append(data)
        if self._opt_buf is not None:
            self._opt_buf.append(data)

    def handle_endtag(self, tag):
        if tag == "label" and self._label_for is not None:
            txt = _txt("".join(self._label_buf))
            if self._label_for and txt:
                self.labels.setdefault(self._label_for, txt)
            self._label_for, self._label_buf = None, []

        elif tag == "button" and self._btn is not None:
            b = self._btn
            b["text"] = _txt("".join(self._btn_buf)) or b["name"] or b["id"]
            self.buttons.append(b)
            self._btn, self._btn_buf = None, []

        elif tag == "option" and self._opt_buf is not None:
            o = _txt("".join(self._opt_buf))
            if o and self._select is not None:
                self._select["options"].append(o)
            self._opt_buf = None

        elif tag == "select":
            self._select = None

    # -- post-processing --------------------------------------------------
    def resolve_labels(self):
        """<label for=X> matches a field by id first, then by name."""
        for f in self.fields:
            f["label"] = (self.labels.get(f["id"])
                          or self.labels.get(f["name"])
                          or f["name"] or f["id"])


# ─────────────────────────────────────────────────────────────── grid xml
def _grid_columns(root: Path, screenname: str) -> tuple[bool, list[str]]:
    up = screenname.upper()
    for base in ("listscreens", "detailscreens"):
        p = root / "src/resources/defaultconfig" / base / "screens" / up / f"{up}Grid.xml"
        if not p.exists():
            continue
        try:
            xml = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        cols = []
        for m in re.finditer(r"<\w+\s+([^>]*?)/?>", xml):
            attrs = dict(re.findall(r'(\w[\w-]*)\s*=\s*"([^"]*)"', m.group(1)))
            if "Caption" not in attrs and "Name" not in attrs:
                continue
            if not _truthy(attrs.get("Show", "Y")):
                continue
            cap = (attrs.get("Caption") or attrs.get("Name") or "").strip()
            if cap and cap not in cols:
                cols.append(cap)
        if cols:
            return True, cols
    return False, []


# ───────────────────────────────────────────────────────── template lookup
def _find_dir(root: Path, screenname: str) -> Path | None:
    for base in ("listscreens", "detailscreens"):
        d = root / "WebContent/mv-assets/templates" / base / screenname
        if d.is_dir():
            return d
    return None


def _pick(d: Path, kind: str) -> Path | None:
    """components.html | <dir>-components.html | <dir>_components.html"""
    for cand in (f"{kind}.html", f"{d.name}-{kind}.html", f"{d.name}_{kind}.html"):
        p = d / cand
        if p.exists():
            return p
    hits = sorted(d.glob(f"*{kind}*.html"))
    return hits[0] if hits else None


def _parse(path: Path | None) -> _ComponentParser | None:
    if not path or not path.exists():
        return None
    p = _ComponentParser()
    try:
        p.feed(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    p.resolve_labels()
    return p


# ──────────────────────────────────────────────────────────────── builder
def build(root: Path, base_url: str = "") -> dict:
    menu_path = root / "WebContent/mv-assets/menu.html"
    if not menu_path.exists():
        raise SystemExit(f"menu.html not found under {root}")

    mp = _MenuParser()
    mp.feed(menu_path.read_text(encoding="utf-8", errors="ignore"))

    screens, seen = [], set()
    for e in mp.entries:
        sn = e["screenname"]
        if not sn or sn in seen:
            continue
        seen.add(sn)

        d = _find_dir(root, sn)
        comp = _parse(_pick(d, "components")) if d else None
        btns = _parse(_pick(d, "buttons")) if d else None
        has_grid, cols = _grid_columns(root, sn)

        # Buttons in buttons.html are the screen's action bar; those found in
        # the components file sit inside the form itself.
        topnav = [b for b in (btns.buttons if btns else [])]
        form = [b for b in (comp.buttons if comp else [])]

        def shape(bs, loc):
            out = []
            for b in bs:
                a = b.get("attrs") or {}
                txt = b.get("text") or b.get("name") or b.get("id")
                if not (txt or b.get("id")):
                    continue
                out.append({"id": b.get("id", ""), "name": b.get("name", ""),
                            "text": txt, "location": loc,
                            **({"module": a["module"]} if a.get("module") else {})})
            return out

        screens.append({
            "screenname": sn,
            "label": e.get("label") or sn,
            "type": e["type"],
            "data_href": e["data_href"],
            "rendered": True,        # source-built: nothing was skipped for failing to load
            "error_words": [],       # see module docstring
            "fields": comp.fields if comp else [],
            "topnav_buttons": shape(topnav, "topnav"),
            "action_menu_items": [],
            "form_buttons": shape(form, "form"),
            "other_buttons": [],
            "has_grid": has_grid,
            "grid_columns": cols,
            "tabs": (comp.tabs if comp else []),
            **({"enterprise": e["enterprise"]} if e.get("enterprise") else {}),
        })

    return {
        "base_url": base_url,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "screen_count": len(screens),
        "source": str(root),
        "screens": screens,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the QA screen catalogue from BackOffice source.")
    ap.add_argument("root", help="path to the BackOffice checkout (the folder holding WebContent/)")
    ap.add_argument("-o", "--out", default="knowledge_base/screens_catalog.json")
    ap.add_argument("--base-url", default="")
    a = ap.parse_args(argv)

    cat = build(Path(a.root), a.base_url)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cat, indent=1), encoding="utf-8")

    withf = sum(1 for s in cat["screens"] if s["fields"])
    withg = sum(1 for s in cat["screens"] if s["has_grid"])
    print(f"screens      : {cat['screen_count']}")
    print(f"with fields  : {withf}")
    print(f"with a grid  : {withg}")
    print(f"total fields : {sum(len(s['fields']) for s in cat['screens'])}")
    print(f"written      : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
