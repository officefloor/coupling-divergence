"""Multi-language snapshot study: how well does the generation placement outlier detect
textbook god classes/methods at a repo's latest main commit?

Detection is a placement (snapshot) property, so we only need the latest commit. For each
repo we shallow-clone the default branch, record the exact commit (the source of truth is
the public repo; we keep only the sha), measure, and delete the clone to bound disk.

References (DI-immune, language-neutral, from lizard):
  * god CLASS  = file WMC >= 47 (Lanza-Marinescu size threshold)
  * god METHOD = function CC >= 14 (McCabe high-risk)
  * Java only, when --pmd given: PMD's GodClass (the full Lanza-Marinescu strategy)
We report, per repo and pooled per language, the enrichment (lift) and recall of the
generation outlier against each reference, plus the leaf-exclusion count (WMC-gods the
generation signal does NOT flag -- the self-contained complex algorithms).

Resumable: results already in the output JSON are skipped. Runnable to 100+ repos.
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import math as _math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

from . import extract
from . import metrics

CC_GOD = 14

DEFAULT_REPOS = [
    # java
    "https://github.com/apache/commons-lang.git",
    "https://github.com/google/gson.git",
    "https://github.com/jhy/jsoup.git",
    "https://github.com/mockito/mockito.git",
    "https://github.com/square/retrofit.git",
    # python
    "https://github.com/pallets/flask.git",
    "https://github.com/psf/requests.git",
    "https://github.com/Textualize/rich.git",
    "https://github.com/encode/httpx.git",
    "https://github.com/psf/black.git",
    # javascript / typescript
    "https://github.com/expressjs/express.git",
    "https://github.com/lodash/lodash.git",
    "https://github.com/axios/axios.git",
    "https://github.com/colinhacks/zod.git",
    "https://github.com/date-fns/date-fns.git",
    # go
    "https://github.com/gin-gonic/gin.git",
    "https://github.com/spf13/cobra.git",
    "https://github.com/sirupsen/logrus.git",
    # c / c++
    "https://github.com/nlohmann/json.git",
    "https://github.com/fmtlib/fmt.git",
    # c#
    "https://github.com/JamesNK/Newtonsoft.Json.git",
    "https://github.com/App-vNext/Polly.git",
    # ruby / php / rust
    "https://github.com/sinatra/sinatra.git",
    "https://github.com/guzzle/guzzle.git",
    "https://github.com/clap-rs/clap.git",
]


def _git(args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


# Extension -> language. `.h` is deliberately absent: it is ambiguous between C and C++
# and is resolved per repo in _lang (a header counts as C++ only when the tree carries a
# C++ marker, the way GitHub's Linguist resolves it). Mapping it blindly to C is what made
# header-heavy C++ projects (mlx, WindowsAppSDK) measure as C.
# Values are the canonical language keys used everywhere else (sample.LANGS, the grow
# targets, the paper): e.g. "javascript", not "js". A mismatch here silently breaks the
# per-language accounting -- grow counts usable repos by this label, so a label that no
# stratum uses can never reach its target (the .js/.ts -> js/ts bug that looped the sampler).
_EXT_LANG = {".java": "java", ".py": "python", ".js": "javascript", ".jsx": "javascript",
             ".ts": "typescript", ".tsx": "typescript",
             ".go": "go", ".c": "c", ".cc": "c++", ".cpp": "c++",
             ".cxx": "c++", ".c++": "c++", ".hpp": "c++", ".hh": "c++", ".hxx": "c++",
             ".cs": "c#", ".rb": "ruby", ".php": "php", ".rs": "rust", ".kt": "kotlin",
             ".swift": "swift", ".scala": "scala"}


def _lang(root: str) -> str:
    """Dominant language of a checkout, by source-file count aggregated into languages.

    Aggregating by language rather than by raw extension fixes two mislabels: the js/ts
    family split across .js/.jsx and .ts/.tsx, and ambiguous C/C++ headers. A `.h` is
    counted as C++ when the tree holds any unambiguous C++ file (.cpp/.cc/.hpp/...) and as
    C otherwise, so a C++ project whose headers outnumber its .cpp files no longer reads as
    C. Unknown extensions fall back to the bare suffix as their own bucket."""
    ext_counts = Counter()
    for rel in extract.source_files(root):
        ext_counts[os.path.splitext(rel)[1].lower()] += 1
    if not ext_counts:
        return "?"
    h_lang = "c++" if any(ext_counts.get(e) for e in
                          (".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx")) else "c"
    by_lang = Counter()
    for ext, n in ext_counts.items():
        lang = h_lang if ext == ".h" else _EXT_LANG.get(ext, ext.lstrip("."))
        by_lang[lang] += n
    # Tie-break deterministically by language name so re-runs are stable.
    return max(sorted(by_lang), key=by_lang.get)


def _rel(root: str, path: str) -> str:
    """A tool-reported path as the repo-relative path the extractor keys files by."""
    return os.path.relpath(os.path.realpath(path), os.path.realpath(root)).replace(os.sep, "/")


def _pmd_violations(pmd: str, root: str, files: list[str]) -> dict | None:
    """rule name -> set of repo-relative paths that violate it, over PMD's whole Java design
    category. PMD is run on EXACTLY the Java files the extractor measured (a --file-list, so
    every module is covered and test code is excluded the same way), and results are keyed
    by path, not basename, so same-named classes in different packages do not collide.
    One PMD pass yields the full oracle panel (GodClass, CouplingBetweenObjects ~ CK's CBO,
    TooManyMethods, ExcessiveClassLength/NcssCount, TooManyFields, ...). Needs PMD 7.
    Returns None if PMD could not be run or parsed."""
    if not files:
        return {}
    fd, lst = tempfile.mkstemp(prefix="cdiv-pmd-", suffix=".txt")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(os.path.join(root, f) for f in files) + "\n")
        out = subprocess.run([pmd, "check", "--file-list", lst, "-R", "category/java/design.xml",
                              "--format", "json", "--no-cache", "--no-progress"],
                             capture_output=True, text=True)
    finally:
        os.unlink(lst)
    try:
        d = json.loads(out.stdout)
    except Exception:
        return None
    byrule: dict = {}
    for f in d.get("files", []):
        rel = _rel(root, f["filename"])
        for v in f.get("violations", []):
            byrule.setdefault(v.get("rule"), set()).add(rel)
    return byrule


# class-level PMD rules used as god-class oracles in the comparison matrix (class-scoped
# "largeness/coupling/cohesion" rules; method-level and stylistic design rules are ignored).
_PMD_ORACLE_RULES = ("GodClass", "CouplingBetweenObjects", "TooManyMethods",
                     "ExcessiveClassLength", "NcssCount", "TooManyFields",
                     "ExcessivePublicCount", "ExcessiveImports")


def _ck_gods(ck_jar: str, root: str, files: list[str]) -> dict | None:
    """Run CK (mauricioaniche/ck) and derive cohesion/coupling god-class DEFINITIONS, keyed by
    repo-relative path (a file is a god if ANY class in it meets the threshold) and limited
    to the files the extractor measured, so CK's view of tests matches ours. These are
    non-size definitions, independent of WMC, which PMD alone does not give directly:
      ck_LCOM    : Henderson-Sellers LCOM* >= 0.8           (low cohesion)
      ck_TCClow  : Tight Class Cohesion < 1/3, >=2 methods  (Lanza-Marinescu cohesion component)
      ck_CBOhigh : Coupling Between Objects >= 14           (high coupling)
      decor_blob : an approximation of DECOR's Blob (Moha et al.): LargeClass (NMD+NAD >= 20)
                   AND (LowCohesion lcom* >= 0.8 OR a controller class name). The ASSOC-to-
                   data-classes clause is omitted (needs cross-class analysis), so this is a
                   faithful-but-partial Blob, not the certified Ptidej detector.
    Returns None if CK did not produce a class table."""
    import csv as _csvmod
    _CONTROLLER = re.compile(r"(process|control|manage|system|command|handler|manager|"
                             r"driver|exec|service|coordinator|dispatcher)", re.I)
    measured = set(files)
    outdir = tempfile.mkdtemp(prefix="cdiv-ck-")
    try:
        subprocess.run(["java", "-jar", ck_jar, root, "false", "0", "false", outdir + os.sep],
                       capture_output=True, text=True, timeout=1800)
        path = os.path.join(outdir, "class.csv")
        if not os.path.isfile(path):
            return None
        defs = {"ck_LCOM": set(), "ck_TCClow": set(), "ck_CBOhigh": set(), "decor_blob": set()}

        def num(row, key):
            try:
                return float(row.get(key, ""))
            except (TypeError, ValueError):
                return None
        with open(path, newline="") as fh:
            for row in _csvmod.DictReader(fh):
                if not row.get("file"):
                    continue
                b = _rel(root, row["file"])
                if b not in measured:
                    continue
                lcomn, tcc, cbo = num(row, "lcom*"), num(row, "tcc"), num(row, "cbo")
                nm = num(row, "totalMethodsQty") or 0
                nf = num(row, "totalFieldsQty") or 0
                name = (row.get("class") or "").split(".")[-1]
                low_coh = lcomn is not None and lcomn >= 0.8
                if low_coh:
                    defs["ck_LCOM"].add(b)
                if tcc is not None and 0 <= tcc < 1 / 3 and nm >= 2:
                    defs["ck_TCClow"].add(b)
                if cbo is not None and cbo >= 14:
                    defs["ck_CBOhigh"].add(b)
                if (nm + nf) >= 20 and (low_coh or _CONTROLLER.search(name)):
                    defs["decor_blob"].add(b)
        return defs
    except Exception:
        return None
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def _enrich(outset: set, godset: set, universe: set) -> dict:
    inter = universe & godset
    base = len(inter) / len(universe) if universe else 0
    cond = len(outset & godset) / len(outset) if outset else None
    lift = round(cond / base, 2) if (base and cond is not None) else None
    recall = round(len(outset & godset) / len(godset), 2) if godset else None
    # n_god is the full god count (recall denominator); n_god_univ is the gods that lie
    # inside the tested universe (the generators). A god with no internal-call generation
    # is not a generator, so n_god can exceed the universe; the Fisher table needs the
    # universe-restricted count to stay consistent.
    return {"lift": lift, "recall": recall, "n_god": len(godset),
            "n_god_univ": len(inter), "n_out": len(outset),
            "n_overlap": len(outset & godset), "n_univ": len(universe)}


def fisher_greater(a: int, b: int, c: int, d: int):
    """One-sided Fisher exact p-value for enrichment (P that the outliers would overlap the
    gods at least this much by chance), pure-python via log-gamma. a=outlier&god,
    b=outlier&not-god, c=not-outlier&god, d=not-outlier&not-god."""
    from math import lgamma, exp
    n, r1, c1 = a + b + c + d, a + b, a + c
    if n == 0 or r1 == 0 or c1 == 0:
        return None

    def lC(N, k):
        return lgamma(N + 1) - lgamma(k + 1) - lgamma(N - k + 1)
    base = lC(n, r1)
    p = sum(exp(lC(c1, x) + lC(n - c1, r1 - x) - base) for x in range(a, min(r1, c1) + 1))
    return round(min(p, 1.0), 5)


def _fisher_from(block: dict):
    """Fisher p from an `_enrich` block's counts. The 2x2 table is built over the tested
    universe, so it uses the gods WITHIN that universe (n_god_univ), not the full god count,
    which can exceed the universe. Older blocks lack n_god_univ, so the god count is clamped
    to the largest value that keeps the table consistent (every cell non-negative)."""
    if not block or block.get("n_univ") in (None, 0):
        return None
    a, n, r1 = block["n_overlap"], block["n_univ"], block["n_out"]
    g = block.get("n_god_univ")
    if g is None:
        g = block["n_god"]
    g = max(a, min(g, n - r1 + a))          # no-op for correct data; guards legacy blocks
    return fisher_greater(a, r1 - a, g - a, n - r1 - g + a)


def _counts(block: dict):
    """(overlap, outliers, gods in universe, universe) from an `_enrich` block, or None.
    Legacy blocks without n_god_univ get the same consistency clamp as _fisher_from."""
    if not block or not block.get("n_univ"):
        return None
    a, n, r1 = block["n_overlap"], block["n_univ"], block["n_out"]
    g = block.get("n_god_univ")
    if g is None:
        g = max(a, min(block["n_god"], n - r1 + a))
    return a, r1, g, n


def log_lift(block: dict):
    """Per-repo log-lift with the Haldane-Anscombe 0.5 correction,
        log[ ((a + 0.5) / (r1 + 1)) / ((g + 0.5) / (n + 1)) ],
    applied to EVERY repo alike. It is finite when the outliers hit no god (a = 0), so a
    repository where the signal fails is kept as a negative observation instead of being
    dropped -- dropping it, and drawing a replacement, is selection on the outcome. It is
    undefined only when there is nothing to test: no outlier or no god in the universe."""
    c = _counts(block)
    if c is None:
        return None
    a, r1, g, n = c
    if r1 == 0 or g == 0:
        return None
    return _math.log(((a + 0.5) / (r1 + 1)) / ((g + 0.5) / (n + 1)))


def usable(row: dict, key: str, min_funcs: int, min_univ: int) -> bool:
    """Whether `row[key]` is an observation, decided ONLY on quantities fixed before the
    outcome is known: the repo parsed, cleared the substance floor (functions, universe
    size), has at least one reference god in the universe and at least one outlier to
    score. Whether the outliers hit any god is never part of the rule."""
    if row.get("error") or row.get("excluded_min_substance"):
        return False
    if (row.get("n_funcs") or 0) < min_funcs:
        return False
    c = _counts(row.get(key) or {})
    return bool(c) and c[3] >= min_univ and c[2] >= 1 and c[1] >= 1


def usable_loglift(row: dict, key: str = "class_vs_wmc47", min_funcs: int = 50,
                   min_univ: int = 15):
    """log_lift of a usable observation, else None. The one rule grow, the summary and the
    paper statistics all use, so the stopping rule and the analysis set cannot drift."""
    return log_lift(row[key]) if usable(row, key, min_funcs, min_univ) else None


# Student-t two-sided 97.5% critical values; exact for small df, Cornish-Fisher beyond.
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
         9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
         16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
         23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045,
         30: 2.042}
_Z975 = 1.959964


def tcrit(df: int) -> float:
    if df <= 0:
        return float("inf")
    if df in _T975:
        return _T975[df]
    z = _Z975
    return z + (z ** 3 + z) / (4 * df) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df * df)


def mean_ci(xs: list[float]) -> dict:
    """Mean of log values with a Student-t 95% interval (df = n - 1), as lifts."""
    n = len(xs)
    if n < 2:
        return {"n": n, "mean_lift": round(_math.exp(xs[0]), 2) if xs else None}
    m = sum(xs) / n
    sd = (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5
    h = tcrit(n - 1) * sd / n ** 0.5
    return {"n": n, "mean_lift": round(_math.exp(m), 2),
            "ci95_lift": [round(_math.exp(m - h), 2), round(_math.exp(m + h), 2)],
            "excludes_1": m - h > 0, "sd_log": round(sd, 3), "mean_log": m}


_APP_FILES = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Procfile")


def classify_kind(root: str, funcs: dict) -> str:
    """Post-hoc heuristic: application vs library. Not controlled by the sampler and not a
    precise taxonomy; it is reported so the realized type mix of the sample is visible.
    Signals of a deployable application: a container/deploy descriptor, a Rust binary crate
    (main.rs without lib.rs), a Python `__main__.py`, or an entry function named `main`
    (Go/Java/C/Rust). Everything else is called a library."""
    try:
        top = set(os.listdir(root))
    except OSError:
        top = set()
    if top & set(_APP_FILES):
        return "application"
    hits = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "vendor",
                                                        "target", "build", "dist", ".venv")]
        for f in filenames:
            if f in _APP_FILES or f == "__main__.py" or f in ("main.rs", "lib.rs"):
                hits.add(f)
    if hits & set(_APP_FILES) or "__main__.py" in hits or ("main.rs" in hits and "lib.rs" not in hits):
        return "application"
    if any(fid[1] == "main" for fid in funcs):           # Go/Java/C/Rust entry point
        return "application"
    return "library"


def _cell(outset: set, god: set, universe: set) -> dict:
    """Lean confusion counts for one (spike, definition) pair; MCC/F1/lift are derived
    downstream from these four, keeping the per-repo matrix small."""
    return {"n_univ": len(universe), "n_out": len(outset),
            "n_overlap": len(outset & god), "n_god_univ": len(universe & god)}


def _matrix_block(funcs, wmc, cg, method_oracles, class_oracles, pmd_godclass) -> dict:
    """The full detection matrix. For every (level, signal, algorithm) cell: the confusion
    counts against every applicable god DEFINITION (all keyed like the extractor: function
    ids at method level, repo-relative paths at class level). Also, for every (level,
    signal, definition): the THRESHOLD-FREE AUC/AP of the raw signal, which separates signal
    quality from the outlier algorithm's threshold. Plus per-algorithm divergence-vs-signal
    alignment. A COMMON per-level universe (funcs cc>0 / files wmc>0) is used for a fair
    head-to-head, unlike the paper's generator-restricted headline."""
    mx = metrics.matrix_outliers(funcs, wmc, cg)

    def sigs(level_dict):
        return [k for k in level_dict if k not in ("universe", "_vals")]

    def cells(level_dict, universe, oracles):
        return {signal: {algo: {name: _cell(outset, god, universe)
                                for name, god in oracles.items()}
                         for algo, outset in level_dict[signal].items()}
                for signal in sigs(level_dict)}

    def auc(level_dict, universe, oracles):
        vals = level_dict["_vals"]
        res = {}
        for signal in sigs(level_dict):
            res[signal] = {}
            for name, god in oracles.items():
                a, p, ng, _ = metrics.auc_ap(vals[signal], god, universe)
                res[signal][name] = {"auc": round(a, 4) if a is not None else None,
                                     "ap": round(p, 4) if p is not None else None, "n_god": ng}
        return res

    def align(level_dict, pmd_godclass):
        res = {}
        div = level_dict["divergence"]
        for signal in sigs(level_dict):
            if signal == "divergence":
                continue
            res[signal] = {}
            for algo in metrics.ALGORITHMS:
                d, c = div[algo], level_dict[signal][algo]
                a = {"jaccard": metrics.jaccard(d, c), "both": len(d & c),
                     "div_only": len(d - c), "sig_only": len(c - d)}
                if pmd_godclass is not None:
                    a["div_only_pmd_gods"] = len((d - c) & pmd_godclass)
                    a["sig_only_pmd_gods"] = len((c - d) & pmd_godclass)
                res[signal][algo] = a
        return res

    m, c = mx["method"], mx["class"]
    return {
        "universe_note": "common per-level universe (funcs cc>0 / files wmc>0), "
                         "unlike the paper's generator-restricted universe",
        "signals_method": sigs(m), "signals_class": sigs(c),
        "method": {
            "n_univ": len(m["universe"]),
            "oracle_gods": {name: len(s) for name, s in method_oracles.items()},
            "cells": cells(m, m["universe"], method_oracles),
            "auc": auc(m, m["universe"], method_oracles),
            "alignment": align(m, None),
        },
        "class": {
            "n_univ": len(c["universe"]), "wmc47_gods": len(class_oracles["wmc47"]),
            "oracle_gods": {name: len(s) for name, s in class_oracles.items()
                            if name != "wmc47"},
            "cells": cells(c, c["universe"], class_oracles),
            "auc": auc(c, c["universe"], class_oracles),
            "alignment": align(c, pmd_godclass),
        },
    }


