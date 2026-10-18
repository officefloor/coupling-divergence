"""Self-tests for the sinew experiment: dependency pinning, source discovery, parameter
parsing (names, types, match keys), the lizard extractor and its forward metrics, the history
miner, the statistics, and -- the ground-truth test -- a synthetic history with a PLANTED
sinew, a planted co-change-without-threading decoy and a planted threaded-but-quiet pair,
checking that the analysis finds the first and neither of the others. Run with:

    . .venv/bin/activate && python -m unittest discover -s tests -q
"""
import json
import os
import subprocess
import tempfile
import unittest

from sinew import deps, discover, extract, lizard_extract, mine, analyze, sinew_study, rate, planted


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


Repo = planted.Repo


A_V1 = """package app;
import java.util.List;
public class A {
  int foo(int a, int b) {
    B x = new B();
    return x.bar(a, b) + List.of(a).size();
  }
}
"""
B_V1 = """package app;
public class B {
  int bar(int a, int b) { return a + b; }
}
"""
A_V2 = A_V1.replace("List.of(a).size();", "List.of(a, b).size() + 1;")
B_V2 = B_V1.replace("return a + b;", "return a + b + 1;")

# The same conduit chain in two languages: top and middle thread ctx (and req / data) down to
# leaf, which forwards nothing. Hand-derived expectations below.
CHAIN_JAVA = """package app;
public class Conduit {
  int top(int req, String ctx, boolean flag) { int d = load(req); return middle(req, ctx, d); }
  int middle(int req, String ctx, int data) { return leaf(ctx, transform(data)); }
  int leaf(String ctx, int x) { return x + ctx.length(); }
  int transform(int d) { return d * 2; }
  int load(int r) { return r; }
}
"""
CHAIN_PY = """import json

def top(req, ctx, flag):
    data = load(req)
    return middle(req, ctx, data)

def middle(req, ctx, data):
    return leaf(ctx, transform(data))

def leaf(ctx, x):
    return json.dumps({"x": x, "c": ctx})

def transform(d):
    return d * 2

def load(r):
    return 1

class K:
    def m(self, p, q):
        return helper(p, [1, 2], {"a": q, "b": (p, q)})

def kw_top(req: Request, ctx):
    return kw_mid(req=req, ctx=ctx)

def kw_mid(req: Request, ctx):
    return 1
"""
CHAIN_PY_V2 = CHAIN_PY.replace("return 1\n\nclass", "return 2\n\nclass").replace(
    "return d * 2", "return d * 3")

EXPECTED_FWD = {            # name -> (fwd_args, fwd_params, fwd_ratio, fwd_names)
    "top": (3, 2, 0.667, ["ctx", "req"]),
    "middle": (2, 2, 0.667, ["ctx", "data"]),
    "leaf": (0, 0, 0.0, []),
    "transform": (0, 0, 0.0, []),
    "load": (0, 0, 0.0, []),
}


def _check_forward(tc, shapes):
    for name, (fa, fp, fr, names) in EXPECTED_FWD.items():
        r = shapes[name]
        tc.assertEqual((r["fwd_args"], r["fwd_params"], r["fwd_ratio"], r["fwd_names"]),
                       (fa, fp, fr, names), name)
    tc.assertEqual(shapes["top"]["params"], 3)
    tc.assertEqual(shapes["top"]["param_names"], ["req", "ctx", "flag"])
    mid = {c["callee"]: c for c in shapes["middle"]["calls"]}
    tc.assertEqual(mid["leaf"]["fwd"], ["ctx"])          # transform(data) is not a forward
    tc.assertEqual(mid["transform"]["fwd"], ["data"])


class TestDeps(unittest.TestCase):
    def test_validate_passes(self):
        deps.validate()                       # raises SystemExit on drift

    def test_lib_dir_override(self):
        self.assertEqual(deps.lib_dir("foo"), "foo")
        self.assertTrue(deps.lib_dir().endswith("lib"))

    def test_lizard_is_pinned(self):
        self.assertIn("lizard", deps.PINNED)


class TestDiscover(unittest.TestCase):
    def test_excludes_tests_and_deps_in_any_language(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/A.java"), "class A{}")
            _write(os.path.join(d, "src/test/java/app/AT.java"), "class AT{}")
            _write(os.path.join(d, "javatests/app/BT.java"), "class BT{}")
            _write(os.path.join(d, "build/gen/G.java"), "class G{}")
            _write(os.path.join(d, "pkg/mod.py"), "def f(): pass")
            _write(os.path.join(d, "pkg/test_mod.py"), "def test_f(): pass")
            _write(os.path.join(d, "svc/main.go"), "package main")
            _write(os.path.join(d, "svc/main_test.go"), "package main")
            _write(os.path.join(d, "svc/api.pb.go"), "package main")
            _write(os.path.join(d, "web/app.spec.ts"), "")
            _write(os.path.join(d, "web/app.ts"), "")
            _write(os.path.join(d, "web/vendor/lib.js"), "")
            files = sorted(discover.source_files(d))
            self.assertEqual(files, ["pkg/mod.py", "src/main/java/app/A.java",
                                     "svc/main.go", "web/app.ts"])
            self.assertEqual(discover.source_files(d, exts={".java"}),
                             ["src/main/java/app/A.java"])
            self.assertEqual(discover.language("x/y.ts"), "typescript")
            self.assertEqual(discover.exts_for({"java"}), {".java"})
            self.assertEqual(extract.languages(d),
                             {"java": 1, "python": 1, "go": 1, "typescript": 1})

    def test_build_directory_names_are_skipped_at_the_root_only(self):
        """`target`, `build`, `out` are build output at the root and ordinary package names
        deeper in (org/springframework/aop/target) -- the sampler's rule, mirrored here."""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "target/classes/X.java"), "class X{}")
            _write(os.path.join(d, "src/main/java/org/x/aop/target/T.java"), "class T{}")
            _write(os.path.join(d, "src/main/java/org/x/build/B.java"), "class B{}")
            _write(os.path.join(d, "node_modules/deep/vendor.js"), "")
            _write(os.path.join(d, "src/node_modules/x.js"), "")
            self.assertEqual(sorted(discover.source_files(d)),
                             ["src/main/java/org/x/aop/target/T.java",
                              "src/main/java/org/x/build/B.java"])


