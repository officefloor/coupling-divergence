"""Derive analysis-ready views from a raw history mine (sinew.mine output).

The mine is deliberately raw and method-centric. This layer DERIVES what it left out, so the
same mine can be looked at many ways without re-walking history:

  * the SINEW analysis (`sinew`) -- the study's instrument. A THREAD LINK joins two methods
    when a parameter one of them forwards is a parameter the other declares, matched on the
    parameter's key (its declared type when the type identifies something, else its name):
    the data one sends and the other consumes. A BOND is a thread-linked pair that also
    co-changes (k >= min_k commits); its thickness is the number of matched keys and its
    heaviness k x thickness. Heavy bonds (extreme for the project) are joined into a graph
    whose components are the sinews, judged by SPAN. Every count is set against a
    PERMUTATION NULL that keeps each commit's size and each method's change frequency and
    shuffles which methods change together, so what is reported is observed against
    expected, not a raw count. The complexity controls partial out change frequency, size
    and complexity together, with bootstrap intervals.
  * the per-(commit, method) PANEL as CSV, for any later analysis in pandas/R.
  * EXPLORATORY views (`relate`, `tangle`) that resolve call edges by callee NAME alone. With
    names like `get` and `add` defined in every project, those edges are noisy; the views are
    kept for inspection and are not part of the study's claims.

Every threshold is in PARAMS and is written into the study's results. Pure standard library.
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import os
import random
import sys
from collections import Counter, defaultdict

from .extract_common import GUARD_CALLEES

# Every analysis threshold, in one place, recorded with the results.
PARAMS = {
    "max_commit_size": 30,     # a commit touching more methods than this counts for frequency, not pairs
    "min_k": 2,                # co-change commits for a pair to be a bond candidate
    "conduit_ratio": 0.5,      # a conduit forwards at least this share of its parameters ...
    "conduit_min_params": 2,   # ... and has at least this many
    "heavy_madz": 3.5,         # Iglewicz-Hoaglin modified z above which a bond is heavy
    "span_concern": 4,         # a sinew spanning this many methods or more is accidental sprawl
    "clique_density": 0.6,     # a concern this dense is a parallel-maintenance clique, else a hub
    "null_samples": 100,       # permutation-null draws (curveball, marginals preserved)
    "bootstrap": 200,          # bootstrap resamples for the partial-correlation intervals
    "seed": 1,
    "split": 0.7,              # history cut for the predictive test
    "sesoi": 0.1,              # smallest partial correlation of interest (H1 / H3 decision)
    "heavy_q": 0.9,            # sensitivity: heavy = top decile of bond mass (min mass 4)
    "heavy_q_min_mass": 4,
}


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load(base: str):
    """(commits, methods_by_sha). commits is ordered by walk index; methods_by_sha maps each
    sha to its list of raw method records."""
    with open(base + ".commits.jsonl") as fh:
        commits = [json.loads(l) for l in fh]
    commits.sort(key=lambda c: c["i"])
    by_sha: dict = defaultdict(list)
    with open(base + ".methods.jsonl") as fh:
        for line in fh:
            r = json.loads(line)
            by_sha[r["sha"]].append(r)
    return commits, by_sha


def iter_blocks(base: str):
    """Stream (i, atime, sha, rows) one commit at a time, holding only a single commit's
    method rows in memory. Relies on methods.jsonl being contiguous and ascending by commit
    (how mine.py writes it), so million-row mines analyse in bounded memory."""
    cur = None
    buf: list = []
    with open(base + ".methods.jsonl") as fh:
        for line in fh:
            r = json.loads(line)
            if r["sha"] != cur and buf:
                yield buf[0]["i"], buf[0]["atime"], cur, buf
                buf = []
            cur = r["sha"]
            buf.append(r)
    if buf:
        yield buf[0]["i"], buf[0]["atime"], cur, buf


def head_rows(base: str) -> list:
    """The method rows at the last (HEAD) commit, streamed so only one commit is held."""
    rows: list = []
    for _, _, _, block in iter_blocks(base):
        rows = block
    return rows


def _commits(base: str) -> list:
    with open(base + ".commits.jsonl") as fh:
        cs = [json.loads(l) for l in fh]
    cs.sort(key=lambda c: c["i"])
    return cs


# ---------------------------------------------------------------------------
# in-coupling by callee NAME (exploratory: see module doc)
# ---------------------------------------------------------------------------
class _Targets:
    """Resolve an internal call edge to the method record(s) it targets at one commit. An
    edge with no `target` (the lexical extractor) matches every method of that name."""

    def __init__(self, rows: list) -> None:
        self.by_fqn: dict = defaultdict(list)
        self.by_simple: dict = defaultdict(list)
        self.by_name: dict = defaultdict(list)
        for r in rows:
            mkey = (r["file"], r["qualname"])
            pkg, typ, name = r.get("package", ""), r.get("type", ""), r["name"]
            self.by_name[name].append(mkey)
            if typ:
                fqn = (pkg + "." if pkg else "") + typ
                self.by_fqn[(fqn, name)].append(mkey)
                self.by_simple[(typ.split(".")[-1], name)].append(mkey)

    def __call__(self, e: dict) -> set:
        if e.get("kind") != "internal":
            return set()
        tgt, callee = e.get("target"), e.get("callee")
        if not callee:
            return set()
        if tgt:
            hits = (self.by_fqn.get((tgt, callee))
                    or self.by_simple.get((tgt.split(".")[-1], callee)) or [])
        else:
            hits = self.by_name.get(callee, [])
        return set(hits)


def in_coupling(rows: list) -> dict:
    """method key -> {"in_args", "in_calls", "in_ambig"} for one commit, over name-resolved
    internal edges (exploratory)."""
    targets = _Targets(rows)
    acc: dict = {(r["file"], r["qualname"]): {"in_args": 0.0, "in_calls": 0.0, "in_ambig": 0}
                 for r in rows}
    for r in rows:
        for e in r.get("calls", []):
            uniq = targets(e)
            if not uniq:
                continue
            share = 1.0 / len(uniq)
            for mk in uniq:
                acc[mk]["in_args"] += e.get("args", 0) * share
                acc[mk]["in_calls"] += share
                if len(uniq) > 1:
                    acc[mk]["in_ambig"] += 1
    return acc


# ---------------------------------------------------------------------------
# the enriched per-(commit, method) panel
# ---------------------------------------------------------------------------
_METRICS = ("cc", "cognitive", "nloc", "tokens", "nesting", "params",
            "out_own", "out_ext", "in_args", "delta", "fwd_args")

PANEL_COLUMNS = [
    "i", "sha", "atime", "file", "package", "type", "qualname", "name", "start", "end",
    "cc", "cognitive", "nloc", "tokens", "nesting", "params",
    "out_own", "out_ext", "out_unres", "n_out_own", "n_out_ext", "n_out_unres",
    "in_args", "in_calls", "in_ambig", "delta",
    "fwd_args", "fwd_params", "fwd_ratio", "fwd_args_any", "fwd_ratio_any",
    "changed", "churn_add", "is_new", "age",
    "d_cc", "d_cognitive", "d_nloc", "d_tokens", "d_nesting", "d_params",
    "d_out_own", "d_ext", "d_in", "d_delta", "d_fwd_args",
]


def panel(base: str):
    """Yield enriched rows (dicts keyed by PANEL_COLUMNS), streaming commit by commit."""
    last: dict = {}
    first_i: dict = {}
    for i_idx, atime, sha, rows in iter_blocks(base):
        inc = in_coupling(rows)
        mc = Counter((r["file"], r["qualname"]) for r in rows)
        for r in rows:
            mkey = (r["file"], r["qualname"])
            ic = inc.get(mkey, {"in_args": 0.0, "in_calls": 0.0, "in_ambig": 0})
            ia = ic["in_args"] / mc[mkey]
            ical = ic["in_calls"] / mc[mkey]
            out_own = r.get("out_args_own", 0)
            cur = {
                "cc": r.get("cc", 0), "cognitive": r.get("cognitive", 0),
                "nloc": r.get("nloc", 0), "tokens": r.get("tokens", 0),
                "nesting": r.get("nesting", 0), "params": r.get("params", 0),
                "out_own": out_own, "out_ext": r.get("out_args_ext", 0),
                "in_args": round(ia, 3),
                "delta": round(out_own - ia, 3),
                "fwd_args": r.get("fwd_args", 0),
            }
            prev = last.get(mkey)
            is_new = prev is None
            if is_new:
                first_i[mkey] = i_idx
            row = {
                "i": i_idx, "sha": sha, "atime": atime,
                "file": r["file"], "package": r.get("package", ""), "type": r.get("type", ""),
                "qualname": r["qualname"], "name": r["name"],
                "start": r.get("start"), "end": r.get("end"),
                **{k: cur[k] for k in ("cc", "cognitive", "nloc", "tokens", "nesting",
                                       "params", "out_own", "out_ext")},
                "out_unres": r.get("out_args_unresolved", 0),
                "n_out_own": r.get("n_calls_own", 0), "n_out_ext": r.get("n_calls_ext", 0),
                "n_out_unres": r.get("n_calls_unresolved", 0),
                "in_args": cur["in_args"], "in_calls": round(ical, 3),
                "in_ambig": ic["in_ambig"], "delta": cur["delta"],
                "fwd_args": cur["fwd_args"], "fwd_params": r.get("fwd_params", 0),
                "fwd_ratio": r.get("fwd_ratio", 0.0),
                "fwd_args_any": r.get("fwd_args_any", 0),
                "fwd_ratio_any": r.get("fwd_ratio_any", 0.0),
                "changed": int(bool(r.get("changed"))), "churn_add": r.get("churn_add", 0),
                "is_new": int(is_new), "age": 0 if is_new else i_idx - first_i[mkey],
            }
            for k in _METRICS:
                dk = {"out_ext": "ext", "in_args": "in"}.get(k, k)
                row["d_" + dk] = 0 if is_new else round(cur[k] - prev[k], 3)
            last[mkey] = cur
            yield row


def to_csv(base: str, path: str) -> int:
    n = 0
    with open(path, "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=PANEL_COLUMNS)
        w.writeheader()
        for row in panel(base):
            w.writerow(row)
            n += 1
    return n


# ---------------------------------------------------------------------------
# statistics (pure python): Spearman, partial Spearman with several controls, bootstrap,
# Fisher-z random-effects pooling
# ---------------------------------------------------------------------------
def _rank(xs: list) -> list:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def _pearson(xs: list, ys: list):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / (sx * sy) ** 0.5


def spearman(xs: list, ys: list):
    if len(xs) < 3:
        return None
    return _pearson(_rank(xs), _rank(ys))


def _solve(A: list, b: list):
    """Gaussian elimination with partial pivoting; None if singular."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                if f:
                    M[r] = [a - f * bb for a, bb in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def _residualise(y: list, zs: list):
    """Residuals of y after least squares on [1, z1, ..., zp]; constant z's are dropped."""
    n = len(y)
    cols = [[1.0] * n] + [z for z in zs if len(set(z)) > 1]
    p = len(cols)
    A = [[sum(cols[i][k] * cols[j][k] for k in range(n)) for j in range(p)] for i in range(p)]
    b = [sum(cols[i][k] * y[k] for k in range(n)) for i in range(p)]
    beta = _solve(A, b)
    if beta is None:
        return None
    return [y[k] - sum(beta[i] * cols[i][k] for i in range(p)) for k in range(n)]


def partial_spearman(x: list, y: list, zs: list):
    """Partial Spearman of (x, y) given the controls in `zs` (a list of equal-length lists):
    rank everything, residualise the ranks of x and y on the ranks of the controls, and
    correlate the residuals. With one control this equals the classic three-correlation
    formula; with none it is Spearman."""
    if len(x) < 4 + len(zs):
        return None
    rx, ry = _rank(x), _rank(y)
    rz = [_rank(z) for z in zs]
    ex, ey = _residualise(rx, rz), _residualise(ry, rz)
    if ex is None or ey is None:
        return None
    return _pearson(ex, ey)


def _partial_spearman(x: list, y: list, z: list):
    return partial_spearman(x, y, [z])


def bootstrap_ci(stat, n: int, B: int, seed: int = 1, level: float = 0.95, clusters=None):
    """Percentile bootstrap interval of `stat(indices)` over resamples of range(n). With
    `clusters` (one label per index, e.g. the method's file) whole clusters are resampled,
    so methods of one file -- edited, and shaped, together -- are not treated as independent
    draws."""
    if n < 4 or B <= 0:
        return None
    rng = random.Random(seed)
    groups = None
    if clusters is not None:
        by: dict = defaultdict(list)
        for i, c in enumerate(clusters):
            by[c].append(i)
        groups = list(by.values())
        if len(groups) < 4:
            groups = None
    vals = []
    for _ in range(B):
        if groups is None:
            idx = [rng.randrange(n) for _ in range(n)]
        else:
            idx = [i for _ in range(len(groups)) for i in groups[rng.randrange(len(groups))]]
        v = stat(idx)
        if v is not None:
            vals.append(v)
    if len(vals) < max(10, B // 2):
        return None
    vals.sort()
    lo = vals[int((1 - level) / 2 * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 + level) / 2 * len(vals)))]
    return [round(lo, 3), round(hi, 3)]


