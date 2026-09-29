"""If you change a resolver rule and one of these breaks, you have found out immediately (and cheaply)."""
from pathlib import Path

import pytest

from codemapper import Index, Resolver, build_call_graph, build_dataflow_graph, blast_radius, trusted
from codemapper.baseline import BaselineResolver

TOY = Path(__file__).parent / "fixtures" / "toy"


@pytest.fixture(scope="module")
def ix():
    return Index.build(TOY)


@pytest.fixture(scope="module")
def res(ix):
    return Resolver(ix)


def call_in(ix, fn_id, text):
    fn = ix.functions[fn_id]
    matches = [c for c in fn.calls if c.text == text]
    assert matches, f"{text!r} not found in {fn_id}; have {[c.text for c in fn.calls]}"
    return fn, matches[0]


def resolve(ix, res, fn_id, text):
    fn, call = call_in(ix, fn_id, text)
    return res.resolve(fn, call)


# ---------------------------------------------------------------- parsing
def test_strict_mode_reports_unparseable_files_instead_of_hiding_them():
    strict = Index.build(TOY, tolerant=False)
    assert sorted(p for p, _ in strict.parse_errors) == ["bad.py", "py2.py"]


def test_tolerant_mode_repairs_old_python_and_records_every_change(ix):
    assert ix.parse_errors == []
    repairs = dict(ix.modules["py2.py"].repairs)
    assert "renamed identifier `async`" in repairs.values()
    assert any(w.startswith("except X, e") for w in repairs.values())
    assert any(w.startswith("dropped unparseable line") for w in repairs.values())
    assert "py2.py::old_style" in ix.functions and "py2.py::caller" in ix.functions


def test_calls_inside_repaired_files_still_resolve(ix, res):
    r = resolve(ix, res, "py2.py::caller", "old_style")
    assert r.targets == ("py2.py::old_style",) and r.confidence == "high"


def test_methods_nested_async_and_duplicate_definitions_are_all_found(ix):
    assert "app.py::with_nested.inner" in ix.functions              # nested function
    assert "dups.py::fetch_async" in ix.functions                   # async def
    assert "dups.py::compat" in ix.functions and "dups.py::compat@L5" in ix.functions   # both branches of try/except
    assert "dups.py::Thing.value" in ix.functions and "dups.py::Thing.value@L19" in ix.functions  # getter + setter


def test_overload_stubs_are_not_functions(ix):
    assert [i for i in ix.functions if i.startswith("dups.py::over")] == ["dups.py::over"]


def test_test_files_are_tagged(ix):
    assert ix.functions["tests/test_app.py::test_main"].is_test
    assert not ix.functions["app.py::main"].is_test


def test_receiver_is_kept(ix):
    fn = ix.functions["app.py::Worker.run"]
    by_text = {c.text: c for c in fn.calls}
    assert by_text["self.step"].receiver == "self" and by_text["self.step"].name == "step"
    assert by_text["os.path.join"].receiver == "os.path"
    assert by_text["', '.join"].receiver == "<literal>"
    assert by_text["helper"].receiver is None


# ---------------------------------------------------------------- resolution rules
def test_self_call_resolves_to_own_class_only(ix, res):
    r = resolve(ix, res, "other.py::A.go", "self.save")
    assert r.targets == ("other.py::A.save",) and r.confidence == "high"


def test_baseline_links_self_call_to_both_classes(ix):
    """This is the bug in your original heuristic, pinned as a test."""
    base = BaselineResolver(ix)
    fn, call = call_in(ix, "other.py::A.go", "self.save")
    assert set(base.resolve(fn, call)) == {"other.py::A.save", "other.py::B.save"}


def test_inherited_method(ix, res):
    r = resolve(ix, res, "app.py::Worker.run", "self.load")
    assert r.targets == ("util.py::Base.load",) and r.confidence == "high"


def test_own_method_beats_inherited_method_of_same_name(ix, res):
    r = resolve(ix, res, "app.py::Worker.run", "self.step")
    assert r.targets == ("app.py::Worker.step",)


def test_super_call(ix, res):
    r = resolve(ix, res, "app.py::Worker.__init__", "super().__init__")
    assert r.targets == ("util.py::Base.__init__",) and r.confidence == "high"


def test_explicit_import_and_module_attribute(ix, res):
    for text in ("helper", "u.helper"):
        r = resolve(ix, res, "app.py::Worker.run", text)
        assert r.targets == ("util.py::helper",) and r.confidence == "high", text


