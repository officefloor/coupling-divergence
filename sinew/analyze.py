"""Derive analysis-ready views from a raw history mine (sinew.mine output).

The mine is deliberately raw and method-centric. This layer DERIVES what it left out, so the
same mine can be looked at many ways without re-walking history:

  * in-coupling (fan-in) -- by resolving each INTERNAL outgoing edge to the callee method(s)
    it targets (match the edge's resolved receiver type + callee name to a method record at
    that same commit; an edge with no resolved type, as the lexical extractors emit, matches
    by callee name alone) and accumulating the arguments that arrive. Ambiguous matches
    (overloads / same-named methods) split the arguments equally and are flagged.
  * signed internal coupling divergence  delta = out_internal - in_internal  per method.
  * change-on-change -- each metric's delta from the method's PREVIOUS commit (identity is
    (file, qualname)), with is_new and age-in-commits.

Outputs a tidy per-(commit, method) panel as CSV (load it in pandas/R/whatever) and the sinew
analyses (`sinew`, `tangle`, `relate`). Pure standard library.
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import os
import sys
from collections import defaultdict


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
    (how mine.py writes it), so million-row mines analyse in bounded memory. Each method row
    already carries its own i/atime/sha, so no join with the commit index is needed."""
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


# ---------------------------------------------------------------------------
# in-coupling: resolve internal edges to target methods at one commit
# ---------------------------------------------------------------------------
class _Targets:
    """Resolve an internal call edge to the method record(s) it targets at one commit. A class
    is identified by its fully-qualified name (package + type) and by its simple name, so
    either form of a resolved `target` matches. An edge with no `target` (the lexical
    extractors, which know only the callee's name) matches every method of that name."""

    def __init__(self, rows: list) -> None:
        self.by_fqn: dict = defaultdict(list)       # (class_fqn, method_name)   -> [mkey]
        self.by_simple: dict = defaultdict(list)    # (class_simple, method_name) -> [mkey]
        self.by_name: dict = defaultdict(list)      # method_name                -> [mkey]
        for r in rows:
            mkey = (r["file"], r["qualname"])
            pkg, typ, name = r.get("package", ""), r.get("type", ""), r["name"]
            self.by_name[name].append(mkey)
            if typ:
                fqn = (pkg + "." if pkg else "") + typ
                self.by_fqn[(fqn, name)].append(mkey)
                self.by_simple[(typ.split(".")[-1], name)].append(mkey)

    def __call__(self, e: dict) -> set:
        """The distinct method keys an internal edge may target (empty if none recorded)."""
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
    """method key (file, qualname) -> {"in_args", "in_calls", "in_ambig"} for one commit:
    the arguments and calls arriving over resolved internal edges (see _Targets)."""
    targets = _Targets(rows)
    acc: dict = {(r["file"], r["qualname"]): {"in_args": 0.0, "in_calls": 0.0, "in_ambig": 0}
                 for r in rows}
    for r in rows:
        for e in r.get("calls", []):
            uniq = targets(e)                  # overloads share one (file,qualname) node: once
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
    "fwd_args", "fwd_params", "fwd_ratio",
    "changed", "churn_add", "is_new", "age",
    "d_cc", "d_cognitive", "d_nloc", "d_tokens", "d_nesting", "d_params",
    "d_out_own", "d_ext", "d_in", "d_delta", "d_fwd_args",
]


def panel(base: str):
    """Yield enriched rows (dicts keyed by PANEL_COLUMNS), streaming commit by commit so the
    whole mine never has to be materialised twice. `delta` is the signed internal coupling
    divergence out_internal - in_internal; `fwd_*` are the forward metrics (parameters handed
    onward unchanged); `d_*` are the change from this method's previous commit (0 and
    is_new=1 on first appearance)."""
    from collections import Counter
    last: dict = {}                            # mkey -> dict of prior metric values
    first_i: dict = {}
    for i_idx, atime, sha, rows in iter_blocks(base):
        inc = in_coupling(rows)
        # inbound is per (file,qualname) NODE; split it across the overload RECORDS that
        # share that id, so summing in_args over rows reproduces the node total (Σin <= Σout).
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
# statistics (pure python Spearman)
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


