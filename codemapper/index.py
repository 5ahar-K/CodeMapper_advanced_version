"""STAGE 2 - INDEX. Parse every file once and answer cross-file questions:
"which file does `from .util import x` point at?", "what is the parent class of Foo?", ...
"""
from __future__ import annotations

import os
import sys
import textwrap
import tokenize
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .parse import ClassInfo, parse_module

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".mypy_cache",
             ".pytest_cache", "build", "dist", "site-packages"}
STDLIB = getattr(sys, "stdlib_module_names", frozenset())


@dataclass
class Symbol:
    """What a top-level name in a module refers to: some functions, or a class."""
    funcs: list | None = None
    cls: ClassInfo | None = None


def _dotted_name(relpath: str) -> str:
    parts = relpath[:-3].split("/")          # strip ".py"
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class Index:
    def __init__(self, root):
        self.root = Path(root)
        self.modules: dict = {}          # relpath -> ModuleInfo
        self.functions: dict = {}        # id -> FunctionInfo
        self.classes: dict = {}          # id -> ClassInfo
        self.parse_errors: list = []     # [(relpath, message)]  <- reported, never silently dropped
        self._dotted_to_file: dict = {}
        self._suffix: dict = defaultdict(list)
        self.functions_by_name: dict = defaultdict(list)   # every function/method with this name
        self.methods_by_name: dict = defaultdict(list)     # only methods
        self.modlevel_by_name: dict = defaultdict(list)    # only module-level functions
        self.classes_by_name: dict = defaultdict(list)
        self._mro_cache: dict = {}

    # ---------------------------------------------------------------- building
    @classmethod
    def build(cls, root, skip_dirs=SKIP_DIRS, tolerant: bool = True) -> "Index":
        ix = cls(root)
        for dirpath, dirnames, filenames in os.walk(ix.root):
            dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                path = Path(dirpath) / fn
                rel = path.relative_to(ix.root).as_posix()
                try:
                    with tokenize.open(path) as f:          # honours "# -*- coding: ... -*-" lines
                        source = f.read()
                    mod, funcs, classes = parse_module(rel, source, tolerant)
                except (SyntaxError, UnicodeDecodeError, ValueError, RecursionError, OSError) as e:
                    ix.parse_errors.append((rel, f"{type(e).__name__}: {e}"))
                    continue
                ix.modules[rel] = mod
                for f_ in funcs:
                    ix.functions[f_.id] = f_
                for c in classes:
                    ix.classes[c.id] = c
        ix._finish()
        return ix

    def _finish(self):
        for rel in self.modules:
            d = _dotted_name(rel)
            if not d:
                continue
            self._dotted_to_file.setdefault(d, rel)
            parts = d.split(".")
            for i in range(len(parts)):                      # "a.b.c" is also findable as "b.c" and "c"
                self._suffix[".".join(parts[i:])].append(rel)
        for f in self.functions.values():
            self.functions_by_name[f.name].append(f.id)
            if f.is_method:
                self.methods_by_name[f.name].append(f.id)
        for m in self.modules.values():
            for name, ids in m.functions.items():
                self.modlevel_by_name[name].extend(ids)
        for c in self.classes.values():
            self.classes_by_name[c.name].append(c)

    # ---------------------------------------------------------------- modules and imports
    def resolve_module(self, file: str, module: str, level: int = 0):
        """Which repo file does `module` (as written in `file`) refer to? None if it is outside the repo."""
        if level:
            base = list(Path(file).parent.parts)
            if level - 1 > len(base):
                return None
            base = base[:len(base) - (level - 1)]
            dotted = ".".join(base + ([module] if module else []))
            return self._dotted_to_file.get(dotted)
        if not module:
            return None
        hit = self._dotted_to_file.get(module)
        if hit:
            return hit
        if module.split(".")[0] in STDLIB:
            return None
        cands = self._suffix.get(module, [])
        return cands[0] if len(cands) == 1 else None         # only accept an unambiguous suffix match

    def lookup_symbol(self, module_file: str, name: str, depth: int = 0):
        """What top-level thing called `name` lives in (or is re-exported by) `module_file`?"""
        mod = self.modules.get(module_file)
        if mod is None:
            return None
        if name in mod.functions:
            return Symbol(funcs=list(mod.functions[name]))
        if name in mod.classes:
            return Symbol(cls=mod.classes[name])
        if depth >= 4:
            return None
        ref = mod.imports.get(name)                          # re-export: `from .core import name`
        if ref and ref.symbol:
            target = self.resolve_module(module_file, ref.module, ref.level)
            if target:
                return self.lookup_symbol(target, ref.symbol, depth + 1)
        for star in mod.star_imports:                        # re-export via `from .core import *`
            target = self.resolve_module(module_file, star.module, star.level)
            if target and target != module_file:
                s = self.lookup_symbol(target, name, depth + 1)
                if s:
                    return s
        return None

    def module_of_receiver(self, file: str, recv: str):
        """For `os.path`, `util`, `pkg.sub` as written in `file`: ("repo", relpath) | ("external", None) | (None, None)."""
        first, *rest = recv.split(".")
        ref = self.modules[file].imports.get(first)
        if ref is None:
            return (None, None)
        if ref.symbol is None:                               # import a.b as x  /  import a
            dotted = ".".join(p for p in [ref.module] + rest if p)
            mf = self.resolve_module(file, dotted, ref.level)
            return ("repo", mf) if mf else ("external", None)
        sub = ".".join(p for p in [ref.module, ref.symbol] + rest if p)       # from pkg import submodule
        mf = self.resolve_module(file, sub, ref.level)
        if mf:
            return ("repo", mf)
        if ref.level == 0 and self.resolve_module(file, ref.module, 0) is None:
            return ("external", None)                        # e.g. `from os import path`
        return (None, None)                                  # probably a class or function, not a module

    # ---------------------------------------------------------------- classes
    def resolve_class_expr(self, file: str, dotted: str):
        """`Foo` or `mod.Foo`, as written in `file`, -> the ClassInfo it names (if defined in this repo)."""
        mod = self.modules.get(file)
        if mod is None:
            return None
        parts = dotted.split(".")
        if len(parts) == 1:
            name = parts[0]
            if name in mod.classes:
                return mod.classes[name]
            ref = mod.imports.get(name)
            if ref and ref.symbol:
                target = self.resolve_module(file, ref.module, ref.level)
                if target:
                    s = self.lookup_symbol(target, ref.symbol)
                    if s and s.cls:
                        return s.cls
            for star in mod.star_imports:
                target = self.resolve_module(file, star.module, star.level)
                if target:
                    s = self.lookup_symbol(target, name)
                    if s and s.cls:
                        return s.cls
            cands = self.classes_by_name.get(name, [])
            return cands[0] if len(cands) == 1 else None
        kind, mf = self.module_of_receiver(file, ".".join(parts[:-1]))
        if kind == "repo":
            s = self.lookup_symbol(mf, parts[-1])
            return s.cls if s else None
        return None

    def mro(self, cls: ClassInfo) -> list:
        """The class and its ancestors (breadth-first; good enough, not exact C3)."""
        if cls.id in self._mro_cache:
            return self._mro_cache[cls.id]
        order, seen, queue = [], set(), [cls]
        while queue:
            c = queue.pop(0)
            if c.id in seen:
                continue
            seen.add(c.id)
            order.append(c)
            for b in c.bases:
                bc = self.resolve_class_expr(c.file, b)
                if bc:
                    queue.append(bc)
        self._mro_cache[cls.id] = order
        return order

    def find_method(self, cls: ClassInfo, name: str, skip_self: bool = False) -> list:
        for c in self.mro(cls)[1 if skip_self else 0:]:
            if name in c.methods:
                return [c.methods[name]]
        return []

    # ---------------------------------------------------------------- helpers for output
    def source_of(self, fid: str) -> str:
        f = self.functions[fid]
        lines = (self.root / f.file).read_text(encoding="utf-8", errors="replace").splitlines()
        return textwrap.dedent("\n".join(lines[f.start_lineno - 1:f.end_lineno]))

    def label(self, fid: str) -> str:
        f = self.functions[fid]
        return f"{f.qualname} ({f.file}:{f.lineno})"

    def find(self, query: str) -> list:
        """'Class.method', 'method', or a full id -> matching function ids."""
        if query in self.functions:
            return [query]
        exact = [f.id for f in self.functions.values() if f.qualname == query]
        return exact or list(self.functions_by_name.get(query, []))