class TestParameters(unittest.TestCase):
    def test_names_and_types_per_language_family(self):
        P = lizard_extract.parse_parameter
        self.assertEqual(P("@ Named final Map m", "java"), ("m", "Map"))
        self.assertEqual(P(" int [ ] arr", "java"), ("arr", "int[]"))
        self.assertEqual(P(" String ... rest", "java"), ("rest", "String[]"))
        self.assertEqual(P(" Order o", "java"), ("o", "Order"))
        # `in`, `ref`, `var` are modifiers in C# / Kotlin and ordinary parameter NAMES in Java
        self.assertEqual(P("JsonReader in", "java"), ("in", "JsonReader"))
        self.assertEqual(P("final Object ref", "java"), ("ref", "Object"))
        self.assertEqual(P("Ctx var", "java"), ("var", "Ctx"))
        self.assertEqual(P("req : Request", "py"), ("req", "Request"))
        self.assertEqual(P(" ctx", "py"), ("ctx", None))
        self.assertEqual(P(" n : int = 3", "py"), ("n", "int"))
        self.assertEqual(P(" ** kw", "py"), ("kw", None))
        self.assertEqual(P("ctx context . Context", "go"), ("ctx", "context.Context"))
        self.assertEqual(P(" o * Order", "go"), ("o", "*Order"))
        self.assertEqual(P(" a", "go"), ("a", None))
        self.assertEqual(P("req Request", "ts"), ("req", "Request"))
        self.assertEqual(P(" ctx : Context = x", "kt"), ("ctx", "Context"))
        self.assertEqual(P("req : & Request", "rs"), ("req", "Request"))
        self.assertEqual(P(" ref int n", "cs"), ("n", "int"))
        self.assertEqual(P(" params string [ ] rest", "cs"), ("rest", "string[]"))
        self.assertEqual(P("const struct req * r", "c"), ("r", "structreq*"))
        self.assertEqual(P(" char buf [ ]", "c"), ("buf", "char[]"))
        self.assertEqual(P("_ req : Request", "swift"), ("req", "Request"))
        self.assertEqual(P(" ctx = nil", "rb"), ("ctx", None))
        self.assertEqual(P(" rest", "js"), ("rest", None))
        self.assertEqual(P("$req", "php"), ("req", None))

    def test_match_key_is_type_when_it_identifies_something_else_name(self):
        K = lizard_extract.match_key
        self.assertEqual(K("order", "Order"), "T:Order")
        self.assertEqual(K("o", "*Order"), "T:*Order")
        self.assertEqual(K("ctx", None), "N:ctx")                  # untyped language
        self.assertEqual(K("tenantId", "String"), "N:tenantId")    # universal type: by name
        self.assertEqual(K("n", "int"), "N:n")
        self.assertEqual(K("items", "List"), "N:items")            # bare collection
        self.assertEqual(K("t", "T"), "N:t")                       # type variable
        self.assertEqual(K("arr", "int[]"), "N:arr")
        self.assertTrue(lizard_extract.informative("List<Order>"))
        self.assertFalse(lizard_extract.informative("Map<String, List<T>>"))

    def test_go_and_typescript_names_come_from_the_full_text(self):
        """lizard's bare parameter list returns TYPE names for Go and TS; ours must not."""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "a.go"),
                   "package p\nfunc f(ctx context.Context, o *Order) int { return g(ctx, o) }\n"
                   "func g(c context.Context, o *Order) int { return 1 }\n")
            _write(os.path.join(d, "a.ts"),
                   "function f(req: Request, ctx: any): number { return g(req) }\n"
                   "function g(r: Request): number { return 1 }\n")
            shapes = {(s["file"], s["name"]): s for s in lizard_extract.method_shapes(d)}
            go_f, ts_f = shapes[("a.go", "f")], shapes[("a.ts", "f")]
            self.assertEqual(go_f["param_names"], ["ctx", "o"])
            self.assertEqual(go_f["param_keys"], ["N:ctx", "T:*Order"])
            self.assertEqual(go_f["fwd_names"], ["ctx", "o"])
            self.assertEqual(ts_f["param_names"], ["req", "ctx"])
            self.assertEqual(ts_f["param_keys"], ["T:Request", "N:ctx"])
            self.assertEqual(ts_f["fwd_keys"], ["T:Request"])


