"""STAGE 5 - LLM. Everything that talks to a model lives here and nowhere else.

An "LLM" in this package is just a function: prompt(str) -> answer(str). That makes it trivial to swap
in a fake during tests and lets eval/llm_eval.py run the same questions under different conditions.
"""
from __future__ import annotations

import os

from .graph import trusted
from .resolve import CONF_RANK

CANNOT = "CANNOT DETERMINE"


def make_gemini(model: str, api_key: str | None = None, temperature: float = 0.0):
    """Returns llm(prompt) -> text. PIN `model` to an explicit name (not '...-latest') so results are reproducible.
    Uses the current `google-genai` package (the old `google.generativeai` package is deprecated)."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
    cfg = types.GenerateContentConfig(temperature=temperature)

    def llm(prompt: str) -> str:
        return client.models.generate_content(model=model, contents=prompt, config=cfg).text or ""
    return llm


def build_context(index, G, fid: str, max_neighbours: int = 15, min_conf: str = "low") -> str:
    """Source code + callers + callees, ranked by confidence and capped, so the prompt stays small and honest."""
    view = trusted(G, min_conf)

    def fmt(ids, edge):
        ids = sorted(ids, key=lambda x: (-CONF_RANK[edge(x)["confidence"]], index.functions[x].qualname))
        shown = [f"- {index.label(i)} [{edge(i)['confidence']}]" for i in ids[:max_neighbours]]
        if len(ids) > max_neighbours:
            shown.append(f"- ... and {len(ids) - max_neighbours} more")
        return "\n".join(shown) or "- (none found)"

    callers = list(view.predecessors(fid))
    callees = list(view.successors(fid))
    hidden = sum(1 for _, _, d in G.in_edges(fid, data=True) if CONF_RANK[d["confidence"]] < CONF_RANK[min_conf]) \
        + sum(1 for _, _, d in G.out_edges(fid, data=True) if CONF_RANK[d["confidence"]] < CONF_RANK[min_conf])
    return (
        f"Function `{index.functions[fid].qualname}` in {index.functions[fid].file}:\n\n"
        f"```python\n{index.source_of(fid)}\n```\n\n"
        f"Called by ({len(callers)}):\n{fmt(callers, lambda c: G[c][fid])}\n\n"
        f"Calls ({len(callees)}):\n{fmt(callees, lambda c: G[fid][c])}\n\n"
        f"({hidden} further call links were left out because the tool was not confident about them.)"
    )


def ask(llm, index, G, fid: str, question: str) -> str:
    prompt = (
        f"{build_context(index, G, fid)}\n\nQuestion: {question}\n\n"
        f"Answer using ONLY the information above. If the information above is not enough, reply exactly: {CANNOT}."
    )
    return llm(prompt)


def generate_test(llm, index, fid: str) -> str:
    return llm(f"Write a pytest unit test for this function. Output only code.\n\n```python\n{index.source_of(fid)}\n```")
