#!/usr/bin/env python3
"""Generate the paper's statistics from a study.json, so no number is ever typed by hand.

    python make_stats.py <study.json> [-o OUTDIR] [-m MANIFEST]

Writes two files into OUTDIR (default: this script's directory):
  stats.tex    every scalar the prose cites, as \\newcommand macros
  bylang.tex   the per-language body rows for Table 1 (tab:bylang)

The script is deliberately self-contained (Python standard library only): it does NOT
import the coupling_divergence package, so building the paper needs no parser or venv.
It mirrors three things from that package, and only those:
  * the "usable observation" rule from coupling_divergence/grow.py (_usable_loglift),
  * the one-sided Fisher exact p-value from coupling_divergence/study.py (fisher_greater),
  * the substance-floor thresholds, which it reads from the run's own sampling manifest
    (falling back to the published defaults) so there is a single source of truth.

Reporting choices, stated so they are auditable:
  * Lift is reported as the GEOMETRIC mean (exp of the mean log-lift). Lift is a ratio, so
    its log is the natural scale; the geometric mean is the matching central tendency and
    is the quantity the confidence interval is actually built on.
  * The 95%% interval is a STUDENT-t interval (df = n-1), not a normal z-interval. With the
    small per-language samples this study can reach, t is the honest choice; it agrees with
    z once n is large. (The pilot draft used z=1.96 even at n=3, which overstated the
    narrowest intervals -- this fixes that.)
  * "needed" is the repository count for a 95%% interval half-width of `hw` on log-lift,
    using the normal z (it is a planning approximation, matching grow's stopping rule).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

# Canonical language order and display labels for Table 1 (the study's 11 languages).
LANGS = ["c", "c#", "c++", "go", "java", "javascript", "php", "python", "ruby", "rust",
         "typescript"]
DISPLAY = {"c#": r"c\#", "javascript": "js", "typescript": "ts"}  # compact table labels
# Legacy study.json files (before study.py was fixed to emit canonical keys) labelled these
# languages "js"/"ts"; normalise so the generator is correct against old and new data alike.
_NORM = {"js": "javascript", "ts": "typescript"}
DEFAULTS = {"min_funcs": 50, "min_univ": 15, "hw": 0.2}

# Student-t two-sided 97.5%% critical values; exact for small df, Cornish-Fisher beyond.
_T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
      9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
      16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
      23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045,
      30: 2.042}
_Z = 1.959964


def _tcrit(df: int) -> float:
    if df <= 0:
        return float("inf")
    if df in _T:
        return _T[df]
    z = _Z
    return z + (z ** 3 + z) / (4 * df) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df * df)


def fisher_greater(a: int, b: int, c: int, d: int):
    """One-sided Fisher exact p (enrichment). Mirrors study.fisher_greater exactly."""
    from math import lgamma, exp
    n, r1, c1 = a + b + c + d, a + b, a + c
    if n == 0 or r1 == 0 or c1 == 0:
        return None

    def lC(N, k):
        return lgamma(N + 1) - lgamma(k + 1) - lgamma(N - k + 1)
    base = lC(n, r1)
    p = sum(exp(lC(c1, x) + lC(n - c1, r1 - x) - base) for x in range(a, min(r1, c1) + 1))
    return round(min(p, 1.0), 5)


def fisher_from(block: dict):
    """Fisher p from an _enrich block's counts. Mirrors study._fisher_from exactly."""
    if not block or block.get("n_univ") in (None, 0):
        return None
    a, n, r1 = block["n_overlap"], block["n_univ"], block["n_out"]
    g = block.get("n_god_univ")
    if g is None:
        g = block["n_god"]
    g = max(a, min(g, n - r1 + a))
    return fisher_greater(a, r1 - a, g - a, n - r1 - g + a)


def usable_loglift(row: dict, min_funcs: int, min_univ: int):
    """log(class lift) if the row is a usable class-level observation, else None.
    Mirrors grow._usable_loglift (the substance floor) so the analysis set matches the run."""
    if row.get("error") or row.get("excluded_min_substance"):
        return None
    if (row.get("n_funcs") or 0) < min_funcs:
        return None
    b = row.get("class_vs_wmc47") or {}
    lift = b.get("lift")
    if not lift or lift <= 0 or (b.get("n_univ") or 0) < min_univ:
        return None
    return math.log(lift)


def _method_loglift(row: dict, min_funcs: int):
    """log(method lift) for a usable method-level observation, else None. The substance
    floor on functions applies; the method universe is the function set, not files."""
    if row.get("error") or row.get("excluded_min_substance"):
        return None
    if (row.get("n_funcs") or 0) < min_funcs:
        return None
    b = row.get("method_vs_cc14") or {}
    lift = b.get("lift")
    if not lift or lift <= 0:
        return None
    return math.log(lift)


