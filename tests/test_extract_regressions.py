"""Regression tests for two extractor bugs found in review.

1. Test files that live beside the code (Go *_test.go, JS/TS *.test.* / *.spec.*, pytest
   test_*.py, RSpec *_spec.rb, gtest *_test.cc, Rust inline #[cfg(test)] modules) were
   measured as source, because only test DIRECTORIES were excluded. On cobra, 9 of the 12
   class outliers were *_test.go files.
2. The call scanner jumped past a call's whole argument list, so every call nested inside
   another call's parentheses was lost: in an argument, in an if/while/for condition, and in
   a lambda or callback passed as an argument.

3. Lexical miscounts: a function's own signature was counted as a call to itself (so every
   same-named declaration in another file inflated in_gen); a nested function's calls were
   counted again in each enclosing function; and comment stripping was language-blind
   (Python `//` floor division, JS `#private`, Rust lifetimes `'a` ate real code).

Assertions are on gain (out - in), the quantity the study uses, worked by hand below.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coupling_divergence import extract


def _tree(files: dict) -> str:
    root = tempfile.mkdtemp(prefix="coupling_divergence_test_")
    for name, body in files.items():
        path = os.path.join(root, name)
        if os.path.dirname(name):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(body)
    return root


def _gain(files: dict) -> dict:
    res = extract.analyze_tree(_tree(files))
    return {fid[1]: r["gain"] for fid, r in res["funcs"].items()}


# --- 1. colocated test files ----------------------------------------------------------

COLOCATED_TESTS = [
    "cmd/command_test.go", "src/util.test.js", "src/util.spec.ts", "src/App.test.tsx",
    "src/app.e2e-spec.ts", "pkg/test_core.py", "pkg/core_test.py", "pkg/conftest.py",
    "app/tests.py", "lib/model_spec.rb", "lib/test_model.rb", "src/parser_test.cc",
    "src/parser_unittest.cpp", "src/foo/tests.rs", "Foo.Tests/FooTests.cs",
    "Foo.UnitTests/BarTests.cs", "Tests/FooTests/FooTests.swift", "pkg/testdata/in.go",
]
SOURCE_FILES = [
    "cmd/command.go", "src/util.js", "src/util.ts", "src/App.tsx", "pkg/core.py",
    "pkg/testing_utils_like_name.py", "lib/model.rb", "src/parser.cc", "src/lib.rs",
    "Foo/Foo.cs", "src/main/java/a/TestCase.java", "src/contest.py",
]


def test_colocated_test_files_are_excluded():
    root = _tree({f: "x\n" for f in COLOCATED_TESTS + SOURCE_FILES})
    got = {p.replace(os.sep, "/") for p in extract.source_files(root)}
    assert got == set(SOURCE_FILES), (sorted(got - set(SOURCE_FILES)),
                                      sorted(set(SOURCE_FILES) - got))


def test_include_tests_keeps_colocated_test_files():
    root = _tree({f: "x\n" for f in COLOCATED_TESTS + SOURCE_FILES})
    got = {p.replace(os.sep, "/") for p in extract.source_files(root, include_tests=True)}
    assert set(COLOCATED_TESTS) <= got


RUST = """fn add(a: i32, b: i32) -> i32 { a + b }
fn run(x: i32) -> i32 { add(x, x) }

#[cfg(test)]
mod tests {
    use super::*;
    // a brace in a comment } must not end the module early
    fn helper<'a>(s: &'a str) -> &'a str { let _c = '}'; s }
    #[test]
    fn checks() { assert_eq!(add(1, 2), 3); assert_eq!(run(add(1, 1)), 4); }
}