def analyse(root: str, pmd: str | None, ck: str | None = None) -> dict:
    res = extract.analyze_tree(root)
    funcs, wmc = res["funcs"], res["wmc"]
    if not funcs:
        return {"error": "no functions"}
    kind = classify_kind(root, funcs)
    # method level
    m_out = extract.outliers(funcs)
    m_god = {fid for fid, r in funcs.items() if r["cc"] >= CC_GOD}
    m_univ = {fid for fid, r in funcs.items() if r["gain"] > 0}
    # method-level god DEFINITIONS (textbook thresholds), so the method comparison is not
    # limited to a single oracle: McCabe CC, Campbell cognitive, deep nesting, long method,
    # long parameter list.
    method_oracles = {
        "cc14": m_god,
        "cognitive15": {fid for fid, r in funcs.items() if r.get("cognitive", 0) >= 15},
        "nesting5": {fid for fid, r in funcs.items() if r.get("nesting", 0) >= 5},
        "loc50": {fid for fid, r in funcs.items() if r.get("nloc", 0) >= 50},
        "params5": {fid for fid, r in funcs.items() if r.get("params", 0) >= 5},
    }
    # class level (cumulated generation vs WMC-47)
    cg = extract.class_gain(funcs)
    c_out = extract.class_outliers(cg)
    c_god = {f for f, w in wmc.items() if w >= extract.WMC_GOD}
    c_univ = {f for f in cg if cg[f] > 0}
    out = {
        "n_funcs": len(funcs), "n_files": len(wmc), "kind": kind,
        "method_vs_cc14": _enrich(m_out, m_god, m_univ),
        "class_vs_wmc47": _enrich(c_out, c_god, c_univ),
        "wmc_gods": len(c_god), "gen_class_outliers": len(c_out),
    }
    # leaf exclusion: WMC-gods the generation signal does NOT flag
    out["wmc_gods_flagged_by_gen"] = len(c_out & c_god)

    # --- exploratory enhancement metrics + complexity-spike comparison (all additive) ---
    # Divergence enhancements (concentration, EVT, gain x cc) and a parallel complexity
    # SPIKE signal: a relative modified-z outlier of cc / WMC, the threshold-free analogue
    # of the fixed WMC>=47 / CC>=14 references. Reported so one can ask whether coupling
    # divergence marks the same units as a complexity spike (a restatement of size) or
    # different ones (a distinct marker of concentration). See coupling_divergence/metrics.py.
    sig = metrics.signals(funcs, wmc, cg)
    m_univ_cc = {fid for fid, r in funcs.items() if r["cc"] > 0}
    c_univ_wmc = {f for f in wmc if wmc[f] > 0}
    cx = sig["complexity"]
    cx["spike_vs_cc14"] = _enrich(sig["cx_method_out"], m_god, m_univ_cc)
    cx["spike_vs_wmc47"] = _enrich(sig["cx_class_out"], c_god, c_univ_wmc)
    out["divergence"] = sig["divergence"]
    out["complexity"] = cx
    out["alignment"] = {
        "class_jaccard": metrics.jaccard(sig["div_class_out"], sig["cx_class_out"]),
        "method_jaccard": metrics.jaccard(sig["div_method_out"], sig["cx_method_out"]),
        "class_both": len(sig["div_class_out"] & sig["cx_class_out"]),
        "class_div_only": len(sig["div_class_out"] - sig["cx_class_out"]),
        "class_cx_only": len(sig["cx_class_out"] - sig["div_class_out"]),
        "method_both": len(sig["div_method_out"] & sig["cx_method_out"]),
        "method_div_only": len(sig["div_method_out"] - sig["cx_method_out"]),
        "method_cx_only": len(sig["cx_method_out"] - sig["div_method_out"]),
        # of the flags unique to each signal, how many are WMC>=47 size-gods (near-circular
        # for the complexity spike; the fair discriminant is the PMD one added below)
        "class_div_only_wmc_gods": len((sig["div_class_out"] - sig["cx_class_out"]) & c_god),
        "class_cx_only_wmc_gods": len((sig["cx_class_out"] - sig["div_class_out"]) & c_god),
    }

    # SIZE BASELINES for the headline, on the same generator universes: a modified-z spike
    # of methods-per-file (nom) and of lines (nloc) at class level, and of lines at method
    # level. If divergence is only a restatement of size, these do as well as it does.
    nom, nloc = Counter(), Counter()
    for fid, r in funcs.items():
        nom[fid[0]] += 1
        nloc[fid[0]] += r.get("nloc", 0)
    b_nom = metrics.z_outliers({f: nom[f] for f in c_univ})
    b_nloc = metrics.z_outliers({f: nloc[f] for f in c_univ})
    out["baseline"] = {
        "class_nom_vs_wmc47": _enrich(b_nom, c_god, c_univ),
        "class_nloc_vs_wmc47": _enrich(b_nloc, c_god, c_univ),
        "method_nloc_vs_cc14": _enrich(
            metrics.z_outliers({fid: funcs[fid].get("nloc", 0) for fid in m_univ}),
            m_god, m_univ),
    }

    # Java reference panel, keyed by repo-relative path over exactly the measured files.
    java_files = sorted(f for f in wmc if f.endswith(".java"))
    class_oracles: dict = {"wmc47": c_god}
    pmd_godclass = None
    if pmd:
        pmd_byrule = _pmd_violations(pmd, root, java_files)
        if pmd_byrule is None:
            out["pmd_error"] = "PMD produced no parsable report"
        else:
            class_oracles.update({"pmd_" + r: pmd_byrule.get(r, set())
                                  for r in _PMD_ORACLE_RULES})
            pg = pmd_godclass = pmd_byrule.get("GodClass", set())
            out["pmd_gods"] = len(pg)
            out["class_vs_pmd"] = _enrich(c_out, pg, c_univ)
            out["baseline"]["class_nom_vs_pmd"] = _enrich(b_nom, pg, c_univ)
            out["baseline"]["class_nloc_vs_pmd"] = _enrich(b_nloc, pg, c_univ)
            # complexity spike vs PMD, and the fair discriminant: of the class flags unique
            # to each signal, how many are PMD gods (PMD is the one oracle not purely size)
            cxo = sig["cx_class_out"]
            cx["spike_vs_pmd"] = _enrich(cxo, pg, c_univ_wmc)
            out["alignment"]["class_div_only_pmd_gods"] = len((c_out - cxo) & pg)
            out["alignment"]["class_cx_only_pmd_gods"] = len((cxo - c_out) & pg)
            # The leaf discriminant needs BOTH sides: of the WMC-gods, the share divergence
            # flags among PMD gods vs among WMC-only leaves. Equal shares = no discrimination.
            leaves = c_god - pg
            out["wmc_only_leaves"] = len(leaves)
            out["wmc_only_leaves_flagged_by_gen"] = len(c_out & leaves)
            out["wmc_pmd_gods"] = len(c_god & pg)
            out["wmc_pmd_gods_flagged_by_gen"] = len(c_out & c_god & pg)

    if ck:
        ck_defs = _ck_gods(ck, root, java_files)
        if ck_defs is None:
            out["ck_error"] = "CK produced no class table"
        else:
            class_oracles.update(ck_defs)
            out["ck_def_gods"] = {name: len(s) for name, s in ck_defs.items()}

    # full (level x signal x algorithm x oracle) comparison matrix
    out["matrix"] = _matrix_block(funcs, wmc, cg, method_oracles, class_oracles, pmd_godclass)
    return out


