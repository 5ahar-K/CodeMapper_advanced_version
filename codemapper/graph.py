"""STAGE 4 - GRAPH. Turn resolutions into networkx graphs and answer questions about them."""
from __future__ import annotations

from collections import Counter

import networkx as nx

from .resolve import CONF_RANK, Resolver


def build_call_graph(index, resolver: Resolver | None = None) -> nx.DiGraph:
    """Edge caller -> callee. Every edge carries `confidence` (best seen), `count` and `lines`.
    Ambiguous edges ARE stored (so you can inspect them) but `trusted()` hides them by default."""
    resolver = resolver or Resolver(index)
    G = nx.DiGraph()
    for f in index.functions.values():
        G.add_node(f.id, name=f.name, qualname=f.qualname, file=f.file, line=f.lineno, is_test=f.is_test)
    for f in index.functions.values():
        for call in f.calls:
            r = resolver.resolve(f, call)
            for t in r.targets:
                if G.has_edge(f.id, t):
                    e = G[f.id][t]
                    e["count"] += 1
                    e["lines"].append(call.lineno)
                    if CONF_RANK[r.confidence] > CONF_RANK[e["confidence"]]:
                        e["confidence"] = r.confidence
                else:
                    G.add_edge(f.id, t, confidence=r.confidence, count=1, lines=[call.lineno])
    return G


def trusted(G: nx.DiGraph, min_conf: str = "low") -> nx.DiGraph:
    """A read-only view containing only edges at least this confident. Default: everything except ambiguous."""
    need = CONF_RANK[min_conf]
    return nx.subgraph_view(G, filter_edge=lambda u, v: CONF_RANK[G[u][v]["confidence"]] >= need)


def callers(G, fid: str, min_conf: str = "low") -> list:
    return sorted(trusted(G, min_conf).predecessors(fid))


def callees(G, fid: str, min_conf: str = "low") -> list:
    return sorted(trusted(G, min_conf).successors(fid))


def blast_radius(G, fid: str, min_conf: str = "low", include_tests: bool = False) -> int:
    """How many functions could be affected if `fid` changes = all direct and indirect callers."""
    view = trusted(G, min_conf)
    if not include_tests:
        view = nx.subgraph_view(view, filter_node=lambda n: not G.nodes[n]["is_test"])
    if fid not in view:
        return 0
    return len(nx.ancestors(view, fid))


def top_blast_radius(G, n: int = 10, min_conf: str = "low", include_tests: bool = False) -> list:
    view = trusted(G, min_conf)
    if not include_tests:
        view = nx.subgraph_view(view, filter_node=lambda x: not G.nodes[x]["is_test"])
    scores = [(node, len(nx.ancestors(view, node))) for node in view.nodes]
    scores.sort(key=lambda t: (-t[1], t[0]))
    return scores[:n]


def build_dataflow_graph(index, resolver: Resolver | None = None) -> nx.DiGraph:
    """Separate graph: edge A -> B means "inside some function, the RESULT of A is passed to B".
    Kept apart from the call graph because the direction means something different
    (a call edge says "caller depends on callee"; this says "B consumes what A produces")."""
    resolver = resolver or Resolver(index)
    D = nx.DiGraph()
    for f in index.functions.values():
        produced = {c.assigned_to: c for c in f.calls if c.assigned_to}
        for consumer in f.calls:
            for var in consumer.arg_names:
                src = produced.get(var)
                if src is None:
                    continue
                r1, r2 = resolver.resolve(f, src), resolver.resolve(f, consumer)
                ok = ("high", "medium")
                if r1.confidence in ok and r2.confidence in ok and len(r1.targets) == 1 and len(r2.targets) == 1:
                    a, b = r1.targets[0], r2.targets[0]
                    if a != b:
                        D.add_edge(a, b, via=var, in_function=f.id)
    return D


def resolution_report(index, resolver: Resolver | None = None) -> dict:
    """The numbers you should print instead of ad-hoc cells: how much of the repo could we resolve, and how sure."""
    resolver = resolver or Resolver(index)
    by_conf, ambiguous_names, unresolved_names = Counter(), Counter(), Counter()
    total = 0
    for f in index.functions.values():
        for c in f.calls:
            r = resolver.resolve(f, c)
            by_conf[r.confidence] += 1
            total += 1
            if r.confidence == "ambiguous":
                ambiguous_names[c.name] += 1
            elif r.confidence == "unresolved":
                unresolved_names[c.name] += 1
    return {
        "files_parsed": len(index.modules),
        "files_failed": len(index.parse_errors),
        "failed_files": list(index.parse_errors),
        "files_repaired": {rel: len(m.repairs) for rel, m in index.modules.items() if m.repairs},
        "functions": len(index.functions),
        "call_sites": total,
        "by_confidence": dict(by_conf),
        "top_ambiguous_names": ambiguous_names.most_common(10),
        "top_unresolved_names": unresolved_names.most_common(10),
    }