class TestExtractorJava(unittest.TestCase):
    def test_internal_vs_external(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/A.java"), A_V1)
            _write(os.path.join(d, "src/main/java/app/B.java"), B_V1)
            shapes = {s["name"]: s for s in lizard_extract.method_shapes(d)}
            self.assertIn("foo", shapes)
            self.assertIn("bar", shapes)
            self.assertEqual(shapes["foo"]["qualname"], "A.foo")
            kinds = {(c["callee"]): c["kind"] for c in shapes["foo"]["calls"]}
            self.assertEqual(kinds.get("bar"), "internal")    # own-code call
            self.assertEqual(kinds.get("of"), "external")     # java.util.List.of
            self.assertGreaterEqual(shapes["foo"]["out_args_own"], 2)   # bar(a, b)
            self.assertEqual(shapes["foo"]["fwd_names"], ["a", "b"])    # both threaded
            self.assertEqual(shapes["foo"]["fwd_ratio"], 1.0)
            self.assertEqual(shapes["foo"]["param_keys"], ["N:a", "N:b"])   # int: by name

    def test_forward_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/Conduit.java"), CHAIN_JAVA)
            _check_forward(self, {s["name"]: s for s in lizard_extract.method_shapes(d)})

    def test_guards_logging_and_annotations_are_not_threading(self):
        src = """package app;
import java.util.*;
public class Ctl {
  @GetMapping("/x")
  public String get(@RequestParam("id") Long id, @RequestBody Req req) {
    Objects.requireNonNull(id);
    log.debug("got {} {}", id, req);
    checkNotNull(req);
    return svc(id);
  }
  String svc(Long id) { return id.toString(); }
  void checkNotNull(Object o) { }
}
"""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/Ctl.java"), src)
            shapes = {s["name"]: s for s in lizard_extract.method_shapes(d)}
            g = shapes["get"]
            callees = [c["callee"] for c in g["calls"]]
            self.assertNotIn("RequestParam", callees)          # annotation, not a call
            self.assertNotIn("GetMapping", callees)
            self.assertEqual(g["fwd_names"], ["id"])            # only svc(id) threads
            self.assertEqual(g["fwd_ratio"], 0.5)
            self.assertEqual(g["fwd_names_any"], ["id", "req"])  # guards kept for sensitivity
            self.assertEqual(g["fwd_ratio_any"], 1.0)
            self.assertEqual(g["param_types"], ["Long", "Req"])
            self.assertEqual(g["param_keys"], ["N:id", "T:Req"])

    def test_anonymous_class_methods_fold_into_the_enclosing_method(self):
        src = """package app;
public class C {
  void listeners() {
    btn.addListener(new Listener() { public void run() { use(1L); } });
    btn2.addListener(new Listener() { public void run() { use(2L); } });
  }
  void use(long x) { }
}
"""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/C.java"), src)
            shapes = {s["qualname"]: s for s in lizard_extract.method_shapes(d)}
            self.assertEqual(set(shapes), {"C.listeners", "C.use"})   # no phantom (anonymous).run
            callees = sorted(c["callee"] for c in shapes["C.listeners"]["calls"])
            self.assertEqual(callees, ["Listener", "Listener", "addListener", "addListener",
                                       "use", "use"])

    def test_generics_varargs_and_nested_named_classes(self):
        src = """package app;
import java.util.*;
public class C {
  <T> Map<String, List<T>> generic(Map<String, List<T>> m, int[] arr, String... rest) {
    return keep(m, rest);
  }
  Object keep(Map m, String[] r) { return m; }
  class Inner { void m(Order o) { keep(null, null); } }
}
"""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/C.java"), src)
            shapes = {s["qualname"]: s for s in lizard_extract.method_shapes(d)}
            g = shapes["C.generic"]
            self.assertEqual(g["param_names"], ["m", "arr", "rest"])
            self.assertEqual(g["fwd_names"], ["m", "rest"])
            self.assertEqual(shapes["Inner.m"]["param_keys"], ["T:Order"])

    def test_overloads_merge_to_the_widest_shape_not_a_sum(self):
        src = """package app;
public class O {
  int over(Order a) { return over(a, 0); }
  int over(Order a, int b) { return over(a, b, 0); }
  int over(Order a, int b, int c) { return a.size() + b + c; }
}
"""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/O.java"), src)
            rows = lizard_extract.method_shapes(d)
            self.assertEqual(len(rows), 3)
            M = analyze.methods_at_head(rows)
            m = M[("src/main/java/app/O.java", "O.over")]
            self.assertEqual((m["IN"], m["OUT"], m["n_overloads"]), (3, 3, 3))
            self.assertEqual(m["param_keys"], {"T:Order", "N:b", "N:c"})
            self.assertEqual(m["fwd_keys"], {"T:Order", "N:b"})

    def test_same_signature_bodies_are_told_apart_but_overloads_still_merge(self):
        """lizard names every enum-constant body `E::read`; they must not merge into one."""
        src = """package app;
public enum E {
  A { int read(T t) { return 1; } },
  B { int read(T t) { return 2; } };
  abstract int read(T t);
  int other(T t) { return 0; }
}
"""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/E.java"), src)
            rows = lizard_extract.method_shapes(d)
            self.assertEqual(sorted(s["qualname"] for s in rows), ["E.other", "E.read#1", "E.read#2"])
            self.assertEqual({s["name"] for s in rows}, {"other", "read"})
            self.assertEqual(len(analyze.methods_at_head(rows)), 3)

    def test_strict_forwards_need_a_resolvable_callee(self):
        """`items.add(o)` is 'internal' by name when any class defines `add`; the strict
        metrics count only callees defined in the caller's file or once in the project."""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/A.java"), """package app;
public class A {
  void use(Order o, Ctx c) { items.add(o); helper(c); }
  void helper(Ctx c) { }
}
""")
            _write(os.path.join(d, "src/main/java/app/B.java"),
                   "package app;\npublic class B { void add(Order o) { } }\n")
            _write(os.path.join(d, "src/main/java/app/C.java"),
                   "package app;\npublic class C { void add(Order o) { } }\n")
            use = {s["name"]: s for s in lizard_extract.method_shapes(d)}["use"]
            self.assertEqual(use["fwd_names"], ["c", "o"])
            self.assertEqual(use["fwd_names_strict"], ["c"])
            self.assertEqual((use["fwd_ratio"], use["fwd_ratio_strict"]), (1.0, 0.5))
            self.assertEqual(use["fwd_keys_strict"], ["T:Ctx"])


class TestThreadLinks(unittest.TestCase):
    def test_a_shared_name_is_not_a_link_unless_the_forward_reaches_the_partner(self):
        src = """package app;
public class V {
  int head(Node node, int depth) { return addHead(depth); }
  int tail(Node node, int depth) { return addTail(depth); }
  int addHead(int depth) { return depth; }
  int addTail(int depth) { return depth + 1; }
  int top(Order order, Ctx ctx) { return mid(order, ctx); }
  int mid(Order order, Ctx ctx) { return leaf(order, ctx); }
  int leaf(Order order, Ctx ctx) { return order.size(); }
}
"""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/V.java"), src)
            M = analyze.methods_at_head(lizard_extract.method_shapes(d))
            links = analyze.thread_links(M)
            k = lambda n: ("src/main/java/app/V.java", "V." + n)  # noqa: E731
            # head and tail both declare `node` and `depth`; neither hands them to the other
            self.assertEqual(analyze.thread_keys(links, k("head"), k("tail")), set())
            self.assertEqual(analyze.thread_keys(links, k("head"), k("addHead")), {"N:depth"})
            self.assertEqual(analyze.thread_keys(links, k("top"), k("mid")), {"T:Order", "T:Ctx"})
            self.assertEqual(links[frozenset((k("top"), k("leaf")))],
                             {"keys": {"T:Order", "T:Ctx"}, "hops": 2})   # through mid
            self.assertNotIn(frozenset((k("head"), k("top"))), links)
            cp = analyze.call_pairs(M)
            self.assertIn(frozenset((k("top"), k("mid"))), cp)
            self.assertNotIn(frozenset((k("head"), k("tail"))), cp)


class TestExtractorPython(unittest.TestCase):
    def test_forward_metrics_match_java_and_keyword_arguments_forward(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "pkg/chain.py"), CHAIN_PY)
            shapes = {s["name"]: s for s in lizard_extract.method_shapes(d)}
            _check_forward(self, shapes)
            m = shapes["m"]                       # self is a receiver, not data
            self.assertEqual(m["params"], 2)
            self.assertEqual(m["param_names"], ["p", "q"])
            helper = next(c for c in m["calls"] if c["callee"] == "helper")
            self.assertEqual(helper["args"], 3)
            self.assertEqual(helper["fwd"], ["p"])
            self.assertEqual(helper["kind"], "external")      # no helper defined in the tree
            self.assertEqual(m["fwd_names"], [])              # external: not threading
            self.assertEqual(m["fwd_names_any"], ["p"])
            kw = shapes["kw_top"]                             # kw_mid(req=req, ctx=ctx)
            self.assertEqual(kw["fwd_names"], ["ctx", "req"])
            self.assertEqual(kw["param_keys"], ["T:Request", "N:ctx"])
            self.assertEqual(kw["fwd_keys"], ["N:ctx", "T:Request"])

    def test_one_checkout_mixed_languages(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/Conduit.java"), CHAIN_JAVA)
            _write(os.path.join(d, "pkg/chain.py"), CHAIN_PY)
            rows = extract.method_shapes(d)
            by_file = {}
            for r in rows:
                by_file.setdefault(r["file"], set()).add(r["name"])
            self.assertTrue(by_file["pkg/chain.py"] >= set(EXPECTED_FWD))
            self.assertEqual(by_file["src/main/java/app/Conduit.java"], set(EXPECTED_FWD))
            for r in rows:                       # one contract for every language
                for k in ("params", "param_names", "param_types", "param_keys", "fwd_args",
                          "fwd_params", "fwd_ratio", "fwd_names", "fwd_keys", "calls", "cc",
                          "nloc", "fwd_ratio_any"):
                    self.assertIn(k, r, (r["file"], k))
            self.assertEqual({r["file"] for r in extract.method_shapes(d, exts={".java"})},
                             {"src/main/java/app/Conduit.java"})


class TestStatistics(unittest.TestCase):
    def test_partial_spearman_one_control_matches_the_classic_formula(self):
        import random
        rng = random.Random(3)
        z = [rng.random() for _ in range(80)]
        x = [zi + 0.3 * rng.random() for zi in z]
        y = [zi + 0.3 * rng.random() for zi in z]
        rxy, rxz, ryz = (analyze.spearman(x, y), analyze.spearman(x, z),
                         analyze.spearman(y, z))
        classic = (rxy - rxz * ryz) / ((1 - rxz ** 2) * (1 - ryz ** 2)) ** 0.5
        self.assertAlmostEqual(analyze.partial_spearman(x, y, [z]), classic, places=9)
        self.assertAlmostEqual(analyze.partial_spearman(x, y, []), rxy, places=12)

    def test_partial_spearman_removes_a_shared_cause_with_several_controls(self):
        import random
        rng = random.Random(5)
        z1 = [rng.random() for _ in range(300)]
        z2 = [rng.random() for _ in range(300)]
        x = [a + b + 0.2 * rng.random() for a, b in zip(z1, z2)]
        y = [a + b + 0.2 * rng.random() for a, b in zip(z1, z2)]
        raw = analyze.spearman(x, y)
        got = analyze.partial_spearman(x, y, [z1, z2])
        self.assertGreater(raw, 0.8)
        self.assertLess(abs(got), 0.6 * raw)
        # agrees with the recursive route: partial out z1 with the classic formula, then z2
        P1 = analyze._partial_spearman
        rxy, rxz, ryz = P1(x, y, z1), P1(x, z2, z1), P1(y, z2, z1)
        via_recursion = (rxy - rxz * ryz) / ((1 - rxz ** 2) * (1 - ryz ** 2)) ** 0.5
        self.assertAlmostEqual(got, via_recursion, places=9)
        self.assertIsNone(analyze.partial_spearman([1, 2], [1, 2], [[1, 1]]))

    def test_bootstrap_and_pooling(self):
        import random
        rng = random.Random(7)
        x = [rng.random() for _ in range(60)]
        y = [xi + 0.5 * rng.random() for xi in x]
        ci = analyze.bootstrap_ci(lambda idx: analyze.spearman([x[i] for i in idx],
                                                               [y[i] for i in idx]), 60, 50)
        self.assertEqual(len(ci), 2)
        self.assertLess(ci[0], analyze.spearman(x, y))
        self.assertGreater(ci[1], analyze.spearman(x, y))
        pooled = analyze.pool_correlations([0.3, 0.2, 0.4, 0.1], [200, 300, 150, 400])
        self.assertEqual(pooled["k"], 4)
        self.assertTrue(pooled["ci95"][0] < pooled["estimate"] < pooled["ci95"][1])
        self.assertEqual(pooled["positive"], 4)
        self.assertIsNone(analyze.pool_correlations([0.3], [100]))

    def test_curveball_preserves_commit_sizes_and_method_frequencies(self):
        import random
        from collections import Counter
        sets = [set(range(i, i + 4)) for i in range(0, 40, 2)] + [{1, 2}, {5}, set()]
        sizes = sorted(len(s) for s in sets)
        freq = Counter(m for s in sets for m in s)
        work = [set(s) for s in sets]
        analyze.curveball(work, random.Random(1), 2000)
        self.assertEqual(sorted(len(s) for s in work), sizes)
        self.assertEqual(Counter(m for s in work for m in s), freq)
        self.assertNotEqual(work, sets)

    def test_stratified_curveball_keeps_per_file_counts(self):
        import random
        from collections import Counter
        fa = [("f", f"m{i}") for i in range(6)]
        fb = [("g", f"m{i}") for i in range(6)]
        sets = [set(fa[:3]) | set(fb[:1]), set(fa[2:5]) | set(fb[1:4]),
                set(fa[1:2]) | set(fb[3:6]), set(fa[3:6]), set(fb[:2])]
        work = [set(s) for s in sets]
        analyze.curveball_stratified(work, random.Random(1), 500,
                                     {m: m[0] for s in sets for m in s})
        per_file = lambda ss: [Counter(m[0] for m in s) for s in ss]  # noqa: E731
        self.assertEqual(per_file(work), per_file(sets))             # per-commit, per-file sizes
        self.assertEqual(Counter(m for s in work for m in s),
                         Counter(m for s in sets for m in s))         # method frequencies
        self.assertNotEqual(work, sets)

    def test_combined_p_values_pooling_and_the_chance_pass_probability(self):
        self.assertAlmostEqual(analyze.stouffer([0.5, 0.5])["p"], 0.5, places=3)
        self.assertLess(analyze.stouffer([0.01, 0.02, 0.03])["p"], 0.001)
        self.assertAlmostEqual(analyze.binom_tail(0, 10, 0.05), 1.0)
        self.assertAlmostEqual(analyze.binom_tail(10, 10, 0.5), 1 / 1024)
        self.assertAlmostEqual(rate.chance_pass(5, 5), (6 / 32) * (6 / 32))
        self.assertAlmostEqual(rate.chance_pass(1, 1), 0.25)
        pooled = analyze.pool_estimates([0.1, 0.12, 0.08], [0.02, 0.03, 0.02])
        self.assertGreater(pooled["ci95"][0], 0)
        rr = analyze.pool_log_ratios([(20, 100, 10, 100), (30, 100, 10, 100)])
        self.assertGreater(rr["ci95"][0], 1)

    def test_cluster_bootstrap_resamples_whole_clusters(self):
        from collections import Counter
        seen = []

        def stat(idx):
            seen.append(list(idx))
            return 0.0
        analyze.bootstrap_ci(stat, 8, 5, clusters=["a", "a", "b", "b", "c", "c", "d", "d"])
        self.assertEqual(len(seen), 5)
        for idx in seen:                       # members of one cluster come and go together
            cnt = Counter(idx)
            for i in range(0, 8, 2):
                self.assertEqual(cnt[i], cnt[i + 1])


N_LIGHT = planted.N_LIGHT
CHAIN_MARKERS = [f"{t1} {t1.lower()}" for _, (t1, _) in planted.CHAINS]   # `Order order` ...


def _sees_a_chain(user: str) -> bool:
    return any(m in user for m in CHAIN_MARKERS)


def key_of(dossier_dir: str) -> list:
    with open(os.path.join(dossier_dir, "mine.key.json")) as fh:
        return json.load(fh)
plant = planted.plant


class TestPlantedSinew(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo, cls.ws_sha = plant(os.path.join(cls.tmp.name, "repo"))
        cls.base = os.path.join(cls.tmp.name, "mine")
        mine.mine(cls.repo.root, cls.base)
        cls.commits = analyze._commits(cls.base)
        cls.rows = analyze.head_rows(cls.base)
        cls.s = analyze.sinew(cls.base, rows=cls.rows, commits=cls.commits,
                              null_samples=20, bootstrap=30)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_whitespace_only_commit_is_not_a_change(self):
        ws = next(c for c in self.commits if c["sha"] == self.ws_sha)
        self.assertEqual(ws["n_changed_methods"], 0)
        self.assertEqual(ws["changed_files"], [])        # nothing but whitespace moved

    def test_creation_commit_is_above_the_pair_cap(self):
        self.assertGreater(self.commits[0]["n_methods"], analyze.PARAMS["max_commit_size"])

    def test_bonds_are_threaded_cochange_only(self):
        h = self.s["heavy"]
        bonds = {frozenset((b["a"], b["b"])): b for b in h["bonds"]}
        chain = ["top", "m1", "m2", "leaf"]
        for i in range(4):
            for j in range(i + 1, 4):
                b = bonds[frozenset((chain[i], chain[j]))]
                self.assertEqual((b["k"], b["thick"], b["keys"], b["hops"]),
                                 (8, 2, ["Ctx", "Order"], j - i))
        self.assertEqual(bonds[frozenset(("encode", "decode"))]["keys"], ["Frame", "Wire"])
        self.assertNotIn(frozenset(("alpha", "beta")), bonds)   # co-change, nothing threaded
        self.assertNotIn(frozenset(("p", "q")), bonds)          # threaded, never changed
        self.assertEqual(h["n_unthreaded"], planted.N_DECOYS)
        self.assertEqual(h["n_pairs"], 6 * planted.N_CHAINS + planted.N_CONTRACTS + N_LIGHT)
        self.assertEqual(h["n_same_file"], h["n_pairs"])
        self.assertEqual(h["hops"], {"1": 3 * planted.N_CHAINS + planted.N_CONTRACTS + N_LIGHT,
                                     "2": 2 * planted.N_CHAINS, "3": planted.N_CHAINS})
        cf = h["counterfactual"]
        self.assertGreater(cf["threaded"]["p_cochange"], 0.9)   # only the quiet pair is silent
        self.assertEqual(cf["unthreaded"]["pairs"], 0)         # decoys do not call each other

    def test_heavy_spans_are_the_planted_chains_and_contracts(self):
        h = self.s["heavy"]
        n_heavy = 6 * planted.N_CHAINS + planted.N_CONTRACTS
        self.assertEqual(h["n_heavy_madz"], n_heavy)
        self.assertEqual(h["heavy_quantile"]["n_heavy"], n_heavy)   # the quantile rule agrees
        self.assertEqual(len(h["spans"]), planted.N_CHAINS + planted.N_CONTRACTS)
        concern = [sp for sp in h["spans"] if sp["concern"]]
        self.assertEqual({frozenset(m[0] for m in sp["members"]) for sp in concern},
                         {frozenset(names) for names, _ in planted.CHAINS})
        for sp in concern:
            self.assertEqual((sp["span"], sp["links"], sp["density"]), (4, 6, 1.0))
            self.assertEqual((sp["verdict"], sp["kind"]), ("CONCERN", "duplication-clique"))
        sp1 = next(sp for sp in concern if {m[0] for m in sp["members"]} == {"top", "m1", "m2", "leaf"})
        self.assertEqual(sp1["threaded"], [["Ctx", 6], ["Order", 6]])
        contracts = [sp for sp in h["spans"] if not sp["concern"]]
        self.assertEqual([(sp["span"], sp["kind"], sp["verdict"]) for sp in contracts],
                         [(2, "contract", "cohesion")] * planted.N_CONTRACTS)

    def test_null_models_find_the_planted_structure_unlikely(self):
        for key in ("null", "null_file"):
            nl = self.s["heavy"][key]
            self.assertEqual(nl["samples"], 20, key)
            self.assertGreater(nl["heavy_mass"]["observed"], nl["heavy_mass"]["null_mean"], key)
            self.assertLess(nl["heavy_mass"]["p"], 0.1, key)
        # the fixed-marginal null scatters the chains across files, so their span is rare;
        # the within-file null keeps each chain's edits in its file and can only dilute them
        # onto the bystanders, so the span itself is not rare there -- the mass is
        nl = self.s["heavy"]["null"]
        self.assertGreater(nl["max_span"]["observed"], nl["max_span"]["null_mean"])
        self.assertGreaterEqual(self.s["heavy"]["null_file"]["max_span"]["observed"],
                                self.s["heavy"]["null_file"]["max_span"]["null_mean"])
        self.assertEqual(self.s["heavy"]["null_file"]["strata"], "file")
        flags = sinew_study.flag_outliers(self.s)
        self.assertTrue(flags[0]["concern"])
        self.assertTrue(flags[0]["outlier"])

    def test_controls_and_window_are_reported(self):
        c = self.s["controls"]
        for k in (sinew_study.PRIMARY, sinew_study.PRIMARY_3,
                  "calls ~ degree | freq,nloc,cc (partial)",
                  "fwd_ratio_strict ~ degree | freq,nloc,cc,calls,params (partial)"):
            self.assertIn(k, c)
            self.assertIsNotNone(c[k], k)
        ci = c[sinew_study.PRIMARY + " ci95"]
        self.assertEqual(len(ci), 2)
        self.assertEqual(c["n_files"], planted.N_CHAINS + planted.N_DECOYS
                         + planted.N_CONTRACTS + 1 + N_LIGHT)
        w = self.s["window"]
        self.assertEqual(w["n_commits"], 234)
        self.assertEqual(w["n_informative"], 233)       # every commit but the whitespace one
        self.assertEqual(w["n_capped"], 1)
        self.assertGreater(w["days"], 0)
        self.assertEqual(self.s["params"]["max_commit_size"], 30)

    def test_predict_reports_increment_over_baselines(self):
        p = sinew_study.predicts(self.base, commits=self.commits, head=self.rows, bootstrap=20)
        self.assertNotIn("error", p)
        for k in ("sinew~future (raw)", "cochange~future (raw)", "freq~future (raw)",
                  sinew_study.PRIMARY_PREDICT, sinew_study.BASELINE_PREDICT, "increment",
                  "increment_ci95", "increment_se", "lift", "baseline_lift"):
            self.assertIn(k, p)
        self.assertAlmostEqual(p["increment"],
                               p[sinew_study.PRIMARY_PREDICT] - p[sinew_study.BASELINE_PREDICT], 3)
        self.assertEqual(p["n_gone_by_end"], 0)
        self.assertEqual(p["n_methods_at_cut"], 152)

    def test_aggregate_row_sensitivity_and_blind_dossiers(self):
        row = sinew_study.aggregate_row(self.base, {"null_samples": 5, "bootstrap": 10},
                                        rows=self.rows, commits=self.commits)
        self.assertEqual((row["concern"], row["max_span"], row["clusters"]),
                         (planted.N_CHAINS, 4, planted.N_CHAINS + planted.N_CONTRACTS))
        self.assertEqual(set(row["sensitivity"]), {"10", "20", "50"})
        self.assertEqual(row["languages"], {"java": 61})
        self.assertIn("predict", row)
        self.assertIn("null_file", row)
        self.assertIn("counterfactual", row)
        out = os.path.join(self.tmp.name, "dossiers")
        n = sinew_study.dossier(self.base, out, repo=self.repo.root, blind=True,
                                s=self.s, rows=self.rows, commits=self.commits)
        with open(os.path.join(out, "mine.key.json")) as fh:
            key = json.load(fh)
        self.assertEqual(n, len(key))
        self.assertEqual({k["truth"] for k in key}, {"concern", "contract", "decoy"})
        decoys = [k for k in key if k["truth"] == "decoy"]
        self.assertEqual(len(decoys), planted.N_DECOYS)
        for k in decoys:                         # matched on commits (8 = 8); size as close as exists
            self.assertEqual((k["match"]["target_span"], k["match"]["target_commits"],
                              k["match"]["commits"], k["span"]), (4, 9, 9, 2))   # 8 edits + creation
        for k in key:
            text = open(os.path.join(out, k["file"])).read()
            self.assertNotIn("CONCERN", text)        # blind: no verdict in the dossier
            self.assertNotIn("**methods**", text)    # and no size / commit counts up front
            self.assertIn("## Members", text)
            self.assertIn("```java", text)           # source read from the clone

    def test_ai_rating_logs_decisions_and_scores_against_the_key(self):
        out = os.path.join(self.tmp.name, "blind")
        sinew_study.dossier(self.base, out, repo=self.repo.root, blind=True,
                            s=self.s, rows=self.rows, commits=self.commits)
        for name in rate.dossier_files(out):
            text = open(os.path.join(out, name)).read()
            self.assertEqual(rate.dossier_leaks(text), [], name)
            self.assertNotIn("fwd_ratio", text)          # no instrument metrics
            self.assertNotIn("tramp data", text)         # no checklist naming the causes
        calls = []

        n_dossiers = planted.N_CHAINS + planted.N_CONTRACTS + planted.N_DECOYS

        def fake(model, system, user):
            calls.append((model, system, user))
            # rater A: consolidate when the group threads a planted chain's type; rater B:
            # always leave, and served by the server-side fallback model
            yes = model == "rater-a" and _sees_a_chain(user)
            return {"text": json.dumps({"decision": "consolidate" if yes else "leave",
                                        "confidence": 4, "cause": rate.CAUSES[0],
                                        "data_passed": ["order", "ctx"] if yes else [],
                                        "justification": "because"}),
                    "request_id": "req_x", "stop_reason": "end_turn",
                    "served_by": "fallback-x" if model == "rater-b" else model,
                    "usage": {"input": 10, "output": 5}}
        log = rate.rate(out, ["rater-a", "rater-b"], runs=2, call=fake, log=lambda *_: None)
        self.assertEqual(len(calls), n_dossiers * 2 * 2)       # dossiers x 2 models x 2 runs
        self.assertTrue(all(rate.leaks(sy) == [] for _, sy, _ in calls))
        with open(log) as fh:
            recs = [json.loads(l) for l in fh]
        self.assertEqual(len(recs), n_dossiers * 4)
        self.assertTrue(all(r["justification"] == "because" and r["prompt_sha256_16"]
                            for r in recs))
        rate.rate(out, ["rater-a", "rater-b"], runs=2, call=fake, log=lambda *_: None)
        self.assertEqual(len(calls), n_dossiers * 4)           # resumed: nothing re-rated
        card = rate.score(out, log=lambda *_: None)
        # the fallback-served ratings are scored as their own rater, not as rater-b
        self.assertEqual(set(card["per_model"]), {"rater-a", "rater-b -> fallback-x"})
        a, b = card["per_model"]["rater-a"], card["per_model"]["rater-b -> fallback-x"]
        self.assertEqual((a["concern"]["rate"], a["contract"]["rate"], a["decoy"]["rate"]),
                         (1.0, 0.0, 0.0))
        self.assertEqual((a["concern_minus_contract"], a["concern_minus_decoy"]), (1.0, 1.0))
        self.assertEqual((b["concern"]["rate"], b["decoy"]["rate"]), (0.0, 0.0))
        self.assertEqual(a["self_consistency"], 1.0)
        self.assertIn("rater-a vs rater-b -> fallback-x", card["agreement"])
        self.assertTrue(os.path.exists(os.path.join(out, "scorecard.json")))
        # human ratings of the same dossiers are scored beside the models
        sheet = rate.human_template(out)
        with open(sheet, "a") as fh:
            for k in key_of(out)[:3]:
                fh.write(f"{k['file']},DS,{'consolidate' if k['truth'] == 'concern' else 'leave'},4\n")
        card = rate.score(out, log=lambda *_: None)
        self.assertIn("human:DS", card["per_model"])
        self.assertIn("human:DS vs rater-a", card["agreement"])
        # an unblinded dossier folder is refused
        plain = os.path.join(self.tmp.name, "dossiers-plain")
        sinew_study.dossier(self.base, plain, s=self.s, rows=self.rows, commits=self.commits)
        with self.assertRaises(SystemExit):
            rate.rate(plain, ["rater-a"], call=fake, log=lambda *_: None)

    def test_rater_check_passes_with_a_discriminating_rater_and_fails_otherwise(self):
        def good(model, system, user):
            yes = _sees_a_chain(user)
            return {"text": json.dumps({"decision": "consolidate" if yes else "leave",
                                        "confidence": 5, "cause": rate.CAUSES[0],
                                        "data_passed": [], "justification": "j"}),
                    "request_id": "r", "served_by": model, "stop_reason": "end_turn",
                    "usage": {"input": 1, "output": 1}}

        def blind_rater(model, system, user):
            return {**good(model, system, user), "text": json.dumps(
                {"decision": "leave", "confidence": 3, "cause": rate.CAUSES[-1],
                 "data_passed": [], "justification": "j"})}
        def yes_man(model, system, user):
            return {**good(model, system, user), "text": json.dumps(
                {"decision": "consolidate", "confidence": 5, "cause": rate.CAUSES[0],
                 "data_passed": [], "justification": "j"})}
        work = os.path.join(self.tmp.name, "check")
        card = rate.check(work, ["m-a"], runs=1, call=good, log=lambda *_: None)
        self.assertTrue(card["passed"])
        self.assertEqual(card["per_model"]["m-a"]["concern_minus_decoy"], 1.0)
        self.assertEqual(card["truths"], {"concern": planted.N_CHAINS,
                                          "contract": planted.N_CONTRACTS,
                                          "decoy": planted.N_DECOYS})
        self.assertAlmostEqual(card["chance_pass"],
                               rate.chance_pass(planted.N_CHAINS, planted.N_DECOYS), 4)
        self.assertLess(card["chance_pass"], 0.05)
        card_y = rate.check(work, ["m-a"], runs=1, call=yes_man, log=lambda *_: None)
        self.assertFalse(card_y["passed"])                  # says consolidate to everything
        cfg = os.path.join(self.tmp.name, "rater.json")
        rate.save_config(card["models"], card["runs"], card["effort"], {}, cfg)
        self.assertEqual(rate.load_config(cfg)["models"], ["m-a"])
        card2 = rate.check(work, ["m-a"], runs=1, call=blind_rater, log=lambda *_: None)
        self.assertFalse(card2["passed"])                  # re-runs cleanly in the same dir

    def test_rating_prompt_is_neutral(self):
        self.assertEqual(rate.leaks(rate.SYSTEM + rate.QUESTION), [])
        self.assertIn("decision", rate.SCHEMA["required"])
        self.assertEqual(rate.SCHEMA["required"][0], "decision")   # decision before justification

    def test_mine_resume_discards_an_interrupted_commits_rows(self):
        base = os.path.join(self.tmp.name, "mine-resume")
        for suffix in (".methods.jsonl", ".commits.jsonl"):
            with open(self.base + suffix) as src, open(base + suffix, "w") as dst:
                dst.write(src.read())
        # drop the last commit's index row, leaving its method rows orphaned
        with open(base + ".commits.jsonl") as fh:
            lines = fh.readlines()
        with open(base + ".commits.jsonl", "w") as fh:
            fh.writelines(lines[:-1])
        mine.mine(self.repo.root, base)
        with open(base + ".methods.jsonl") as fh:
            per_sha = {}
            for line in fh:
                r = json.loads(line)
                per_sha.setdefault(r["sha"], []).append(r["qualname"])
        self.assertEqual(len(per_sha), 234)
        self.assertEqual(sorted(per_sha[self.ws_sha]),
                         sorted(r["qualname"] for r in self.rows))   # once, not twice


class TestMineAndSinew(unittest.TestCase):
    def test_end_to_end_mixed_languages_and_language_restriction(self):
        with tempfile.TemporaryDirectory() as d:
            r = Repo(os.path.join(d, "repo"))
            r.write("src/main/java/app/A.java", A_V1)
            r.write("src/main/java/app/B.java", B_V1)
            r.write("pkg/chain.py", CHAIN_PY)
            r.commit("c1")
            for i in range(3):                   # foo+bar and load+transform co-change 3x
                r.write("src/main/java/app/A.java", A_V2.replace("+ 1", f"+ {i + 1}"))
                r.write("src/main/java/app/B.java", B_V2.replace("+ 1", f"+ {i + 1}"))
                r.write("pkg/chain.py", CHAIN_PY_V2.replace("return 2", f"return {i + 2}"))
                r.commit(f"c{i + 2} edit both")

            base = os.path.join(d, "mine")
            mine.mine(r.root, base)
            rows = analyze.head_rows(base)
            names = {x["name"] for x in rows}
            self.assertEqual(names, {"foo", "bar", "m", "kw_top", "kw_mid"} | set(EXPECTED_FWD))
            with open(base + ".methods.jsonl") as fh:
                recs = [json.loads(l) for l in fh]
            self.assertTrue(all("param_keys" in x and "fwd_keys" in x for x in recs))

            s = analyze.sinew(base, null_samples=0, bootstrap=0)
            pairs = {frozenset((t["a"][0], t["b"][0])): t for t in s["heavy"]["top"]}
            self.assertIn(frozenset(("foo", "bar")), pairs)   # foo forwards a, b to bar(a, b)
            self.assertEqual(pairs[frozenset(("foo", "bar"))]["shared"], ["a", "b"])
            # load and transform co-change but neither forwards a parameter: not a bond
            self.assertNotIn(frozenset(("load", "transform")), pairs)
            self.assertGreaterEqual(s["heavy"]["n_unthreaded"], 1)
            csv_path = os.path.join(d, "panel.csv")
            analyze.to_csv(base, csv_path)
            with open(csv_path) as fh:
                header = fh.readline().strip().split(",")
            for col in ("params", "fwd_args", "fwd_ratio", "fwd_ratio_any", "d_fwd_args"):
                self.assertIn(col, header)
            self.assertIn("error", sinew_study.predicts(base))   # too few commits -> graceful

            jbase = os.path.join(d, "mine-java")
            mine.mine(r.root, jbase, langs={"java"})
            self.assertEqual({x["name"] for x in analyze.head_rows(jbase)}, {"foo", "bar"})


class TestStudyOverSample(unittest.TestCase):
    def test_mines_each_usable_repository_up_to_its_pinned_commit_in_its_language(self):
        from open_source_repository_sampler import files
        with tempfile.TemporaryDirectory() as d:
            r = Repo(os.path.join(d, "repo"))
            for i, (a_src, b_src) in enumerate([(A_V1, B_V1), (A_V2, B_V2)]):
                r.write("src/main/java/app/A.java", a_src)
                r.write("src/main/java/app/B.java", b_src)
                r.write("pkg/side.py", CHAIN_PY)       # a Python side-car: not Java
                r.commit(f"c{i + 1}")
            sampled = r.git("rev-parse", "HEAD")
            r.write("src/main/java/app/C.java",
                    "package app;\npublic class C { int later() { return 1; } }\n")
            r.commit("after sampling")                  # must NOT be mined

            sample = os.path.join(d, "open-source-repository-sample.csv")
            files.write_csv(sample, [
                {"draw": 1, "status": "usable", "url": "file://" + r.root, "full_name": "o/proj",
                 "commit": sampled, "language": "java", "n_commits": 2},
                {"draw": 2, "status": "excluded", "reason": "n_commits 3 < 500",
                 "url": "file:///nope", "full_name": "o/short", "commit": "0" * 40,
                 "language": "java"}])
            out = os.path.join(d, "results")
            calls = []

            def fake(model, system, user):
                calls.append(model)
                return {"text": json.dumps({"decision": "leave", "confidence": 3,
                                            "cause": rate.CAUSES[-1], "data_passed": [],
                                            "justification": "j"}),
                        "request_id": "r", "served_by": model, "stop_reason": "end_turn",
                        "usage": {"input": 1, "output": 1}}
            rater = {"models": ["m-a"], "runs": 1, "effort": "high"}
            rows = sinew_study.study(sample, out, recent=500,
                                     params={"null_samples": 3, "bootstrap": 5},
                                     rater=rater, rate_call=fake, limit=1, pilot={"o/proj"})

            self.assertEqual([x["full_name"] for x in rows], ["o/proj"])
            self.assertEqual((rows[0]["commit"], rows[0]["base"]), (sampled, "o__proj"))
            with open(os.path.join(out, "mines", "o__proj.commits.jsonl")) as fh:
                mined = [json.loads(line)["sha"] for line in fh if line.strip()]
            self.assertEqual(len(mined), 2)                    # the later commit is absent
            self.assertEqual(mined[-1], sampled)
            names = {x["name"] for x in analyze.head_rows(os.path.join(out, "mines", "o__proj"))}
            self.assertEqual(names, {"foo", "bar"})            # Java only
            with open(os.path.join(out, "sinew-study.json")) as fh:
                study = json.load(fh)
            self.assertEqual([x["full_name"] for x in study["projects"]], ["o/proj"])
            self.assertEqual(study["parameters"]["max_commit_size"], 30)
            self.assertEqual(study["parameters"]["restrict_language"], True)
            self.assertIn("lizard", study["environment"])
            self.assertIn("predict", study["projects"][0])
            self.assertIn("n_projects", study["pooled"])
            self.assertEqual(study["parameters"]["rater"], rater)
            self.assertEqual(study["parameters"]["limit"], 1)
            self.assertTrue(study["projects"][0]["pilot"])
            self.assertEqual((study["pooled"]["n_projects"], study["pooled"]["n_pilot_excluded"]),
                             (0, 1))                        # a pilot repository is never pooled
            self.assertIn("decisions", study["pooled"])
            self.assertEqual(calls, [])                    # no concern cluster: nothing rated
            self.assertTrue(os.path.exists(os.path.join(out, "rated", "scorecard.json")))
            self.assertEqual(study["rating"]["dossiers"], 0)
            self.assertEqual(sinew_study.study(sample, out, recent=500), rows)   # resumed


if __name__ == "__main__":
    unittest.main()
