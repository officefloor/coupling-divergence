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
import json
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
    return {"lift": lift, "recall": recall, "n_god": len(godset), "n_out": len(outset)}


def analyse(root: str, pmd: str | None) -> dict:
    res = extract.analyze_tree(root)
    funcs, wmc = res["funcs"], res["wmc"]
    if not funcs:
        return {"error": "no functions"}
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
        "n_funcs": len(funcs), "n_files": len(wmc),
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


def run(repos: list[str], out_path: str, pmd: str | None, keep: bool) -> None:
    results = {}
    if os.path.exists(out_path):
        try:
            results = {r["repo"]: r for r in json.load(open(out_path))}
        except Exception:
            results = {}
    work = tempfile.mkdtemp(prefix="godwatch-study-")
    total = len(repos)
    print(f"GodWatch study: {total} repositories | references: class WMC>=47, "
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
        row = {"repo": url, "name": name, "lang": lang, "commit": sha, "date": date, **a}
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
    _summary(list(results.values()))


def _summary(rows: list[dict]) -> None:
    print("\n" + "=" * 70 + "\nPOOLED PER LANGUAGE (class generation outlier vs WMC>=47)")
    by_lang = defaultdict(list)
    for r in rows:
        if r.get("class_vs_wmc47"):
            by_lang[r.get("lang", "?")].append(r)
    for lang, rs in sorted(by_lang.items()):
        lifts = [r["class_vs_wmc47"]["lift"] for r in rs if r["class_vs_wmc47"]["lift"]]
        recs = [r["class_vs_wmc47"]["recall"] for r in rs if r["class_vs_wmc47"]["recall"] is not None]
        ml = round(sum(lifts) / len(lifts), 2) if lifts else None
        mr = round(sum(recs) / len(recs), 2) if recs else None
        print(f"  {lang:8} {len(rs):>2} repos  mean class lift {ml}  mean recall {mr}  "
              f"repos={[r['name'] for r in rs]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default="godwatch_study.json")
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