def _pmd_loglift(row: dict, min_funcs: int):
    if usable_loglift(row, min_funcs, 0) is None:   # must be a usable repo
        return None
    b = row.get("class_vs_pmd") or {}
    lift = b.get("lift")
    if not lift or lift <= 0:
        return None
    return math.log(lift)


def agg(logs: list[float], hw: float) -> dict:
    """Geometric-mean lift, Student-t 95%% interval, and repos-needed for half-width hw."""
    n = len(logs)
    if n < 2:
        return {"n": n, "lift": math.exp(logs[0]) if logs else None}
    m = sum(logs) / n
    sd = (sum((x - m) ** 2 for x in logs) / (n - 1)) ** 0.5
    se = sd / n ** 0.5
    t = _tcrit(n - 1)
    lo, hi = m - t * se, m + t * se
    need = int(math.ceil((_Z * sd / hw) ** 2)) if sd > 0 else n
    return {"n": n, "lift": math.exp(m), "lo": math.exp(lo), "hi": math.exp(hi),
            "excl": lo > 0, "sd": sd, "need": need}


def _f2(x) -> str:
    return "" if x is None else f"{x:.2f}"


def _grouped(ints, hw):
    return int(math.ceil((_Z * ints / hw) ** 2))


def _thousands(n: int) -> str:
    return f"{n:,}".replace(",", "{,}")


