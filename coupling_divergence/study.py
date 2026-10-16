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
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

from . import extract

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


def _lang(root: str) -> str:
    c = Counter()
    for rel in extract.source_files(root):
        c[os.path.splitext(rel)[1].lower()] += 1
    if not c:
        return "?"
    ext = c.most_common(1)[0][0]
    return {".java": "java", ".py": "python", ".js": "js", ".jsx": "js", ".ts": "ts",
            ".tsx": "ts", ".go": "go", ".c": "c", ".cc": "c++", ".cpp": "c++", ".h": "c",
            ".cs": "c#", ".rb": "ruby", ".php": "php", ".rs": "rust", ".kt": "kotlin",
            ".swift": "swift", ".scala": "scala"}.get(ext, ext.lstrip("."))


def _pmd_gods(pmd: str, srcdir: str) -> set | None:
    out = subprocess.run([pmd, "check", "-d", srcdir, "-R",
                          "category/java/design.xml/GodClass", "--format", "json",
                          "--no-cache", "--no-progress"], capture_output=True, text=True)
    try:
        d = json.loads(out.stdout)
    except Exception:
        return None
    return {os.path.basename(f["filename"]) for f in d.get("files", [])
            if any(v.get("rule") == "GodClass" for v in f.get("violations", []))}


def _enrich(outset: set, godset: set, universe: set) -> dict:
    base = len(universe & godset) / len(universe) if universe else 0
    cond = len(outset & godset) / len(outset) if outset else None
    lift = round(cond / base, 2) if (base and cond is not None) else None
    recall = round(len(outset & godset) / len(godset), 2) if godset else None
    return {"lift": lift, "recall": recall, "n_god": len(godset), "n_out": len(outset),
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
    """Fisher p from an `_enrich` block's counts."""
    if not block or block.get("n_univ") in (None, 0):
        return None
    a = block["n_overlap"]
    return fisher_greater(a, block["n_out"] - a, block["n_god"] - a,
                          block["n_univ"] - block["n_out"] - block["n_god"] + a)


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


def analyse(root: str, pmd: str | None) -> dict:
    res = extract.analyze_tree(root)
    funcs, wmc = res["funcs"], res["wmc"]
    if not funcs:
        return {"error": "no functions"}
    kind = classify_kind(root, funcs)
    # method level
    m_out = extract.outliers(funcs)
    m_god = {fid for fid, r in funcs.items() if r["cc"] >= CC_GOD}
    m_univ = {fid for fid, r in funcs.items() if r["gain"] > 0}
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
    if pmd:
        src = os.path.join(root, "src", "main", "java")
        pg = _pmd_gods(pmd, src if os.path.isdir(src) else root)
        if pg is not None:
            cob = {os.path.basename(f) for f in c_out}
            godb = {os.path.basename(f): f for f in c_god}
            out["pmd_gods"] = len(pg)
            out["class_vs_pmd"] = _enrich(cob, pg, {os.path.basename(f) for f in c_univ})
            # WMC-only leaves (WMC-god but not PMD-god) flagged by gen
            leaves = {b for b in godb if b not in pg}
            out["wmc_only_leaves"] = len(leaves)
            out["wmc_only_leaves_flagged_by_gen"] = len(cob & leaves)
    return out


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


def run(repos: list[str], out_path: str, pmd: str | None, keep: bool,
        manifest_path: str | None = None) -> None:
    results = {}
    if os.path.exists(out_path):
        try:
            results = {r["repo"]: r for r in json.load(open(out_path))}
        except Exception:
            results = {}
    selection = _load_selection(manifest_path)
    work = tempfile.mkdtemp(prefix="cdiv-study-")
    total = len(repos)
    print(f"Coupling Divergence study: {total} repositories | references: class WMC>=47, "
          f"method CC>={CC_GOD}" + (", Java PMD GodClass" if pmd else "") + "\n"
          f"each repo is shallow-cloned (latest default branch), measured, its commit "
          f"recorded, then deleted. output -> {out_path}\n", flush=True)
    for i, url in enumerate(repos, 1):
        name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        if url in results:
            print(f"[{i}/{total}] {name}: skip (already in {os.path.basename(out_path)})",
                  flush=True)
            continue
        print(f"[{i}/{total}] {name}: cloning {url} ...", flush=True)
        dest = os.path.join(work, name)
        shutil.rmtree(dest, ignore_errors=True)
        cl = _git(["clone", "--depth", "1", "--quiet", url, dest])
        if cl.returncode != 0:
            print(f"CLONE FAIL {name}: {cl.stderr.strip()[:120]}")
            results[url] = {"repo": url, "name": name, "error": "clone failed"}
            json.dump(list(results.values()), open(out_path, "w"), indent=2)
            continue
        sha = _git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
        date = _git(["show", "-s", "--format=%ci", "HEAD"], cwd=dest).stdout.strip()[:10]
        lang = _lang(dest)
        try:
            a = analyse(dest, pmd if lang == "java" else None)
        except Exception as e:
            a = {"error": f"{type(e).__name__}: {e}"}
        row = {"repo": url, "name": name, "lang": lang, "commit": sha, "date": date,
               **selection.get(url, {}), **a}
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
]


