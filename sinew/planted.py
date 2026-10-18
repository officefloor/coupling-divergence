"""A synthetic git history with KNOWN ground truth, for verifying the instrument and the
raters before any real repository is touched.

The history has 42 commits over 32 Java methods:

  * a PLANTED SINEW: `top -> m1 -> m2 -> leaf` thread `Order` and `Ctx` down the chain and
    are edited together in 8 commits (span 4: a concern);
  * a PLANTED DECOY: `alpha` and `beta` are edited together just as often but pass nothing
    to each other (co-change without data flow);
  * a PLANTED QUIET PAIR: `p` forwards `Order` to `q`, and neither ever changes again;
  * 12 light pairs (`u_i` forwards `Ctx` to `v_i`, edited together twice each), so the heavy
    threshold has a body of ordinary bonds to be extreme against;
  * one whitespace-only commit, which must not count as a change.

The tests assert what the analysis must find here; `sinew rate --check` has the AI raters
judge the blind dossiers of this history, so a rater that cannot tell the chain from the
decoy is caught before it rates anything real.
"""
from __future__ import annotations

import os
import subprocess

N_LIGHT = 12


class Repo:
    """A throwaway git repository with deterministic authorship and dates."""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                    "GIT_AUTHOR_DATE": "2024-01-01T00:00:00",
                    "GIT_COMMITTER_DATE": "2024-01-01T00:00:00"}
        self.n = 0
        self.git("init", "-q")

    def git(self, *a: str) -> str:
        return subprocess.run(["git", "-C", self.root, *a], check=True, env=self.env,
                              capture_output=True, text=True).stdout.strip()

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def commit(self, msg: str = "c") -> str:
        self.n += 1
        day, hour = 1 + self.n // 24, self.n % 24
        self.env["GIT_AUTHOR_DATE"] = f"2024-01-{day:02d}T{hour:02d}:00:00"
        self.env["GIT_COMMITTER_DATE"] = self.env["GIT_AUTHOR_DATE"]
        self.git("add", "-A")
        self.git("commit", "-qm", msg)
        return self.git("rev-parse", "HEAD")


def chain_src(v: list) -> str:
    return f"""package app;
public class Chain {{
  int top(Order order, Ctx ctx, int flag) {{ return m1(order, ctx) + {v[0]}; }}
  int m1(Order order, Ctx ctx) {{ return m2(order, ctx) + {v[1]}; }}
  int m2(Order order, Ctx ctx) {{ return leaf(order, ctx) + {v[2]}; }}
  int leaf(Order order, Ctx ctx) {{ return order.size() + ctx.depth() + {v[3]}; }}
}}
"""


def noise_src(v: int) -> str:
    return f"""package app;
public class Noise {{
  int alpha(int n) {{ return n + {v}; }}
  int beta(String s) {{ return s.length() + {v}; }}
}}
"""


QUIET_SRC = """package app;
public class Quiet {
  int p(Order o) { return q(o); }
  int q(Order o) { return o.size(); }
}
"""


def light_src(i: int, v: int) -> str:
    return f"""package app;
public class Light{i} {{
  int u{i}(Ctx ctx, int k) {{ return v{i}(ctx) + {v}; }}
  int v{i}(Ctx c) {{ return c.depth() + {v}; }}
}}
"""


def plant(root: str) -> tuple:
    """Build the planted history at `root`. Returns (Repo, sha of the whitespace-only
    commit)."""
    r = Repo(root)
    chain = [0, 0, 0, 0]
    r.write("src/main/java/app/Chain.java", chain_src(chain))
    r.write("src/main/java/app/Noise.java", noise_src(0))
    r.write("src/main/java/app/Quiet.java", QUIET_SRC)
    for i in range(1, N_LIGHT + 1):
        r.write(f"src/main/java/app/Light{i}.java", light_src(i, 0))
    r.commit("create")
    light = {i: 0 for i in range(1, N_LIGHT + 1)}
    li = 1
    for step in ["chain", "noise", "light", "light", "light"] * 8:
        if step == "chain":
            chain = [x + 1 for x in chain]
            r.write("src/main/java/app/Chain.java", chain_src(chain))
            r.commit("chain")
        elif step == "noise":
            r.write("src/main/java/app/Noise.java", noise_src(max(light.values()) + chain[0]))
            r.commit("noise")
        elif li <= N_LIGHT * 2:
            i = (li - 1) % N_LIGHT + 1
            light[i] += 1
            r.write(f"src/main/java/app/Light{i}.java", light_src(i, light[i]))
            r.commit(f"light{i}")
            li += 1
    r.write("src/main/java/app/Chain.java", chain_src(chain).replace("\n  int", "\n      int"))
    ws = r.commit("reindent")
    return r, ws
