#!/usr/bin/env python3
"""Generate the paper's statistics from a study.json, so no number is ever typed by hand.

    python make_stats.py <study.json> [-o OUTDIR] [-m MANIFEST]

Writes two files into OUTDIR (default: this script's directory):
  stats.tex    every scalar the prose cites, as \\newcommand macros
  bylang.tex   the per-language body rows for Table 1 (tab:bylang)

The script is deliberately self-contained (Python standard library only): it does NOT
import the coupling_divergence package, so building the paper needs no parser or venv.
It mirrors four things from that package, and only those:
  * the "usable observation" rule and the 0.5-corrected log-lift from
    coupling_divergence/study.py (usable, log_lift) -- decided before the outcome, so a repo
    whose outliers hit no god is kept as a negative observation, never dropped,
  * the one-sided Fisher exact p-value from coupling_divergence/study.py (fisher_greater),
  * the substance-floor thresholds, which it reads from the run's own sampling manifest
    (falling back to the published defaults) so there is a single source of truth,
  * the size baselines (study.BASELINE_PAIRS), reported as a paired divergence/baseline
    lift ratio, since a lift alone cannot separate divergence from size.

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


def _counts(block: dict):
    """Mirrors study._counts."""
    if not block or not block.get("n_univ"):
        return None
    a, n, r1 = block["n_overlap"], block["n_univ"], block["n_out"]
    g = block.get("n_god_univ")
    if g is None:
        g = max(a, min(block["n_god"], n - r1 + a))
    return a, r1, g, n


def log_lift(block: dict):
    """Mirrors study.log_lift: Haldane-Anscombe 0.5-corrected log-lift."""
    c = _counts(block)
    if c is None:
        return None
    a, r1, g, n = c
    if r1 == 0 or g == 0:
        return None
    return math.log(((a + 0.5) / (r1 + 1)) / ((g + 0.5) / (n + 1)))


def usable(row: dict, block: dict, min_funcs: int, min_univ: int) -> bool:
    """Mirrors study.usable: substance floor, >= 1 god and >= 1 outlier; never the outcome."""
    if row.get("error") or row.get("excluded_min_substance"):
        return False
    if (row.get("n_funcs") or 0) < min_funcs:
        return False
    c = _counts(block or {})
    return bool(c) and c[3] >= min_univ and c[2] >= 1 and c[1] >= 1


def loglift_of(row: dict, key: str, min_funcs: int, min_univ: int):
    """log-lift of a usable observation of row[key] (a `baseline_` key reads row.baseline)."""
    b = (row.get("baseline") or {}).get(key[9:]) if key.startswith("baseline_") else row.get(key)
    return log_lift(b) if usable(row, b, min_funcs, min_univ) else None


def usable_loglift(row: dict, min_funcs: int, min_univ: int):
    """The class-level observation the headline and the per-language table use."""
    return loglift_of(row, "class_vs_wmc47", min_funcs, min_univ)


BASELINE_PAIRS = {   # mirrors study.BASELINE_PAIRS
    "class_vs_wmc47": ("baseline_class_nom_vs_wmc47", "baseline_class_nloc_vs_wmc47"),
    "method_vs_cc14": ("baseline_method_nloc_vs_cc14",),
    "class_vs_pmd": ("baseline_class_nom_vs_pmd", "baseline_class_nloc_vs_pmd"),
}


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
    # cleared the floor but nothing to score (no reference god or no outlier in universe)
    no_target = [r for r in measured if r not in excluded and usable_loglift(r, min_funcs, min_univ) is None]

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
        ll = loglift_of(r, "method_vs_cc14", min_funcs, min_univ)
        if ll is None:
            continue
        method_all.append(ll)
        p = fisher_from(r.get("method_vs_cc14"))
        if p is not None and p < 0.05:
            method_sig += 1

    pmd_all = [ll for ll in (loglift_of(r, "class_vs_pmd", min_funcs, min_univ)
                             for r in measured if r.get("lang") == "java") if ll is not None]

    # paired divergence/baseline lift ratios: does divergence beat size on the same repos?
    ratios = {}
    for key, bases in BASELINE_PAIRS.items():
        for bk in bases:
            d = []
            for r in measured:
                a, b = loglift_of(r, key, min_funcs, min_univ), loglift_of(r, bk, min_funcs, min_univ)
                if a is not None and b is not None:
                    d.append(a - b)
            ratios[bk[9:]] = agg(d, hw)

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
    # ... and the other side of the discriminant: the share of WMC-gods that ARE PMD gods
    # which gen flags. Divergence discriminates only if this share exceeds leaf_pct.
    pg_total = sum((r.get("wmc_pmd_gods") or 0) for r in measured if r.get("lang") == "java")
    pg_flag = sum((r.get("wmc_pmd_gods_flagged_by_gen") or 0)
                  for r in measured if r.get("lang") == "java")
    pg_pct = round(100 * pg_flag / pg_total) if pg_total else 0

    langs_present = [L for L in LANGS if len(class_by_lang.get(L, [])) >= 2]

    m = []  # (macro, value) pairs for stats.tex
    m += [("nSampled", len(rows)), ("nMeasured", len(measured)), ("nErrored", len(errored)),
          ("nExcluded", len(excluded)), ("nNoTarget", len(no_target)),
          ("nAnalyzed", len(class_all)),
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
    m += [("leafTotal", leaf_total), ("leafFlagPct", leaf_pct),
          ("pmdGodWmcTotal", pg_total), ("pmdGodWmcFlagPct", pg_pct)]
    macro = {"class_nom_vs_wmc47": "ClassNom", "class_nloc_vs_wmc47": "ClassNloc",
             "method_nloc_vs_cc14": "MethodNloc", "class_nom_vs_pmd": "PmdNom",
             "class_nloc_vs_pmd": "PmdNloc"}
    for k, a in ratios.items():
        if a.get("n", 0) >= 2:
            nm = "vsBase" + macro[k]
            m += [(nm + "N", a["n"]), (nm + "Ratio", _f2(a["lift"])),
                  (nm + "Lo", _f2(a["lo"])), (nm + "Hi", _f2(a["hi"]))]

    stats = ["% Generated by make_stats.py from " + os.path.basename(study_path) + " -- DO NOT EDIT BY HAND.",
             "% Regenerate with:  python make_stats.py <study.json>",
             f"% Substance floor: >= {min_funcs} functions and a >= {min_univ}-file class universe.",
             "% Lift is the geometric mean of the 0.5-corrected per-repo lift; intervals are",
             "% Student-t (df = n-1). Repos whose outliers hit no god are KEPT. vsBase*Ratio is",
             "% the paired divergence/size-baseline lift ratio (> 1 = divergence beats size).",
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
        "class": cls, "method": mth, "pmd": pmd, "langs": langs_present, "ratios": ratios,
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
    for k, a in summ["ratios"].items():
        if a.get("n", 0) >= 2:
            print(f"  divergence / {k} baseline lift ratio {a['lift']:.2f} "
                  f"95%t[{a['lo']:.2f},{a['hi']:.2f}] over {a['n']} paired repos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
