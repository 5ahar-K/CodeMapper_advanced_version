"""STAGE 3 - RESOLVE. For each call site decide what it refers to, and say how sure we are.

confidence (strongest evidence first):
  high       resolved by scope rules / imports / class hierarchy; we would bet on it
  medium     inferred: the receiver's class came from `x = Foo(...)`, or a name is defined twice in one file
  low        NAME ONLY: the receiver is unknown and exactly one repo function/method has this name.
             Often right, but it is a guess: `msg.walk()` can be the stdlib method, not the repo's `walk`
  ambiguous  several repo functions could match and we cannot tell which; we list them but do not pick
  external   the call leaves the repo (builtin, stdlib, third-party) - no edge
  unresolved we could not tell (local callable, dynamic code) - no edge
"""
from __future__ import annotations

import builtins
from dataclasses import dataclass

PY2_BUILTINS = {"unicode", "long", "xrange", "basestring", "raw_input", "unichr", "reduce", "reload",
                "cmp", "file", "execfile", "intern", "apply", "coerce", "buffer"}

CONF_RANK = {"unresolved": 0, "external": 0, "ambiguous": 1, "low": 2, "medium": 3, "high": 4}

# Method names that also exist on builtin/stdlib types. `x.close()` with an unknown `x` is often a
# file or socket, not a repo class, so we refuse to call it "medium" even if only one repo class has `close`.
COMMON_METHODS = (
    set().union(*(dir(t) for t in (str, bytes, list, dict, set, tuple, int, float)))
    | {"read", "write", "close", "readline", "readlines", "flush", "seek", "tell", "open",
       "start", "join", "is_alive", "acquire", "release", "wait", "is_set",
       "send", "recv", "connect", "bind", "listen", "accept",
       "debug", "info", "warning", "error", "critical",
       "load", "dump", "loads", "dumps", "communicate", "poll", "kill", "terminate", "run",
       "match", "search", "sub", "findall", "finditer", "group", "groups",      # re / subprocess / threading
       "isAlive", "isDaemon", "setDaemon", "getName", "setName",                # Python 2 threading names
       "iteritems", "iterkeys", "itervalues", "has_key"}                        # Python 2 dict names
)


@dataclass(frozen=True)
class Resolution:
    targets: tuple
    confidence: str
    reason: str


def _scopes(qualname: str):
    """'A.b.c' -> ['A.b.c', 'A.b', 'A']  (innermost first)"""
    parts = qualname.split(".")
    return [".".join(parts[:i]) for i in range(len(parts), 0, -1)]