def test_external_calls_make_no_edge(ix, res):
    for text in ("os.path.join", "', '.join"):
        r = resolve(ix, res, "app.py::Worker.run", text)
        assert r.confidence == "external" and r.targets == (), text
    assert resolve(ix, res, "app.py::main", "len").confidence == "external"


def test_constructor_call_goes_to_init(ix, res):
    r = resolve(ix, res, "app.py::Worker.run", "Base")
    assert r.targets == ("util.py::Base.__init__",)
    r = resolve(ix, res, "app.py::main", "Worker")
    assert r.targets == ("app.py::Worker.__init__",)


def test_type_inferred_from_local_assignment(ix, res):
    r = resolve(ix, res, "app.py::Worker.run", "x.save")
    assert r.targets == ("util.py::Base.save",) and r.confidence == "medium"


def test_unknown_receiver_with_many_candidates_is_ambiguous_not_guessed(ix, res):
    r = resolve(ix, res, "app.py::main", "thing.save")
    assert r.confidence == "ambiguous" and len(r.targets) >= 3


def test_calling_a_local_variable_is_not_linked_to_anything(ix, res):
    r = resolve(ix, res, "app.py::main", "callback")
    assert r.confidence == "unresolved" and r.targets == ()


def test_nested_function_call(ix, res):
    r = resolve(ix, res, "app.py::with_nested", "inner")
    assert r.targets == ("app.py::with_nested.inner",) and r.confidence == "high"


def test_reexport_through_package_init_and_star_import(ix, res):
    assert resolve(ix, res, "use.py::a", "make").targets == ("pkg/core.py::make",)
    r = resolve(ix, res, "star.py::b", "helper")
    assert r.targets == ("util.py::helper",) and r.confidence == "high"


# ---------------------------------------------------------------- graph
def test_default_graph_hides_ambiguous_edges(ix, res):
    G = build_call_graph(ix, res)
    assert G.has_edge("app.py::main", "other.py::A.save")                 # stored ...
    assert not trusted(G).has_edge("app.py::main", "other.py::A.save")    # ... but hidden by default
    assert trusted(G, "ambiguous").has_edge("app.py::main", "other.py::A.save")


def test_blast_radius_ignores_tests_by_default(ix, res):
    G = build_call_graph(ix, res)
    assert G.has_edge("tests/test_app.py::test_main", "app.py::main")
    assert blast_radius(G, "app.py::main") == 0
    assert blast_radius(G, "app.py::main", include_tests=True) == 1
    # helper() <- Worker.run <- main  (callers of helper: Worker.run, star.b; run is called by main)
    assert blast_radius(G, "util.py::helper") >= 3


def test_dataflow_edges_follow_variables_through_a_pipeline(ix, res):
    D = build_dataflow_graph(ix, res)
    assert D.has_edge("pipeline.py::fetch", "pipeline.py::clean")
    assert D.has_edge("pipeline.py::clean", "pipeline.py::save_it")
    assert not D.has_edge("pipeline.py::fetch", "pipeline.py::save_it")


# ---------------------------------------------------------------- fixes found by error analysis
def _one_file_repo(tmp_path, source):
    (tmp_path / "m.py").write_text(source)
    ix = Index.build(tmp_path)
    return ix, Resolver(ix)


def test_generic_base_classes_are_followed(tmp_path):
    ix, res = _one_file_repo(tmp_path, (
        "class Base:\n    def hello(self):\n        pass\n\n\n"
        "class Child(Base[int]):\n    def go(self):\n        self.hello()\n        super().hello()\n"))
    fn = ix.functions["m.py::Child.go"]
    for text in ("self.hello", "super().hello"):
        r = res.resolve(fn, next(c for c in fn.calls if c.text == text))
        assert r.targets == ("m.py::Base.hello",) and r.confidence == "high", (text, r)


def test_nested_function_defined_in_several_branches_returns_every_definition(tmp_path):
    ix, res = _one_file_repo(tmp_path, (
        "def outer(x):\n    if x:\n        def conv(v):\n            return 1\n"
        "    else:\n        def conv(v):\n            return 2\n    return conv(x)\n"))
    fn = ix.functions["m.py::outer"]
    r = res.resolve(fn, next(c for c in fn.calls if c.text == "conv"))
    assert len(r.targets) == 2 and r.confidence == "medium"