# ---------------------------------------------------------------------------
# relationships among the three dimensions: complexity, coupling, cohesion
# (cohesion = co-change, per the three-dimensions model: methods that change together)
# ---------------------------------------------------------------------------
def _commits(base: str) -> list:
    with open(base + ".commits.jsonl") as fh:
        cs = [json.loads(l) for l in fh]
    cs.sort(key=lambda c: c["i"])
    return cs


def internal_edges(rows: list) -> set:
    """Undirected structural call pairs frozenset({caller, callee}) for the resolved INTERNAL
    edges at one snapshot (self-loops dropped)."""
    targets = _Targets(rows)
    edges: set = set()
    for r in rows:
        caller = (r["file"], r["qualname"])
        for e in r.get("calls", []):
            for mk in targets(e):
                if mk != caller:
                    edges.add(frozenset((caller, mk)))
    return edges


def cochange(base: str, max_commit_size: int = 30):
    """(change_count, pair_count) from history. change_count[m] = commits that touched method
    m; pair_count[{a,b}] = commits that touched both. Commits touching more than
    max_commit_size methods (sweeping refactors/renames) are counted for per-method frequency
    but NOT for pairs, so a single mega-commit cannot fabricate co-change (Zimmermann)."""
    from collections import Counter
    change_count: Counter = Counter()
    pair: Counter = Counter()
    for c in _commits(base):
        cm = list(dict.fromkeys(tuple(m) for m in (c.get("changed_methods") or [])))
        for m in cm:
            change_count[m] += 1
        if 2 <= len(cm) <= max_commit_size:
            for i in range(len(cm)):
                for j in range(i + 1, len(cm)):
                    pair[frozenset((cm[i], cm[j]))] += 1
    return change_count, pair


def relationships(base: str, max_commit_size: int = 30) -> dict:
    from collections import Counter
    rows = head_rows(base)                            # HEAD snapshot (streamed)
    inc = in_coupling(rows)
    mc = Counter((r["file"], r["qualname"]) for r in rows)   # split inbound across overloads
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

    # (1) complexity <-> coupling, cross-sectional at HEAD
    cross = {}
    for cpl in ("out_own", "out_ext", "in_args", "delta"):
        for cx in ("cc", "cognitive", "nloc"):
            cross[f"{cpl} ~ {cx}"] = spearman(col(cpl), col(cx))

    # cohesion = co-change
    change_count, pair = cochange(base, max_commit_size)
    edges = internal_edges(rows)
    n = len(meth)
    total_pairs = n * (n - 1) // 2
    co_pairs = set(pair)
    # (2) coupling <-> cohesion bridge: do coupled pairs co-change more than chance?
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
    # (3) complexity/coupling <-> cohesion, per method
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


def _print_relationships(base: str, max_commit_size: int = 30) -> None:
    rep = relationships(base, max_commit_size)

    def line(d):
        for k, v in d.items():
            print(f"    {k:26} {'  n/a' if v is None else f'{v:+.3f}'}")
    print(f"\n=== relationships: {base} ===")
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


