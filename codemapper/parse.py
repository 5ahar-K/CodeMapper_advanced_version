"""STAGE 1 - PARSE. Turn one Python file into plain data (functions, classes, imports, call sites).

Nothing in this file looks at other files or tries to work out WHAT a call refers to.
That is the resolver's job. Keeping the two apart is what makes each testable.
"""
from __future__ import annotations

import ast
import re
import warnings
from collections import defaultdict
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class CallSite:
    name: str                       # the function/method name being called: "join", "save", "helper"
    receiver: str | None            # None for bare calls foo(); else "self", "os.path", "super()", "<expr>", "<literal>"
    lineno: int
    col: int
    text: str                       # the callee as written, e.g. "self.save"
    assigned_to: str | None = None  # `x = call(...)` -> "x" (only if x is assigned exactly once)
    arg_names: tuple = ()           # plain variable names passed as arguments


@dataclass
class FunctionInfo:
    id: str                         # "path/to/file.py::Class.method"  (+ "@L<line>" if the same name is defined twice)
    name: str
    qualname: str                   # "Class.method" or "outer.inner"
    file: str
    start_lineno: int               # includes decorators
    lineno: int
    end_lineno: int
    is_method: bool                 # defined directly in a class body
    owner_class_id: str | None      # nearest enclosing class (also set for functions nested inside methods)
    is_test: bool
    params: tuple
    locals: frozenset               # parameter names + every name assigned in the body
    calls: list = field(default_factory=list)
    local_types: dict = field(default_factory=dict)   # var -> "Foo" for `var = Foo(...)`, single assignment only


@dataclass
class ClassInfo:
    id: str
    name: str
    qualname: str
    file: str
    bases: list
    methods: dict = field(default_factory=dict)       # method name -> FunctionInfo.id


@dataclass(frozen=True)
class ImportRef:
    module: str                     # "os.path", "mailpile.util", "" for `from . import x`
    symbol: str | None              # None for `import x`; the imported name for `from m import name`
    level: int = 0                  # number of leading dots in a relative import


@dataclass
class ModuleInfo:
    file: str
    imports: dict = field(default_factory=dict)        # local name -> ImportRef
    star_imports: list = field(default_factory=list)   # every `from m import *`
    functions: dict = field(default_factory=dict)      # module-level function name -> [ids]
    classes: dict = field(default_factory=dict)        # module-level class name -> ClassInfo
    by_qualname: dict = field(default_factory=dict)    # qualname -> [ids]
    repairs: list = field(default_factory=list)        # [(lineno, what we changed)] if the file needed repairs to parse