_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def read_repo_pins(path: str) -> list[tuple[str, str | None]]:
    """(url, commit or None) per line of a repo-list file. A line is `URL [SHA]  # comment`;
    the commit, when present, pins the exact revision to re-measure (grow writes it), so a
    re-run measures the same code rather than whatever the default branch holds today.
    Inline comments, whole-line comments and blanks are tolerated."""
    out = []
    for ln in open(path):
        toks = ln.split("#", 1)[0].split()
        if toks:
            sha = toks[1].lower() if len(toks) > 1 and _SHA.match(toks[1].lower()) else None
            out.append((toks[0], sha))
    return out


def read_repo_list(path: str) -> list[str]:
    """Git URLs from a repo-list file (any pinned commit is dropped; see read_repo_pins)."""
    return [u for u, _ in read_repo_pins(path)]


def clone_at(url: str, dest: str, sha: str | None = None,
             timeout: float | None = None) -> str | None:
    """Shallow-clone `url` into `dest`, at commit `sha` when given (fetched by id, depth 1),
    else at the default branch's latest commit. Returns None on success, else an error."""
    shutil.rmtree(dest, ignore_errors=True)
    try:
        if not sha:
            cl = subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, dest],
                                capture_output=True, text=True, timeout=timeout)
            return None if cl.returncode == 0 else "clone failed"
        os.makedirs(dest, exist_ok=True)
        for args in (["init", "--quiet"], ["remote", "add", "origin", url],
                     ["fetch", "--depth", "1", "--quiet", "origin", sha],
                     ["checkout", "--quiet", "--detach", "FETCH_HEAD"]):
            r = subprocess.run(["git", *args], cwd=dest, capture_output=True, text=True,
                               timeout=timeout)
            if r.returncode != 0:
                return f"pinned clone failed at `git {args[0]}`"
        head = _git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
        return None if head.startswith(sha) else f"pinned clone landed on {head[:10]}"
    except subprocess.TimeoutExpired:
        return f"clone timeout ({timeout}s)"