def pool_correlations(rs: list, ns: list) -> dict | None:
    """DerSimonian-Laird random-effects pooling of correlations on the Fisher-z scale:
    {estimate, ci95, tau2, k, i2}. Needs at least two projects."""
    import math
    pairs = [(r, n) for r, n in zip(rs, ns) if r is not None and n and n > 3 and abs(r) < 1]
    if len(pairs) < 2:
        return None
    z = [0.5 * math.log((1 + r) / (1 - r)) for r, _ in pairs]
    w = [n - 3 for _, n in pairs]
    k = len(z)
    zbar = sum(wi * zi for wi, zi in zip(w, z)) / sum(w)
    q = sum(wi * (zi - zbar) ** 2 for wi, zi in zip(w, z))
    c = sum(w) - sum(wi * wi for wi in w) / sum(w)
    tau2 = max(0.0, (q - (k - 1)) / c) if c > 0 else 0.0
    ws = [1 / (1 / wi + tau2) for wi in w]
    zre = sum(wi * zi for wi, zi in zip(ws, z)) / sum(ws)
    se = (1 / sum(ws)) ** 0.5
    back = lambda v: (math.exp(2 * v) - 1) / (math.exp(2 * v) + 1)  # noqa: E731
    i2 = max(0.0, (q - (k - 1)) / q) if q > 0 else 0.0
    return {"estimate": round(back(zre), 3),
            "ci95": [round(back(zre - 1.96 * se), 3), round(back(zre + 1.96 * se), 3)],
            "tau2": round(tau2, 4), "i2": round(i2, 3), "k": k,
            "positive": sum(1 for zi in z if zi > 0)}


def pool_estimates(ests: list, ses: list) -> dict | None:
    """DerSimonian-Laird random-effects pooling of raw estimates with standard errors (used
    for the H3 increment, a difference of two partial correlations)."""
    pairs = [(e, s) for e, s in zip(ests, ses) if e is not None and s and s > 0]
    if len(pairs) < 2:
        return None
    w = [1 / s ** 2 for _, s in pairs]
    e = [x for x, _ in pairs]
    k = len(e)
    ebar = sum(wi * ei for wi, ei in zip(w, e)) / sum(w)
    q = sum(wi * (ei - ebar) ** 2 for wi, ei in zip(w, e))
    c = sum(w) - sum(wi * wi for wi in w) / sum(w)
    tau2 = max(0.0, (q - (k - 1)) / c) if c > 0 else 0.0
    ws = [1 / (1 / wi + tau2) for wi in w]
    ere = sum(wi * ei for wi, ei in zip(ws, e)) / sum(ws)
    se = (1 / sum(ws)) ** 0.5
    return {"estimate": round(ere, 3), "ci95": [round(ere - 1.96 * se, 3), round(ere + 1.96 * se, 3)],
            "tau2": round(tau2, 5), "k": k, "positive": sum(1 for x in e if x > 0)}