def build(study_path: str, manifest_path: str | None):
    rows = json.load(open(study_path))
    for r in rows:                              # normalise legacy js/ts labels in place
        if r.get("lang") in _NORM:
            r["lang"] = _NORM[r["lang"]]
    thr = dict(DEFAULTS)
    if manifest_path and os.path.exists(manifest_path):
        man = json.load(open(manifest_path))
        cfg = man.get("config", man)
        for k in ("min_funcs", "min_univ", "hw"):
            if cfg.get(k) is not None:
                thr[k] = cfg[k]
    min_funcs, min_univ, hw = thr["min_funcs"], thr["min_univ"], thr["hw"]

    measured = [r for r in rows if not r.get("error")]
    errored = [r for r in rows if r.get("error")]
    excluded = [r for r in measured
                if r.get("excluded_min_substance")
                or (r.get("n_funcs") or 0) < min_funcs
                or ((r.get("class_vs_wmc47") or {}).get("n_univ") or 0) < min_univ]

    # usable class-level observations, keyed by language
    class_by_lang: dict[str, list[float]] = {}
    class_all: list[float] = []
    class_sig = 0
    for r in measured:
        ll = usable_loglift(r, min_funcs, min_univ)
        if ll is None:
            continue
        class_by_lang.setdefault(r.get("lang"), []).append(ll)
        class_all.append(ll)
        p = fisher_from(r.get("class_vs_wmc47"))
        if p is not None and p < 0.05:
            class_sig += 1

    method_all, method_sig = [], 0
    for r in measured:
        ll = _method_loglift(r, min_funcs)
        if ll is None:
            continue
        method_all.append(ll)
        p = fisher_from(r.get("method_vs_cc14"))
        if p is not None and p < 0.05:
            method_sig += 1

    pmd_all = [ll for ll in (_pmd_loglift(r, min_funcs) for r in measured if r.get("lang") == "java")
               if ll is not None]

    cls, mth, pmd = agg(class_all, hw), agg(method_all, hw), agg(pmd_all, hw)

    # tier and kind breakdowns over the usable class set (the set lift is defined on)
    usable_rows = [r for r in measured if usable_loglift(r, min_funcs, min_univ) is not None]

    def grp(pred):
        logs = [usable_loglift(r, min_funcs, min_univ) for r in usable_rows if pred(r)]
        return agg([x for x in logs if x is not None], hw)
    tier = {t: grp(lambda r, t=t: r.get("tier") == t) for t in ("high", "mid", "low")}
    app, lib = grp(lambda r: r.get("kind") == "application"), grp(lambda r: r.get("kind") == "library")

    # leaf discriminant: WMC-god files PMD does not call a god, and the share gen still flags
    leaf_total = sum((r.get("wmc_only_leaves") or 0) for r in measured if r.get("lang") == "java")
    leaf_flag = sum((r.get("wmc_only_leaves_flagged_by_gen") or 0)
                    for r in measured if r.get("lang") == "java")
    leaf_pct = round(100 * leaf_flag / leaf_total) if leaf_total else 0

    langs_present = [L for L in LANGS if len(class_by_lang.get(L, [])) >= 2]

    m = []  # (macro, value) pairs for stats.tex
    m += [("nSampled", len(rows)), ("nMeasured", len(measured)), ("nErrored", len(errored)),
          ("nExcluded", len(excluded)), ("nAnalyzed", len(class_all)),
          ("nLangs", len(langs_present)), ("nTiers", 3),
          ("nFuncs", _thousands(sum(r.get("n_funcs") or 0 for r in measured))),
          ("nFiles", _thousands(sum(r.get("n_files") or 0 for r in measured))),
          ("nWmcGods", _thousands(sum(r.get("wmc_gods") or 0 for r in measured)))]
    m += [("classN", cls["n"]), ("classLift", _f2(cls["lift"])), ("classLo", _f2(cls["lo"])),
          ("classHi", _f2(cls["hi"])), ("classSig", class_sig), ("classNeed", cls["need"])]
    m += [("methodN", mth["n"]), ("methodLift", _f2(mth["lift"])), ("methodLo", _f2(mth["lo"])),
          ("methodHi", _f2(mth["hi"])), ("methodSig", method_sig), ("methodNeed", mth["need"])]
    if pmd.get("n", 0) >= 2:
        m += [("pmdN", pmd["n"]), ("pmdLift", _f2(pmd["lift"])), ("pmdLo", _f2(pmd["lo"])),
              ("pmdHi", _f2(pmd["hi"]))]
    m += [("tierHigh", _f2(tier["high"].get("lift"))), ("tierMid", _f2(tier["mid"].get("lift"))),
          ("tierLow", _f2(tier["low"].get("lift"))), ("kindApp", _f2(app.get("lift"))),
          ("kindLib", _f2(lib.get("lift"))), ("nApp", app.get("n", 0)), ("nLib", lib.get("n", 0))]
    m += [("leafTotal", leaf_total), ("leafFlagPct", leaf_pct)]

    stats = ["% Generated by make_stats.py from " + os.path.basename(study_path) + " -- DO NOT EDIT BY HAND.",
             "% Regenerate with:  python make_stats.py <study.json>",
             f"% Substance floor: >= {min_funcs} functions and a >= {min_univ}-file class universe.",
             "% Lift is the geometric mean; intervals are Student-t (df = n-1).",
             ""]
    stats += [f"\\newcommand{{\\{name}}}{{{val}}}" for name, val in m]

    # per-language Table 1 body
    body = ["% Generated by make_stats.py -- per-language rows for Table 1 (tab:bylang).",
            "% \\input this inside the tabular, between \\midrule and the `all' row."]
    for L in langs_present:
        a = agg(class_by_lang[L], hw)
        label = DISPLAY.get(L, L)
        body.append(f"{label:7} & {a['n']:>3} & {a['lift']:.2f} & "
                    f"{a['lo']:.2f}--{a['hi']:.2f} & {a['need']:>3} \\\\")

    return "\n".join(stats) + "\n", "\n".join(body) + "\n", {
        "min_funcs": min_funcs, "min_univ": min_univ, "hw": hw,
        "measured": len(measured), "analyzed": len(class_all), "excluded": len(excluded),
        "class": cls, "method": mth, "pmd": pmd, "langs": langs_present,
        "class_by_lang": {L: agg(class_by_lang[L], hw) for L in langs_present}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate stats.tex and bylang.tex from study.json")
    ap.add_argument("study", help="path to study.json")
    ap.add_argument("-o", "--out-dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("-m", "--manifest", default=None,
                    help="sampling-manifest.json (default: sibling of study.json)")
    args = ap.parse_args(argv)
    manifest = args.manifest or os.path.join(os.path.dirname(os.path.abspath(args.study)),
                                              "sampling-manifest.json")
    stats, body, summ = build(args.study, manifest)
    os.makedirs(args.out_dir, exist_ok=True)
    sp = os.path.join(args.out_dir, "stats.tex")
    bp = os.path.join(args.out_dir, "bylang.tex")
    open(sp, "w").write(stats)
    open(bp, "w").write(body)
    c = summ["class"]
    print(f"wrote {sp}\nwrote {bp}")
    print(f"  floor >= {summ['min_funcs']} funcs / >= {summ['min_univ']}-file universe"
          f" | measured {summ['measured']}, excluded(substance) {summ['excluded']},"
          f" analyzed {summ['analyzed']}")
    print(f"  CLASS pooled lift {c['lift']:.2f} 95%t[{c['lo']:.2f},{c['hi']:.2f}]"
          f" excludes1={c['excl']} over {summ['langs'].__len__()} languages")
    fails = [L for L, a in summ["class_by_lang"].items() if not a.get("excl", False)]
    print(f"  per-language 'excludes 1' failures: {fails or 'NONE'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
