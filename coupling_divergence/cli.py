"""coupling-divergence command line.

  coupling-divergence report   <repo>              areas of concern at HEAD (the forming gods)
  coupling-divergence collect  <repo> [-o f.json]  the divergence trajectory over sampled history
  coupling-divergence evaluate <repo> [repo ...]   lead-lag: does divergence predict the god first
  coupling-divergence study    [--repos f]         multi-language detection study vs textbook gods
  coupling-divergence sample   [--k N --seed S]    seeded stratified GitHub sample -> repos + manifest
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import extract, history, evaluate, study, sample


def _report(repo: str, top: int, include_tests: bool) -> int:
    res = extract.analyze_tree(repo, include_tests)
    funcs, wmc = res["funcs"], res["wmc"]
    gains = {fid: r["gain"] for fid, r in funcs.items() if r["gain"] > 0}
    if not gains:
        print("no generators found (is this a source tree?)")
        return 1
    outs = extract.outliers(funcs)
    gods = {f for f, w in wmc.items() if w >= extract.WMC_GOD}
    ranked = sorted(gains, key=lambda k: gains[k], reverse=True)
    print(f"{repo}: {len(funcs)} functions, {len(gods)} god files (WMC>={extract.WMC_GOD}), "
          f"{len(outs)} divergence outliers\n")
    print(f"  {'gain':>6} {'fileWMC':>8}  status                 node")
    for fid in ranked[:top]:
        w = wmc.get(fid[0], 0)
        tag = ("GOD + outlier" if fid in outs and fid[0] in gods else
               "forming (outlier, not yet god)" if fid in outs else
               "in a god file" if fid[0] in gods else "")
        print(f"  {gains[fid]:>6} {w:>8}  {tag:<22} {fid[1]} ({os.path.basename(fid[0])})")
    return 0


def _collect(repo: str, samples: int, out: str | None, include_tests: bool) -> int:
    series = history.walk(repo, samples, include_tests)
    data = [{"i": s["i"], "sha": s["sha"], "time": s["time"],
             "n_god_files": sum(1 for w in s["wmc"].values() if w >= extract.WMC_GOD),
             "class_outlier_files": s["class_outlier_files"],
             "method_outlier_files": s["method_outlier_files"],
             "top_by_file": {f: list(v) for f, v in s["top_by_file"].items()}}
            for s in series]
    text = json.dumps(data, indent=2)
    if out:
        with open(out, "w") as fh:
            fh.write(text)
        print(f"wrote {out} ({len(series)} sampled commits)")
    else:
        print(text)
    return 0


def _evaluate(repos: list[str], samples: int, out: str | None, include_tests: bool) -> int:
    results = [evaluate.evaluate_repo(r, samples, include_tests) for r in repos]
    print(evaluate.summarise(results))
    if out:
        with open(out, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nwrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="coupling-divergence", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-tests", action="store_true",
                    help="include test directories (excluded by default)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="areas of concern at HEAD")
    r.add_argument("repo")
    r.add_argument("--top", type=int, default=20)
    c = sub.add_parser("collect", help="divergence trajectory over history")
    c.add_argument("repo")
    c.add_argument("--samples", type=int, default=40)
    c.add_argument("-o", "--out", default=None)
    e = sub.add_parser("evaluate", help="lead-lag prediction over one or more repos")
    e.add_argument("repos", nargs="+")
    e.add_argument("--samples", type=int, default=40)
    e.add_argument("-o", "--out", default=None)
    st = sub.add_parser("study", help="multi-language detection study vs textbook gods "
                        "(clones latest default branch, records the commit, deletes clone)")
    st.add_argument("-o", "--out", default="coupling-divergence_study.json")
    st.add_argument("--repos", default=None, help="file with one git URL per line")
    st.add_argument("--pmd", default=None, help="PMD binary, for the Java GodClass reference")
    st.add_argument("--anc", default=None, help="write the ancillary-data package here "
                    "(results.csv + README.txt)")
    st.add_argument("--keep", action="store_true", help="do not delete clones")
    sm = sub.add_parser("sample", help="seeded stratified GitHub sample (needs the gh CLI) "
                        "-> repos list + auditable manifest")
    sm.add_argument("-o", "--out", default="repos.txt")
    sm.add_argument("-m", "--manifest", default="sampling-manifest.json")
    sm.add_argument("--k", type=int, default=3, help="repos per (language, tier) stratum")
    sm.add_argument("--seed", type=int, default=1)
    sm.add_argument("--pool", type=int, default=100, help="candidates fetched per stratum")
    sm.add_argument("--langs", default=",".join(sample.LANGS))
    sm.add_argument("--tiers", default="high,mid,low")
    args = ap.parse_args(argv)

    if args.cmd == "report":
        return _report(args.repo, args.top, args.include_tests)
    if args.cmd == "collect":
        return _collect(args.repo, args.samples, args.out, args.include_tests)
    if args.cmd == "evaluate":
        return _evaluate(args.repos, args.samples, args.out, args.include_tests)
    if args.cmd == "study":
        repos = study.DEFAULT_REPOS
        if args.repos:
            repos = [x.strip() for x in open(args.repos) if x.strip() and not x.startswith("#")]
        study.run(repos, args.out, args.pmd, args.keep)
        if args.anc:
            import json as _json
            rows = list({r["repo"]: r for r in _json.load(open(args.out))}.values())
            manifest = os.path.join(os.path.dirname(args.repos or "."), "sampling-manifest.json")
            study.write_anc(rows, args.anc, frame_files=[args.repos, manifest])
        return 0
    if args.cmd == "sample":
        return sample.main(["-o", args.out, "-m", args.manifest, "--k", str(args.k),
                            "--seed", str(args.seed), "--pool", str(args.pool),
                            "--langs", args.langs, "--tiers", args.tiers])
    return 2


if __name__ == "__main__":
    sys.exit(main())
