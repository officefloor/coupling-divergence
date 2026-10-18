"""Self-tests for the sinew experiment: dependency pinning, source discovery, the lizard
extractor and its forward metrics in Java and Python, an end-to-end mixed-language history
mine, and the sinew analysis. Run with:

    . .venv/bin/activate && python -m unittest discover -s tests -q
"""
import json
import os
import subprocess
import tempfile
import unittest

from sinew import deps, discover, extract, lizard_extract, mine, analyze, sinew_study


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


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
  int bar(int x, int y) { return x + y; }
}
"""
A_V2 = """package app;
import java.util.List;
public class A {
  int foo(int a, int b) {
    B x = new B();
    return x.bar(a, b) + List.of(a, b).size() + 1;
  }
}
"""
B_V2 = """package app;
public class B {
  int bar(int x, int y) { return x + y + 1; }
}
"""
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
"""
CHAIN_PY_V2 = CHAIN_PY.replace("return 1", "return 2").replace("return d * 2", "return d * 3")

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
            _write(os.path.join(d, "web/app.spec.ts"), "")
            _write(os.path.join(d, "web/app.ts"), "")
            files = sorted(discover.source_files(d))
            self.assertEqual(files, ["pkg/mod.py", "src/main/java/app/A.java",
                                     "svc/main.go", "web/app.ts"])
            self.assertEqual(discover.source_files(d, exts={".java"}),
                             ["src/main/java/app/A.java"])
            self.assertEqual(discover.language("x/y.ts"), "typescript")
            self.assertEqual(extract.languages(d),
                             {"java": 1, "python": 1, "go": 1, "typescript": 1})


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

    def test_forward_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/Conduit.java"), CHAIN_JAVA)
            _check_forward(self, {s["name"]: s for s in lizard_extract.method_shapes(d)})

    def test_generics_varargs_and_anonymous_classes(self):
        src = """package app;
import java.util.*;
public class C {
  <T> Map<String, List<T>> generic(Map<String, List<T>> m, int[] arr, String... rest) {
    return keep(m, rest);
  }
  Runnable anon() {
    return new Runnable() {
      public void run() { generic(null, null); }
    };
  }
}
"""
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/C.java"), src)
            shapes = {s["qualname"]: s for s in lizard_extract.method_shapes(d)}
            g = shapes["C.generic"]
            self.assertEqual(g["param_names"], ["m", "arr", "rest"])
            self.assertEqual(g["fwd_names"], ["m", "rest"])
            run = shapes["(anonymous).run"]               # recorded, with its own calls
            self.assertEqual([c["callee"] for c in run["calls"]], ["generic"])
            self.assertEqual([c["callee"] for c in shapes["C.anon"]["calls"]], ["Runnable"])