def _row_to_csv(r: dict) -> dict:
    cv, mv = r.get("class_vs_wmc47") or {}, r.get("method_vs_cc14") or {}
    pv = r.get("class_vs_pmd") or {}
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
    }


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            if not r.get("error"):
                w.writerow(_row_to_csv(r))
    print(f"\nwrote results table -> {path}")


def _pooled_lift(rows: list[dict], key: str) -> dict:
    """Mean of log-lift across repos (each repo one observation), with a normal 95% CI and
    the sample size that would reach a target CI half-width. Also the share of repos whose
    enrichment is individually significant (Fisher p < 0.05)."""
    lifts, sig, nrep = [], 0, 0
    for r in rows:
        b = r.get(key)
        if not b or not b.get("lift") or b["lift"] <= 0:
            continue
        nrep += 1
        lifts.append(_math.log(b["lift"]))
        p = _fisher_from(b)
        if p is not None and p < 0.05:
            sig += 1
    if len(lifts) < 2:
        return {"n_repos": nrep, "sig_repos": sig}
    m = sum(lifts) / len(lifts)
    sd = (sum((x - m) ** 2 for x in lifts) / (len(lifts) - 1)) ** 0.5
    se = sd / len(lifts) ** 0.5
    lo, hi = m - 1.96 * se, m + 1.96 * se
    # repos needed so the 95% CI half-width on log-lift is <= 0.2 (a ~1.22x factor)
    need = int(_math.ceil((1.96 * sd / 0.2) ** 2)) if sd > 0 else len(lifts)
    return {"n_repos": nrep, "sig_repos": sig,
            "mean_lift": round(_math.exp(m), 2),
            "ci95_lift": [round(_math.exp(lo), 2), round(_math.exp(hi), 2)],
            "excludes_1": lo > 0, "sd_log": round(sd, 3),
            "n_for_ci_halfwidth_0.2": need}


