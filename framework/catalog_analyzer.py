"""Catalog analyzer — extract insights from the saved screen catalog.

Once the catalog is built, this produces an analysis report:
  • count of screens per type
  • common button patterns (which buttons appear on most lists/details?)
  • field-type distribution
  • screens with anomalies (no buttons, no fields, error words detected)
  • coverage estimate

This is what enables enterprise-quality testing — we KNOW what's there
before we test it.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from framework.catalog_builder import load_catalog


def analyze() -> dict:
    cat = load_catalog()
    if not cat:
        return {"error": "no catalog found"}
    screens = cat.get("screens") or []

    by_type = Counter(s.get("type", "other") for s in screens)
    field_types = Counter()
    button_text_freq = Counter()
    rendered_count = sum(1 for s in screens if s.get("rendered"))
    error_screens = [s for s in screens if s.get("error_words")]
    no_buttons = [s for s in screens
                  if not (s.get("topnav_buttons") or s.get("form_buttons")
                          or s.get("action_menu_items") or s.get("other_buttons"))]
    no_fields = [s for s in screens if not s.get("fields")]

    # Most common buttons across all screens (by text)
    for s in screens:
        for src in ("topnav_buttons", "form_buttons", "action_menu_items", "other_buttons"):
            for b in s.get(src) or []:
                txt = b.get("text") or b.get("id")
                if txt: button_text_freq[txt] += 1

    # Field-type distribution
    for s in screens:
        for f in s.get("fields") or []:
            field_types[f.get("type", "text")] += 1

    # Field counts per screen (for sizing tests)
    field_counts_per_screen = Counter()
    for s in screens:
        n = len(s.get("fields") or [])
        bucket = "0" if n == 0 else "1-5" if n <= 5 else "6-15" if n <= 15 else "16-30" if n <= 30 else "30+"
        field_counts_per_screen[bucket] += 1

    # Module grouping by screenname prefix
    modules = defaultdict(int)
    for s in screens:
        sn = s.get("screenname", "")
        # Common prefixes
        for prefix in ("customer", "sku", "item", "receipt", "employee", "vendor",
                       "store", "report", "po", "invoice", "inventory", "payment",
                       "cash", "register", "shipping", "tax", "promo", "markdown",
                       "buytrade", "consignment"):
            if sn.startswith(prefix):
                modules[prefix] += 1
                break
        else:
            modules["other"] += 1

    return {
        "total_screens": len(screens),
        "rendered_ok": rendered_count,
        "by_type": dict(by_type),
        "by_module": dict(sorted(modules.items(), key=lambda kv: -kv[1])),
        "top_buttons": button_text_freq.most_common(20),
        "field_type_distribution": dict(field_types),
        "field_count_buckets": dict(field_counts_per_screen),
        "screens_with_no_buttons": len(no_buttons),
        "screens_with_no_fields": len(no_fields),
        "screens_with_errors": len(error_screens),
        "error_screen_names": [s["screenname"] for s in error_screens][:20],
    }


def print_report():
    a = analyze()
    if "error" in a:
        print(f"⚠ {a['error']}")
        return
    print(f"\n{'='*60}")
    print(f"  Stratus Catalog Analysis Report")
    print(f"{'='*60}")
    print(f"  Total screens cataloged:  {a['total_screens']}")
    print(f"  Rendered successfully:    {a['rendered_ok']} ({a['rendered_ok']*100//max(a['total_screens'],1)}%)")
    print(f"  Screens with errors:      {a['screens_with_errors']}")
    print(f"  Screens with no buttons:  {a['screens_with_no_buttons']}")
    print(f"  Screens with no fields:   {a['screens_with_no_fields']}")
    print()
    print(f"  By screen type:")
    for t, n in sorted(a['by_type'].items(), key=lambda kv: -kv[1]):
        print(f"    {t:10} {n:>5}")
    print()
    print(f"  By Stratus module (top 10):")
    for m, n in list(a['by_module'].items())[:10]:
        print(f"    {m:15} {n:>5}")
    print()
    print(f"  Field type distribution:")
    for t, n in sorted(a['field_type_distribution'].items(), key=lambda kv: -kv[1]):
        print(f"    {t:10} {n:>5}")
    print()
    print(f"  Field-count buckets (fields per screen):")
    for b, n in a['field_count_buckets'].items():
        print(f"    {b:10} {n:>5}")
    print()
    print(f"  Top 20 button labels across all screens:")
    for label, n in a['top_buttons']:
        print(f"    {label[:35]:35} {n:>4}")


if __name__ == "__main__":
    print_report()
