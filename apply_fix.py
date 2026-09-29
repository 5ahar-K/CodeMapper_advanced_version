import io

def sub(path, old, new):
    with io.open(path, encoding="utf-8") as f:
        s = f.read()
    assert old in s, "text not found in " + path + ": " + old[:50]
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(s.replace(old, new, 1))

sub("codemapper/parse.py",
    "def _receiver(node) -> str:",
    "def _base_name(node) -> str | None:\n"
    "    # Name of a base class. ParamType[int] (a generic base) counts as ParamType.\n"
    "    if isinstance(node, ast.Subscript):\n"
    "        node = node.value\n"
    "    return _dotted(node)\n"
    "\n"
    "\n"
    "def _receiver(node) -> str:")

sub("codemapper/parse.py",
    "bases=[d for d in (_dotted(b) for b in node.bases) if d])",
    "bases=[d for d in (_base_name(b) for b in node.bases) if d])")

sub("codemapper/resolve.py",
    "        for scope in _scopes(fn.qualname):                       # 1. a function defined inside an enclosing function\n"
    "            for fid in mod.by_qualname.get(f\"{scope}.{name}\", []):\n"
    "                if not ix.functions[fid].is_method:\n"
    "                    return Resolution((fid,), \"high\", \"nested function in enclosing scope\")",
    "        for scope in _scopes(fn.qualname):                       # 1. a function defined inside an enclosing function\n"
    "            nested = [fid for fid in mod.by_qualname.get(f\"{scope}.{name}\", []) if not ix.functions[fid].is_method]\n"
    "            if len(nested) == 1:\n"
    "                return Resolution((nested[0],), \"high\", \"nested function in enclosing scope\")\n"
    "            if nested:                                           # e.g. one definition per if/elif/else branch\n"
    "                return Resolution(tuple(nested), \"medium\", f\"nested function defined {len(nested)} times (branches)\")")

test_code = (
    "\n\n"
    "# ---------------------------------------------------------------- fixes found by error analysis\n"
    "def _one_file_repo(tmp_path, source):\n"
    "    (tmp_path / \"m.py\").write_text(source)\n"
    "    ix = Index.build(tmp_path)\n"
    "    return ix, Resolver(ix)\n"
    "\n\n"
    "def test_generic_base_classes_are_followed(tmp_path):\n"
    "    ix, res = _one_file_repo(tmp_path, (\n"
    "        \"class Base:\\n    def hello(self):\\n        pass\\n\\n\\n\"\n"
    "        \"class Child(Base[int]):\\n    def go(self):\\n        self.hello()\\n        super().hello()\\n\"))\n"
    "    fn = ix.functions[\"m.py::Child.go\"]\n"
    "    for text in (\"self.hello\", \"super().hello\"):\n"
    "        r = res.resolve(fn, next(c for c in fn.calls if c.text == text))\n"
    "        assert r.targets == (\"m.py::Base.hello\",) and r.confidence == \"high\", (text, r)\n"
    "\n\n"
    "def test_nested_function_defined_in_several_branches_returns_every_definition(tmp_path):\n"
    "    ix, res = _one_file_repo(tmp_path, (\n"
    "        \"def outer(x):\\n    if x:\\n        def conv(v):\\n            return 1\\n\"\n"
    "        \"    else:\\n        def conv(v):\\n            return 2\\n    return conv(x)\\n\"))\n"
    "    fn = ix.functions[\"m.py::outer\"]\n"
    "    r = res.resolve(fn, next(c for c in fn.calls if c.text == \"conv\"))\n"
    "    assert len(r.targets) == 2 and r.confidence == \"medium\"\n"
)

with io.open("tests/test_codemapper.py", "a", encoding="utf-8") as f:
    f.write(test_code)

print("patched OK")