# ---------------------------------------------------------------------------
# tangle (F2): is co-change driven by the WEIGHT of the parameter coupling, and
# does it TIGHTEN as parameter counts grow? (The "sinew" hypothesis.)
#
#   The bridge in relationships() is binary -- edge present or not. This asks the
#   sharper question: a coupling edge carries a weight (the arguments that flow
#   across it). Do FAT edges (many parameters) co-change more than THIN ones, are
#   high-parameter-traffic methods the co-change hubs, and -- the dynamic claim --
#   do methods whose parameter counts GROW over their life become more entangled?
# ---------------------------------------------------------------------------
def weighted_edges(rows: list) -> dict:
    """frozenset({caller, callee}) -> {"w": total call-arguments flowing across the internal
    edges between the two methods (both directions summed), "n": number of resolved call
    sites}. Ambiguous targets split the arguments."""
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
    over each method's life, from one streaming pass. `growth` = last-first; `pos_growth` sums
    only the per-commit increases (so a param added then removed still counts as churn)."""
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


def tangle(base: str, max_commit_size: int = 30) -> dict:
    from collections import Counter
    rows = head_rows(base)
    inc = in_coupling(rows)
    mc = Counter((r["file"], r["qualname"]) for r in rows)
    change_count, pair = cochange(base, max_commit_size)
    wedges = weighted_edges(rows)

    # [1] edge weight vs co-change. For every structural edge at HEAD, its parameter weight
    # and how many commits the pair co-changed (0 if never). Does co-change rise with weight?
    ew, ec = [], []
    for pr, ed in wedges.items():
        ew.append(ed["w"])
        ec.append(pair.get(pr, 0))
    # stratified lift: P(co-change | edge in weight bucket) / P(co-change over all pairs)
    n = len(rows_keys := [(r["file"], r["qualname"]) for r in rows])
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
        p = co / len(sel)
        strat.append({"bucket": label, "n_edges": len(sel), "p_cochange": round(p, 4),
                      "lift": round(p / p_co, 2) if p_co else None})

    # [2] per-method parameter traffic vs co-change entanglement
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
        meth.append({
            "mk": mk, "params": r.get("params", 0), "cc": r.get("cc", 0),
            "in_args": ia, "out_own": out_own,
            "traffic": r.get("params", 0) + out_own + ia,   # all parameter flow touching it
            "degree": partners.get(mk, 0), "freq": change_count.get(mk, 0),
        })

    def col(k):
        return [m[k] for m in meth]
    traffic = {
        "params ~ cochange_degree": spearman(col("params"), col("degree")),
        "params ~ change_freq": spearman(col("params"), col("freq")),
        "traffic ~ cochange_degree": spearman(col("traffic"), col("degree")),
        "traffic ~ change_freq": spearman(col("traffic"), col("freq")),
        # control: is it coupling traffic specifically, or just size (cc)?
        "cc ~ cochange_degree": spearman(col("cc"), col("degree")),
    }

    # [3] the dynamic claim: do methods whose params GREW get more entangled?
    hist = _param_history(base)
    at_head = {m["mk"] for m in meth}
    gr, dg, fq = [], [], []
    for mk, h in hist.items():
        if mk not in at_head:
            continue
        gr.append(h["pos_growth"])
        dg.append(partners.get(mk, 0))
        fq.append(change_count.get(mk, 0))
    grew = [d for mk, h in hist.items() if mk in at_head and h["grew"]
            for d in (partners.get(mk, 0),)]
    flat = [partners.get(mk, 0) for mk, h in hist.items()
            if mk in at_head and not h["grew"]]
    dynamic = {
        "param_growth ~ cochange_degree": spearman(gr, dg),
        "param_growth ~ change_freq": spearman(gr, fq),
        "n_grew": len(grew), "n_flat": len(flat),
        "mean_degree_grew": round(sum(grew) / len(grew), 3) if grew else None,
        "mean_degree_flat": round(sum(flat) / len(flat), 3) if flat else None,
    }
    return {
        "base": base, "n_methods": n, "n_edges": len(wedges),
        "edge_weight_vs_cochange": spearman(ew, ec),
        "P(cochange)": round(p_co, 5), "strata": strat,
        "traffic": traffic, "dynamic": dynamic,
    }


def _print_tangle(base: str, max_commit_size: int = 30) -> None:
    t = tangle(base, max_commit_size)
    print(f"\n=== tangle (F2): {base} ===")
    print(f"    {t['n_methods']} methods, {t['n_edges']} structural edges, "
          f"P(cochange)={t['P(cochange)']}")
    print("\n[1] EDGE WEIGHT -> CO-CHANGE  (do fat coupling edges co-change more?)")
    ew = t["edge_weight_vs_cochange"]
    print(f"    Spearman(edge parameter-weight, pair co-change count) = "
          f"{'  n/a' if ew is None else f'{ew:+.3f}'}")
    print(f"    {'bucket':>7} {'edges':>6} {'P(cochange)':>12} {'lift':>6}")
    for s in t["strata"]:
        p = "   n/a" if s["p_cochange"] is None else f"{s['p_cochange']:.4f}"
        lift = "  n/a" if s["lift"] is None else f"{s['lift']:.2f}"
        print(f"    {s['bucket']:>7} {s['n_edges']:>6} {p:>12} {lift:>6}")
    print("\n[2] PARAMETER TRAFFIC -> ENTANGLEMENT  (Spearman over methods at HEAD)")
    for k, v in t["traffic"].items():
        print(f"    {k:28} {'  n/a' if v is None else f'{v:+.3f}'}")
    print("\n[3] DYNAMIC: do methods whose params GREW get more entangled?")
    d = t["dynamic"]
    for k in ("param_growth ~ cochange_degree", "param_growth ~ change_freq"):
        v = d[k]
        print(f"    {k:28} {'  n/a' if v is None else f'{v:+.3f}'}")
    print(f"    mean co-change degree: grew={d['mean_degree_grew']} (n={d['n_grew']})  "
          f"vs flat={d['mean_degree_flat']} (n={d['n_flat']})")


# ---------------------------------------------------------------------------
# sinew (F2, directional): the data-flow skeleton. Each method has a DIRECTION --
# OUT = args it forwards to callees (upstream source), IN = params it accepts
# (downstream sink); high on BOTH = a JOINT, a conduit that receives and forwards
# a lot. The forward metrics sharpen "conduit": fwd_ratio is the share of a method's
# own parameters it hands onward unchanged (tramp data). Joints chained by co-change
# form a "sinew network": the load-bearing knots where co-related change concentrates
# and refactoring gets expensive. Whether two co-changing methods also call each other
# is NOT a criterion: the cost of a sinew is in the threading, which a direct call edge
# neither creates nor removes, so no call resolution is needed here. Complexity-
# CONTROLLED, so the signal is not just "big methods cluster".
# ---------------------------------------------------------------------------
def _partial_spearman(x: list, y: list, z: list):
    """Partial Spearman of (x, y) controlling for z, from the three rank correlations."""
    rxy, rxz, ryz = spearman(x, y), spearman(x, z), spearman(y, z)
    if None in (rxy, rxz, ryz):
        return None
    denom = ((1 - rxz ** 2) * (1 - ryz ** 2)) ** 0.5
    return (rxy - rxz * ryz) / denom if denom > 0 else None


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
            for v in adj.get(u, ()):  # noqa: E1133
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(comp)
    return comps


# A conduit: at least this share of its own parameters handed onward unchanged, and at
# least this many parameters (a one-parameter pass-through is a wrapper, not a thread).
CONDUIT_RATIO = 0.5
CONDUIT_MIN_PARAMS = 2


def _threaded(members: list, M: dict, min_members: int = 2) -> list:
    """Parameter names forwarded by at least `min_members` of the cluster's members: the
    identifiers a cluster threads, the direct evidence of WHY it co-changes."""
    from collections import Counter
    cnt: Counter = Counter()
    for mk in members:
        for nm in M[mk]["fwd_names"]:
            cnt[nm] += 1
    return sorted(nm for nm, c in cnt.items() if c >= min_members)


def sinew(base: str, max_commit_size: int = 30, min_k: int = 2) -> dict:
    from collections import Counter
    rows = head_rows(base)
    change_count, pair = cochange(base, max_commit_size)

    M: dict = {}
    for r in rows:
        mk = (r["file"], r["qualname"])
        m = M.get(mk)
        if m is None:
            M[mk] = m = {
                "IN": r.get("params", 0),
                "OUT": r.get("out_args_own", 0) + r.get("out_args_ext", 0),
                "fwd_args": r.get("fwd_args", 0), "fwd_ratio": r.get("fwd_ratio", 0.0),
                "fwd_names": set(r.get("fwd_names", [])),
                "cc": r.get("cc", 0), "nloc": r.get("nloc", 0),
                "name": r["name"], "qualname": r["qualname"], "file": r["file"],
            }
        else:                              # overloads share a node: keep the widest shape
            m["IN"] = max(m["IN"], r.get("params", 0))
            m["OUT"] += r.get("out_args_own", 0) + r.get("out_args_ext", 0)
            m["fwd_args"] += r.get("fwd_args", 0)
            m["fwd_ratio"] = max(m["fwd_ratio"], r.get("fwd_ratio", 0.0))
            m["fwd_names"] |= set(r.get("fwd_names", []))
            m["cc"] = max(m["cc"], r.get("cc", 0))
            m["nloc"] += r.get("nloc", 0)
    for mk, m in M.items():
        m["net"] = m["OUT"] - m["IN"]
        m["joint"] = min(m["IN"], m["OUT"])
        m["conduit"] = m["fwd_ratio"] >= CONDUIT_RATIO and m["IN"] >= CONDUIT_MIN_PARAMS

    # co-change degree among methods alive at HEAD
    partners: Counter = Counter()
    for pr in pair:
        a, b = tuple(pr)
        if a in M and b in M:
            partners[a] += 1
            partners[b] += 1
    for mk, m in M.items():
        m["degree"] = partners.get(mk, 0)

    # directional typology by upper-tercile thresholds on IN and OUT
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
    # conduit typology (orthogonal to direction): methods that thread their parameters
    cond = [m["degree"] for m in M.values() if m["conduit"]]
    noncond = [m["degree"] for m in M.values() if not m["conduit"]]
    typology["conduit"] = {"n": len(cond),
                           "mean_degree": round(sum(cond) / len(cond), 3) if cond else 0.0}
    typology["non-conduit"] = {"n": len(noncond), "mean_degree":
                               round(sum(noncond) / len(noncond), 3) if noncond else 0.0}

    # complexity-controlled: does being a conduit predict entanglement BEYOND complexity?
    ms = list(M.values())
    jointv = [m["joint"] for m in ms]
    deg = [m["degree"] for m in ms]
    ccv = [m["cc"] for m in ms]
    nlocv = [m["nloc"] for m in ms]
    fwdv = [m["fwd_args"] for m in ms]
    ratiov = [m["fwd_ratio"] for m in ms]
    controls = {
        "jointness ~ degree (raw)": spearman(jointv, deg),
        "fwd_args ~ degree (raw)": spearman(fwdv, deg),
        "cc ~ degree (raw)": spearman(ccv, deg),
        "nloc ~ degree (raw)": spearman(nlocv, deg),
        "jointness ~ degree | cc (partial)": _partial_spearman(jointv, deg, ccv),
        "OUT ~ degree | cc (partial)": _partial_spearman([m["OUT"] for m in ms], deg, ccv),
        "IN ~ degree | cc (partial)": _partial_spearman([m["IN"] for m in ms], deg, ccv),
        "fwd_args ~ degree | cc (partial)": _partial_spearman(fwdv, deg, ccv),
        "fwd_args ~ degree | nloc (partial)": _partial_spearman(fwdv, deg, nlocv),
        "fwd_ratio ~ degree | cc (partial)": _partial_spearman(ratiov, deg, ccv),
    }

    # the sinew network: nodes = JOINTS, edges = co-change (k>=min_k) between two joints
    joints = {mk for mk, m in M.items() if m["class"] == "joint"}
    adj: dict = defaultdict(set)
    for pr, k in pair.items():
        if k < min_k:
            continue
        a, b = tuple(pr)
        if a in joints and b in joints:
            adj[a].add(b)
            adj[b].add(a)
    comps = [c for c in _components(joints, adj) if len(c) >= 2]
    comps.sort(key=lambda c: len(c), reverse=True)
    clusters = []
    for c in comps[:8]:
        nets = sorted(M[mk]["net"] for mk in c)
        n_edges = sum(len(adj[mk] & set(c)) for mk in c) // 2
        members = sorted(c, key=lambda mk: M[mk]["joint"], reverse=True)
        clusters.append({
            "size": len(c), "edges": n_edges,
            "net_span": [nets[0], nets[-1]],
            "threaded": _threaded(c, M),
            "members": [(M[mk]["name"], M[mk]["IN"], M[mk]["OUT"], M[mk]["degree"],
                         M[mk]["cc"], M[mk]["fwd_ratio"]) for mk in members[:6]],
        })

    # outlier joints: ranked by degree*jointness, each with its complexity percentile
    cc_sorted = sorted(m["cc"] for m in ms)
    nM = len(ms)

    def cc_pct(cc):
        import bisect
        return round(bisect.bisect_right(cc_sorted, cc) / nM, 2) if nM else None
    scored = [(m["degree"] * m["joint"], m) for m in ms if m["class"] == "joint"]
    scored.sort(key=lambda t: t[0], reverse=True)
    outliers = [{"name": m["name"], "IN": m["IN"], "OUT": m["OUT"], "net": m["net"],
                 "degree": m["degree"], "cc": m["cc"], "cc_pct": cc_pct(m["cc"]),
                 "fwd_ratio": m["fwd_ratio"], "score": round(s, 1)}
                for s, m in scored[:15]]

    # heavy sinews: thick plumbing (surface = IN+OUT) AND repeatedly co-changed. Not
    # conservation -- triage by thickness: find the few heavy bonds (hard to cut) amid the
    # many light capillaries (cheap to cut). The thinner end sets the thickness (a sinew is
    # only as hard to break as its breakable end). Every co-changing pair is a candidate,
    # whether or not the two also call each other.
    surf = {mk: m["IN"] + m["OUT"] for mk, m in M.items()}
    bonds = []
    for pr, k in pair.items():
        if k < min_k:
            continue
        a, b = tuple(pr)
        if a not in M or b not in M:
            continue
        thick = min(surf[a], surf[b])
        if thick <= 0:
            continue
        bonds.append((k * thick, k, thick, a, b))
    bonds.sort(key=lambda t: t[0], reverse=True)
    masses = [t[0] for t in bonds]
    total = sum(masses) or 1.0
    dec = max(1, len(masses) // 10)
    med = _median(masses)
    mad = _median([abs(x - med) for x in masses]) or 1.0
    heavy = {
        "n_pairs": len(bonds),
        "top_decile_share": round(sum(masses[:dec]) / total, 3),
        "top_1pct_share": round(sum(masses[:max(1, len(masses) // 100)]) / total, 3),
        "n_heavy_madz": sum(1 for x in masses if 0.6745 * (x - med) / mad > 3.5),
        "top": [{"heaviness": round(h, 1), "k": k, "thick": th,
                 "shared": sorted(M[a]["fwd_names"] & M[b]["fwd_names"]),
                 "a": (M[a]["name"], M[a]["IN"], M[a]["OUT"], M[a]["cc"], M[a]["fwd_ratio"]),
                 "b": (M[b]["name"], M[b]["IN"], M[b]["OUT"], M[b]["cc"], M[b]["fwd_ratio"])}
                for h, k, th, a, b in bonds[:15]],
    }

    # [6] SPAN discriminator: connect the HEAVY sinews into a graph and take components.
    # Daniel's rule -- a big SPAN (many methods roped together) = accidental sprawl (CONCERN);
    # a small tight clique = intrinsic cohesion/contract (equals/hashCode/toString), leave alone.
    # density ~1 = clique (contract-like); symmetry = mean min(surf)/max(surf) over the bond's
    # ends (contracts are symmetric duals; sprawl is asymmetric: a thin method roped to a thick).
    # `threaded` names the parameters most members hand onward: the thing the sinew threads.
    hv_thr = med + (3.5 / 0.6745) * mad
    hedges = [(a, b) for h, k, th, a, b in bonds if h > hv_thr]
    adjH: dict = defaultdict(set)
    nodesH: set = set()
    for a, b in hedges:
        adjH[a].add(b)
        adjH[b].add(a)
        nodesH.add(a)
        nodesH.add(b)
    hset = {frozenset(e) for e in hedges}
    spans = []
    for comp in _components(nodesH, adjH):
        cs = set(comp)
        ce = [e for e in hset if e <= cs]
        span = len(comp)
        poss = span * (span - 1) / 2
        syms = []
        for e in ce:
            a, b = tuple(e)
            lo, hi = min(surf[a], surf[b]), max(surf[a], surf[b])
            syms.append(lo / hi if hi else 1.0)
        members = sorted(comp, key=lambda mk: surf[mk], reverse=True)
        ratios = [M[mk]["fwd_ratio"] for mk in comp]
        spans.append({
            "span": span, "links": len(ce),
            "density": round(len(ce) / poss, 2) if poss else 0.0,
            "symmetry": round(sum(syms) / len(syms), 2) if syms else 0.0,
            "files": len({M[mk]["file"] for mk in comp}),
            "mean_fwd_ratio": round(sum(ratios) / len(ratios), 2),
            "n_conduits": sum(1 for mk in comp if M[mk]["conduit"]),
            "threaded": _threaded(comp, M),
            "verdict": "cohesion" if span <= 3 else "CONCERN",
            "members": [(M[mk]["name"], M[mk]["IN"], M[mk]["OUT"], M[mk]["cc"],
                         M[mk]["fwd_ratio"]) for mk in members],
            "member_keys": [list(mk) for mk in members],
        })
    spans.sort(key=lambda s: (s["span"], s["links"]), reverse=True)
    heavy["spans"] = spans

    inv = [m["IN"] for m in ms]
    return {
        "base": base, "n_methods": nM, "qIN": qin, "qOUT": qout,
        "params": {"mean": round(sum(inv) / nM, 3) if nM else 0.0, "median": _median(inv),
                   "p90": _quantile(inv, 0.9), "max": max(inv, default=0),
                   "mean_fwd_ratio": round(sum(ratiov) / nM, 3) if nM else 0.0},
        "n_joints": len(joints), "n_conduits": typology["conduit"]["n"],
        "typology": typology, "controls": controls, "clusters": clusters, "outliers": outliers,
        "heavy": heavy,
    }


def _print_sinew(base: str, max_commit_size: int = 30) -> None:
    s = sinew(base, max_commit_size)
    print(f"\n=== sinew (F2 directional): {base} ===")
    pm = s["params"]
    print(f"    declared parameters per method: mean {pm['mean']}, median {pm['median']}, "
          f"p90 {pm['p90']}, max {pm['max']}; mean forward ratio {pm['mean_fwd_ratio']}")
    print(f"    {s['n_methods']} methods; JOINT threshold IN>={s['qIN']} & OUT>={s['qOUT']} "
          f"(upper tercile); {s['n_joints']} joints; {s['n_conduits']} conduits "
          f"(fwd_ratio>={CONDUIT_RATIO}, params>={CONDUIT_MIN_PARAMS})")
    print("\n[1] TYPOLOGY  (co-change degree by flow class, and by conduit-ness)")
    print(f"    {'class':>11} {'n':>6} {'mean co-change degree':>22}")
    for c in ("joint", "source", "sink", "minor", "conduit", "non-conduit"):
        t = s["typology"].get(c)
        if t:
            print(f"    {c:>11} {t['n']:>6} {t['mean_degree']:>22}")
    print("\n[2] COMPLEXITY CONTROL  (is the conduit signal real beyond complexity / size?)")
    for k, v in s["controls"].items():
        print(f"    {k:38} {'  n/a' if v is None else f'{v:+.3f}'}")
    print("\n[3] SINEW NETWORK  (joints linked by co-change)")
    for i, c in enumerate(s["clusters"], 1):
        thr = ", ".join(c["threaded"]) or "-"
        print(f"    cluster {i}: {c['size']} joints, {c['edges']} links, net span "
              f"{c['net_span'][0]:+d}..{c['net_span'][1]:+d}, threads: {thr}")
        for nm, IN, OUT, dg, cc, fr in c["members"]:
            print(f"        {nm[:30]:<30} IN={IN:>3} OUT={OUT:>3} net={OUT - IN:>+4} "
                  f"deg={dg:>3} cc={cc:>3} fwd={fr:.2f}")
    print("\n[4] OUTLIER JOINTS  (degree x jointness; cc_pct = complexity percentile)")
    print(f"    {'score':>7} {'IN':>4} {'OUT':>4} {'net':>5} {'deg':>4} {'cc':>4} "
          f"{'cc_pct':>6} {'fwd':>5}  method")
    for o in s["outliers"]:
        print(f"    {o['score']:>7} {o['IN']:>4} {o['OUT']:>4} {o['net']:>+5} {o['degree']:>4} "
              f"{o['cc']:>4} {o['cc_pct']:>6} {o['fwd_ratio']:>5.2f}  {o['name'][:34]}")
    h = s["heavy"]
    print(f"\n[5] HEAVY SINEWS  (thick plumbing + co-change; {h['n_pairs']} co-changing pairs)")
    print(f"    concentration: top-decile holds {h['top_decile_share'] * 100:.0f}% of sinew "
          f"mass, top-1% holds {h['top_1pct_share'] * 100:.0f}%; {h['n_heavy_madz']} heavy "
          f"(MAD-z>3.5)")
    print(f"    {'heavy':>6} {'k':>3} {'thick':>5}  {'method A (IN/OUT/cc/fwd)':<34} "
          f"{'method B (IN/OUT/cc/fwd)':<34} shared")
    for t in h["top"]:
        an, ai, ao, ac, af = t["a"]
        bn, bi, bo, bc, bf = t["b"]
        a = f"{an[:18]} {ai}/{ao}/{ac}/{af:.2f}"
        b = f"{bn[:18]} {bi}/{bo}/{bc}/{bf:.2f}"
        print(f"    {t['heaviness']:>6} {t['k']:>3} {t['thick']:>5}  {a:<34} {b:<34} "
              f"{', '.join(t['shared']) or '-'}")
    print("\n[6] SPAN DISCRIMINATOR  (components of the heavy-sinew graph; "
          "small=cohesion, big=CONCERN)")
    print(f"    {'span':>4} {'links':>5} {'dens':>4} {'symm':>4} {'files':>5} {'fwd':>4} "
          f"{'verdict':>9}  threads | members (IN/OUT/cc/fwd)")
    for sp in s["heavy"]["spans"]:
        mem = ", ".join(f"{nm[:16]}({i}/{o}/{cc}/{fr:.1f})"
                        for nm, i, o, cc, fr in sp["members"][:5])
        thr = ",".join(sp["threaded"][:4]) or "-"
        print(f"    {sp['span']:>4} {sp['links']:>5} {sp['density']:>4} {sp['symmetry']:>4} "
              f"{sp['files']:>5} {sp['mean_fwd_ratio']:>4} {sp['verdict']:>9}  {thr} | {mem}")


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