class Resolver:
    def __init__(self, index):
        self.ix = index
        self._cache: dict = {}

    def resolve(self, fn, call) -> Resolution:
        key = (fn.id, call.lineno, call.col, call.text)
        if key not in self._cache:
            self._cache[key] = (self._bare(fn, call) if call.receiver is None else self._attr(fn, call))
        return self._cache[key]

    # ------------------------------------------------------------------ foo(...)
    def _bare(self, fn, call) -> Resolution:
        ix, name = self.ix, call.name
        mod = ix.modules[fn.file]

        for scope in _scopes(fn.qualname):                       # 1. a function defined inside an enclosing function
            nested = [fid for fid in mod.by_qualname.get(f"{scope}.{name}", []) if not ix.functions[fid].is_method]
            if len(nested) == 1:
                return Resolution((nested[0],), "high", "nested function in enclosing scope")
            if nested:                                           # e.g. one definition per if/elif/else branch
                return Resolution(tuple(nested), "medium", f"nested function defined {len(nested)} times (branches)")

        if name in fn.locals:                                    # 2. a variable or parameter shadows everything else
            return Resolution((), "unresolved", "call of a local variable or parameter")

        ids = mod.functions.get(name)                            # 3. module-level function in the same file
        if ids:
            return Resolution(tuple(ids), "high" if len(ids) == 1 else "medium", "same-file function")

        if name in mod.classes:                                  # 4. class in the same file -> its constructor
            return self._ctor(mod.classes[name], "same-file class")

        ref = mod.imports.get(name)                              # 5. explicitly imported name
        if ref is not None and ref.symbol is not None:
            target = ix.resolve_module(fn.file, ref.module, ref.level)
            if target is None:
                return Resolution((), "external", "imported from outside the repo")
            sym = ix.lookup_symbol(target, ref.symbol)
            if sym:
                return self._from_symbol(sym, "explicit import")
            return Resolution((), "unresolved", "imported name not found in that module")

        for star in mod.star_imports:                            # 6. `from x import *`
            target = ix.resolve_module(fn.file, star.module, star.level)
            if target:
                sym = ix.lookup_symbol(target, name)
                if sym:
                    return self._from_symbol(sym, "star import")

        if hasattr(builtins, name) or name in PY2_BUILTINS:                              # 7. builtin (after the repo scopes: shadowing wins)
            return Resolution((), "external", "builtin")

        cands = list(ix.modlevel_by_name.get(name, []))          # 8. last resort: match by name across the repo
        for c in ix.classes_by_name.get(name, []):
            cands += ix.find_method(c, "__init__")
        if len(cands) == 1:
            return Resolution(tuple(cands), "low", "only definition with this name in the repo (not imported here)")
        if cands:
            return Resolution(tuple(cands), "ambiguous", f"{len(cands)} definitions share this name")
        return Resolution((), "unresolved", "no definition found")

    # ------------------------------------------------------------------ recv.foo(...)
    def _attr(self, fn, call) -> Resolution:
        ix, name, recv = self.ix, call.name, call.receiver
        mod = ix.modules[fn.file]
        if recv == "<literal>":
            return Resolution((), "external", "method on a literal (builtin type)")

        cls = ix.classes.get(fn.owner_class_id) if fn.owner_class_id else None
        first_param = fn.params[0] if fn.params else None
        if cls is not None and (recv in ("self", "cls") or (fn.is_method and recv == first_param)):
            ids = ix.find_method(cls, name)
            if ids:
                return Resolution((ids[0],), "high", "method on own class or an ancestor")
            return self._unknown(name, "not on own class chain (mixin? subclass override?)")
        if recv == "super()" and cls is not None:
            ids = ix.find_method(cls, name, skip_self=True)
            if ids:
                return Resolution((ids[0],), "high", "super() method")
            return Resolution((), "external", "super() method not found in repo ancestors")

        first = recv.split(".")[0]
        if recv == first and recv in fn.local_types and first in fn.locals:      # x = Foo(); x.bar()
            c = ix.resolve_class_expr(fn.file, fn.local_types[recv])
            if c is not None:
                ids = ix.find_method(c, name)
                if ids:
                    return Resolution((ids[0],), "medium", "type inferred from `x = Class(...)`")
        elif first not in fn.locals and recv != "<expr>":
            if recv in mod.classes:                                              # ClassName.method()
                ids = ix.find_method(mod.classes[recv], name)
                if ids:
                    return Resolution((ids[0],), "high", "method on a class in this file")
            ref = mod.imports.get(first)
            if ref is not None:
                if ref.symbol is not None and recv == first:                     # imported class: Foo.method()
                    c = ix.resolve_class_expr(fn.file, recv)
                    if c is not None:
                        ids = ix.find_method(c, name)
                        if ids:
                            return Resolution((ids[0],), "high", "method on an imported class")
                kind, mf = ix.module_of_receiver(fn.file, recv)
                if kind == "external":
                    return Resolution((), "external", "call into a module outside the repo")
                if kind == "repo":
                    sym = ix.lookup_symbol(mf, name)
                    if sym:
                        return self._from_symbol(sym, "module attribute")
                    return Resolution((), "unresolved", "attribute not found in that module")
        return self._unknown(name, "receiver type unknown")

    # ------------------------------------------------------------------ helpers
    def _unknown(self, name: str, why: str) -> Resolution:
        cands = self.ix.methods_by_name.get(name, [])
        if not cands:
            return Resolution((), "external", "no repo method has this name")
        if name in COMMON_METHODS:
            return Resolution(tuple(cands), "ambiguous", f"{why}; name also exists on builtin/stdlib types")
        if len(cands) == 1:
            return Resolution(tuple(cands), "low", f"{why}; only one method with this name in the repo")
        return Resolution(tuple(cands), "ambiguous", f"{why}; {len(cands)} methods share this name")

    def _ctor(self, cls, why: str) -> Resolution:
        ids = self.ix.find_method(cls, "__init__")
        if ids:
            return Resolution((ids[0],), "high", f"constructor ({why})")
        return Resolution((), "external", "class has no __init__ in the repo")

    def _from_symbol(self, sym, why: str) -> Resolution:
        if sym.funcs:
            return Resolution(tuple(sym.funcs), "high" if len(sym.funcs) == 1 else "medium", why)
        return self._ctor(sym.cls, why)
