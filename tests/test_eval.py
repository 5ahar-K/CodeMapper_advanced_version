"""The measuring tools need tests too: a wrong metric is worse than no metric."""
import csv
import json
import re
from pathlib import Path

from codemapper import Index, Resolver, build_call_graph
from codemapper.llm import CANNOT
from evaluation import llm_eval
from evaluation.common import make_predictors
from evaluation.sample_calls import main as sample_main
from evaluation.score import prf, read_labels, score_rows

TOY = Path(__file__).parent / "fixtures" / "toy"


def find_row(ix, fn_id, text):
    fn = ix.functions[fn_id]
    c = next(c for c in fn.calls if c.text == text)
    return fn_id, c.lineno, c.col


def test_prf_arithmetic():
    items = [({"a", "b"}, {"a"}), ({"c"}, {"c", "d"}), (set(), {"e"})]
    p, r, ab = prf(items)
    assert p == 2 / 3          # 2 correct of 3 predicted
    assert r == 2 / 4          # 2 found of 4 true
    assert ab == 1 / 3


def test_new_resolver_beats_baseline_on_known_cases():
    ix = Index.build(TOY)
    truth = [
        (*find_row(ix, "other.py::A.go", "self.save"), {"other.py::A.save"}),
        (*find_row(ix, "app.py::Worker.run", "x.save"), {"util.py::Base.save"}),
        (*find_row(ix, "app.py::Worker.run", "helper"), {"util.py::helper"}),
        (*find_row(ix, "app.py::Worker.run", "self.load"), {"util.py::Base.load"}),
    ]
    predictors, _ = make_predictors(ix)
    results = score_rows(ix, truth, predictors)
    pr = {m: prf([(p, t) for p, t, _, _ in items]) for m, items in results.items()}
    base_p, base_r, _ = pr["baseline (your original heuristic)"]
    new_p, new_r, _ = pr["new: high + medium"]
    assert new_p == 1.0 and new_r == 1.0
    assert base_p < new_p                # baseline over-links: it adds B.save, Worker.save ...


def test_sample_then_label_then_read_roundtrip(tmp_path):
    out = tmp_path / "labels.csv"
    sample_main([str(TOY), "--n", "10000", "--out", str(out)])
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    assert rows and rows[0]["true_labels"] == ""
    assert "predict" not in " ".join(rows[0].keys())          # blind: no predictions in the file
    # label exactly one row by hand: A.go's self.save -> the candidate that is A.save
    for r in rows:
        if r["caller"] == "other.py::A.go" and r["call_text"] == "self.save":
            cands = json.loads(r["_candidate_ids"])
            r["true_labels"] = str(cands.index("other.py::A.save") + 1)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    labelled, unsure, blank = read_labels(out)
    assert len(labelled) == 1 and labelled[0][3] == {"other.py::A.save"}
    assert blank == len(rows) - 1 and unsure == 0


# ---------------------------------------------------------------- llm eval with fake models
def _setup():
    ix = Index.build(TOY)
    return ix, build_call_graph(ix, Resolver(ix))


def test_llm_eval_always_abstain_model():
    ix, G = _setup()
    qs = llm_eval.make_questions(ix, G, n=10)
    assert {q.kind for q in qs} == {"callers", "calls", "unanswerable"}
    s = llm_eval.run(lambda prompt: json.dumps({"callers": CANNOT, "answer": CANNOT}), ix, G, qs)
    assert s["unanswerable / with_graph"]["accuracy"] == 1.0          # abstaining is right for unanswerable questions
    assert s["callers / with_graph"]["mean_f1"] == 0.0                # ... and wrong when the graph had the answer
    assert s["calls / code_only"]["abstain_rate"] == 1.0


def test_llm_eval_faithful_model_gets_full_marks_from_the_graph_context():
    ix, G = _setup()
    qs = llm_eval.make_questions(ix, G, n=10)

    def oracle(prompt):
        if "List every function" in prompt:
            block = prompt.split("Called by")[1].split("Calls (")[0]
            names = [re.sub(r"\s*\(.*$", "", l[2:]) for l in block.splitlines() if l.startswith("- ") and "(none" not in l]
            return json.dumps({"callers": names})
        if "Does `" in prompt:
            b, f = re.search(r"call `(.+?)` defined in (\S+?)\?", prompt).groups()
            callees = prompt.split("Calls (")[1] if "Calls (" in prompt else ""
            return json.dumps({"answer": "yes" if f"- {b} ({f}:" in callees else "no"})
        return json.dumps({"answer": CANNOT})

    s = llm_eval.run(oracle, ix, G, qs, conditions=("with_graph",))
    assert s["callers / with_graph"]["mean_f1"] == 1.0
    assert s["callers / with_graph"]["hallucinated_items_per_answer"] == 0.0
    assert s["calls / with_graph"]["accuracy"] == 1.0
    assert s["unanswerable / with_graph"]["accuracy"] == 1.0
