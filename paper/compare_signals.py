#!/usr/bin/env python3
"""Which concentration spike best IDENTIFIES a textbook god class -- across many definitions.

    python compare_signals.py <study.json> [--level class|method]

Two questions, kept separate (the fix for the threshold confound):

  WHICH SIGNAL ranks gods best?      -> AUC / average precision of the RAW signal value,
                                        threshold-free, no outlier algorithm involved.
  WHICH ALGORITHM makes the best      -> MCC of the flagged set at the algorithm's threshold
  flag set from a signal?               (Matthews correlation, the balanced rare-class choice).

Signals : divergence (gain); each complexity definition (cc, cognitive, nloc, tokens, params,
          nesting; nom at class level); and divergence WEIGHTED by each (div_x_cc, ...).
Defs    : the god-class DEFINITIONS, grouped so correlated size restatements do not outvote
          the cohesion/coupling ones -- size/complexity, cohesion, coupling, composite.

Aggregation is the Demsar (2006) average rank (Friedman + Nemenyi) alongside the mean, per
the literature. The Friedman BLOCK is the repository: per repo the scores are first averaged
over the definitions in a group, so correlated definitions of one repo are not counted as
independent blocks (which would shrink p-values by pseudo-replication).

Repositories are split, by a hash of their URL, into an EXPLORE half and a CONFIRM half.
Every data-driven choice (which signals are degenerate, which competitor is strongest) is
made on EXPLORE; the pre-registered H1/H2 are then tested once, on CONFIRM only, with a
one-sided paired Wilcoxon signed-rank test. See paper/MATRIX_NOTES.md for the hypotheses
and caveats. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys

MIN_FUNCS = 50          # the study's substance floor, mirrored from study.MIN_FUNCS/MIN_UNIV
MIN_UNIV = 15
PURE_COMPLEXITY = ["cc", "cognitive", "nloc", "tokens", "params", "nesting", "nom"]
# the pre-registered hypotheses (paper/MATRIX_NOTES.md): (label, signal, definitions)
HYPOTHESES = [
    ("H1", "div_x_cc", ["pmd_GodClass", "decor_blob"]),
    ("H2", "divergence", ["pmd_CouplingBetweenObjects", "ck_CBOhigh"]),
]
ALGOS = ["mad_z", "double_mad", "iqr", "adj_boxplot", "evt_pot", "pct_top1", "zscore"]
# A signal is degenerate -- dropped from the AGGREGATES (ranking, family, algorithm average)
# but kept in the raw JSON -- if its best mean AUC across every definition is at or below this,
# i.e. it is never better than chance anywhere (e.g. div_x_nesting, whose factor is usually 0).
DEGENERATE_AUC = 0.52
# god DEFINITIONS grouped by what they actually measure, so five size restatements do not
# drown the two cohesion / two coupling definitions in any "overall" average.
GROUPS = {
    "size/complexity": ["wmc47", "cc14", "cognitive15", "nesting5", "loc50", "params5",
                        "pmd_TooManyMethods", "pmd_NcssCount", "pmd_ExcessiveClassLength",
                        "pmd_TooManyFields", "pmd_ExcessivePublicCount", "pmd_ExcessiveImports"],
    "cohesion": ["ck_LCOM", "ck_TCClow"],
    "coupling": ["pmd_CouplingBetweenObjects", "ck_CBOhigh"],
    "composite": ["pmd_GodClass", "decor_blob"],
}
_NEMENYI_Q = {2: 1.960, 3: 2.343, 4: 2.569, 5: 2.728, 6: 2.850, 7: 2.949, 8: 3.031, 9: 3.102,
              10: 3.164, 11: 3.219, 12: 3.268, 13: 3.313, 14: 3.354, 15: 3.391, 16: 3.426}


def _dig(d, path):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def mcc(c):
    if not isinstance(c, dict) or not c.get("n_univ"):
        return None
    tp, pp, ap, n = c.get("n_overlap", 0), c.get("n_out", 0), c.get("n_god_univ", 0), c["n_univ"]
    fp, fn = pp - tp, ap - tp
    tn = n - pp - ap + tp
    den = (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)
    return 0.0 if den <= 0 else (tp * tn - fp * fn) / math.sqrt(den)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def ranks(scores: dict):
    items = sorted(scores.items(), key=lambda kv: -kv[1])
    out, i, n = {}, 0, len(items)
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        for k in range(i, j + 1):
            out[items[k][0]] = (i + j) / 2 + 1
        i = j + 1
    return out


def half_of(repo_url: str) -> str:
    """Deterministic explore/confirm assignment from the URL alone (no seed to tune)."""
    import hashlib
    return "confirm" if hashlib.sha256(repo_url.encode()).digest()[0] & 1 else "explore"


def wilcoxon_greater(diffs):
    """One-sided Wilcoxon signed-rank p for median(diffs) > 0, zero differences dropped:
    exact for up to 30 untied differences, else the normal approximation with tie and
    continuity correction. None if fewer than 6 non-zero differences."""
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n < 6:
        return None
    order = sorted(range(n), key=lambda i: abs(d[i]))
    rk = [0.0] * n
    i, ties = 0, 0.0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        for t in range(i, j + 1):
            rk[order[t]] = (i + j) / 2 + 1
        c = j - i + 1
        ties += c ** 3 - c
        i = j + 1
    w = sum(r for r, x in zip(rk, d) if x > 0)
    if ties == 0 and n <= 30:
        # exact null distribution of W+ (ranks 1..n, each sign equally likely), by DP
        counts = [1] + [0] * (n * (n + 1) // 2)
        for k in range(1, n + 1):
            for t in range(len(counts) - 1, k - 1, -1):
                counts[t] += counts[t - k]
        return sum(counts[int(w):]) / 2 ** n
    mu = n * (n + 1) / 4
    var = n * (n + 1) * (2 * n + 1) / 24 - ties / 48
    if var <= 0:
        return None
    z = (w - mu - 0.5) / math.sqrt(var)
    return 0.5 * math.erfc(z / math.sqrt(2))


def chi2_sf(x, df):
    if x <= 0 or df <= 0:
        return 1.0
    z = ((x / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
    return 0.5 * math.erfc(z / math.sqrt(2))


def friedman(rank_rows, methods):
    n, k = len(rank_rows), len(methods)
    if n < 2 or k < 2:
        return None
    mr = {m: sum(r[m] for r in rank_rows) / n for m in methods}
    chi = (12 * n) / (k * (k + 1)) * sum((mr[m] - (k + 1) / 2) ** 2 for m in methods)
    cd = _NEMENYI_Q.get(k, 3.4) * math.sqrt(k * (k + 1) / (6 * n))
    return {"chi2": round(chi, 2), "p": round(chi2_sf(chi, k - 1), 5), "n": n,
            "mean_rank": mr, "cd": round(cd, 3)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("study")
    ap.add_argument("--level", choices=["class", "method"], default="class")
    args = ap.parse_args(argv)
    lvl = args.level
    rows = [r for r in json.load(open(args.study))
            if not r.get("error") and not r.get("excluded_min_substance")
            and (r.get("n_funcs") or 0) >= MIN_FUNCS
            and ((r.get("class_vs_wmc47") or {}).get("n_univ") or 0) >= MIN_UNIV
            and "matrix" in r]
    if not rows:
        print("No usable rows with a 'matrix' block; re-run the patched study.py.",
              file=sys.stderr)
        return 1
    explore = [r for r in rows if half_of(r["repo"]) == "explore"]
    confirm = [r for r in rows if half_of(r["repo"]) == "confirm"]
    signals = rows[0]["matrix"][f"signals_{lvl}"]
    oracles = sorted({o for r in rows
                      for o in (_dig(r, ["matrix", lvl, "auc", "divergence"]) or {})})
    grp_of = {o: g for g, lst in GROUPS.items() for o in lst}
    groups_present = [g for g in GROUPS if any(grp_of.get(o) == g for o in oracles)]

    def auc(r, sig, o):
        b = _dig(r, ["matrix", lvl, "auc", sig, o])
        return b.get("auc") if isinstance(b, dict) else None

    def has_gods(r, o):
        b = _dig(r, ["matrix", lvl, "auc", "divergence", o])
        return bool(b and b.get("n_god"))

    def repo_auc(r, s, defs):
        """One repo's AUC for signal s, averaged over the definitions in `defs` it has."""
        return mean([auc(r, s, o) for o in defs if has_gods(r, o)])

    # degenerate signals are decided on EXPLORE only, so the filter cannot tune the test
    def best_auc(s, rs):
        vals = [mean([auc(r, s, o) for r in rs]) for o in oracles]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None
    excluded = [s for s in signals
                if (best_auc(s, explore) is None or best_auc(s, explore) <= DEGENERATE_AUC)]
    active = [s for s in signals if s not in excluded]

    print(f"=== identification over {len(rows)} usable repos ({len(explore)} explore / "
          f"{len(confirm)} confirm) | level={lvl} | {len(oracles)} god definitions in "
          f"{len(groups_present)} groups | {len(active)} signals ===")
    print("    SIGNAL quality = AUC (threshold-free); ALGORITHM quality = MCC (thresholded)")
    print("    Friedman blocks are repositories (definitions averaged within a repo first)")
    if excluded:
        print(f"    auto-excluded on the EXPLORE half (max AUC <= {DEGENERATE_AUC}, kept in "
              f"raw): {excluded}")
    print()

    # ---- 1. SIGNAL QUALITY by AUC, grouped so size restatements do not dominate ----
    print("1. SIGNAL RANKING by mean AUC, per DEFINITION GROUP  (1.0=perfect, 0.5=chance)"
          "  [all usable repos; exploratory]")
    for g in groups_present:
        gor = [o for o in oracles if grp_of.get(o) == g]
        per_repo = {s: [repo_auc(r, s, gor) for r in rows] for s in active}
        sig_mean = {s: mean(v) for s, v in per_repo.items()}
        rrows = []
        for k in range(len(rows)):
            sc = {s: per_repo[s][k] for s in active if per_repo[s][k] is not None}
            if len(sc) == len(active):
                rrows.append(ranks(sc))
        fr = friedman(rrows, active)
        order = sorted((s for s in active if sig_mean[s] is not None),
                       key=lambda s: -sig_mean[s])
        print(f"\n   [{g}]  definitions={gor}")
        for s in order[:6]:
            mr = f" rank {fr['mean_rank'][s]:.1f}" if fr else ""
            star = " *" if s == "divergence" else ("+" if s.startswith("div_x_") else "")
            print(f"     {s:16} AUC={sig_mean[s]:.3f}{mr}{star}")
        if fr:
            print(f"     Friedman p~{fr['p']} (N={fr['n']} repos); Nemenyi CD={fr['cd']} on rank")

    # ---- 1b. the DIVERGENCE FAMILY head-to-head
    family = [s for s in (["divergence"] + [s for s in signals if s.startswith("div_x_")])
              if s in active]
    if len(family) > 1:
        print("\n1b. DIVERGENCE FAMILY  (plain divergence vs divergence x each complexity), "
              "mean per-repo AUC per group")
        for g in groups_present:
            gor = [o for o in oracles if grp_of.get(o) == g]
            fm = {s: mean([repo_auc(r, s, gor) for r in rows]) for s in family}
            fm = {s: v for s, v in fm.items() if v is not None}
            if not fm:
                continue
            order = sorted(fm, key=lambda s: -fm[s])
            best = order[0]
            cells = "  ".join(f"{s.replace('div_x_','x')}={fm[s]:.3f}"
                              f"{'<' if s == best else ''}" for s in order)
            print(f"   [{g:15}] {cells}")

    # ---- 2. BEST ALGORITHM, by MCC; one block per repo
    print("\n2. ALGORITHM RANKING by mean MCC  (per repo: averaged over signals and the "
          "definitions it has)")
    arows = []
    amean = {a: [] for a in ALGOS}
    for r in rows:
        aa = {}
        for a in ALGOS:
            vs = [mcc(_dig(r, ["matrix", lvl, "cells", s, a, o]))
                  for s in active for o in oracles if has_gods(r, o)]
            v = mean(vs)
            if v is not None:
                aa[a] = v
                amean[a].append(v)
        if len(aa) == len(ALGOS):
            arows.append(ranks(aa))
    fra = friedman(arows, ALGOS)
    for a in sorted(ALGOS, key=lambda a: -(mean(amean[a]) or -9)):
        mr = f" rank {fra['mean_rank'][a]:.2f}" if fra else ""
        print(f"   {a:12} MCC={(mean(amean[a]) or 0):.3f}{mr}")
    if fra:
        print(f"   Friedman p~{fra['p']} (N={fra['n']} repos); Nemenyi CD={fra['cd']}")

    # ---- 3. per DEFINITION: best signal (AUC) and best signal+algorithm flag set (MCC) ----
    print("\n3. BEST PER DEFINITION   (best signal by AUC | best signal+algorithm by MCC)"
          "  [exploratory: the best of many cells is upward-biased]")
    for g in groups_present:
        for o in [x for x in oracles if grp_of.get(x) == g]:
            sm = {s: mean([auc(r, s, o) for r in rows]) for s in signals}
            sm = {s: v for s, v in sm.items() if v is not None}
            if not sm:
                continue
            bs = max(sm, key=sm.get)
            cm = {}
            for s in signals:
                for a in ALGOS:
                    v = mean([mcc(_dig(r, ["matrix", lvl, "cells", s, a, o])) for r in rows])
                    if v is not None:
                        cm[(s, a)] = v
            bc = max(cm, key=cm.get) if cm else (None, None)
            print(f"   {o:28} AUC: {bs}={sm[bs]:.3f}   MCC: {bc[0]}/{bc[1]}="
                  f"{cm.get(bc, 0):.3f}")

    # ---- 4. the PRE-REGISTERED hypotheses, tested once on the CONFIRM half ----
    if lvl == "class":
        print("\n4. PRE-REGISTERED H1/H2 on the CONFIRM half only (one-sided paired Wilcoxon)")
        competitors = [s for s in PURE_COMPLEXITY if s in signals]
        for label, sig, defs in HYPOTHESES:
            present = [o for o in defs if o in oracles]
            if not present or sig not in signals:
                print(f"   {label}: {sig} vs pure complexity on {defs} -- NOT TESTABLE "
                      f"(definitions missing from this run; was CK/PMD enabled?)")
                continue
            rivals = competitors + (["divergence"] if label == "H1" else [])
            # strongest rival chosen on EXPLORE, then tested on CONFIRM
            ex = {s: mean([repo_auc(r, s, present) for r in explore]) for s in rivals}
            ex = {s: v for s, v in ex.items() if v is not None}
            if not ex:
                print(f"   {label}: no explore-half repos carry {present}")
                continue
            rival = max(ex, key=ex.get)
            d_fixed, d_best = [], []
            for r in confirm:
                a = repo_auc(r, sig, present)
                b = repo_auc(r, rival, present)
                bb = [repo_auc(r, s, present) for s in rivals]
                bb = [x for x in bb if x is not None]
                if a is not None and b is not None:
                    d_fixed.append(a - b)
                if a is not None and bb:
                    d_best.append(a - max(bb))
            p1, p2 = wilcoxon_greater(d_fixed), wilcoxon_greater(d_best)
            med = sorted(d_fixed)[len(d_fixed) // 2] if d_fixed else None
            print(f"   {label}: {sig} > {rival} (strongest rival on explore) on {present}: "
                  f"N={len(d_fixed)} confirm repos, median dAUC="
                  f"{'n/a' if med is None else f'{med:+.3f}'}, p="
                  f"{'n/a (N<6)' if p1 is None else f'{p1:.4f}'}"
                  f"  | vs per-repo best rival (stricter): p="
                  f"{'n/a (N<6)' if p2 is None else f'{p2:.4f}'}")

    print("\n(* = divergence, + = divergence x complexity. See paper/MATRIX_NOTES.md for the "
          "pre-registered primary hypothesis and caveats: size definitions are correlated and "
          "grouped for that reason; oracles are tool-derived, not human truth; the PMD/CK panel "
          "is Java-only; and the type-aware oracles can see gods the type-blind signals cannot, "
          "so signal AUC is a floor. Sections 1-3 are exploratory; only section 4 confirms.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