def tool_versions(pmd: str | None, ck: str | None) -> dict:
    """The versions of every tool that shapes a measurement, recorded with each run so a
    replication can tell a tool change from a real difference."""
    import hashlib
    import lizard
    import platform
    v = {"python": platform.python_version(),
         "lizard": getattr(lizard, "version", None) or getattr(lizard, "__version__", None)}
    try:
        from importlib.metadata import version
        v["lizard"] = version("lizard")
        v["coupling_divergence"] = version("coupling-divergence")
    except Exception:
        pass
    if pmd:
        r = subprocess.run([pmd, "--version"], capture_output=True, text=True)
        v["pmd"] = (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) \
            else None
    if ck:
        try:
            v["ck_jar"] = f"{os.path.basename(ck)} sha256:" + \
                hashlib.sha256(open(ck, "rb").read()).hexdigest()[:16]
        except OSError:
            v["ck_jar"] = os.path.basename(ck)
    return v


def _load_selection(manifest_path: str | None) -> dict:
    """url -> selection metadata {stratum_lang, tier, stars, rank} from a sampling manifest,
    so results can be grouped by how each repo was selected."""
    if not manifest_path or not os.path.isfile(manifest_path):
        return {}
    try:
        m = json.load(open(manifest_path))
    except Exception:
        return {}
    sel = {}
    for s in m.get("strata", []):
        for it in s.get("selected", []):
            sel[it["url"]] = {"stratum_lang": s.get("lang"), "tier": s.get("tier"),
                              "stars": it.get("stars"), "rank": it.get("rank")}
    return sel


