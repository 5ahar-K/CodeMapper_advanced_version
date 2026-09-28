"""Command line: replaces the `while True: input()` loop and the ad-hoc statistics cells.

  python -m codemapper stats   REPO
  python -m codemapper find    REPO NAME
  python -m codemapper callers REPO NAME [--min-conf high|medium|low|ambiguous]
  python -m codemapper callees REPO NAME
  python -m codemapper blast   REPO NAME
  python -m codemapper top     REPO [-n 10]
  python -m codemapper ask     REPO NAME "question" --model <pinned model name>     (needs GEMINI_API_KEY)
"""
from __future__ import annotations

import argparse
import json

from . import graph as g
from .index import Index
from .resolve import Resolver


def _pick(ix, query):
    ids = ix.find(query)
    if not ids:
        raise SystemExit(f"no function matches {query!r}")
    if len(ids) > 1:
        print(f"{len(ids)} matches - be more specific (Class.method or the full id):")
        for i in ids[:25]:
            print("  ", i)
        if len(ids) > 25:
            print(f"   ... and {len(ids) - 25} more")
        raise SystemExit(1)
    return ids[0]


def main(argv=None):
    p = argparse.ArgumentParser(prog="codemapper")
    p.add_argument("cmd", choices=["stats", "find", "callers", "callees", "blast", "top", "ask"])
    p.add_argument("repo")
    p.add_argument("name", nargs="?")
    p.add_argument("question", nargs="?")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--min-conf", default="low", choices=["high", "medium", "low", "ambiguous"])
    p.add_argument("--model")
    p.add_argument("--strict", action="store_true", help="do not repair old-style files, just report them")
    a = p.parse_args(argv)

    ix = Index.build(a.repo, tolerant=not a.strict)
    res = Resolver(ix)
    G = g.build_call_graph(ix, res)

    if a.cmd == "stats":
        print(json.dumps(g.resolution_report(ix, res), indent=2, default=list))
    elif a.cmd == "find":
        for i in ix.find(a.name):
            print(i)
    elif a.cmd in ("callers", "callees"):
        fid = _pick(ix, a.name)
        for other in (g.callers if a.cmd == "callers" else g.callees)(G, fid, a.min_conf):
            print(ix.label(other), f"[{(G[other][fid] if a.cmd == 'callers' else G[fid][other])['confidence']}]")
    elif a.cmd == "blast":
        fid = _pick(ix, a.name)
        print(g.blast_radius(G, fid, a.min_conf))
    elif a.cmd == "top":
        for fid, score in g.top_blast_radius(G, a.n, a.min_conf):
            print(f"{score:5d}  {ix.label(fid)}")
    elif a.cmd == "ask":
        from . import llm
        fid = _pick(ix, a.name)
        print(llm.ask(llm.make_gemini(a.model), ix, G, fid, a.question))
