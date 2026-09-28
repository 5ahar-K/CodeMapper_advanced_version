"""STEP 1 of measuring: draw a random sample of call sites for you to label BY HAND.

    python -m evaluation.sample_calls /path/to/repo --n 100 --seed 0 --out labels.csv

The CSV is BLIND on purpose: it does not show what the tool predicts, so your labels cannot be
anchored on the tool's answer. How to label each row:

  1. read `source_line`, then open `file` at `lineno` and work out which function is REALLY called
     (follow imports, check the class of `self`, look at how the variable was created)
  2. in `true_labels` write the numbers from `candidates`, separated by |     e.g.  2   or   1|3
       0  = none of the candidates (it is a builtin / stdlib / third-party call, or dynamic)
       ?  = you honestly cannot tell (row is excluded from scoring; the count is reported)
"""
from __future__ import annotations

import argparse
import csv
import json
import random

from codemapper import Index

from .common import universe


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="labels.csv")
    ap.add_argument("--include-tests", action="store_true")
    a = ap.parse_args(argv)

    ix = Index.build(a.repo)
    rows = list(universe(ix, a.include_tests))
    total = len(rows)
    random.Random(a.seed).shuffle(rows)
    rows = rows[:a.n]
    print(f"universe: {total} call sites whose name matches a repo function; sampled {len(rows)}")

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "file", "lineno", "caller", "call_text", "source_line",
                    "n_candidates", "candidates", "true_labels", "_col", "_candidate_ids"])
        for i, (f, c, cands) in enumerate(rows, 1):
            src = (ix.root / f.file).read_text(encoding="utf-8", errors="replace").splitlines()[c.lineno - 1].strip()
            human = "  ||  ".join(f"{k}: {ix.label(cid)}" for k, cid in enumerate(cands, 1))
            w.writerow([i, f.file, c.lineno, f.id, c.text, src, len(cands), human, "", c.col, json.dumps(cands)])
    print(f"wrote {a.out}. Fill in `true_labels` for every row, then run evaluation.score")


if __name__ == "__main__":
    main()