def run(repos: list, out_path: str, pmd: str | None, keep: bool,
        manifest_path: str | None = None, ck: str | None = None) -> None:
    """Measure each repo. `repos` holds URLs or (url, commit) pins; a pinned repo is measured
    at that exact commit (and re-measured if a previous result recorded another one)."""
    results = {}
    if os.path.exists(out_path):
        try:
            results = {r["repo"]: r for r in json.load(open(out_path))}
        except Exception:
            results = {}
    selection = _load_selection(manifest_path)
    tools = tool_versions(pmd, ck)
    work = tempfile.mkdtemp(prefix="cdiv-study-")
    pins = [(r, None) if isinstance(r, str) else tuple(r) for r in repos]
    total = len(pins)
    n_pinned = sum(1 for _, sha in pins if sha)
    print(f"Coupling Divergence study: {total} repositories ({n_pinned} pinned to a commit) | "
          f"references: class WMC>=47, method CC>={CC_GOD}"
          + (", Java PMD panel" if pmd else "") + (", Java CK panel" if ck else "") + "\n"
          f"each repo is shallow-cloned (at its pinned commit, else the latest default branch), "
          f"measured, its commit recorded, then deleted. tools {tools}\n"
          f"output -> {out_path}\n", flush=True)
    for i, (url, pin) in enumerate(pins, 1):
        name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        prev = results.get(url)
        if prev and (not pin or (prev.get("commit") or "").startswith(pin)):
            print(f"[{i}/{total}] {name}: skip (already in {os.path.basename(out_path)})",
                  flush=True)
            continue
        print(f"[{i}/{total}] {name}: cloning {url}" + (f" @ {pin[:10]}" if pin else "")
              + " ...", flush=True)
        dest = os.path.join(work, name)
        err = clone_at(url, dest, pin)
        if err:
            print(f"CLONE FAIL {name}: {err}")
            results[url] = {"repo": url, "name": name, "error": err, "pinned": pin}
            json.dump(list(results.values()), open(out_path, "w"), indent=2)
            continue
        sha = _git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
        date = _git(["show", "-s", "--format=%ci", "HEAD"], cwd=dest).stdout.strip()[:10]
        lang = _lang(dest)
        try:
            a = analyse(dest, pmd if lang == "java" else None,
                        ck if lang == "java" else None)
        except Exception as e:
            a = {"error": f"{type(e).__name__}: {e}"}
        row = {"repo": url, "name": name, "lang": lang, "commit": sha, "date": date,
               **selection.get(url, {}), "tools": tools, **a}
        results[url] = row
        json.dump(list(results.values()), open(out_path, "w"), indent=2)
        cv = row.get("class_vs_wmc47", {})
        mv = row.get("method_vs_cc14", {})
        print(f"        {lang:7} {row.get('n_funcs','?'):>6} fns @ {sha[:10]} ({date}) | "
              f"class lift {cv.get('lift')} recall {cv.get('recall')} | "
              f"method lift {mv.get('lift')} recall {mv.get('recall')}"
              + (f" | PMD lift {row['class_vs_pmd']['lift']}" if 'class_vs_pmd' in row else ""),
              flush=True)
        if not keep:
            shutil.rmtree(dest, ignore_errors=True)
    rows = list(results.values())
    write_csv(rows, os.path.splitext(out_path)[0] + ".csv")
    _summary(rows)


