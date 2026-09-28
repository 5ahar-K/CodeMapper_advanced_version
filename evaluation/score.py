"""STEP 2 of measuring: score every resolver variant against YOUR hand labels.

    python -m evaluation.score /path/to/repo labels.csv [--show-errors "new: high only"]

Per labelled call site, P = the targets a method predicts and T = the true targets you wrote.
  precision = correct predicted links / all predicted links     ("when it draws an edge, is it right?")
  recall    = correct predicted links / all true links         ("of the real edges, how many did it find?")
  abstain   = share of calls where the method predicted nothing
95% intervals come from bootstrapping the labelled call sites, so you can say how much to trust a number.
"""
from __future__ import annotations

import argparse
import csv
import json
import random

from codemapper import Index

from .common import make_predictors


def read_labels(path):
    rows, unsure, blank = [], 0, 0
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            lab = (r["true_labels"] or "").strip()
            if not lab:
                blank += 1
                continue
            if lab == "?":
                unsure += 1
                continue
            cands = json.loads(r["_candidate_ids"])
            truth = set()
            for tok in (t.strip() for t in lab.split("|")):
                if tok in ("", "0"):
                    continue
                truth.add(cands[int(tok) - 1] if tok.isdigit() else tok)
            rows.append((r["caller"], int(r["lineno"]), int(r["_col"]), truth, r["call_text"]))
    return rows, unsure, blank


def prf(items):
    """items: [(predicted_set, true_set)] -> (precision, recall, abstain_rate)"""
    tp = sum(len(p & t) for p, t in items)
    fp = sum(len(p - t) for p, t in items)
    fn = sum(len(t - p) for p, t in items)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    abstain = sum(1 for p, _ in items if not p) / len(items)
    return prec, rec, abstain


def bootstrap(items, reps=1000, seed=0):
    rnd = random.Random(seed)
    precs, recs = [], []
    for _ in range(reps):
        p, r, _ = prf([rnd.choice(items) for _ in items])
        if p == p:
            precs.append(p)
        if r == r:
            recs.append(r)
    ci = lambda xs: (sorted(xs)[int(0.025 * len(xs))], sorted(xs)[int(0.975 * len(xs)) - 1]) if xs else (float("nan"),) * 2
    return ci(precs), ci(recs)


def score_rows(ix, rows, predictors):
    """rows: [(caller_id, lineno, col, truth_set[, call_text])] -> {method: [(pred, truth, fn, call), ...]}

    In `a.b().c()` the calls `a.b()` and `.c()` START at the same line and column, so position alone does
    not identify a call. The call text (`a.b` vs `a.b().c`) does, so it is part of the match."""
    out = {m: [] for m in predictors}
    for row in rows:
        caller, lineno, col, truth, *rest = row
        text = rest[0] if rest else None
        fn = ix.functions.get(caller)
        call = next((c for c in fn.calls
                     if c.lineno == lineno and c.col == col and (text is None or c.text == text)), None) if fn else None
        if call is None:
            print(f"warning: cannot find {caller} line {lineno}; was the repo changed since sampling?")
            continue
        for m, pred in predictors.items():
            out[m].append((pred(fn, call), truth, fn, call))
    return out


def table(results):
    lines = ["| method | precision | recall | abstains | n |", "|---|---|---|---|---|"]
    for m, items in results.items():
        pairs = [(p, t) for p, t, _, _ in items]
        p, r, ab = prf(pairs)
        (plo, phi), (rlo, rhi) = bootstrap(pairs)
        lines.append(f"| {m} | {p:.2f} ({plo:.2f}-{phi:.2f}) | {r:.2f} ({rlo:.2f}-{rhi:.2f}) | {ab:.0%} | {len(pairs)} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("labels")
    ap.add_argument("--show-errors", metavar="METHOD")
    a = ap.parse_args(argv)

    ix = Index.build(a.repo)
    predictors, res = make_predictors(ix)
    rows, unsure, blank = read_labels(a.labels)
    print(f"{len(rows)} labelled rows scored; {unsure} marked '?' (excluded); {blank} still blank (excluded)\n")
    if not rows:
        raise SystemExit("Nothing to score yet: fill in the `true_labels` column first (see evaluation/sample_calls.py).")
    results = score_rows(ix, rows, predictors)
    print(table(results))

    if a.show_errors:
        print(f"\nErrors for: {a.show_errors}")
        for pred, truth, fn, call in results[a.show_errors]:
            if pred != truth:
                r = res.resolve(fn, call)
                print(f"\n  {fn.file}:{call.lineno}   {call.text}(...)   in {fn.qualname}")
                print(f"     predicted: {sorted(pred)}\n     true:      {sorted(truth)}\n     resolver said: {r.confidence} - {r.reason}")


if __name__ == "__main__":
    main()
