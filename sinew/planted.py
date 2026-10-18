"""A synthetic git history with KNOWN ground truth, for verifying the instrument and the
raters before any real repository is touched.

The history has 32 + ... methods in 61 Java files (see the counts below) and some 230 commits:

  * N_CHAINS PLANTED SINEWS: in Chain{i}.java four methods thread two types down a chain
    (`top -> m1 -> m2 -> leaf` threads `Order` and `Ctx`; the others have their own names
    and types) and are edited together in 8 commits (span 4: a concern). Each chain file also
    holds four BYSTANDER methods, each edited alone twice, so a within-file permutation can
    move the chain's edits onto them (and a concern that is simply "the whole file changed"
    is not mistaken for a structure);
  * N_DECOYS PLANTED DECOYS: pairs (`alpha`/`beta`, ...) in Noise{i}.java edited together just
    as often but passing nothing to each other (co-change without data flow);
  * N_CONTRACTS PLANTED CONTRACTS: pairs (`encode`/`decode`, ...) that thread two types and
    are edited together 8 times -- heavy, but span 2;
  * a PLANTED QUIET PAIR: `p` forwards `Order` to `q`, and neither ever changes again;
  * N_LIGHT light pairs (`u_i` forwards `Ctx` to `v_i`, edited together twice each), so the
    heavy threshold has a body of ordinary bonds to be extreme against;
  * one whitespace-only commit, which must not count as a change.

The tests assert what the analysis must find here; `sinew rate --check` has the AI raters
judge the blind dossiers of this history (N_CHAINS concern, N_CONTRACTS contract and
N_DECOYS matched decoy dossiers), so a rater that cannot tell the chains from the decoys is
caught before it rates anything real -- and the probability that a coin-flip rater passes the
gate is small and is printed.
"""
from __future__ import annotations

import os
import subprocess

N_CHAINS = 5
N_DECOYS = 5
N_CONTRACTS = 2
N_LIGHT = 48
N_BYSTANDERS = 4
ROUNDS = 8

CHAINS = [  # (methods, types)
    (("top", "m1", "m2", "leaf"), ("Order", "Ctx")),
    (("receive", "normalise", "route", "persist"), ("Invoice", "Session")),
    (("handle", "prepare", "apply", "store"), ("Cart", "Tenant")),
    (("dispatch", "enrich", "reconcile", "ship"), ("Shipment", "Clock")),
    (("open", "triage", "escalate", "close"), ("Ticket", "Budget")),
]
DECOYS = [("alpha", "beta"), ("gamma", "delta"), ("eps", "zeta"), ("eta", "theta"),
          ("iota", "kappa")]
CONTRACTS = [(("encode", "decode"), ("Frame", "Wire")), (("pack", "unpack"), ("Packet", "Link"))]


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
        self.env["GIT_AUTHOR_DATE"] = f"2024-{1 + day // 28:02d}-{1 + day % 28:02d}T{hour:02d}:00:00"
        self.env["GIT_COMMITTER_DATE"] = self.env["GIT_AUTHOR_DATE"]
        self.git("add", "-A")
        self.git("commit", "-qm", msg)
        return self.git("rev-parse", "HEAD")


def chain_src(i: int, v: list, by: list) -> str:
    (a, b, c, d), (T1, T2) = CHAINS[i]
    t1, t2 = T1.lower(), T2.lower()
    bys = "\n".join(f"  int helper{j}(int x) {{ return x * {j} + {by[j]}; }}"
                    for j in range(N_BYSTANDERS))
    return f"""package app;
public class Chain{i + 1} {{
  int {a}({T1} {t1}, {T2} {t2}, int flag) {{ return {b}({t1}, {t2}) + {v[0]}; }}
  int {b}({T1} {t1}, {T2} {t2}) {{ return {c}({t1}, {t2}) + {v[1]}; }}
  int {c}({T1} {t1}, {T2} {t2}) {{ return {d}({t1}, {t2}) + {v[2]}; }}
  int {d}({T1} {t1}, {T2} {t2}) {{ return {t1}.size() + {t2}.depth() + {v[3]}; }}
{bys}
}}
"""


def noise_src(i: int, v: int) -> str:
    a, b = DECOYS[i]
    return f"""package app;
public class Noise{i + 1} {{
  int {a}(int n) {{ return n + {v}; }}
  int {b}(String s) {{ return s.length() + {v}; }}
}}
"""


def contract_src(i: int, v: int) -> str:
    (a, b), (T1, T2) = CONTRACTS[i]
    t1, t2 = T1.lower(), T2.lower()
    return f"""package app;
public class Codec{i + 1} {{
  int {a}({T1} {t1}, {T2} {t2}) {{ return {b}({t1}, {t2}) + {v}; }}
  int {b}({T1} {t1}, {T2} {t2}) {{ return {t1}.bytes() + {t2}.width() + {v}; }}
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
    chain = [[0, 0, 0, 0] for _ in range(N_CHAINS)]
    bys = [[0] * N_BYSTANDERS for _ in range(N_CHAINS)]
    for i in range(N_CHAINS):
        r.write(f"src/main/java/app/Chain{i + 1}.java", chain_src(i, chain[i], bys[i]))
    for i in range(N_DECOYS):
        r.write(f"src/main/java/app/Noise{i + 1}.java", noise_src(i, 0))
    for i in range(N_CONTRACTS):
        r.write(f"src/main/java/app/Codec{i + 1}.java", contract_src(i, 0))
    r.write("src/main/java/app/Quiet.java", QUIET_SRC)
    light = {i: 0 for i in range(1, N_LIGHT + 1)}
    for i in light:
        r.write(f"src/main/java/app/Light{i}.java", light_src(i, 0))
    r.commit("create")
    per_round = N_LIGHT * 2 // ROUNDS
    li = 1
    for rnd in range(ROUNDS):
        for i in range(N_CHAINS):
            chain[i] = [x + 1 for x in chain[i]]
            r.write(f"src/main/java/app/Chain{i + 1}.java", chain_src(i, chain[i], bys[i]))
            r.commit(f"chain{i + 1}")
        for i in range(N_DECOYS):
            r.write(f"src/main/java/app/Noise{i + 1}.java", noise_src(i, rnd + 1))
            r.commit(f"noise{i + 1}")
        for i in range(N_CONTRACTS):
            r.write(f"src/main/java/app/Codec{i + 1}.java", contract_src(i, rnd + 1))
            r.commit(f"codec{i + 1}")
        for _ in range(per_round):
            i = (li - 1) % N_LIGHT + 1
            light[i] += 1
            r.write(f"src/main/java/app/Light{i}.java", light_src(i, light[i]))
            r.commit(f"light{i}")
            li += 1
        if rnd % 2 == 1:                      # each bystander edited alone, twice over
            for i in range(N_CHAINS):
                j = (rnd // 2) % N_BYSTANDERS
                for jj in (j, (j + 2) % N_BYSTANDERS):
                    bys[i][jj] += 1
                    r.write(f"src/main/java/app/Chain{i + 1}.java",
                            chain_src(i, chain[i], bys[i]))
                    r.commit(f"bystander{i + 1}.{jj}")
    r.write("src/main/java/app/Chain1.java",
            chain_src(0, chain[0], bys[0]).replace("\n  int", "\n      int"))
    ws = r.commit("reindent")
    return r, ws