# ---------------------------------------------------------------------------
# statistics, table, and ancillary-data package
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "name", "repo", "lang", "kind", "stratum_lang", "tier", "stars", "rank",
    "commit", "date", "n_files", "n_funcs",
    "wmc_gods", "gen_class_outliers", "class_lift", "class_recall", "class_fisher_p",
    "cc_gods", "gen_method_outliers", "method_lift", "method_recall", "method_fisher_p",
    "pmd_gods", "class_lift_vs_pmd", "wmc_only_leaves", "wmc_only_leaves_flagged_by_gen",
    "wmc_pmd_gods", "wmc_pmd_gods_flagged_by_gen",
    "class_loglift_cc", "method_loglift_cc", "pmd_loglift_cc",
    "base_class_nom_lift", "base_class_nloc_lift", "base_method_nloc_lift", "lizard",
]


def _row_to_csv(r: dict) -> dict:
    cv, mv = r.get("class_vs_wmc47") or {}, r.get("method_vs_cc14") or {}
    pv = r.get("class_vs_pmd") or {}
    bl = r.get("baseline") or {}
    return {
        "name": r.get("name"), "repo": r.get("repo"), "lang": r.get("lang"),
        "kind": r.get("kind"), "stratum_lang": r.get("stratum_lang"), "tier": r.get("tier"),
        "stars": r.get("stars"), "rank": r.get("rank"),
        "commit": r.get("commit"), "date": r.get("date"),
        "n_files": r.get("n_files"), "n_funcs": r.get("n_funcs"),
        "wmc_gods": r.get("wmc_gods"), "gen_class_outliers": r.get("gen_class_outliers"),
        "class_lift": cv.get("lift"), "class_recall": cv.get("recall"),
        "class_fisher_p": _fisher_from(cv),
        "cc_gods": mv.get("n_god"), "gen_method_outliers": mv.get("n_out"),
        "method_lift": mv.get("lift"), "method_recall": mv.get("recall"),
        "method_fisher_p": _fisher_from(mv),
        "pmd_gods": r.get("pmd_gods"), "class_lift_vs_pmd": pv.get("lift"),
        "wmc_only_leaves": r.get("wmc_only_leaves"),
        "wmc_only_leaves_flagged_by_gen": r.get("wmc_only_leaves_flagged_by_gen"),
        "wmc_pmd_gods": r.get("wmc_pmd_gods"),
        "wmc_pmd_gods_flagged_by_gen": r.get("wmc_pmd_gods_flagged_by_gen"),
        "class_loglift_cc": _r4(log_lift(cv)), "method_loglift_cc": _r4(log_lift(mv)),
        "pmd_loglift_cc": _r4(log_lift(pv)),
        "base_class_nom_lift": (bl.get("class_nom_vs_wmc47") or {}).get("lift"),
        "base_class_nloc_lift": (bl.get("class_nloc_vs_wmc47") or {}).get("lift"),
        "base_method_nloc_lift": (bl.get("method_nloc_vs_cc14") or {}).get("lift"),
        "lizard": (r.get("tools") or {}).get("lizard"),
    }


def _r4(x):
    return None if x is None else round(x, 4)


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            if not r.get("error"):
                w.writerow(_row_to_csv(r))
    print(f"\nwrote results table -> {path}")


MIN_FUNCS, MIN_UNIV = 50, 15          # the study's substance floor (grow.STUDY mirrors it)

# The size baselines each headline signal is compared against, on the SAME universe, as
# (signal block, baseline block). Divergence is only more than a restatement of size if it
# beats these; a lift alone cannot show that, since lift is bounded above by 1/base-rate.
BASELINE_PAIRS = {
    "class_vs_wmc47": ("baseline_class_nom_vs_wmc47", "baseline_class_nloc_vs_wmc47"),
    "method_vs_cc14": ("baseline_method_nloc_vs_cc14",),
    "class_vs_pmd": ("baseline_class_nom_vs_pmd", "baseline_class_nloc_vs_pmd"),
}


def _block(r: dict, key: str):
    return r.get(key) if not key.startswith("baseline_") else (r.get("baseline") or {}).get(key[9:])


