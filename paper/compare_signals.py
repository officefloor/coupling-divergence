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
the literature. See paper/MATRIX_NOTES.md for the pre-registered primary hypothesis and the
caveats (multiple comparisons, type-aware oracle vs type-blind signal, Java-only panel, and
the tool-oracle ceiling). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys

MIN_FUNCS = 50
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
            if not r.get("error") and (r.get("n_funcs") or 0) >= MIN_FUNCS and "matrix" in r]
    if not rows:
        print("No rows with a 'matrix' block; re-run the patched study.py.", file=sys.stderr)
        return 1
    signals = rows[0]["matrix"][f"signals_{lvl}"]
    # oracles present at this level (keys of any AUC cell)
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

    # auto-exclude degenerate signals from the aggregates (kept in raw): a signal whose best
    # mean AUC over all definitions is <= DEGENERATE_AUC is never informative anywhere.
    def best_auc(s):
        vals = [mean([auc(r, s, o) for r in rows]) for o in oracles]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None
    excluded = [s for s in signals if (best_auc(s) is None or best_auc(s) <= DEGENERATE_AUC)]
    active = [s for s in signals if s not in excluded]

    print(f"=== identification over {len(rows)} repos | level={lvl} | {len(oracles)} god "
          f"definitions in {len(groups_present)} groups | {len(active)} signals ===")
    print("    SIGNAL quality = AUC (threshold-free); ALGORITHM quality = MCC (thresholded)")
    if excluded:
        print(f"    auto-excluded from aggregates (max AUC <= {DEGENERATE_AUC}, kept in raw): "
              f"{excluded}")
    print()

    # ---- 1. SIGNAL QUALITY by AUC, grouped so size restatements do not dominate ----
    print("1. SIGNAL RANKING by mean AUC, per DEFINITION GROUP  (1.0=perfect, 0.5=chance)")
    for g in groups_present:
        gor = [o for o in oracles if grp_of.get(o) == g]
        sig_mean = {s: mean([auc(r, s, o) for r in rows for o in gor]) for s in active}
        # Demsar average rank over (repo, oracle) instances in this group
        rrows = []
        for r in rows:
            for o in gor:
                if not has_gods(r, o):
                    continue
                sc = {s: auc(r, s, o) for s in active if auc(r, s, o) is not None}
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
            print(f"     Friedman p~{fr['p']} (N={fr['n']}); Nemenyi CD={fr['cd']} on rank")

    # ---- 1b. the DIVERGENCE FAMILY head-to-head: plain divergence vs divergence x each
    #      complexity definition, so one can see which complexity best amplifies divergence.
    family = [s for s in (["divergence"] + [s for s in signals if s.startswith("div_x_")])
              if s in active]
    if len(family) > 1:
        print("\n1b. DIVERGENCE FAMILY  (plain divergence vs divergence x each complexity), "
              "mean AUC per group")
        for g in groups_present:
            gor = [o for o in oracles if grp_of.get(o) == g]
            fm = {s: mean([auc(r, s, o) for r in rows for o in gor]) for s in family}
            fm = {s: v for s, v in fm.items() if v is not None}
            if not fm:
                continue
            order = sorted(fm, key=lambda s: -fm[s])
            best = order[0]
            cells = "  ".join(f"{s.replace('div_x_','x')}={fm[s]:.3f}"
                              f"{'<' if s == best else ''}" for s in order)
            print(f"   [{g:15}] {cells}")
        print("   (plain 'divergence' vs 'x<metric>'; '<' marks the best; the raw gain factor "
              "makes these non-circular even against size definitions)")

    # ---- 2. BEST ALGORITHM (operational: turning a signal into a flag set), by MCC ----
    print("\n2. ALGORITHM RANKING by mean MCC  (over all signals, definitions, repos)")
    arows = []
    amean = {a: [] for a in ALGOS}
    for r in rows:
        for o in oracles:
            if not has_gods(r, o):
                continue
            aa = {}
            for a in ALGOS:
                vs = [mcc(_dig(r, ["matrix", lvl, "cells", s, a, o])) for s in active]
                vs = [v for v in vs if v is not None]
                if vs:
                    aa[a] = sum(vs) / len(vs)
                    amean[a].append(aa[a])
            if len(aa) == len(ALGOS):
                arows.append(ranks(aa))
    fra = friedman(arows, ALGOS)
    for a in sorted(ALGOS, key=lambda a: -(mean(amean[a]) or -9)):
        mr = f" rank {fra['mean_rank'][a]:.2f}" if fra else ""
        print(f"   {a:12} MCC={(mean(amean[a]) or 0):.3f}{mr}")
    if fra:
        print(f"   Friedman p~{fra['p']} (N={fra['n']}); Nemenyi CD={fra['cd']}")

    # ---- 3. per DEFINITION: best signal (AUC) and best signal+algorithm flag set (MCC) ----
    print("\n3. BEST PER DEFINITION   (best signal by AUC | best signal+algorithm by MCC)")
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

    print("\n(* = divergence, + = divergence x complexity. See paper/MATRIX_NOTES.md for the "
          "pre-registered primary hypothesis and caveats: size definitions are correlated and "
          "grouped for that reason; oracles are tool-derived, not human truth; the PMD/CK panel "
          "is Java-only; and the type-aware oracles can see gods the type-blind signals cannot, "
          "so signal AUC is a floor.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