def pool_log_ratios(cells: list) -> dict | None:
    """Inverse-variance random-effects pooling of risk ratios from (a, n1, b, n2) cells:
    a of n1 'exposed' and b of n2 'unexposed' had the outcome. Haldane 0.5 correction when a
    cell is empty. Returns the pooled ratio with its interval."""
    import math
    ests, ses = [], []
    for a, n1, b, n2 in cells:
        if not n1 or not n2:
            continue
        if min(a, b, n1 - a, n2 - b) == 0:
            a, b, n1, n2 = a + 0.5, b + 0.5, n1 + 1, n2 + 1
        ests.append(math.log((a / n1) / (b / n2)))
        ses.append((1 / a - 1 / n1 + 1 / b - 1 / n2) ** 0.5)
    pooled = pool_estimates(ests, ses)
    if not pooled:
        return None
    return {"ratio": round(math.exp(pooled["estimate"]), 3),
            "ci95": [round(math.exp(v), 3) for v in pooled["ci95"]],
            "tau2": pooled["tau2"], "k": pooled["k"], "above_one": pooled["positive"]}


def _norm_cdf(z: float) -> float:
    import math
    return 0.5 * (1 + math.erf(z / 2 ** 0.5))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF by bisection (pure python, more than enough precision)."""
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def stouffer(ps: list) -> dict | None:
    """Stouffer's combined one-sided p over projects (equal weights). Permutation p-values
    are bounded below by 1/(B+1), so none is zero."""
    ps = [p for p in ps if p is not None]
    if not ps:
        return None
    zs = [_norm_ppf(1 - min(max(p, 1e-12), 1 - 1e-12)) for p in ps]
    z = sum(zs) / len(zs) ** 0.5
    return {"z": round(z, 3), "p": round(1 - _norm_cdf(z), 6), "k": len(ps)}


def binom_tail(k: int, n: int, p0: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p0), exactly."""
    from math import comb
    return sum(comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(k, n + 1)) if n else 1.0