def _pooled_lift(rows: list[dict], key: str, min_funcs: int = MIN_FUNCS,
                 min_univ: int = MIN_UNIV) -> dict:
    """Geometric-mean lift across repos (each repo one observation), Student-t 95% interval,
    and the repos needed for a 0.2 half-width on log-lift. Usability is decided before the
    outcome (see `usable`); a repo whose outliers hit no god stays in as a negative
    observation. Also the share of repos individually significant (Fisher p < 0.05)."""
    logs, sig, no_obs = [], 0, 0
    for r in rows:
        ll = usable_loglift(r, key, min_funcs, min_univ)
        if ll is None:
            no_obs += 1 if r.get(key) else 0
            continue
        logs.append(ll)
        p = _fisher_from(r[key])
        if p is not None and p < 0.05:
            sig += 1
    out = {"n_repos": len(logs), "sig_repos": sig, "not_usable": no_obs}
    ci = mean_ci(logs)
    out.update({k: v for k, v in ci.items() if k not in ("n", "mean_log")})
    if "sd_log" in ci:
        sd = ci["sd_log"]
        out["n_for_ci_halfwidth_0.2"] = int(_math.ceil((_Z975 * sd / 0.2) ** 2)) if sd > 0 \
            else len(logs)
    return out


def _paired_vs_baseline(rows: list[dict], key: str, base_key: str,
                        min_funcs: int = MIN_FUNCS, min_univ: int = MIN_UNIV) -> dict:
    """Paired comparison on the repos where BOTH the signal and the baseline are usable:
    the ratio of divergence lift to baseline lift (geometric mean, t interval). An interval
    above 1 means divergence finds the reference gods better than size alone does."""
    diffs = []
    for r in rows:
        a = usable_loglift(r, key, min_funcs, min_univ)
        bb = _block(r, base_key)
        b = None
        if a is not None and bb and usable({**r, "_b": bb}, "_b", min_funcs, min_univ):
            b = log_lift(bb)
        if a is not None and b is not None:
            diffs.append(a - b)
    ci = mean_ci(diffs)
    return {"n_repos": ci["n"], "lift_ratio": ci.get("mean_lift"),
            "ci95_ratio": ci.get("ci95_lift"), "excludes_1": ci.get("excludes_1")}


def _summary(rows: list[dict]) -> None:
    ok = [r for r in rows if not r.get("error") and r.get("class_vs_wmc47")]
    print("\n" + "=" * 72 + "\nPOOLED PER LANGUAGE (class generation outlier vs WMC>=47)")
    by_lang = defaultdict(list)
    for r in ok:
        by_lang[r.get("lang", "?")].append(r)
    for lang, rs in sorted(by_lang.items()):
        p = _pooled_lift(rs, "class_vs_wmc47")
        print(f"  {lang:10} {p['n_repos']:>3} usable  lift {p.get('mean_lift')}  "
              f"95% t {p.get('ci95_lift')}  individually significant {p['sig_repos']}/"
              f"{p['n_repos']}")
    for key, label in (("class_vs_wmc47", "CLASS vs WMC>=47"),
                       ("method_vs_cc14", "METHOD vs CC>=14"),
                       ("class_vs_pmd", "CLASS vs PMD GodClass")):
        p = _pooled_lift(ok, key)
        if "ci95_lift" in p:
            print(f"\nPOOLED {label} over {p['n_repos']} repos: lift {p['mean_lift']} "
                  f"95% t {p['ci95_lift']} (excludes 1: {p['excludes_1']}) | "
                  f"individually significant {p['sig_repos']}/{p['n_repos']} | "
                  f"repos for CI half-width 0.2 on log-lift: ~{p['n_for_ci_halfwidth_0.2']}"
                  f" | measured but no outlier/god to score: {p['not_usable']}")
        for bk in BASELINE_PAIRS.get(key, ()):
            q = _paired_vs_baseline(ok, key, bk)
            if q["ci95_ratio"]:
                print(f"    vs size baseline {bk[9:]}: divergence/baseline lift ratio "
                      f"{q['lift_ratio']} 95% t {q['ci95_ratio']} over {q['n_repos']} paired "
                      f"repos (beats size: {q['excludes_1'] and q['lift_ratio'] > 1})")
    # grouped by popularity tier and by type, so a difference across strata is visible
    for dim in ("tier", "kind"):
        groups = defaultdict(list)
        for r in ok:
            groups[r.get(dim)].append(r)
        if len(groups) > 1 or (groups and None not in groups):
            print(f"\nBY {dim.upper()} (class vs WMC>=47):")
            for g, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
                p = _pooled_lift(rs, "class_vs_wmc47")
                print(f"  {str(g):12} {p['n_repos']:>3} usable  lift {p.get('mean_lift')}  "
                      f"95% t {p.get('ci95_lift')}")
    kinds = Counter(r.get("kind") for r in ok)
    print(f"\nREALIZED TYPE MIX (post-hoc heuristic): "
          f"{dict(kinds)} over {len(ok)} repos")
    print("Note: each repo is one observation; lift is the geometric mean of the 0.5-corrected "
          "per-repo lift, and a repo whose outliers hit no god is kept, not dropped. "
          "'individually significant' is Fisher exact p<0.05 on that repo's outlier x god "
          "table. Lift is bounded by 1/base-rate, so compare signals by the paired ratio "
          "against the size baselines (or the matrix AUC), not by lift alone. Type "
          "(application/library) is a post-hoc heuristic, not controlled by sampling.")


def write_anc(rows: list[dict], anc_dir: str, frame_files=None) -> None:
    """Write the paper's ancillary-data package: results table + README, plus the frame
    (the repo list and sampling manifest) so the attachment shows how the population was
    chosen as well as what was found."""
    os.makedirs(anc_dir, exist_ok=True)
    write_csv(rows, os.path.join(anc_dir, "results.csv"))
    for f in (frame_files or []):
        if f and os.path.isfile(f):
            shutil.copy(f, os.path.join(anc_dir, os.path.basename(f)))
            print(f"  + frame file {os.path.basename(f)}")
    ok = [r for r in rows if not r.get("error") and r.get("class_vs_wmc47")]
    pc = _pooled_lift(ok, "class_vs_wmc47")
    langs = sorted({r.get("lang") for r in ok})
    with open(os.path.join(anc_dir, "README.txt"), "w") as fh:
        fh.write(_ANC_README.format(
            n_repos=len(ok), n_langs=len(langs), langs=", ".join(langs),
            mean_lift=pc.get("mean_lift"), ci=pc.get("ci95_lift"),
            sig=pc.get("sig_repos"), ntot=pc.get("n_repos")))
    print(f"wrote ancillary-data package -> {anc_dir}/ (results.csv, README.txt)")


