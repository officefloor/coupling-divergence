"""Self-tests for the sinew experiment: dependency pinning, source discovery, the Java
internal/external resolver, an end-to-end history mine, and the sinew analysis. Run with:

    . .venv/bin/activate && python -m unittest -q
"""
import os
import subprocess
import tempfile
import unittest

from sinew import deps, discover, java_extract, mine, analyze, sinew_study


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


class TestDeps(unittest.TestCase):
    def test_validate_passes(self):
        deps.validate()                       # raises SystemExit on drift

    def test_lib_dir_override(self):
        self.assertEqual(deps.lib_dir("foo"), "foo")
        self.assertTrue(deps.lib_dir().endswith("lib"))


class TestDiscover(unittest.TestCase):
    def test_excludes_tests_and_deps(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/A.java"), "class A{}")
            _write(os.path.join(d, "src/test/java/app/AT.java"), "class AT{}")
            _write(os.path.join(d, "javatests/app/BT.java"), "class BT{}")
            _write(os.path.join(d, "build/gen/G.java"), "class G{}")
            files = discover.source_files(d)
            self.assertEqual(files, ["src/main/java/app/A.java"])


class TestJavaResolver(unittest.TestCase):
    def test_internal_vs_external(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "src/main/java/app/A.java"), A_V1)
            _write(os.path.join(d, "src/main/java/app/B.java"), B_V1)
            shapes = {s["name"]: s for s in java_extract.method_shapes(d)}
            self.assertIn("foo", shapes)
            self.assertIn("bar", shapes)
            kinds = {(c["callee"]): c["kind"] for c in shapes["foo"]["calls"]}
            self.assertEqual(kinds.get("bar"), "internal")    # own-code call
            self.assertEqual(kinds.get("of"), "external")     # java.util.List.of
            self.assertGreaterEqual(shapes["foo"]["out_args_own"], 2)   # bar(a, b)


class TestMineAndSinew(unittest.TestCase):
    def test_end_to_end(self):
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
            git("add", "-A")
            git("commit", "-qm", "c1")
            _write(os.path.join(repo, "src/main/java/app/A.java"), A_V2)
            _write(os.path.join(repo, "src/main/java/app/B.java"), B_V2)
            git("add", "-A")
            git("commit", "-qm", "c2 edit both")

            base = os.path.join(d, "mine")
            mine.mine(repo, base)
            self.assertTrue(os.path.exists(base + ".methods.jsonl"))
            self.assertTrue(os.path.exists(base + ".commits.jsonl"))

            rows = analyze.head_rows(base)
            names = {r["name"] for r in rows}
            self.assertEqual(names, {"foo", "bar"})

            # foo and bar changed together in commit 2 -> a co-change pair
            _, pair = analyze.cochange(base)
            self.assertTrue(any(len(p) == 2 for p in pair), "expected a co-change pair")

            # the sinew report runs and returns its sections
            s = analyze.sinew(base)
            for key in ("typology", "controls", "heavy", "n_methods"):
                self.assertIn(key, s)
            self.assertIn("spans", s["heavy"])

            # study primitives run on the mine
            row = sinew_study.aggregate_row(base)
            self.assertEqual(row["base"], "mine")
            self.assertIn("error", sinew_study.predicts(base))   # too few commits -> graceful


class TestStudyOverSample(unittest.TestCase):
    def test_mines_each_usable_repository_up_to_its_pinned_commit_only(self):
        import json
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
