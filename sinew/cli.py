"""Sinew experiment command line — mine a repository's history and find its heavy
change-coupling (sinews): thick, repeatedly co-changed bundles of methods that thread data.
Any language lizard parses, through one extractor. Method only; no result data lives in
this code.

  sinew mine         <repo> [-o base]      raw per-method shape at EVERY commit (JSONL)
  sinew analyze      <base> -o panel.csv   the derived per-(commit,method) panel as CSV
  sinew relate       <base>                complexity ↔ coupling ↔ cohesion relationships
  sinew tangle       <base>                edge-weight ladder + the dynamic (param-growth) test
  sinew sinew        <base>                the directional sinew report + span verdict
  sinew study        [--sample f] [-o dir] mine+analyse a sample; outliers, dossiers, predict
  sinew predict      <base ...>            does PAST sinew predict FUTURE change (beyond complexity)?
  sinew dossier      <base> [--repo r]     inspection dossiers for a mine's CONCERN clusters

The sample (open-source-repository-sample.csv) is drawn by open-source-repository-sampler
(./step2_sample.sh), whose run loop also clones each repository for `study`. All output data is
written under the chosen results directory (default results/<date>), which is git-ignored;
nothing is computed outside this code.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

from . import deps


DEFAULT_SAMPLE = os.path.join("sample", "open-source-repository-sample.csv")


def _results_default() -> str:
    return os.path.join("results", datetime.date.today().isoformat())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sinew", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-tests", action="store_true",
                    help="include test directories (excluded by default)")
    ap.add_argument("--lib-dir", default=None,
                    help="directory of vendored dependencies (default ./lib or $SINEW_LIB)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    mn = sub.add_parser("mine", help="raw per-method shape at every commit over history "
                        "(JSONL; append-only and resumable)")
    mn.add_argument("repo")
    mn.add_argument("-o", "--out", default=None, help="output base path (default "
                    "<results>/mines/<repo-name>); writes <out>.methods.jsonl and .commits.jsonl")
    mn.add_argument("--ref", default="HEAD", help="ref/branch to walk (default HEAD)")
    mn.add_argument("--full-ancestry", action="store_true",
                    help="walk every ancestor, not just the first-parent mainline")
    mn.add_argument("--max-commits", type=int, default=None, help="oldest N commits only")
    mn.add_argument("--recent", type=int, default=None,
                    help="mine only the most recent N commits (the mature codebase)")

    an = sub.add_parser("analyze", help="derive the per-(commit,method) panel CSV from a mine")
    an.add_argument("base", help="mine base path (<base>.methods.jsonl / .commits.jsonl)")
    an.add_argument("-o", "--csv", default=None, help="write the panel CSV here")

    rl = sub.add_parser("relate", help="relationships among complexity, coupling and cohesion")
    rl.add_argument("base", help="mine base path")
    rl.add_argument("--max-commit-size", type=int, default=30,
                    help="ignore commits touching more methods than this for co-change pairs")

    tg = sub.add_parser("tangle", help="edge-weight ladder + the dynamic param-growth test")
    tg.add_argument("base", help="mine base path")
    tg.add_argument("--max-commit-size", type=int, default=30)

    sw = sub.add_parser("sinew", help="the directional sinew report + span verdict")
    sw.add_argument("base", help="mine base path")
    sw.add_argument("--max-commit-size", type=int, default=30)

    ss = sub.add_parser("study", help="run across a sample of repos: mine, flag outliers, "
                        "aggregate, emit dossiers + the predictive test")
    ss.add_argument("-o", "--out", default=None, help="results directory (default results/<date>)")
    ss.add_argument("--sample", default=DEFAULT_SAMPLE,
                    help="the sample CSV from open-source-repository-sampler (default "
                         "sample/open-source-repository-sample.csv); each usable repository is "
                         "cloned deep at its pinned commit, mined, then deleted")
    ss.add_argument("--bases", nargs="*", default=None,
                    help="existing mine bases to aggregate instead of mining the sample")
    ss.add_argument("--recent", type=int, default=500, help="mine the most recent N commits")
    ss.add_argument("--no-dossiers", action="store_true")

    sp = sub.add_parser("predict", help="predictive-value test: does PAST sinew predict FUTURE "
                        "change beyond complexity? (split-time)")
    sp.add_argument("bases", nargs="+", help="one or more mine base paths")
    sp.add_argument("--split", type=float, default=0.7, help="history cut fraction")

    sd = sub.add_parser("dossier", help="emit inspection dossiers for a mine's CONCERN clusters "
                        "(pass --repo for source bodies + commit messages)")
    sd.add_argument("base", help="mine base path")
    sd.add_argument("-o", "--out", default=None, help="output directory (default <results>/dossiers)")
    sd.add_argument("--repo", default=None, help="repo checkout at HEAD, for source + messages")

    args = ap.parse_args(argv)
    deps.validate(args.lib_dir)                 # fail closed unless pinned deps are present
    from . import mine as mine_mod, analyze as analyze_mod, sinew_study as sinew_mod

    results = _results_default()
    if args.cmd == "mine":
        out = args.out or os.path.join(results, "mines",
                                       os.path.basename(os.path.abspath(args.repo.rstrip("/"))))
        mine_mod.mine(args.repo, out, args.ref, not args.full_ancestry,
                      args.include_tests, args.max_commits, args.recent)
        return 0
    if args.cmd == "analyze":
        csv = args.csv or os.path.join(results, os.path.basename(args.base) + ".panel.csv")
        n = analyze_mod.to_csv(args.base, csv)
        print(f"wrote panel -> {csv} ({n} rows)")
        return 0
    if args.cmd == "relate":
        analyze_mod._print_relationships(args.base, args.max_commit_size)
        return 0
    if args.cmd == "tangle":
        analyze_mod._print_tangle(args.base, args.max_commit_size)
        return 0
    if args.cmd == "sinew":
        analyze_mod._print_sinew(args.base, args.max_commit_size)
        return 0
    if args.cmd == "study":
        out = args.out or results
        if args.bases:
            rows = [sinew_mod.aggregate_row(b) for b in args.bases]
            sinew_mod._print_aggregate(rows)
            if not args.no_dossiers:
                for b in args.bases:
                    sinew_mod.dossier(b, os.path.join(out, "dossiers"))
            sinew_mod._print_predicts(args.bases)
        elif os.path.isfile(args.sample):
            sinew_mod.study(args.sample, out, args.recent, emit_dossiers=not args.no_dossiers)
        else:
            print(f"no sample at {args.sample}: draw one with ./step2_sample.sh, or pass "
                  f"--sample <csv> (or --bases <base ...> to aggregate existing mines)")
            return 2
        return 0
    if args.cmd == "predict":
        sinew_mod._print_predicts(args.bases, args.split)
        return 0
    if args.cmd == "dossier":
        out = args.out or os.path.join(results, "dossiers")
        n = sinew_mod.dossier(args.base, out, args.repo)
        print(f"wrote {n} dossiers -> {out}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