_ANC_README = """Coupling Divergence: god-class/method detection study (ancillary data)
================================================================================

WHAT THIS IS
  results.csv holds one row per open-source repository, measuring how well the Coupling
  Divergence placement outlier detects textbook god classes and god methods at the
  repository's latest default-branch commit. This package is self-contained: it needs
  neither the paper nor any external repository to be understood. Each row records the
  exact commit measured, so any result can be reproduced by checking that commit out.

THE MEASURE (per function v, over calls to code defined in the same repository)
  out(v) = arguments the function passes over those calls (information it sends on)
  in(v)  = arguments passed into it by its callers
  divergence delta(v) = max(out(v) - in(v), 0)   -- net coupling flow the function creates
  A god method is a modified-z outlier (Iglewicz-Hoaglin, cutoff 3.5) in delta across the
  functions that create (delta>0). A god class is the outlier of the per-class sum of delta.
  Calls leaving into libraries do not count, so converters and mappers score near zero.

REFERENCES (the textbook "ground truth" each outlier is scored against)
  god class  = file WMC >= 47          (Lanza-Marinescu size threshold; WMC = sum of method
                                         cyclomatic complexity)
  god method = function CC >= 14       (McCabe high-risk)
  Java only  = PMD's GodClass rule     (the full Lanza-Marinescu strategy: WMC and ATFD and
                                         TCC together)

COLUMNS
  name, repo, lang, commit, date   repository identity and the exact revision measured
  kind                             application or library, a post-hoc heuristic (container or
                                   deploy descriptor, a binary-crate / __main__ / main entry);
                                   reported so the sample's type mix is visible, not controlled
  stratum_lang, tier, stars, rank  selection metadata from the sampling manifest: the
                                   language and popularity tier the repo was drawn from, its
                                   star count and rank at sampling time. Group results by
                                   these (tier, language, kind) to check the effect is stable
                                   across strata. `repo` is the full clone URL and `commit`
                                   the exact revision, so any row checks back against the code.
  n_files, n_funcs                 source files and functions parsed (tests excluded)
  wmc_gods                         files with WMC >= 47
  gen_class_outliers               files whose cumulated divergence is a modified-z outlier
  class_lift                       enrichment: P(WMC-god | divergence outlier) / base rate.
                                   1.0 = no better than chance; >1 = divergence concentrates
                                   on the god classes
  class_recall                     share of WMC-gods that contain a divergence outlier
  class_fisher_p                   one-sided Fisher exact p for that enrichment in this repo
  cc_gods, gen_method_outliers, method_lift, method_recall, method_fisher_p
                                   the same, at method level vs CC >= 14
  pmd_gods, class_lift_vs_pmd      Java only: enrichment against the full PMD GodClass
  wmc_only_leaves                  files WMC>=47 that PMD does NOT call a god (self-contained
                                   complex algorithms; Java only)
  wmc_only_leaves_flagged_by_gen   of those, how many the divergence signal flags
  wmc_pmd_gods, wmc_pmd_gods_flagged_by_gen
                                   WMC>=47 files that ARE PMD gods, and how many divergence
                                   flags. Compare the two flagged shares: divergence
                                   discriminates the god pattern from mere size only if it
                                   flags a larger share of these than of the leaves
  class_loglift_cc, method_loglift_cc, pmd_loglift_cc
                                   per-repo log-lift with the 0.5 (Haldane-Anscombe)
                                   correction -- the value the pooled statistics average; it
                                   is finite when the outliers hit no god, so failures stay in
  base_class_nom_lift, base_class_nloc_lift, base_method_nloc_lift
                                   the SIZE BASELINES: lift of a modified-z spike of methods
                                   per file / lines per file / lines per function, on the same
                                   universe. Divergence adds to size only where it beats these
  lizard                           the lizard version that parsed the repository

HEADLINE (this run)
  {n_repos} repositories, {n_langs} languages ({langs}).
  Class divergence outlier vs WMC>=47: geometric-mean lift {mean_lift} (0.5-corrected,
  every usable repo kept), 95% t interval {ci} across repos;
  individually significant (Fisher p<0.05) in {sig}/{ntot} repositories.
  Interpretation: a divergence outlier is several times more likely than chance to be a
  textbook god, consistently across languages. The measure detects gods; it is not a
  before-the-fact predictor of a forming god (a separate, unproven claim).

REPRODUCE
  pip install coupling-divergence   (or: pip install -e . from the Coupling Divergence source)
  coupling-divergence grow --anc <out dir>
      Reproduces the whole study with built-in defaults: seeded, stratified, grown until
      each language reaches its usable-repo target. Prints "CANONICAL STUDY CONFIGURATION"
      when no default is overridden. A `pmd` on PATH is used automatically for the Java
      GodClass reference.
  coupling-divergence study --repos repos.txt --anc <out dir>
      Re-measures exactly the repositories in this table, each at its recorded commit
      (repos.txt lines are `URL COMMIT`), instead of sampling afresh.
  Each clone is shallow, measured, and deleted; only the recorded commit is kept. Both are
  resumable (re-running skips repos already in the output).
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default="coupling_divergence_study.json")
    ap.add_argument("--repos", default=None, help="file with one git URL per line")
    ap.add_argument("--pmd", default=None, help="PMD binary, for the Java GodClass reference")
    ap.add_argument("--ck", default=None, help="CK jar, for the Java cohesion/coupling panel")
    ap.add_argument("--anc", default=None, help="write the ancillary-data package here "
                    "(results.csv + README.txt)")
    ap.add_argument("--keep", action="store_true", help="do not delete clones")
    args = ap.parse_args(argv)
    repos = DEFAULT_REPOS
    if args.repos:
        repos = read_repo_pins(args.repos)
    manifest = os.path.join(os.path.dirname(args.repos or "."), "sampling-manifest.json")
    run(repos, args.out, args.pmd, args.keep, manifest, args.ck)
    if args.anc:
        write_anc(list({r["repo"]: r for r in json.load(open(args.out))}.values()),
                  args.anc, frame_files=[args.repos, manifest])
    return 0


if __name__ == "__main__":
    sys.exit(main())
