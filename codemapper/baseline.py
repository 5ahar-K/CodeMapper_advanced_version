"""YOUR ORIGINAL HEURISTIC, frozen. It exists only so you can score 'before' and 'after' on the same labels.

The rules are the ones from your notebook's build_graph pass 2:
  1. match on the bare name only (the receiver is ignored)
  2. candidates in the same file win
  3. else, candidates whose module path is a substring of the file path (from-imports only, relative dots ignored)
  4. else, if there is exactly one candidate, use it
  5. else link to ALL candidates
Like the original it only indexes top-level functions and methods of top-level classes.
"""
from __future__ import annotations

import os
from collections import defaultdict


class BaselineResolver:
    def __init__(self, index):
        self.ix = index
        self.by_name = defaultdict(list)
        for f in index.functions.values():
            top_level_function = "." not in f.qualname
            method_of_top_level_class = f.is_method and "." not in index.classes[f.owner_class_id].qualname
            if top_level_function or method_of_top_level_class:
                self.by_name[f.name].append(f)
        self.old_imports = {}
        for rel, mod in index.modules.items():
            self.old_imports[rel] = {local: ref.module for local, ref in mod.imports.items() if ref.symbol is not None}

    @staticmethod
    def _module_matches_file(module: str, filepath: str) -> bool:
        return bool(module) and module.replace(".", os.sep) in filepath

    def resolve(self, fn, call) -> tuple:
        cands = self.by_name.get(call.name)
        if not cands:
            return ()
        same_file = [c for c in cands if c.file == fn.file]
        resolved = None
        if same_file:
            resolved = same_file
        elif call.name in self.old_imports.get(fn.file, {}):
            module = self.old_imports[fn.file][call.name]
            matched = [c for c in cands if self._module_matches_file(module, c.file)]
            if matched:
                resolved = matched
        if resolved is None and len(cands) == 1:
            resolved = cands
        if resolved is None:
            resolved = cands
        return tuple(c.id for c in resolved)