def _quantile(xs: list, q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    return s[min(len(s) - 1, int(q * len(s)))]


def _median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _components(nodes: set, adj: dict) -> list:
    """Connected components of an undirected graph given as adjacency sets."""
    seen: set = set()
    comps = []
    for n in nodes:
        if n in seen:
            continue
        stack, comp = [n], []
        seen.add(n)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(comp)
    return comps


# ---------------------------------------------------------------------------
# co-change: per-commit change sets, pair counts, and the permutation null
# ---------------------------------------------------------------------------
def change_sets(commits: list) -> list:
    """One set of method ids (tuples) per commit, in walk order."""
    return [set(tuple(m) for m in (c.get("changed_methods") or [])) for c in commits]


def pair_counts(sets: list, max_commit_size: int):
    """(change_count, pair_count). change_count[m] = commits that touched m; pair[{a,b}] =
    commits that touched both. Commits touching more than max_commit_size methods (sweeping
    refactors / renames) count for frequency but NOT for pairs, so one mega-commit cannot
    fabricate co-change (Zimmermann)."""
    change_count: Counter = Counter()
    pair: Counter = Counter()
    for s in sets:
        for m in s:
            change_count[m] += 1
        if 2 <= len(s) <= max_commit_size:
            cm = list(s)
            for i in range(len(cm)):
                for j in range(i + 1, len(cm)):
                    pair[frozenset((cm[i], cm[j]))] += 1
    return change_count, pair


def cochange(base: str, max_commit_size: int = PARAMS["max_commit_size"]):
    return pair_counts(change_sets(_commits(base)), max_commit_size)


def commit_window(commits: list, max_commit_size: int) -> dict:
    """How comparable this project's co-change units are: the window's calendar span, the
    distribution of commit sizes (changed methods) and the share of multi-method commits the
    cap excludes from pair counting."""
    sizes = [len(c.get("changed_methods") or []) for c in commits]
    multi = [s for s in sizes if s >= 2]
    capped = [s for s in multi if s > max_commit_size]
    times = [c["atime"] for c in commits if c.get("atime")]
    return {
        "n_commits": len(commits),
        "n_informative": sum(1 for s in sizes if s >= 1),
        "days": round((max(times) - min(times)) / 86400, 1) if len(times) > 1 else 0.0,
        "median_size": _median(sizes), "p90_size": _quantile(sizes, 0.9),
        "n_multi": len(multi), "n_capped": len(capped),
        "share_capped": round(len(capped) / len(multi), 3) if multi else 0.0,
    }


def curveball(sets: list, rng: random.Random, trades: int) -> None:
    """In-place curveball trades (Strona et al. 2014) on the commit x method incidence:
    each trade picks two commits and re-deals the methods only one of them has, so every
    commit keeps its size and every method its change frequency while who-changes-with-whom
    is randomised. Uniform over the fixed-marginal space once mixed."""
    n = len(sets)
    if n < 2:
        return
    for _ in range(trades):
        i, j = rng.randrange(n), rng.randrange(n)
        if i == j:
            continue
        A, B = sets[i], sets[j]
        a_only, b_only = A - B, B - A
        if not a_only or not b_only:
            continue
        pool = list(a_only | b_only)
        rng.shuffle(pool)
        both = A & B
        sets[i] = both | set(pool[:len(a_only)])
        sets[j] = both | set(pool[len(a_only):])


def curveball_stratified(sets: list, rng: random.Random, trades: int, file_of: dict) -> None:
    """Curveball trades WITHIN FILES: each trade picks a file and two commits that both touch
    it, and re-deals that file's methods between them. Every commit keeps its size per file
    (so same-file co-change, the strongest regularity in any history, is preserved), every
    method keeps its change frequency, and only WHICH methods of a file change together is
    randomised. This is the null that asks whether threading picks out the co-changing
    pairs, rather than whether co-change concentrates at all."""
    by_file: dict = defaultdict(list)
    for i, s in enumerate(sets):
        for f in {file_of.get(m, m[0]) for m in s}:
            by_file[f].append(i)
    files = [f for f, cs in by_file.items() if len(cs) >= 2]
    if not files:
        return
    for _ in range(trades):
        f = files[rng.randrange(len(files))]
        cs = by_file[f]
        i, j = cs[rng.randrange(len(cs))], cs[rng.randrange(len(cs))]
        if i == j:
            continue
        A, B = sets[i], sets[j]
        Af = {m for m in A if file_of.get(m, m[0]) == f}
        Bf = {m for m in B if file_of.get(m, m[0]) == f}
        a_only, b_only = Af - Bf, Bf - Af
        if not a_only or not b_only:
            continue
        pool = list(a_only | b_only)
        rng.shuffle(pool)
        both = Af & Bf
        sets[i] = (A - Af) | both | set(pool[:len(a_only)])
        sets[j] = (B - Bf) | both | set(pool[len(a_only):])


# ---------------------------------------------------------------------------
# the sinew analysis
# ---------------------------------------------------------------------------
def _resolve(M: dict, by_name: dict, src: tuple, callee: str) -> list:
    """The method keys a call to `callee` from `src` may target: the methods of that name in
    the caller's own file if there are any, else every method of that name in the project.
    Lexical resolution, so a common name is ambiguous; same-file first keeps it honest."""
    cands = by_name.get(callee, ())
    same = [mk for mk in cands if mk[0] == src[0] and mk != src]
    return same or [mk for mk in cands if mk != src]


def thread_links(M: dict, max_hops: int = 0) -> dict:
    """frozenset({a, b}) -> {"keys": set, "hops": int}: the parameter keys THREADED between
    two methods. Key K threads from a to b when a forwards K to a call that resolves to b and
    b declares K (hops 1), or when it reaches b along a chain of such hops in which every
    intermediary declares K and forwards it on (hops > 1): the data one sends and the other
    consumes, directly or through a conduit. A key both merely declare, or that a forwards
    somewhere else, is NOT a link. `max_hops` 0 = unbounded."""
    by_name: dict = defaultdict(list)
    for mk, m in M.items():
        by_name[m["name"]].append(mk)
    # per key: a -> the methods it hands that key to
    step: dict = defaultdict(lambda: defaultdict(set))
    for mk, m in M.items():
        for callee, keys in m["fwd_to"].items():
            for tgt in _resolve(M, by_name, mk, callee):
                for key in keys:
                    if key in M[tgt]["param_keys"]:
                        step[key][mk].add(tgt)
    links: dict = {}
    for key, adj in step.items():
        for src in adj:
            seen = {src: 0}
            frontier = [src]
            while frontier:
                nxt = []
                for u in frontier:
                    d = seen[u] + 1
                    if max_hops and d > max_hops:
                        continue
                    for v in adj.get(u, ()):
                        if v not in seen:
                            seen[v] = d
                            nxt.append(v)
                frontier = nxt
            for v, d in seen.items():
                if v == src:
                    continue
                pr = frozenset((src, v))
                ln = links.setdefault(pr, {"keys": set(), "hops": d})
                ln["keys"].add(key)
                ln["hops"] = min(ln["hops"], d)
    return links


def thread_keys(links: dict, a: tuple, b: tuple) -> set:
    """The keys threaded between two methods (empty if they are not thread-linked)."""
    ln = links.get(frozenset((a, b)))
    return ln["keys"] if ln else set()


def call_pairs(M: dict) -> set:
    """Unordered pairs (a, b) where a calls b by resolved name (any internal call, forwarding
    or not): the structural call coupling that thread links are a subset of, and the
    counterfactual the paper compares them against."""
    by_name: dict = defaultdict(list)
    for mk, m in M.items():
        by_name[m["name"]].append(mk)
    out: set = set()
    for mk, m in M.items():
        for callee in m["calls_to"]:
            for tgt in _resolve(M, by_name, mk, callee):
                out.add(frozenset((mk, tgt)))
    return out


def _label(key: str) -> str:
    return key[2:] if key[:2] in ("T:", "N:") else key


def methods_at_head(rows: list) -> dict:
    """method key -> the node's shape at HEAD. Overloads share one node and take the WIDEST
    overload's values (max, never a sum, so a delegating overload chain is not inflated) and
    the union of parameter and forwarded keys."""
    M: dict = {}
    for r in rows:
        mk = (r["file"], r["qualname"])
        fwd_to: dict = defaultdict(set)
        calls_to: set = set()
        key_of = dict(zip(r.get("param_names", []), r.get("param_keys", [])))
        for c in r.get("calls", []):
            if c.get("kind") != "internal" or c["callee"] in GUARD_CALLEES:
                continue
            calls_to.add(c["callee"])
            if c.get("fwd"):
                fwd_to[c["callee"]] |= {key_of.get(n, "N:" + n) for n in c["fwd"]}
        cur = {
            "IN": r.get("params", 0),
            "OUT": r.get("out_args_own", 0) + r.get("out_args_ext", 0),
            "fwd_args": r.get("fwd_args", 0), "fwd_ratio": r.get("fwd_ratio", 0.0),
            "fwd_ratio_any": r.get("fwd_ratio_any", 0.0),
            "fwd_ratio_strict": r.get("fwd_ratio_strict", 0.0),
            "n_calls_own": r.get("n_calls_own", 0),
            "fwd_keys": set(r.get("fwd_keys", [])),
            "param_keys": set(r.get("param_keys", [])),
            "param_names": set(r.get("param_names", [])),
            "fwd_to": fwd_to, "calls_to": calls_to,
            "cc": r.get("cc", 0), "nloc": r.get("nloc", 0),
            "name": r["name"], "qualname": r["qualname"], "file": r["file"],
            "n_overloads": 1,
        }
        m = M.get(mk)
        if m is None:
            M[mk] = cur
            continue
        for k in ("IN", "OUT", "fwd_args", "fwd_ratio", "fwd_ratio_any", "fwd_ratio_strict",
                  "n_calls_own", "cc", "nloc"):
            m[k] = max(m[k], cur[k])
        for k in ("fwd_keys", "param_keys", "param_names", "calls_to"):
            m[k] |= cur[k]
        for callee, keys in fwd_to.items():
            m["fwd_to"][callee] |= keys
        m["n_overloads"] += 1
    return M


def _bonds(pair: Counter, change_count: Counter, M: dict, min_k: int,
           links: dict) -> tuple[list, int]:
    """(bonds, n_unthreaded). A bond is a co-changing pair (k >= min_k, both alive at HEAD)
    that is thread-linked (see thread_links); n_unthreaded counts the co-changing pairs that
    thread nothing (co-change without data flow, which this method does not call a sinew)."""
    bonds, unthreaded = [], 0
    for pr, k in pair.items():
        if k < min_k:
            continue
        a, b = tuple(pr)
        if a not in M or b not in M:
            continue
        ln = links.get(pr)
        if not ln:
            unthreaded += 1
            continue
        keys = ln["keys"]
        thick = len(keys)
        fa, fb = change_count.get(a, k), change_count.get(b, k)
        bonds.append({
            "mass": k * thick, "k": k, "thick": thick, "a": a, "b": b, "keys": keys,
            "conf": round(k / max(1, min(fa, fb)), 3),
            "hops": ln["hops"], "direct": ln["hops"] == 1,
            "same_file": a[0] == b[0],
        })
    bonds.sort(key=lambda t: t["mass"], reverse=True)
    return bonds, unthreaded


def _heavy(bonds: list, M: dict, p: dict) -> dict:
    """Heavy bonds (modified z > heavy_madz among this project's bond masses), their graph,
    and its components as spans. Returns the summary statistics the null is compared on, plus
    the spans themselves."""
    masses = [b["mass"] for b in bonds]
    if p.get("heavy_rule") == "quantile":        # sensitivity: top (1 - heavy_q) by mass
        thr = max(_quantile(masses, p["heavy_q"]), p["heavy_q_min_mass"]) - 1e-9
    else:
        med = _median(masses)
        mad = _median([abs(x - med) for x in masses]) or 1.0
        thr = med + (p["heavy_madz"] / 0.6745) * mad
    heavy = [b for b in bonds if b["mass"] > thr] if bonds else []
    adj: dict = defaultdict(set)
    nodes: set = set()
    for b in heavy:
        adj[b["a"]].add(b["b"])
        adj[b["b"]].add(b["a"])
        nodes.update((b["a"], b["b"]))
    spans = []
    for comp in _components(nodes, adj):
        cs = set(comp)
        ce = [b for b in heavy if b["a"] in cs and b["b"] in cs]
        span = len(comp)
        poss = span * (span - 1) / 2
        degs = {mk: len(adj[mk] & cs) for mk in comp}
        hub = max(degs.values()) / (span - 1) if span > 1 else 0.0
        threaded: Counter = Counter()
        for b in ce:
            for key in b["keys"]:
                threaded[key] += 1
        density = len(ce) / poss if poss else 0.0
        concern = span >= p["span_concern"]
        kind = ("contract" if not concern else
                "duplication-clique" if density >= p["clique_density"] else "god-hub")
        members = sorted(comp, key=lambda mk: degs[mk], reverse=True)
        spans.append({
            "span": span, "links": len(ce), "mass": sum(b["mass"] for b in ce),
            "density": round(density, 2), "hubness": round(hub, 2),
            "files": len({M[mk]["file"] for mk in comp}),
            "mean_fwd_ratio": round(sum(M[mk]["fwd_ratio"] for mk in comp) / span, 2),
            "n_conduits": sum(1 for mk in comp if M[mk].get("conduit")),
            "threaded": [[_label(k), n] for k, n in
                         sorted(threaded.items(), key=lambda kv: (-kv[1], kv[0]))],
            "concern": concern, "kind": kind,
            "verdict": "CONCERN" if concern else "cohesion",
            "members": [(M[mk]["name"], M[mk]["IN"], M[mk]["OUT"], M[mk]["cc"],
                         M[mk]["fwd_ratio"], degs[mk]) for mk in members],
            "member_keys": [list(mk) for mk in members],
        })
    spans.sort(key=lambda s: (s["span"], s["links"]), reverse=True)
    return {
        "threshold": round(thr, 1), "n_heavy": len(heavy),
        "heavy_mass": sum(b["mass"] for b in heavy),
        "max_span": max((s["span"] for s in spans), default=0),
        "n_concern": sum(1 for s in spans if s["concern"]),
        "max_cluster_mass": max((s["mass"] for s in spans), default=0),
        "n_bonds": len(bonds), "bond_mass": sum(masses),
        "n_heavy_cross_file": sum(1 for b in heavy if not b["same_file"]),
        "spans": spans, "heavy_bonds": heavy,
    }


_NULL_STATS = ("n_bonds", "bond_mass", "n_heavy", "heavy_mass", "max_span", "n_concern",
               "max_cluster_mass")


def null_model(sets: list, M: dict, p: dict, observed: dict, B: int, seed: int,
               links: dict, strata: str | None = None) -> dict:
    """Draw B permutations of the change sets and run the same bond / heavy / span pipeline
    on each. `strata` None: fixed-marginal curveball (commit sizes and method frequencies
    kept). `strata` "file": the within-file curveball (per-commit, per-file counts kept as
    well; the H2 null). For each statistic: the null mean, its 95th percentile, and the
    one-sided p-value (1 + #null >= observed) / (1 + B)."""
    if B <= 0 or not sets:
        return {"samples": 0}
    rng = random.Random(seed)
    work = [set(s) for s in sets]
    if strata == "file":
        file_of = {m: m[0] for s in work for m in s}
        n = sum(len({m[0] for m in s}) for s in work)       # (commit, file) incidences
        shuffle = lambda t: curveball_stratified(work, rng, t, file_of)  # noqa: E731
    else:
        n = len(work)
        shuffle = lambda t: curveball(work, rng, t)  # noqa: E731
    shuffle(10 * n)
    draws: dict = {k: [] for k in _NULL_STATS}
    for _ in range(B):
        shuffle(5 * n)
        cc, pair = pair_counts(work, p["max_commit_size"])
        bonds, _ = _bonds(pair, cc, M, p["min_k"], links)
        h = _heavy(bonds, M, p)
        for k in _NULL_STATS:
            draws[k].append(h[k])
    out = {"samples": B, "strata": strata}
    for k in _NULL_STATS:
        xs = draws[k]
        obs = observed[k]
        out[k] = {"observed": obs, "null_mean": round(sum(xs) / len(xs), 2),
                  "null_q95": _quantile(xs, 0.95), "null_max": max(xs),
                  "p": round((1 + sum(1 for x in xs if x >= obs)) / (1 + B), 4)}
    return out


def sinew(base: str, max_commit_size: int | None = None, min_k: int | None = None,
          null_samples: int | None = None, bootstrap: int | None = None,
          seed: int | None = None, rows: list | None = None,
          commits: list | None = None, params: dict | None = None) -> dict:
    """The sinew analysis of one mine (see module doc). `rows` / `commits` may be passed
    pre-loaded so several analyses of one mine read it once."""
    p = dict(PARAMS, **(params or {}))
    if max_commit_size is not None:
        p["max_commit_size"] = max_commit_size
    if min_k is not None:
        p["min_k"] = min_k
    if null_samples is not None:
        p["null_samples"] = null_samples
    if bootstrap is not None:
        p["bootstrap"] = bootstrap
    if seed is not None:
        p["seed"] = seed
    rows = head_rows(base) if rows is None else rows
    commits = _commits(base) if commits is None else commits
    sets = change_sets(commits)
    change_count, pair = pair_counts(sets, p["max_commit_size"])

    M = methods_at_head(rows)
    for m in M.values():
        m["net"] = m["OUT"] - m["IN"]
        m["joint"] = min(m["IN"], m["OUT"])
        m["conduit"] = m["fwd_ratio"] >= p["conduit_ratio"] and m["IN"] >= p["conduit_min_params"]
        m["freq"] = change_count.get((m["file"], m["qualname"]), 0)
    links = thread_links(M)

    # co-change degree and THREAD degree among methods alive at HEAD
    partners: Counter = Counter()
    tpartners: Counter = Counter()
    for pr in pair:
        a, b = tuple(pr)
        if a in M and b in M:
            partners[a] += 1
            partners[b] += 1
            if pr in links:
                tpartners[a] += 1
                tpartners[b] += 1
    for mk, m in M.items():
        m["degree"] = partners.get(mk, 0)
        m["thread_degree"] = tpartners.get(mk, 0)

    # directional typology (descriptive) by upper-tercile thresholds on IN and OUT
    qin = _quantile([m["IN"] for m in M.values()], 2 / 3)
    qout = _quantile([m["OUT"] for m in M.values()], 2 / 3)
    for m in M.values():
        hi_in, hi_out = m["IN"] >= qin and m["IN"] > 0, m["OUT"] >= qout and m["OUT"] > 0
        m["class"] = ("joint" if hi_in and hi_out else "source" if hi_out else
                      "sink" if hi_in else "minor")
    cls_deg: dict = defaultdict(list)
    for m in M.values():
        cls_deg[m["class"]].append(m["degree"])
    typology = {c: {"n": len(v), "mean_degree": round(sum(v) / len(v), 3) if v else 0.0}
                for c, v in cls_deg.items()}
    cond = [m["degree"] for m in M.values() if m["conduit"]]
    noncond = [m["degree"] for m in M.values() if not m["conduit"]]
    typology["conduit"] = {"n": len(cond),
                           "mean_degree": round(sum(cond) / len(cond), 3) if cond else 0.0}
    typology["non-conduit"] = {"n": len(noncond), "mean_degree":
                               round(sum(noncond) / len(noncond), 3) if noncond else 0.0}

    # controls: does threading predict entanglement BEYOND change frequency, size,
    # complexity, call out-degree and parameter count? (H1 is the fwd_ratio ~ degree partial
    # given all five; the three-control partial is kept as a secondary.) A method that
    # forwards must call, and a method with more parameters has more to forward, so without
    # the last two the partial would re-test call coupling and interface size.
    ms = list(M.values())
    col = lambda k: [m[k] for m in ms]  # noqa: E731
    deg, freq, nloc, cc = col("degree"), col("freq"), col("nloc"), col("cc")
    ratio, fwd, IN, OUT = col("fwd_ratio"), col("fwd_args"), col("IN"), col("OUT")
    calls = col("n_calls_own")
    three = [freq, nloc, cc]
    full = [freq, nloc, cc, calls, IN]
    files = col("file")
    controls = {
        "fwd_ratio ~ degree | freq,nloc,cc,calls,params (partial)":
            partial_spearman(ratio, deg, full),
        "fwd_ratio_strict ~ degree | freq,nloc,cc,calls,params (partial)":
            partial_spearman(col("fwd_ratio_strict"), deg, full),
        "fwd_ratio_any ~ degree | freq,nloc,cc,calls,params (partial)":
            partial_spearman(col("fwd_ratio_any"), deg, full),
        "calls ~ degree | freq,nloc,cc (partial)": partial_spearman(calls, deg, three),
        "params ~ degree | freq,nloc,cc (partial)": partial_spearman(IN, deg, three),
        "fwd_ratio ~ degree (raw)": spearman(ratio, deg),
        "fwd_args ~ degree (raw)": spearman(fwd, deg),
        "freq ~ degree (raw)": spearman(freq, deg),
        "nloc ~ degree (raw)": spearman(nloc, deg),
        "cc ~ degree (raw)": spearman(cc, deg),
        "IN ~ degree | cc (partial)": partial_spearman(IN, deg, [cc]),
        "OUT ~ degree | cc (partial)": partial_spearman(OUT, deg, [cc]),
        "fwd_args ~ degree | cc (partial)": partial_spearman(fwd, deg, [cc]),
        "fwd_args ~ degree | nloc (partial)": partial_spearman(fwd, deg, [nloc]),
        "fwd_ratio ~ degree | cc (partial)": partial_spearman(ratio, deg, [cc]),
        "fwd_ratio ~ degree | freq,nloc,cc (partial)": partial_spearman(ratio, deg, three),
        "fwd_args ~ degree | freq,nloc,cc (partial)": partial_spearman(fwd, deg, three),
        "fwd_ratio ~ thread_degree | freq,nloc,cc (partial)":
            partial_spearman(ratio, col("thread_degree"), three),
        "fwd_ratio_any ~ degree | freq,nloc,cc (partial)":
            partial_spearman(col("fwd_ratio_any"), deg, three),
    }
    n = len(ms)

    def _primary(idx):
        return partial_spearman([ratio[i] for i in idx], [deg[i] for i in idx],
                                [[z[i] for i in idx] for z in full])
    # the interval resamples FILES (cluster bootstrap), not methods
    controls["fwd_ratio ~ degree | freq,nloc,cc,calls,params (partial) ci95"] = bootstrap_ci(
        _primary, n, p["bootstrap"], p["seed"], clusters=files)
    controls["n_files"] = len(set(files))

    # bonds, heavy bonds, spans, and the two nulls
    bonds, unthreaded = _bonds(pair, change_count, M, p["min_k"], links)
    masses = [b["mass"] for b in bonds]
    total = sum(masses) or 1.0
    dec = max(1, len(masses) // 10)
    h = _heavy(bonds, M, p)
    hq = _heavy(bonds, M, {**p, "heavy_rule": "quantile"})
    null = null_model(sets, M, p, h, p["null_samples"], p["seed"], links)
    null_file = null_model(sets, M, p, h, p["null_samples"], p["seed"], links, strata="file")

    # the counterfactual: among CALL-linked pairs (a calls b), do the thread-linked ones
    # co-change more often than the ones that pass no parameter along?
    cf = {"threaded": [0, 0, 0], "unthreaded": [0, 0, 0]}   # [pairs, co-changing, sum k]
    for pr in call_pairs(M):
        k = pair.get(pr, 0)
        cell = cf["threaded" if pr in links else "unthreaded"]
        cell[0] += 1
        cell[1] += k >= p["min_k"]
        cell[2] += k
    counterfactual = {
        g: {"pairs": v[0], "cochanging": v[1],
            "p_cochange": round(v[1] / v[0], 4) if v[0] else None,
            "mean_k": round(v[2] / v[0], 3) if v[0] else None}
        for g, v in cf.items()}
    pt, pu = counterfactual["threaded"]["p_cochange"], counterfactual["unthreaded"]["p_cochange"]
    counterfactual["ratio"] = round(pt / pu, 3) if pt is not None and pu else None
    counterfactual["n_thread_links"] = len(links)

    heavy = {
        "n_cochange_pairs": len(bonds) + unthreaded,
        "n_pairs": len(bonds), "n_unthreaded": unthreaded,
        "share_threaded": round(len(bonds) / (len(bonds) + unthreaded), 3)
        if (bonds or unthreaded) else 0.0,
        "n_direct": sum(1 for b in bonds if b["direct"]),
        "n_same_file": sum(1 for b in bonds if b["same_file"]),
        "hops": {str(k): v for k, v in sorted(Counter(b["hops"] for b in bonds).items())},
        "n_heavy_cross_file": h["n_heavy_cross_file"],
        "heavy_quantile": {"threshold": hq["threshold"], "n_heavy": hq["n_heavy"],
                           "heavy_mass": hq["heavy_mass"], "max_span": hq["max_span"],
                           "n_concern": hq["n_concern"]},
        "counterfactual": counterfactual,
        "top_decile_share": round(sum(masses[:dec]) / total, 3),
        "top_1pct_share": round(sum(masses[:max(1, len(masses) // 100)]) / total, 3),
        "heavy_threshold": h["threshold"], "n_heavy_madz": h["n_heavy"],
        "heavy_mass": h["heavy_mass"], "max_span": h["max_span"], "n_concern": h["n_concern"],
        "top": [{"heaviness": b["mass"], "k": b["k"], "thick": b["thick"], "conf": b["conf"],
                 "direct": b["direct"], "shared": sorted(_label(x) for x in b["keys"]),
                 "a": (M[b["a"]]["name"], M[b["a"]]["IN"], M[b["a"]]["OUT"], M[b["a"]]["cc"],
                       M[b["a"]]["fwd_ratio"]),
                 "b": (M[b["b"]]["name"], M[b["b"]]["IN"], M[b["b"]]["OUT"], M[b["b"]]["cc"],
                       M[b["b"]]["fwd_ratio"])}
                for b in bonds[:15]],
        "spans": h["spans"], "null": null, "null_file": null_file,
        "bonds": [{"a": M[b["a"]]["name"], "b": M[b["b"]]["name"], "k": b["k"],
                   "thick": b["thick"], "mass": b["mass"], "hops": b["hops"],
                   "same_file": b["same_file"], "keys": sorted(_label(x) for x in b["keys"])}
                  for b in bonds],
    }

    inv = [m["IN"] for m in ms]
    return {
        "base": base, "params": p, "n_methods": n, "qIN": qin, "qOUT": qout,
        "window": commit_window(commits, p["max_commit_size"]),
        "params_stats": {"mean": round(sum(inv) / n, 3) if n else 0.0, "median": _median(inv),
                         "p90": _quantile(inv, 0.9), "max": max(inv, default=0),
                         "mean_fwd_ratio": round(sum(ratio) / n, 3) if n else 0.0,
                         "share_typed_keys": round(sum(
                             1 for m in ms for k in m["param_keys"] if k.startswith("T:"))
                             / max(1, sum(len(m["param_keys"]) for m in ms)), 3)},
        "n_joints": typology.get("joint", {}).get("n", 0),
        "n_conduits": typology["conduit"]["n"],
        "typology": typology, "controls": controls, "heavy": heavy,
    }


def _fmt(v) -> str:
    return "  n/a" if v is None else f"{v:+.3f}"


def _print_sinew(base: str, max_commit_size: int | None = None,
                 null_samples: int | None = None) -> None:
    s = sinew(base, max_commit_size, null_samples=null_samples)
    p = s["params"]
    print(f"\n=== sinew: {base} ===")
    pm = s["params_stats"]
    w = s["window"]
    print(f"    window: {w['n_commits']} commits over {w['days']} days; commit size median "
          f"{w['median_size']}, p90 {w['p90_size']}; {w['n_capped']}/{w['n_multi']} "
          f"multi-method commits above the cap of {p['max_commit_size']} "
          f"({w['share_capped']:.0%}) excluded from pairs")
    print(f"    declared parameters per method: mean {pm['mean']}, median {pm['median']}, "
          f"p90 {pm['p90']}, max {pm['max']}; mean forward ratio {pm['mean_fwd_ratio']}; "
          f"{pm['share_typed_keys']:.0%} of parameter keys are types")
    print(f"    {s['n_methods']} methods; {s['n_joints']} joints; {s['n_conduits']} conduits "
          f"(fwd_ratio>={p['conduit_ratio']}, params>={p['conduit_min_params']})")
    print("\n[1] TYPOLOGY  (co-change degree by flow class, and by conduit-ness)")
    print(f"    {'class':>11} {'n':>6} {'mean co-change degree':>22}")
    for c in ("joint", "source", "sink", "minor", "conduit", "non-conduit"):
        t = s["typology"].get(c)
        if t:
            print(f"    {c:>11} {t['n']:>6} {t['mean_degree']:>22}")
    print("\n[2] CONTROLS  (is threading entangled beyond change frequency, size, complexity?)")
    for k, v in s["controls"].items():
        if k.endswith("ci95"):
            print(f"    {k:52} {v}")
        else:
            print(f"    {k:52} {_fmt(v)}")
    h = s["heavy"]
    print(f"\n[3] BONDS  (co-change k>={p['min_k']} AND thread-linked)")
    print(f"    {h['n_cochange_pairs']} co-changing pairs, {h['n_pairs']} thread-linked "
          f"({h['share_threaded']:.0%}), {h['n_direct']} direct, {h['n_same_file']} same-file; "
          f"top-decile holds {h['top_decile_share'] * 100:.0f}% of mass; "
          f"{h['n_heavy_madz']} heavy (MAD-z>{p['heavy_madz']}, mass>{h['heavy_threshold']}; "
          f"{h['n_heavy_cross_file']} cross-file); quantile rule: "
          f"{h['heavy_quantile']['n_heavy']} heavy, max span {h['heavy_quantile']['max_span']}")
    cf = h["counterfactual"]
    print(f"    call-linked pairs: thread-linked {cf['threaded']['pairs']} "
          f"(P(co-change)={cf['threaded']['p_cochange']}) vs unthreaded "
          f"{cf['unthreaded']['pairs']} (P={cf['unthreaded']['p_cochange']}); ratio {cf['ratio']}")
    print(f"    {'heavy':>6} {'k':>3} {'thick':>5} {'conf':>5} {'dir':>3}  "
          f"{'method A (IN/OUT/cc/fwd)':<34} {'method B (IN/OUT/cc/fwd)':<34} threads")
    for t in h["top"]:
        an, ai, ao, ac, af = t["a"]
        bn, bi, bo, bc, bf = t["b"]
        a = f"{an[:18]} {ai}/{ao}/{ac}/{af:.2f}"
        b = f"{bn[:18]} {bi}/{bo}/{bc}/{bf:.2f}"
        print(f"    {t['heaviness']:>6} {t['k']:>3} {t['thick']:>5} {t['conf']:>5} "
              f"{'y' if t['direct'] else '-':>3}  {a:<34} {b:<34} {', '.join(t['shared'])}")
    print("\n[4] SPANS  (components of the heavy-bond graph; "
          f"span>={p['span_concern']} = CONCERN)")
    print(f"    {'span':>4} {'links':>5} {'mass':>5} {'dens':>4} {'hub':>4} {'files':>5} "
          f"{'verdict':>9} {'kind':>18}  threads | members (IN/OUT/cc/fwd)")
    for sp in h["spans"]:
        mem = ", ".join(f"{nm[:16]}({i}/{o}/{cc}/{fr:.1f})"
                        for nm, i, o, cc, fr, _ in sp["members"][:5])
        thr = ",".join(k for k, _ in sp["threaded"][:4]) or "-"
        print(f"    {sp['span']:>4} {sp['links']:>5} {sp['mass']:>5} {sp['density']:>4} "
              f"{sp['hubness']:>4} {sp['files']:>5} {sp['verdict']:>9} {sp['kind']:>18}  "
              f"{thr} | {mem}")
    for label, nl in (("WITHIN-FILE null (H2: per-commit, per-file counts kept)", h["null_file"]),
                      ("fixed-marginal null (secondary)", h["null"])):
        print(f"\n[5] {label}: {nl.get('samples', 0)} permutations; p = P(null >= observed)")
        if nl.get("samples"):
            print(f"    {'statistic':>17} {'observed':>9} {'null mean':>10} {'null q95':>9} {'p':>7}")
            for k in _NULL_STATS:
                d = nl[k]
                print(f"    {k:>17} {d['observed']:>9} {d['null_mean']:>10} {d['null_q95']:>9} "
                      f"{d['p']:>7}")


# ---------------------------------------------------------------------------
# EXPLORATORY: relationships and tangle, over NAME-resolved call edges
# ---------------------------------------------------------------------------
def internal_edges(rows: list) -> set:
    """Undirected structural call pairs for the NAME-resolved internal edges at one snapshot
    (self-loops dropped). Exploratory: see module doc."""
    targets = _Targets(rows)
    edges: set = set()
    for r in rows:
        caller = (r["file"], r["qualname"])
        for e in r.get("calls", []):
            for mk in targets(e):
                if mk != caller:
                    edges.add(frozenset((caller, mk)))
    return edges


def relationships(base: str, max_commit_size: int = PARAMS["max_commit_size"]) -> dict:
    rows = head_rows(base)
    inc = in_coupling(rows)
    mc = Counter((r["file"], r["qualname"]) for r in rows)
    meth = []
    for r in rows:
        mk = (r["file"], r["qualname"])
        ia = inc.get(mk, {}).get("in_args", 0.0) / mc[mk]
        meth.append({"mk": mk, "cc": r.get("cc", 0), "cognitive": r.get("cognitive", 0),
                     "nloc": r.get("nloc", 0), "nesting": r.get("nesting", 0),
                     "params": r.get("params", 0), "out_own": r.get("out_args_own", 0),
                     "out_ext": r.get("out_args_ext", 0), "in_args": ia,
                     "delta": r.get("out_args_own", 0) - ia})

    def col(k):
        return [m[k] for m in meth]
    cross = {}
    for cpl in ("out_own", "out_ext", "in_args", "delta"):
        for cx in ("cc", "cognitive", "nloc"):
            cross[f"{cpl} ~ {cx}"] = spearman(col(cpl), col(cx))
    change_count, pair = cochange(base, max_commit_size)
    edges = internal_edges(rows)
    n = len(meth)
    total_pairs = n * (n - 1) // 2
    co_pairs = set(pair)
    edge_co = len(edges & co_pairs)
    p_co = (len(co_pairs) / total_pairs) if total_pairs else 0
    p_co_given_edge = (edge_co / len(edges)) if edges else 0
    bridge = {
        "n_methods": n, "n_struct_edges": len(edges), "n_cochange_pairs": len(co_pairs),
        "edge_and_cochange": edge_co,
        "P(cochange)": round(p_co, 5),
        "P(cochange|edge)": round(p_co_given_edge, 5),
        "lift": round(p_co_given_edge / p_co, 2) if p_co else None,
        "P(edge|cochange)": round(edge_co / len(co_pairs), 4) if co_pairs else None,
    }
    partners: Counter = Counter()
    for pr in pair:
        a, b = tuple(pr)
        partners[a] += 1
        partners[b] += 1
    cf = [change_count.get(m["mk"], 0) for m in meth]
    deg = [partners.get(m["mk"], 0) for m in meth]
    coh = {
        "cc ~ change_freq": spearman(col("cc"), cf),
        "cc ~ cochange_degree": spearman(col("cc"), deg),
        "out_own ~ change_freq": spearman(col("out_own"), cf),
        "out_own ~ cochange_degree": spearman(col("out_own"), deg),
        "delta ~ change_freq": spearman(col("delta"), cf),
        "delta ~ cochange_degree": spearman(col("delta"), deg),
    }
    return {"base": base, "cross_sectional": cross, "bridge": bridge, "cohesion": coh}


def _print_relationships(base: str, max_commit_size: int = PARAMS["max_commit_size"]) -> None:
    rep = relationships(base, max_commit_size)

    def line(d):
        for k, v in d.items():
            print(f"    {k:26} {_fmt(v)}")
    print(f"\n=== relationships (EXPLORATORY, name-resolved edges): {base} ===")
    print("\n[1] COMPLEXITY <-> COUPLING  (Spearman over methods at HEAD)")
    line(rep["cross_sectional"])
    print("\n[2] COUPLING <-> COHESION  (co-change; structural edge vs co-change)")
    b = rep["bridge"]
    print(f"    {b['n_methods']} methods, {b['n_struct_edges']} structural edges, "
          f"{b['n_cochange_pairs']} co-changing pairs")
    print(f"    P(cochange)={b['P(cochange)']}  P(cochange|edge)={b['P(cochange|edge)']}  "
          f"LIFT={b['lift']}  P(edge|cochange)={b['P(edge|cochange)']}")
    print("\n[3] COMPLEXITY/COUPLING <-> COHESION  (Spearman over methods at HEAD)")
    line(rep["cohesion"])


def weighted_edges(rows: list) -> dict:
    """frozenset({caller, callee}) -> {"w", "n"} over NAME-resolved internal edges
    (exploratory)."""
    targets = _Targets(rows)
    edges: dict = defaultdict(lambda: {"w": 0.0, "n": 0.0})
    for r in rows:
        caller = (r["file"], r["qualname"])
        for e in r.get("calls", []):
            uniq = {mk for mk in targets(e) if mk != caller}
            if not uniq:
                continue
            share = 1.0 / len(uniq)
            for mk in uniq:
                ed = edges[frozenset((caller, mk))]
                ed["w"] += e.get("args", 0) * share
                ed["n"] += share
    return edges


def _param_history(base: str) -> dict:
    """mkey -> {first, last, grew, growth, pos_growth, age} for the DECLARED parameter count
    over each method's life, from one streaming pass."""
    hist: dict = {}
    for _, _, _, rows in iter_blocks(base):
        for r in rows:
            mk = (r["file"], r["qualname"])
            p = r.get("params", 0)
            h = hist.get(mk)
            if h is None:
                hist[mk] = {"first": p, "last": p, "pos_growth": 0, "age": 0}
            else:
                if p > h["last"]:
                    h["pos_growth"] += p - h["last"]
                h["last"] = p
                h["age"] += 1
    for h in hist.values():
        h["growth"] = h["last"] - h["first"]
        h["grew"] = h["growth"] > 0
    return hist


def tangle(base: str, max_commit_size: int = PARAMS["max_commit_size"]) -> dict:
    rows = head_rows(base)
    inc = in_coupling(rows)
    mc = Counter((r["file"], r["qualname"]) for r in rows)
    change_count, pair = cochange(base, max_commit_size)
    wedges = weighted_edges(rows)
    ew, ec = [], []
    for pr, ed in wedges.items():
        ew.append(ed["w"])
        ec.append(pair.get(pr, 0))
    n = len(rows)
    total_pairs = n * (n - 1) // 2
    p_co = (len(pair) / total_pairs) if total_pairs else 0
    buckets = [("w=0", 0, 0), ("w=1", 1, 1), ("w=2-3", 2, 3), ("w>=4", 4, 10 ** 9)]
    strat = []
    for label, lo, hi in buckets:
        sel = [pr for pr, ed in wedges.items() if lo <= ed["w"] <= hi]
        if not sel:
            strat.append({"bucket": label, "n_edges": 0, "p_cochange": None, "lift": None})
            continue
        co = sum(1 for pr in sel if pr in pair)
        pp = co / len(sel)
        strat.append({"bucket": label, "n_edges": len(sel), "p_cochange": round(pp, 4),
                      "lift": round(pp / p_co, 2) if p_co else None})
    partners: Counter = Counter()
    for pr in pair:
        a, b = tuple(pr)
        partners[a] += 1
        partners[b] += 1
    meth = []
    for r in rows:
        mk = (r["file"], r["qualname"])
        ia = inc.get(mk, {}).get("in_args", 0.0) / mc[mk]
        out_own = r.get("out_args_own", 0)
        meth.append({"mk": mk, "params": r.get("params", 0), "cc": r.get("cc", 0),
                     "traffic": r.get("params", 0) + out_own + ia,
                     "degree": partners.get(mk, 0), "freq": change_count.get(mk, 0)})

    def col(k):
        return [m[k] for m in meth]
    traffic = {
        "params ~ cochange_degree": spearman(col("params"), col("degree")),
        "params ~ change_freq": spearman(col("params"), col("freq")),
        "traffic ~ cochange_degree": spearman(col("traffic"), col("degree")),
        "traffic ~ change_freq": spearman(col("traffic"), col("freq")),
        "cc ~ cochange_degree": spearman(col("cc"), col("degree")),
    }
    hist = _param_history(base)
    at_head = {m["mk"] for m in meth}
    gr, dg, fq = [], [], []
    for mk, h in hist.items():
        if mk not in at_head:
            continue
        gr.append(h["pos_growth"])
        dg.append(partners.get(mk, 0))
        fq.append(change_count.get(mk, 0))
    grew = [partners.get(mk, 0) for mk, h in hist.items() if mk in at_head and h["grew"]]
    flat = [partners.get(mk, 0) for mk, h in hist.items() if mk in at_head and not h["grew"]]
    dynamic = {
        "param_growth ~ cochange_degree": spearman(gr, dg),
        "param_growth ~ change_freq": spearman(gr, fq),
        "n_grew": len(grew), "n_flat": len(flat),
        "mean_degree_grew": round(sum(grew) / len(grew), 3) if grew else None,
        "mean_degree_flat": round(sum(flat) / len(flat), 3) if flat else None,
    }
    return {"base": base, "n_methods": n, "n_edges": len(wedges),
            "edge_weight_vs_cochange": spearman(ew, ec),
            "P(cochange)": round(p_co, 5), "strata": strat,
            "traffic": traffic, "dynamic": dynamic}


def _print_tangle(base: str, max_commit_size: int = PARAMS["max_commit_size"]) -> None:
    t = tangle(base, max_commit_size)
    print(f"\n=== tangle (EXPLORATORY, name-resolved edges): {base} ===")
    print(f"    {t['n_methods']} methods, {t['n_edges']} structural edges, "
          f"P(cochange)={t['P(cochange)']}")
    print("\n[1] EDGE WEIGHT -> CO-CHANGE")
    print(f"    Spearman(edge parameter-weight, pair co-change count) = "
          f"{_fmt(t['edge_weight_vs_cochange'])}")
    print(f"    {'bucket':>7} {'edges':>6} {'P(cochange)':>12} {'lift':>6}")
    for s in t["strata"]:
        pp = "   n/a" if s["p_cochange"] is None else f"{s['p_cochange']:.4f}"
        lift = "  n/a" if s["lift"] is None else f"{s['lift']:.2f}"
        print(f"    {s['bucket']:>7} {s['n_edges']:>6} {pp:>12} {lift:>6}")
    print("\n[2] PARAMETER TRAFFIC -> ENTANGLEMENT")
    for k, v in t["traffic"].items():
        print(f"    {k:28} {_fmt(v)}")
    print("\n[3] DYNAMIC: do methods whose params GREW get more entangled?")
    d = t["dynamic"]
    for k in ("param_growth ~ cochange_degree", "param_growth ~ change_freq"):
        print(f"    {k:28} {_fmt(d[k])}")
    print(f"    mean co-change degree: grew={d['mean_degree_grew']} (n={d['n_grew']})  "
          f"vs flat={d['mean_degree_flat']} (n={d['n_flat']})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", help="mine base path (reads <base>.methods.jsonl / .commits.jsonl)")
    ap.add_argument("-o", "--csv", default=None, help="write the enriched panel CSV here")
    args = ap.parse_args(argv)
    if args.csv:
        n = to_csv(args.base, args.csv)
        print(f"wrote panel -> {args.csv} ({n} rows)")
    else:
        _print_sinew(args.base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
