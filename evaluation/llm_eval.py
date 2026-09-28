"""Measure what the LLM does with the graph context. Three question types x two conditions.

  callers       "List every function that directly calls X"     truth = trusted callers in the graph
  calls         "Does A directly call B?"                        truth = trusted edge (yes) or no edge (no)
  unanswerable  "How often is X called in production?"           correct behaviour = CANNOT DETERMINE

  condition code_only   the model sees only the function's source code
  condition with_graph  the model sees the source + callers/callees produced by codemapper

IMPORTANT: this measures how the model USES its context: does it copy it faithfully, invent extra items,
and admit ignorance? It does NOT measure whether the graph is correct. That is what evaluation.score is for.
Use temperature 0 and a pinned model name, and report the model name next to every number.

    python -m evaluation.llm_eval /path/to/repo --model <pinned-model-name> -n 30     (needs GEMINI_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass

from codemapper import Index, Resolver, build_call_graph, trusted
from codemapper.llm import CANNOT, build_context


@dataclass
class Question:
    kind: str
    fid: str
    text: str
    truth: object       # set of qualnames | "yes"/"no" | CANNOT


def make_questions(ix, G, n: int = 30, seed: int = 0) -> list:
    rnd = random.Random(seed)
    view = trusted(G)
    prod = [f for f in ix.functions.values() if not f.is_test]
    qs = []

    with_callers = [f for f in prod if 1 <= view.in_degree(f.id) <= 10]
    rnd.shuffle(with_callers)
    for f in with_callers[:n]:
        truth = {ix.functions[c].qualname for c in view.predecessors(f.id)}
        qs.append(Question("callers", f.id,
                           f"List every function that directly calls `{f.qualname}`. Reply with JSON only: "
                           f'{{"callers": ["Class.method", ...]}} or {{"callers": "{CANNOT}"}} if the information is not enough.',
                           truth))

    edges = [(u, v) for u, v in view.edges if not ix.functions[u].is_test]
    rnd.shuffle(edges)
    for u, v in edges[:n // 2]:
        # negative example: a different function with the SAME NAME as v (tests how the model handles name collisions)
        same_name = [x for x in ix.functions_by_name[ix.functions[v].name] if x != v and not view.has_edge(u, x)]
        neg = rnd.choice(same_name) if same_name else None
        for target, truth in ((v, "yes"), (neg, "no")):
            if target is None:
                continue
            qs.append(Question("calls", u,
                               f"Does `{ix.functions[u].qualname}` directly call `{ix.functions[target].qualname}` "
                               f"defined in {ix.functions[target].file}? Reply with JSON only: "
                               f'{{"answer": "yes"}} or {{"answer": "no"}} or {{"answer": "{CANNOT}"}}.', truth))

    templates = ["How many times per second is `{q}` called in production?",
                 "Who wrote `{q}` and in which year?",
                 "Is `{q}` covered by tests in the CI pipeline?"]
    for f in rnd.sample(prod, min(n // 3, len(prod))):
        qs.append(Question("unanswerable", f.id,
                           rnd.choice(templates).format(q=f.qualname) + f' Reply with JSON only: {{"answer": "..."}} '
                           f'(use "{CANNOT}" if the information is not enough).', CANNOT))
    return qs


def context_for(ix, G, q: Question, condition: str) -> str:
    if condition == "with_graph":
        return build_context(ix, G, q.fid)
    f = ix.functions[q.fid]
    return f"Function `{f.qualname}` in {f.file}:\n\n```python\n{ix.source_of(q.fid)}\n```"


def _json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def _norm(name: str) -> str:
    return re.sub(r"\s*\(.*$", "", str(name)).strip().strip("`")


def score_answer(q: Question, raw: str) -> dict:
    parsed = _json(raw) or {}
    if q.kind == "callers":
        ans = parsed.get("callers")
        if ans == CANNOT or ans is None:
            return {"abstained": True, "f1": 0.0, "hallucinated": 0, "valid": ans is not None}
        pred = {_norm(x) for x in ans} if isinstance(ans, list) else set()
        tp = len(pred & q.truth)
        p, r = tp / len(pred) if pred else 0.0, tp / len(q.truth)
        return {"abstained": False, "f1": 2 * p * r / (p + r) if p + r else 0.0,
                "hallucinated": len(pred - q.truth), "valid": True}
    ans = str(parsed.get("answer", "")).strip().lower()
    abstained = ans == CANNOT.lower() or ans == ""
    if q.kind == "calls":
        return {"abstained": abstained, "correct": ans == q.truth, "valid": bool(parsed)}
    return {"abstained": abstained, "correct": abstained, "valid": bool(parsed)}      # unanswerable


def run(llm, ix, G, questions, conditions=("code_only", "with_graph")) -> dict:
    per = defaultdict(list)
    for q in questions:
        for cond in conditions:
            prompt = f"{context_for(ix, G, q, cond)}\n\nQuestion: {q.text}"
            per[(q.kind, cond)].append(score_answer(q, llm(prompt)))
    summary = {}
    for (kind, cond), rs in sorted(per.items()):
        n = len(rs)
        row = {"n": n, "abstain_rate": sum(r["abstained"] for r in rs) / n,
               "unparseable": sum(not r["valid"] for r in rs) / n}
        if kind == "callers":
            row["mean_f1"] = sum(r["f1"] for r in rs) / n
            row["hallucinated_items_per_answer"] = sum(r["hallucinated"] for r in rs) / n
        else:
            row["accuracy"] = sum(r["correct"] for r in rs) / n
        summary[f"{kind} / {cond}"] = row
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--model", required=True)
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    from codemapper.llm import make_gemini
    ix = Index.build(a.repo)
    G = build_call_graph(ix, Resolver(ix))
    qs = make_questions(ix, G, a.n, a.seed)
    print(f"{len(qs)} questions, model={a.model}, temperature=0")
    print(json.dumps(run(make_gemini(a.model), ix, G, qs), indent=2))


if __name__ == "__main__":
    main()
