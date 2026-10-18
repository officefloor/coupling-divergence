"""Sinew experiment command line — mine a repository's history and find its heavy
change-coupling (sinews): thread-linked, repeatedly co-changed bundles of methods. Any
language lizard parses, through one extractor. Method only; no result data lives in this code.

  sinew mine         <repo> [-o base]      raw per-method shape at EVERY commit (JSONL)
  sinew analyze      <base> -o panel.csv   the derived per-(commit,method) panel as CSV
  sinew sinew        <base>                the sinew report: bonds, spans, null, controls
  sinew study        [--sample f] [-o dir] mine+analyse a sample; aggregate, pool, dossiers
  sinew predict      <base ...>            does PAST sinew predict FUTURE change beyond baselines?
  sinew dossier      <base> [--repo r]     inspection dossiers for a mine's CONCERN clusters
  sinew rate         <dossier-dir>         AI rating of blind dossiers (decision + justification
                                           on record); --score unblinds against the key
  sinew relate       <base>                EXPLORATORY: complexity ↔ coupling ↔ cohesion
  sinew tangle       <base>                EXPLORATORY: edge-weight ladder + param-growth test

The sample (open-source-repository-sample.csv) is drawn by open-source-repository-sampler
(./step3_sample.sh), whose run loop also clones each repository for `study`. All output data is
written under the chosen results directory (default results/<date>), which is git-ignored;
nothing is computed outside this code. Every analysis threshold is in sinew/analyze.py PARAMS
and is recorded with the results.
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


def _langs(s: str | None):
    return {l.strip() for l in s.split(",") if l.strip()} if s else None


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
    mn.add_argument("--langs", default=None,
                    help="comma list of language keys to mine, e.g. java (default: all)")

    an = sub.add_parser("analyze", help="derive the per-(commit,method) panel CSV from a mine")
    an.add_argument("base", help="mine base path (<base>.methods.jsonl / .commits.jsonl)")
    an.add_argument("-o", "--csv", default=None, help="write the panel CSV here")

    rl = sub.add_parser("relate", help="EXPLORATORY (name-resolved edges): relationships "
                        "among complexity, coupling and cohesion")
    rl.add_argument("base", help="mine base path")
    rl.add_argument("--max-commit-size", type=int, default=None)

    tg = sub.add_parser("tangle", help="EXPLORATORY (name-resolved edges): edge-weight "
                        "ladder + the dynamic param-growth test")
    tg.add_argument("base", help="mine base path")
    tg.add_argument("--max-commit-size", type=int, default=None)

    sw = sub.add_parser("sinew", help="the sinew report: thread-linked co-change bonds, heavy "
                        "spans, permutation null, controls")
    sw.add_argument("base", help="mine base path")
    sw.add_argument("--max-commit-size", type=int, default=None)
    sw.add_argument("--null", type=int, default=None,
                    help="permutation-null draws (default PARAMS; 0 to skip)")

    ss = sub.add_parser("study", help="run across a sample of repos: mine, analyse, null, "
                        "predict, aggregate, pool, dossiers")
    ss.add_argument("-o", "--out", default=None, help="results directory (default results/<date>)")
    ss.add_argument("--sample", default=DEFAULT_SAMPLE,
                    help="the sample CSV from open-source-repository-sampler (default "
                         "sample/open-source-repository-sample.csv); each usable repository is "
                         "cloned deep at its pinned commit, mined, then deleted")
    ss.add_argument("--bases", nargs="*", default=None,
                    help="existing mine bases to aggregate instead of mining the sample")
    ss.add_argument("--recent", type=int, default=500, help="mine the most recent N commits")
    ss.add_argument("--no-dossiers", action="store_true")
    ss.add_argument("--all-languages", action="store_true",
                    help="mine every language in a repository, not only the one it was "
                         "sampled as")
    ss.add_argument("--rate", action="store_true",
                    help="write blind dossiers per project and rate them with the raters in "
                         "rater.json (from `sinew rate --check --save`)")
    ss.add_argument("--rater-config", default=None, help="rater configuration (default rater.json)")
    ss.add_argument("--null", type=int, default=None, help="permutation-null draws per project")
    ss.add_argument("--bootstrap", type=int, default=None, help="bootstrap resamples")

    sp = sub.add_parser("predict", help="predictive-value test: does PAST sinew predict FUTURE "
                        "change beyond past frequency, co-change, size and complexity?")
    sp.add_argument("bases", nargs="+", help="one or more mine base paths")
    sp.add_argument("--split", type=float, default=None, help="history cut fraction")

    sd = sub.add_parser("dossier", help="emit inspection dossiers for a mine's CONCERN clusters "
                        "(pass --repo for source bodies + commit messages)")
    sd.add_argument("base", help="mine base path")
    sd.add_argument("-o", "--out", default=None, help="output directory (default <results>/dossiers)")
    sd.add_argument("--repo", default=None, help="repo checkout at HEAD, for source + messages")
    sd.add_argument("--blind", action="store_true",
                    help="also write contract clusters and decoys under shuffled numbers, "
                         "with the truth in <out>/<project>.key.json (rater study)")

    rt = sub.add_parser("rate", help="have one or more models rate a folder of BLIND dossiers, "
                        "logging every decision and justification to ratings.jsonl; "
                        "--score joins the log to the key files and writes scorecard.json")
    rt.add_argument("dossier_dir", help="folder written by `sinew dossier --blind`")
    rt.add_argument("--models", default=None,
                    help="comma list of model ids (default claude-opus-5-5); use several "
                         "families for independent raters")
    rt.add_argument("--runs", type=int, default=None, help="ratings per model per dossier")
    rt.add_argument("--effort", default=None, help="thinking effort (default high)")
    rt.add_argument("--score", action="store_true",
                    help="do not rate; unblind the existing ratings against the key")
    rt.add_argument("--check", action="store_true",
                    help="verify the raters on the planted ground truth (sinew/planted.py) "
                         "instead of rating DOSSIER_DIR; DOSSIER_DIR is then the work directory")
    rt.add_argument("--save", action="store_true",
                    help="with --check: when it passes, save the raters to rater.json so "
                         "`sinew study` and ./step4_run.sh use them")
    rt.add_argument("--config", default=None,
                    help="rater configuration file (default rater.json)")

    args = ap.parse_args(argv)
    deps.validate(args.lib_dir)                 # fail closed unless pinned deps are present
    from . import mine as mine_mod, analyze as analyze_mod, sinew_study as sinew_mod

    results = _results_default()
    if args.cmd == "mine":
        out = args.out or os.path.join(results, "mines",
                                       os.path.basename(os.path.abspath(args.repo.rstrip("/"))))
        mine_mod.mine(args.repo, out, args.ref, not args.full_ancestry,
                      args.include_tests, args.max_commits, args.recent, _langs(args.langs))
        return 0
    if args.cmd == "analyze":
        csv = args.csv or os.path.join(results, os.path.basename(args.base) + ".panel.csv")
        n = analyze_mod.to_csv(args.base, csv)
        print(f"wrote panel -> {csv} ({n} rows)")
        return 0
    cap = analyze_mod.PARAMS["max_commit_size"]
    if args.cmd == "relate":
        analyze_mod._print_relationships(args.base, args.max_commit_size or cap)
        return 0
    if args.cmd == "tangle":
        analyze_mod._print_tangle(args.base, args.max_commit_size or cap)
        return 0
    if args.cmd == "sinew":
        analyze_mod._print_sinew(args.base, args.max_commit_size, args.null)
        return 0
    if args.cmd == "study":
        out = args.out or results
        params = {}
        if args.null is not None:
            params["null_samples"] = args.null
        if args.bootstrap is not None:
            params["bootstrap"] = args.bootstrap
        if args.bases:
            rows = [sinew_mod.aggregate_row(b, params) for b in args.bases]
            sinew_mod._print_aggregate(rows)
            if not args.no_dossiers:
                for b in args.bases:
                    sinew_mod.dossier(b, os.path.join(out, "dossiers"))
        elif os.path.isfile(args.sample):
            rater = None
            if args.rate:
                from . import rate as rate_mod
                rater = rate_mod.load_config(args.rater_config)
                if not rater:
                    print("--rate needs a verified rater configuration: run "
                          "`sinew rate --check --save <work-dir>` (./step2_rater.sh) first")
                    return 2
            sinew_mod.study(args.sample, out, args.recent, emit_dossiers=not args.no_dossiers,
                            params=params, restrict_language=not args.all_languages,
                            rater=rater)
        else:
            print(f"no sample at {args.sample}: draw one with ./step3_sample.sh, or pass "
                  f"--sample <csv> (or --bases <base ...> to aggregate existing mines)")
            return 2
        return 0
    if args.cmd == "predict":
        sinew_mod._print_predicts(args.bases, args.split or analyze_mod.PARAMS["split"])
        return 0
    if args.cmd == "rate":
        from . import rate as rate_mod
        models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
        if args.check:
            card = rate_mod.check(args.dossier_dir, models, args.runs, args.effort)
            if card["passed"] and args.save:
                path = rate_mod.save_config(card["models"], card["runs"], card["effort"],
                                            {"work_dir": card["work_dir"],
                                             "per_model": card["per_model"]}, args.config)
                print(f"saved raters -> {path}")
            return 0 if card["passed"] else 1
        if args.score:
            rate_mod.score(args.dossier_dir)
        else:
            rate_mod.rate(args.dossier_dir, models, args.runs, effort=args.effort,
                          config=args.config)
        return 0
    if args.cmd == "dossier":
        out = args.out or os.path.join(results, "dossiers")
        n = sinew_mod.dossier(args.base, out, args.repo, blind=args.blind)
        print(f"wrote {n} dossiers -> {out}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