def _summary(rows: list[dict]) -> None:
    ok = [r for r in rows if not r.get("error") and r.get("class_vs_wmc47")]
    print("\n" + "=" * 72 + "\nPOOLED PER LANGUAGE (class generation outlier vs WMC>=47)")
    by_lang = defaultdict(list)
    for r in ok:
        by_lang[r.get("lang", "?")].append(r)
    for lang, rs in sorted(by_lang.items()):
        lifts = [r["class_vs_wmc47"]["lift"] for r in rs if r["class_vs_wmc47"]["lift"]]
        ml = round(sum(lifts) / len(lifts), 2) if lifts else None
        sig = sum(1 for r in rs if (_fisher_from(r["class_vs_wmc47"]) or 1) < 0.05)
        print(f"  {lang:8} {len(rs):>2} repos  mean class lift {ml}  "
              f"individually significant {sig}/{len(rs)}")
    for key, label in (("class_vs_wmc47", "CLASS vs WMC>=47"),
                       ("method_vs_cc14", "METHOD vs CC>=14")):
        p = _pooled_lift(ok, key)
        if "mean_lift" in p:
            print(f"\nPOOLED {label} over {p['n_repos']} repos: mean lift {p['mean_lift']} "
                  f"95% CI {p['ci95_lift']} (excludes 1: {p['excludes_1']}) | "
                  f"individually significant {p['sig_repos']}/{p['n_repos']} | "
                  f"repos for CI half-width 0.2 on log-lift: ~{p['n_for_ci_halfwidth_0.2']}")
    # grouped by popularity tier and by type, so a difference across strata is visible
    for dim in ("tier", "kind"):
        groups = defaultdict(list)
        for r in ok:
            groups[r.get(dim)].append(r)
        if len(groups) > 1 or (groups and None not in groups):
            print(f"\nBY {dim.upper()} (class vs WMC>=47):")
            for g, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
                lifts = [r["class_vs_wmc47"]["lift"] for r in rs if r["class_vs_wmc47"]["lift"]]
                ml = round(sum(lifts) / len(lifts), 2) if lifts else None
                print(f"  {str(g):12} {len(rs):>2} repos  mean class lift {ml}")
    kinds = Counter(r.get("kind") for r in ok)
    print(f"\nREALIZED TYPE MIX (post-hoc heuristic): "
          f"{dict(kinds)} over {len(ok)} repos")
    print("Note: each repo is one observation; 'individually significant' is Fisher exact "
          "p<0.05 on that repo's outlier x god table. The sample-size estimate uses the "
          "observed between-repo variance of log-lift. Type (application/library) is a "
          "post-hoc heuristic, not controlled by sampling.")


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
  wmc_only_leaves_flagged_by_gen   of those, how many the divergence signal flags (should be
                                   low: divergence excludes essential algorithmic complexity)

HEADLINE (this run)
  {n_repos} repositories, {n_langs} languages ({langs}).
  Class divergence outlier vs WMC>=47: mean lift {mean_lift}, 95% CI {ci} across repos;
  individually significant (Fisher p<0.05) in {sig}/{ntot} repositories.
  Interpretation: a divergence outlier is several times more likely than chance to be a
  textbook god, consistently across languages. The measure detects gods; it is not a
  before-the-fact predictor of a forming god (a separate, unproven claim).

REPRODUCE
  pip install coupling-divergence   (or: pip install -e . from the Coupling Divergence source)
  coupling-divergence study --repos <the repo URLs in this table> --anc <out dir>
  Each clone is shallow, measured, and deleted; only the recorded commit is kept. Re-running
  is resumable. For the Java PMD reference, pass --pmd <pmd binary>.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default="coupling_divergence_study.json")
    ap.add_argument("--repos", default=None, help="file with one git URL per line")
    ap.add_argument("--pmd", default=None, help="PMD binary, for the Java GodClass reference")
    ap.add_argument("--anc", default=None, help="write the ancillary-data package here "
                    "(results.csv + README.txt)")
    ap.add_argument("--keep", action="store_true", help="do not delete clones")
    args = ap.parse_args(argv)
    repos = DEFAULT_REPOS
    if args.repos:
        repos = [l.strip() for l in open(args.repos) if l.strip() and not l.startswith("#")]
    manifest = os.path.join(os.path.dirname(args.repos or "."), "sampling-manifest.json")
    run(repos, args.out, args.pmd, args.keep, manifest)
    if args.anc:
        write_anc(list({r["repo"]: r for r in json.load(open(args.out))}.values()),
                  args.anc, frame_files=[args.repos, manifest])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default="coupling_divergence_study.json")
    ap.add_argument("--repos", default=None, help="file with one git URL per line")
    ap.add_argument("--pmd", default=None, help="PMD binary, for the Java GodClass reference")
    ap.add_argument("--keep", action="store_true", help="do not delete clones")
    args = ap.parse_args(argv)
    repos = DEFAULT_REPOS
    if args.repos:
        repos = [l.strip() for l in open(args.repos) if l.strip() and not l.startswith("#")]
    run(repos, args.out, args.pmd, args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