def is_test_path(relpath: str) -> bool:
    parts = relpath.lower().split("/")
    name = parts[-1]
    return ("tests" in parts[:-1] or "test" in parts[:-1]
            or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py")


def _dotted(node) -> str | None:
    """`a.b.c` -> "a.b.c" if it is a plain chain of names, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


_LITERALS = (ast.Constant, ast.JoinedStr, ast.List, ast.Dict, ast.Set, ast.Tuple,
             ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)


def _receiver(node) -> str:
    d = _dotted(node)
    if d:
        return d
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super":
        return "super()"
    if isinstance(node, _LITERALS):
        return "<literal>"
    return "<expr>"


def _param_names(args: ast.arguments) -> tuple:
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return tuple(names)


def _store_names(target) -> list:
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]


def _collect_body(fn):
    """Calls made directly by `fn` (NOT by functions/classes nested inside it), plus local-variable facts."""
    assign_count = defaultdict(int)
    for p in _param_names(fn.args):
        assign_count[p] += 1
    call_assign: dict = {}      # id(call node) -> variable it is assigned to
    call_type: dict = {}        # variable -> dotted callee text
    calls = []

    stack = list(reversed(fn.body))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                                    # nested defs are collected as their own functions
        if isinstance(node, ast.Assign):
            for t in node.targets:
                for n in _store_names(t):
                    assign_count[n] += 1
            if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Call)):
                call_assign[id(node.value)] = node.targets[0].id
                d = _dotted(node.value.func)
                if d:
                    call_type[node.targets[0].id] = d
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            for n in _store_names(node.target):
                assign_count[n] += 1
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for n in _store_names(node.target):
                assign_count[n] += 1
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            for n in _store_names(node.optional_vars):
                assign_count[n] += 1
        elif isinstance(node, ast.ExceptHandler) and node.name:
            assign_count[node.name] += 1
        elif isinstance(node, ast.NamedExpr):
            for n in _store_names(node.target):
                assign_count[n] += 1

        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                name, recv = f.id, None
            elif isinstance(f, ast.Attribute):
                name, recv = f.attr, _receiver(f.value)
            else:
                name, recv = None, None
            if name:
                args = tuple(a.id for a in node.args if isinstance(a, ast.Name)) + \
                       tuple(k.value.id for k in node.keywords if isinstance(k.value, ast.Name))
                try:
                    text = ast.unparse(f)
                except Exception:
                    text = name
                calls.append(CallSite(name, recv, node.lineno, node.col_offset, text,
                                      call_assign.get(id(node)), args))
        stack.extend(reversed(list(ast.iter_child_nodes(node))))

    calls = [replace(c, assigned_to=None) if c.assigned_to and assign_count[c.assigned_to] != 1 else c
             for c in calls]
    calls.sort(key=lambda c: (c.lineno, c.col))
    local_types = {v: t for v, t in call_type.items() if assign_count[v] == 1}
    locals_ = frozenset(n for n, k in assign_count.items() if k > 0)
    return calls, local_types, locals_


class _Collector:
    def __init__(self, relpath: str):
        self.file = relpath
        self.is_test_file = is_test_path(relpath)
        self.mod = ModuleInfo(file=relpath)
        self.functions: list = []
        self.classes: list = []
        self._ids: set = set()

    def _unique_id(self, qualname: str, lineno: int) -> str:
        base = f"{self.file}::{qualname}"
        if base in self._ids:                # same name defined twice (property setter, try/except fallback...)
            base = f"{base}@L{lineno}"
        self._ids.add(base)
        return base

    # `qual` = dotted name of the enclosing scope ("" at module level)
    # `cls`  = nearest enclosing ClassInfo (also while inside a method, for nested functions)
    def walk(self, node, qual: str, cls, in_class_body: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self._class(child, qual)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(child, qual, cls, in_class_body)
            else:
                if isinstance(child, ast.Import):
                    for a in child.names:
                        if a.asname:
                            self.mod.imports[a.asname] = ImportRef(a.name, None)
                        else:                # `import a.b.c` binds the name `a`
                            top = a.name.split(".")[0]
                            self.mod.imports.setdefault(top, ImportRef(top, None))
                elif isinstance(child, ast.ImportFrom):
                    for a in child.names:
                        ref = ImportRef(child.module or "", None if a.name == "*" else a.name, child.level)
                        if a.name == "*":
                            self.mod.star_imports.append(ref)
                        else:
                            self.mod.imports[a.asname or a.name] = ref
                self.walk(child, qual, cls, in_class_body)

    def _class(self, node: ast.ClassDef, qual: str):
        qn = f"{qual}.{node.name}" if qual else node.name
        info = ClassInfo(id=self._unique_id(qn, node.lineno), name=node.name, qualname=qn, file=self.file,
                         bases=[d for d in (_dotted(b) for b in node.bases) if d])
        self.classes.append(info)
        if not qual:
            self.mod.classes.setdefault(node.name, info)
        self.walk(node, qn, info, True)

    def _function(self, node, qual: str, cls, in_class_body: bool):
        if any((_dotted(d) or "").split(".")[-1] == "overload" for d in node.decorator_list):
            return                           # typing.overload stubs are signatures, not real functions
        qn = f"{qual}.{node.name}" if qual else node.name
        fid = self._unique_id(qn, node.lineno)
        calls, local_types, locals_ = _collect_body(node)
        info = FunctionInfo(
            id=fid, name=node.name, qualname=qn, file=self.file,
            start_lineno=min([node.lineno] + [d.lineno for d in node.decorator_list]),
            lineno=node.lineno, end_lineno=node.end_lineno or node.lineno,
            is_method=bool(in_class_body and cls is not None),
            owner_class_id=cls.id if cls is not None else None,
            is_test=self.is_test_file or node.name.startswith("test_"),
            params=_param_names(node.args), locals=locals_, calls=calls, local_types=local_types)
        self.functions.append(info)
        if info.is_method:
            cls.methods.setdefault(node.name, fid)
        if not qual:
            self.mod.functions.setdefault(node.name, []).append(fid)
        self.mod.by_qualname.setdefault(qn, []).append(fid)
        self.walk(node, qn, cls, False)      # find functions nested inside this one


_EXCEPT_COMMA = re.compile(r"^(\s*except\s+[\w.()\s,]+?),\s*(\w+)\s*:")
_ASYNC_IDENT = re.compile(r"\basync\b(?!\s+(?:def|for|with)\b)")


def _parse(source: str, relpath: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # silences the wall of "invalid escape sequence" warnings
        return ast.parse(source, filename=relpath)


def _repair_and_parse(source: str, relpath: str, max_repairs: int = 60):
    """For old code (Python 2 style) that does not parse today. Fixes the two mechanical cases
    (`except X, e:` and `async` used as a variable name), and for anything else replaces the broken
    line with `pass`. Every change is returned, so a repaired file is never mistaken for a clean one."""
    repairs = []
    try:
        _parse(source, relpath)
    except TabError:
        source = source.expandtabs(8)                       # Python 2 read a tab as 8 spaces
        repairs.append((0, "expanded tabs to 8 spaces"))
    except SyntaxError:
        pass
    lines = source.splitlines(keepends=True)
    for _ in range(max_repairs):
        try:
            return _parse("".join(lines), relpath), repairs
        except SyntaxError as e:
            n = (e.lineno or 0) - 1
            if not 0 <= n < len(lines):
                raise
            line = lines[n]
            fixed = _EXCEPT_COMMA.sub(r"\1 as \2:", line)
            what = "except X, e -> except X as e"
            if fixed == line:
                fixed = _ASYNC_IDENT.sub("async_", line)
                what = "renamed identifier `async`"
            if fixed == line:
                indent = line[:len(line) - len(line.lstrip())]
                header = line.rstrip().endswith(":")
                fixed = indent + ("if True:  # codemapper: unparseable header replaced\n" if header
                                  else "pass  # codemapper: unparseable line dropped\n")
                what = "dropped unparseable line: " + line.strip()[:60]
            lines[n] = fixed
            repairs.append((n + 1, what))
    raise SyntaxError(f"gave up after {max_repairs} repairs")


def parse_module(relpath: str, source: str, tolerant: bool = False):
    """Returns (ModuleInfo, [FunctionInfo], [ClassInfo]). Raises SyntaxError etc. on unparseable files
    unless tolerant=True, in which case old-style files are repaired (see mod.repairs)."""
    repairs = []
    try:
        tree = _parse(source, relpath)
    except SyntaxError:
        if not tolerant:
            raise
        tree, repairs = _repair_and_parse(source, relpath)
    c = _Collector(relpath)
    c.walk(tree, "", None, False)
    c.mod.repairs = repairs
    return c.mod, c.functions, c.classes