fn after(y: i32) -> i32 { run(y) }
"""


def test_rust_inline_test_module_is_excluded():
    funcs = extract.analyze_tree(_tree({"src/lib.rs": RUST}))["funcs"]
    names = {fid[1] for fid in funcs}
    assert names == {"add", "run", "after"}, names
    # with the test module gone, its calls no longer feed in_gen: add gets 2 (from run),
    # run gets 1 (from after). run: out 2 - in 1 = 1; after: out 1 - in 0 = 1.
    g = {fid[1]: r["gain"] for fid, r in funcs.items()}
    assert g == {"add": 0, "run": 1, "after": 1}, g


def test_rust_inline_tests_kept_with_include_tests():
    funcs = extract.analyze_tree(_tree({"src/lib.rs": RUST}), include_tests=True)["funcs"]
    assert {"helper", "checks"} <= {fid[1] for fid in funcs}


# --- 2. nested call sites ------------------------------------------------------------

def test_call_sites_yields_nested_calls():
    sites = list(extract._call_sites("h(g(x, y), k(z))"))
    assert sites == [("h", 2), ("g", 2), ("k", 1)], sites


def test_call_in_if_condition_counts():
    # f sends g 2 args from inside the condition; nothing calls f. gain(f) = 2.
    g = _gain({"A.java": """class A {
  void f(int x) { if (g(x, x)) { return; } }
  boolean g(int a, int b) { return true; }
}"""})
    assert g == {"f": 2, "g": 0}, g


def test_call_in_while_and_for_counts():
    g = _gain({"A.java": """class A {
  void f(int x) {
    while (more(x, x)) { }
    for (int i = 0; i < size(x, x, x); i++) { }
  }
  boolean more(int a, int b) { return false; }
  int size(int a, int b, int c) { return 0; }
}"""})
    assert g == {"f": 5, "more": 0, "size": 0}, g


def test_call_nested_in_argument_counts():
    # f: h gets 2, g gets 2 -> out 4, in 0, gain 4 (was 2 when g(x, x) was skipped)
    g = _gain({"A.java": """class A {
  void f(int x) { h(g(x, x), x); }
  int g(int a, int b) { return 1; }
  void h(int a, int b) { }
}"""})
    assert g == {"f": 4, "g": 0, "h": 0}, g


def test_call_in_java_lambda_argument_counts():
    # forEach is library code, but the g(x, x) inside the lambda is ours: gain(f) = 2
    g = _gain({"A.java": """class A {
  void f(java.util.List<Integer> xs) { xs.forEach(x -> g(x, x)); }
  void g(int a, int b) { }
}"""})
    assert g == {"f": 2, "g": 0}, g


def test_call_in_multiline_js_callback_counts():
    # app.get is library code; handle(req, res, 1) inside the callback is ours.
    res = extract.analyze_tree(_tree({"a.js": """function setup(app) {
  app.get('/x', function (req, res) {
    handle(req, res, 1);
  });
}
function handle(a, b, c) { }
"""}))
    r = {fid[1]: v for fid, v in res["funcs"].items()}
    # the inline callback is setup's own code: counted there, once, and not again on
    # lizard's (anonymous) record
    assert r["setup"]["gain"] == 3, r
    assert r["(anonymous)"]["out_gen"] == 0, r
    assert r["handle"]["in_gen"] == 3, r


def test_python_call_in_condition_and_argument():
    g = _gain({"a.py": """def f(x):
    if check(x, x):
        emit(build(x, x, x))

def check(a, b):
    return True

def build(a, b, c):
    return a

def emit(v):
    pass
"""})
    assert g == {"f": 6, "check": 0, "build": 0, "emit": 0}, g


# --- 3. lexical miscounts -------------------------------------------------------------

def _recs(files: dict) -> dict:
    res = extract.analyze_tree(_tree(files))
    return {fid[1]: r for fid, r in res["funcs"].items()}


def test_signature_is_not_a_call():
    # run is declared in A and B and called once with 3 args from C. Declarations push
    # nothing, so in_gen(run) = 3, and go sends 3 and receives nothing.
    r = _recs({"A.java": "class A { void run(int a, int b, int c) { } }",
               "B.java": "class B { void run(int a, int b, int c) { } }",
               "C.java": "class C { void go(int a) { run(a, a, a); } }"})
    assert {k: (v["out_gen"], v["in_gen"]) for k, v in r.items()} == \
        {"run": (0, 3), "go": (3, 0)}, r


def test_recursion_on_the_signature_line_still_counts():
    # Go one-liner: the declaration Foo(a int) is dropped, the recursive r.Foo(a) is kept.
    r = _recs({"a.go": "package a\nfunc (r *T) Foo(a int) int { return r.Foo(a) }\n"})
    assert (r["Foo"]["out_gen"], r["Foo"]["in_gen"]) == (1, 1), r


def test_nested_function_calls_count_once():
    # inner's call to handle belongs to inner only; outer sends inner 1 arg.
    r = _recs({"a.js": """function outer(a) {
  function inner(b) {
    handle(b, b, b);
  }
  return inner(a);
}
function handle(a, b, c) { }
"""})
    assert r["outer"]["out_gen"] == 1, r
    assert r["inner"]["out_gen"] == 3, r
    assert r["handle"]["in_gen"] == 3, r


def test_top_level_callback_owns_its_calls():
    # no enclosing function, so the module-level callback is the unit that sends the flow
    r = _recs({"a.js": """app.get('/x', function (req, res) {
  handle(req, res, 1);
});
function handle(a, b, c) { }
"""})
    assert r["(anonymous)"]["out_gen"] == 3, r


def test_python_floor_division_is_not_a_comment():
    g = _gain({"a.py": """def f(x):
    y = x // 2; g(x, y)

def g(a, b):
    pass
"""})
    assert g == {"f": 2, "g": 0}, g


def test_python_hash_is_a_comment():
    g = _gain({"a.py": """def f(x):
    # g(x, x, x) is commented out
    g(x, x)

def g(a, b):
    pass
"""})
    assert g == {"f": 2, "g": 0}, g


def test_js_private_field_is_not_a_comment():
    g = _gain({"a.js": """class A {
  f(x) { this.#n = 1; g(x, x); }
}
function g(a, b) { }
"""})
    assert g["f"] == 2, g


def test_rust_lifetime_is_not_a_char_literal():
    # a naive '...' rule would swallow from 'a to the next quote, hiding g(x, x)
    g = _gain({"src/lib.rs": """fn f<'a>(x: &'a str) -> i32 { g(x, x) }
fn g(a: &str, b: &str) -> i32 { 0 }
"""})
    assert g == {"f": 2, "g": 0}, g


if __name__ == "__main__":
    ok = True
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  [PASS] {name}")
            except AssertionError as e:
                ok = False
                print(f"  [FAIL] {name}: {e}")
    print("OVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