class TestExtractorPython(unittest.TestCase):
    def test_forward_metrics_match_java(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "pkg/chain.py"), CHAIN_PY)
            shapes = {s["name"]: s for s in lizard_extract.method_shapes(d)}
            _check_forward(self, shapes)
            # self is a receiver, not data; literals holding commas are one argument each
            m = shapes["m"]
            self.assertEqual(m["params"], 2)
            self.assertEqual(m["param_names"], ["p", "q"])
            helper = next(c for c in m["calls"] if c["callee"] == "helper")
            self.assertEqual(helper["args"], 3)
            self.assertEqual(helper["fwd"], ["p"])
            self.assertEqual(helper["kind"], "external")      # no helper defined in the tree
            top = next(c for c in shapes["top"]["calls"] if c["callee"] == "middle")
            self.assertEqual(top["kind"], "internal")

    def test_one_checkout_mixed_languages(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/Conduit.java"), CHAIN_JAVA)
            _write(os.path.join(d, "pkg/chain.py"), CHAIN_PY)
            rows = extract.method_shapes(d)
            by_file = {}
            for r in rows:
                by_file.setdefault(r["file"], set()).add(r["name"])
            self.assertEqual(by_file["pkg/chain.py"] >= set(EXPECTED_FWD), True)
            self.assertEqual(by_file["src/main/java/app/Conduit.java"], set(EXPECTED_FWD))
            for r in rows:                       # one contract for every language
                for k in ("params", "param_names", "fwd_args", "fwd_params", "fwd_ratio",
                          "fwd_names", "calls", "cc", "nloc"):
                    self.assertIn(k, r, (r["file"], k))


class TestMineAndSinew(unittest.TestCase):
    def test_end_to_end_mixed_languages(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            os.makedirs(repo)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

            def git(*a):
                subprocess.run(["git", "-C", repo, *a], check=True, env=env,
                               capture_output=True)
            git("init", "-q")
            _write(os.path.join(repo, "src/main/java/app/A.java"), A_V1)
            _write(os.path.join(repo, "src/main/java/app/B.java"), B_V1)
            _write(os.path.join(repo, "pkg/chain.py"), CHAIN_PY)
            git("add", "-A")
            git("commit", "-qm", "c1")
            for i in range(3):                   # foo+bar and load+transform co-change 3x
                _write(os.path.join(repo, "src/main/java/app/A.java"),
                       A_V2.replace("+ 1", f"+ {i + 1}"))
                _write(os.path.join(repo, "src/main/java/app/B.java"),
                       B_V2.replace("+ 1", f"+ {i + 1}"))
                _write(os.path.join(repo, "pkg/chain.py"),
                       CHAIN_PY_V2.replace("return 2", f"return {i + 2}"))
                git("add", "-A")
                git("commit", "-qm", f"c{i + 2} edit both")

            base = os.path.join(d, "mine")
            mine.mine(repo, base)
            self.assertTrue(os.path.exists(base + ".methods.jsonl"))
            self.assertTrue(os.path.exists(base + ".commits.jsonl"))

            rows = analyze.head_rows(base)
            names = {r["name"] for r in rows}
            self.assertEqual(names, {"foo", "bar", "m"} | set(EXPECTED_FWD))
            # the mine keeps the declared parameter count and names at every commit
            with open(base + ".methods.jsonl") as fh:
                recs = [json.loads(l) for l in fh]
            self.assertTrue(all("params" in r and "param_names" in r and "fwd_ratio" in r
                                for r in recs))
            self.assertEqual({r["params"] for r in recs if r["name"] == "foo"}, {2})

            _, pair = analyze.cochange(base)
            self.assertTrue(any(len(p) == 2 for p in pair), "expected a co-change pair")

            s = analyze.sinew(base)
            for key in ("typology", "controls", "heavy", "n_methods", "params", "n_conduits"):
                self.assertIn(key, s)
            self.assertIn("spans", s["heavy"])
            self.assertEqual(s["params"]["max"], 3)
            # foo calls bar directly AND they co-change: with the latent split gone they are a
            # heavy-sinew candidate, with the threaded names shared across the bond
            pairs = {frozenset((t["a"][0], t["b"][0])): t for t in s["heavy"]["top"]}
            self.assertIn(frozenset(("foo", "bar")), pairs)
            self.assertGreaterEqual(s["heavy"]["n_pairs"], 2)
            self.assertIn("fwd_args ~ degree | cc (partial)", s["controls"])

            # panel carries the parameter and forward columns
            csv_path = os.path.join(d, "panel.csv")
            analyze.to_csv(base, csv_path)
            with open(csv_path) as fh:
                header = fh.readline().strip().split(",")
            for col in ("params", "in_args", "fwd_args", "fwd_params", "fwd_ratio",
                        "d_fwd_args"):
                self.assertIn(col, header)

            # study primitives run on the mine
            row = sinew_study.aggregate_row(base)
            self.assertEqual(row["base"], "mine")
            for k in ("pairs", "params_mean", "params_p90", "fwd_ratio_mean", "conduits",
                      "in_cc_partial", "fwd_cc_partial", "fwd_nloc_partial"):
                self.assertIn(k, row)
            self.assertIn("error", sinew_study.predicts(base))   # too few commits -> graceful


class TestStudyOverSample(unittest.TestCase):
    def test_mines_each_usable_repository_up_to_its_pinned_commit_only(self):
        from open_source_repository_sampler import files
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            os.makedirs(repo)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

            def git(*a):
                return subprocess.run(["git", "-C", repo, *a], check=True, env=env,
                                      capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            for i, (a_src, b_src) in enumerate([(A_V1, B_V1), (A_V2, B_V2)]):
                _write(os.path.join(repo, "src/main/java/app/A.java"), a_src)
                _write(os.path.join(repo, "src/main/java/app/B.java"), b_src)
                git("add", "-A")
                git("commit", "-qm", f"c{i + 1}")
            sampled = git("rev-parse", "HEAD")
            _write(os.path.join(repo, "src/main/java/app/C.java"),
                   "package app;\npublic class C { int later() { return 1; } }\n")
            git("add", "-A")
            git("commit", "-qm", "after sampling")          # must NOT be mined

            sample = os.path.join(d, "open-source-repository-sample.csv")
            files.write_csv(sample, [
                {"draw": 1, "status": "usable", "url": "file://" + repo, "full_name": "o/proj",
                 "commit": sampled, "language": "java", "n_commits": 2},
                {"draw": 2, "status": "excluded", "reason": "n_commits 3 < 500",
                 "url": "file:///nope", "full_name": "o/short", "commit": "0" * 40,
                 "language": "java"}])
            out = os.path.join(d, "results")
            rows = sinew_study.study(sample, out, recent=500)

            self.assertEqual([r["full_name"] for r in rows], ["o/proj"])
            self.assertEqual((rows[0]["commit"], rows[0]["base"]), (sampled, "o__proj"))
            with open(os.path.join(out, "mines", "o__proj.commits.jsonl")) as fh:
                mined = [json.loads(line)["sha"] for line in fh if line.strip()]
            self.assertEqual(len(mined), 2)                    # the later commit is absent
            self.assertEqual(mined[-1], sampled)
            names = {r["name"] for r in analyze.head_rows(os.path.join(out, "mines", "o__proj"))}
            self.assertEqual(names, {"foo", "bar"})
            with open(os.path.join(out, "sinew-study.json")) as fh:
                self.assertEqual([r["full_name"] for r in json.load(fh)], ["o/proj"])
            self.assertEqual(sinew_study.study(sample, out, recent=500), rows)   # resumed


if __name__ == "__main__":
    unittest.main()
