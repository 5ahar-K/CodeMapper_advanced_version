"""Shared by sample_calls.py and score.py so that both use exactly the same definition of 'candidate'."""
from __future__ import annotations

from codemapper.baseline import BaselineResolver
from codemapper.resolve import Resolver


def candidate_ids(ix, call) -> list:
    """Every repo function this call COULD refer to, judging by name alone:
    functions/methods called `name`, plus the constructors (__init__) of classes called `name`."""
    ids = set(ix.functions_by_name.get(call.name, []))
    for cls in ix.classes_by_name.get(call.name, []):
        ids.update(ix.find_method(cls, "__init__"))
    return sorted(ids)


def universe(ix, include_tests: bool = False):
    """All call sites worth labelling: those whose name matches at least one repo function.
    (Calls to names that exist nowhere in the repo cannot be repo-internal calls, so labelling them teaches nothing.)"""
    for f in sorted(ix.functions.values(), key=lambda f: f.id):
        if f.is_test and not include_tests:
            continue
        for c in f.calls:
            cands = candidate_ids(ix, c)
            if cands:
                yield f, c, cands


def make_predictors(ix):
    """name -> function(fn, call) -> set of predicted target ids. The rows of your results table."""
    res, base = Resolver(ix), BaselineResolver(ix)

    def at(levels):
        def f(fn, call):
            r = res.resolve(fn, call)
            return set(r.targets) if r.confidence in levels else set()
        return f

    return {
        "baseline (your original heuristic)": lambda fn, call: set(base.resolve(fn, call)),
        "new: high only": at({"high"}),
        "new: high + medium": at({"high", "medium"}),
        "new: high + medium + low": at({"high", "medium", "low"}),
        "new: everything incl. ambiguous": at({"high", "medium", "low", "ambiguous"}),
    }, res
