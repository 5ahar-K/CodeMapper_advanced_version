# Code-Mapper

A static call graph for Python repositories in which **every edge carries an honest confidence label**, plus a small harness that
*measures* how right the graph is and how an LLM behaves when it is given the graph as context.

```
python -m codemapper stats   REPO                   # coverage: files, call sites, confidence breakdown, parse problems
python -m codemapper top     REPO -n 10             # highest "blast radius" functions (production code only)
python -m codemapper callers REPO Class.method      # who calls this?
python -m codemapper ask     REPO Class.method "what breaks if this raises?" --model <pinned-model>   # needs GEMINI_API_KEY
pytest                                              # 28 tests
```

## How it works (five stages, one file each)

| stage | file | job |
|---|---|---|
| 1 parse | `codemapper/parse.py` | one file -> functions, classes, imports, call sites (keeps the *receiver*: `self.save()` is not `save()`) |
| 2 index | `codemapper/index.py` | whole repo: module/import resolution, re-exports, star imports, class hierarchy, parse errors |
| 3 resolve | `codemapper/resolve.py` | each call -> target(s) + **confidence** + human-readable reason |
| 4 graph | `codemapper/graph.py` | networkx graphs, blast radius, data-flow graph (kept separate: different edge meaning) |
| 5 llm | `codemapper/llm.py` | prompt building + model client; the only file that talks to a model |

Confidence tiers: **high** (scope/import/class-hierarchy proof) > **medium** (type inferred from `x = Foo()`) >
**low** (name-only guess: unknown receiver, exactly one repo method with that name) > **ambiguous** (several candidates; listed, never picked)
> external / unresolved (no edge). `trusted(G, min_conf)` returns the graph at a chosen threshold.
`baseline.py` is the original name-matching heuristic, frozen so before/after comparisons are fair.

## Results  <- fill these in from YOUR labels; do not publish numbers you did not measure

Repo: `______` at commit `______`. Labelled call sites: `___` (random sample, seed `__`, blind labelling; `___` marked "?").

| method | precision (95% CI) | recall (95% CI) | abstains | n |
|---|---|---|---|---|
| baseline (original heuristic) | | | | |
| new: high only | | | | |
| new: high + medium | | | | |
| new: high + medium + low | | | | |
| new: everything incl. ambiguous | | | | |

Error analysis (3-5 bullets, in your own words: what kinds of call does the resolver still get wrong, and why?):

LLM behaviour (`evaluation/llm_eval.py`, model `______`, temperature 0, n=`__`):

| question type / condition | accuracy or F1 | abstain rate | invented items per answer |
|---|---|---|---|
| callers / code_only | | | |
| callers / with_graph | | | |
| calls (yes/no) / code_only | | | |
| calls (yes/no) / with_graph | | | |
| unanswerable / with_graph | | | |

## Reproducing the labelling

```
git -C target_repo checkout <commit>                                   # pin the repo: the sample depends on it
python -m evaluation.sample_calls target_repo --n 100 --seed 0 --out evaluation/labels/labels.csv
# label every row BY HAND, reading the code, without running the tool (instructions: docstring of sample_calls.py)
python -m evaluation.score target_repo evaluation/labels/labels.csv --show-errors "new: high + medium + low"
```

## Known limits (state these before an interviewer finds them)

* **Static analysis cannot see dynamic dispatch**: plugin registries, `getattr`, decorators that register callbacks.
  Example: Mailpile's `Command.command()` methods are invoked by a framework, so they show ~0 callers.
* **Receiver types are only inferred locally** (`x = Foo()`), not through attributes (`self.ui = UI()`) or return values.
  This is the main source of `ambiguous`/`low` edges; calls like `x.get()`/`.append()` on unknown `x` stay ambiguous.
* **Class hierarchy uses breadth-first order, not exact C3 MRO**; overrides in subclasses are not considered.
* **Old Python is repaired, not fully parsed**: `except X, e:` and `async` as a name are fixed; other unparseable
  lines are replaced by `pass`. Every repair is listed in `stats` (`files_repaired`).
* Precision/recall are measured on a **sample of call sites whose name matches some repo function**; calls the parser cannot even
  see (dynamic) are outside that universe.
* Python only, one repo at a time, no incremental updates.

## Development-time observations (structure, not accuracy)

Measured while building, on Mailpile `741e610` and click `6aabf09`:
Mailpile has 8 files that do not parse as modern Python (incl. `util.py`, star-imported by 49 modules, and `commands.py`,
which defines the `Command` base class of ~85 classes). With repair on, all 8 parse (9 lines dropped in total);
high-confidence call sites rose from 4,071 to 5,065 and unresolved fell from 493 to 301.
