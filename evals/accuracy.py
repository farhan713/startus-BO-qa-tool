#!/usr/bin/env python3
"""Accuracy benchmark for the test-case importer.

Ground truth comes from ~/Desktop/stratus-qa-test-files/README.md, whose numbers
were themselves measured against the real endpoint. The headline number is the
share of steps that convert into a runnable action instead of falling through to
`todo` (which becomes a question the tester has to answer by hand).
"""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework.testcase_importer import import_xlsx

DIR = os.path.expanduser("~/Desktop/stratus-qa-test-files")

# file -> (tests, steps, questions) as recorded in the README
EXPECTED = {
    "01-all-understood-single-case.xlsx": (1,   5,  0),
    "02-all-understood-many-cases.xlsx":  (5,  25,  0),
    "03-mixed-some-questions.xlsx":       (3,  17,  2),
    "04-every-question-type.xlsx":        (6,  18,  6),
    "05-nothing-understood.xlsx":         (1,   4,  4),
    "06-large-many-steps.xlsx":           (12, 108, 0),
    "07-STRAT-33608-surcharge-label.xlsx":(2,   8,  1),
    "08-layout-numbered-TCno.xlsx":       (3,  11,  0),
    "09-layout-grouped-TS.xlsx":          (2,   8,  0),
    "10-layout-official-template.xlsx":   (3,  11,  0),
}

def measure(path):
    res = import_xlsx(open(path, "rb").read())
    cases = getattr(res, "cases", None) or getattr(res, "tests", None) or []
    steps, todos = 0, 0
    todo_text = []
    for c in cases:
        for st in (getattr(c, "steps", None) or []):
            act = st.get("action") if isinstance(st, dict) else getattr(st, "action", None)
            steps += 1
            if act == "todo":
                todos += 1
                tgt = st.get("target") if isinstance(st, dict) else getattr(st, "target", "")
                todo_text.append((tgt or "")[:90])
    return len(cases), steps, todos, todo_text

def main():
    rows, tot_steps, tot_todo, all_todos = [], 0, 0, []
    for name in sorted(EXPECTED):
        p = os.path.join(DIR, name)
        if not os.path.exists(p):
            rows.append((name, "MISSING", "", "", "")); continue
        try:
            ncases, nsteps, ntodo, todos = measure(p)
        except Exception as e:
            rows.append((name, "ERROR: %s" % type(e).__name__, str(e)[:50], "", "")); continue
        exp_c, exp_s, exp_q = EXPECTED[name]
        ok = "ok" if (ncases == exp_c and nsteps == exp_s) else "DRIFT"
        rows.append((name, "%d/%d" % (ncases, exp_c), "%d/%d" % (nsteps, exp_s),
                     "%d/%d" % (ntodo, exp_q), ok))
        tot_steps += nsteps; tot_todo += ntodo
        all_todos.extend(todos)

    print("  %-40s %-9s %-11s %-11s %s" % ("file", "cases", "steps", "todo/exp", ""))
    print("  " + "-"*84)
    for r in rows:
        print("  %-40s %-9s %-11s %-11s %s" % r)
    print("  " + "-"*84)
    if tot_steps:
        auto = tot_steps - tot_todo
        print("  TOTAL steps: %d   auto-converted: %d   todo: %d   ACCURACY: %.1f%%"
              % (tot_steps, auto, tot_todo, 100.0*auto/tot_steps))
    if "-v" in sys.argv and all_todos:
        print("\n  --- every step that fell through to todo ---")
        from collections import Counter
        for t, n in Counter(all_todos).most_common(40):
            print("   %2dx  %s" % (n, t))

if __name__ == "__main__":
    main()
