"""Adaptive, seeded stratified sampler that *grows the sample until each language reaches a
usable-repo target*. It exists because the plain `sample` command cannot guarantee a per-
language count: a repository's measured language and whether it yields a usable class-level
lift are only known after it is cloned and measured. A C++ repo may measure as C, a half of
the Ruby draws are small gems with no class-level outlier, and curated "awesome-*" lists
hold almost no code. So this module couples sampling to measurement and keeps topping up the
short languages, in seeded rounds, until every language is done.

A language is DONE when it has at least `--target` usable repos AND its 95% half-width on
log-lift is at or below `--hw` (the paper's precision bar), or it has reached the `--cap`,
or its strata are exhausted (recorded honestly in the manifest rather than hidden). "Usable"
excludes repos below a minimum substance floor (`--min-funcs`, `--min-univ`) so a near-empty
repo cannot consume a slot. Everything stays seeded and reproducible; the manifest simply
records more rounds and the exact selection, star and rank of every repository drawn.

    coupling-divergence grow --target 30 --cap 40 --seed 1 \\
        -o repos.txt -m sampling-manifest.json -O study.json --anc anc/ --pmd pmd

Requires the `gh` CLI, authenticated, for the GitHub search rate limit (same as `sample`).
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

from . import study, extract
from .sample import LANGS, TIERS, EXCLUDE_OWNERS

BATCH_PER_TIER = 2          # repos drawn per tier, per short language, per round
Z = 1.96
CLONE_TIMEOUT = 600         # seconds; a single clone cannot stall the whole run past this

# ---------------------------------------------------------------------------
# The canonical study configuration, in one place. Running
#     coupling-divergence grow
# with no arguments reproduces the published study exactly: same seed, same
# per-language target and precision bar, same substance floor, same languages
# and tiers. Every value here is a frozen study parameter -- change one only to
# run a DIFFERENT study, and say so. The CLI reads its defaults straight from
# this dict, so there is a single source of truth and nothing can drift.
# ---------------------------------------------------------------------------
STUDY = {
    "target": 30,           # usable repos per language (the floor)
    "cap": 40,              # stop a language here even if the precision bar is unmet
    "hw": 0.2,              # 95% half-width target on log-lift
    "seed": 1,              # the one knob that fixes the draw
    "pool": 400,            # candidates fetched per stratum before the seeded shuffle
    "min_funcs": 50,        # substance floor: minimum parsed functions
    "min_univ": 15,         # substance floor: minimum class-level universe
    "max_clones": 2000,     # hard ceiling on total clones (a safety stop, not a target)
    "pause": 2.0,           # seconds between GitHub searches
    "langs": ",".join(LANGS),
    "tiers": "high,mid,low",
}


def _search_page(query: str, per_page: int, page: int) -> dict:
    out = subprocess.run(
        ["gh", "api", "-X", "GET", "search/repositories",
         "-f", f"q={query}", "-f", "sort=stars", "-f", "order=desc",
         "-f", f"per_page={per_page}", "-f", f"page={page}"],
        capture_output=True, text=True)
    if out.returncode != 0:
        return {"total_count": 0, "items": [], "error": out.stderr.strip()[:200]}
    try:
        return json.loads(out.stdout)
    except Exception as e:
        return {"total_count": 0, "items": [], "error": str(e)}


def _candidates(lang: str, tier: str, pool: int, exclude: set, pause: float) -> tuple[list, int, str | None]:
    """Up to `pool` candidate repos for a stratum, stars descending, across pages. Returns
    (candidates, total_matched, error). GitHub caps search at 1000 results / 100 per page."""
    q = (f"language:{LANGS[lang]} {TIERS[tier]} archived:false fork:false"
         + "".join(f" -user:{o}" for o in sorted(exclude)))
    items, total, err, page = [], 0, None, 1
    while len(items) < pool and page <= 10:
        res = _search_page(q, min(100, pool - len(items)), page)
        total = res.get("total_count", total)
        if res.get("error"):
            err = res["error"]
            break
        batch = [it for it in res.get("items", [])
                 if it.get("full_name")
                 and it["full_name"].split("/")[0].lower() not in exclude]
        if not batch:
            break
        items.extend(batch)
        page += 1
        time.sleep(pause)
    cands = [{"full_name": it["full_name"], "url": it["clone_url"],
              "stars": it.get("stargazers_count"), "rank": r}
             for r, it in enumerate(items, 1)]
    return cands, total, err


def _usable_loglift(row: dict, min_funcs: int, min_univ: int) -> float | None:
    """log(class lift) if the row is a usable observation, else None. A row is usable when it
    parsed, cleared the substance floor, and produced a positive class-level lift on a
    universe large enough for an outlier to mean something."""
    if row.get("error") or row.get("excluded_min_substance"):
        return None
    if (row.get("n_funcs") or 0) < min_funcs:
        return None
    b = row.get("class_vs_wmc47") or {}
    lift = b.get("lift")
    if not lift or lift <= 0 or (b.get("n_univ") or 0) < min_univ:
        return None
    return math.log(lift)


def _halfwidth(logs: list[float]) -> float:
    n = len(logs)
    if n < 2:
        return float("inf")
    m = sum(logs) / n
    sd = (sum((x - m) ** 2 for x in logs) / (n - 1)) ** 0.5
    return Z * sd / n ** 0.5


def _needs_more(logs: list[float], target: int, cap: int, hw: float) -> bool:
    n = len(logs)
    if n < target:
        return True
    if n >= cap:
        return False
    return _halfwidth(logs) > hw


def grow(langs: list[str], tiers: list[str], target: int, cap: int, hw: float, seed: int,
         pool: int, min_funcs: int, min_univ: int, pmd: str | None, out_path: str,
         manifest_path: str, repos_path: str, keep: bool, pause: float, max_clones: int,
         exclude: set | None = None, ck: str | None = None) -> list[dict]:
    exclude = {o.lower() for o in (exclude if exclude is not None else EXCLUDE_OWNERS)}
    t0 = time.time()

    def el() -> str:
        s = int(time.time() - t0)
        return f"{s // 60:3d}:{s % 60:02d}"

    # Create the parent directories of every output path up front, so a nested -O/-m/-o
    # target (e.g. paper/data/2026-10-17/study.json) does not crash on the first checkpoint.
    for p in (out_path, manifest_path, repos_path):
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)

    results: dict = {}
    if os.path.exists(out_path):                     # resume
        try:
            results = {r["repo"]: r for r in json.load(open(out_path))}
        except Exception:
            results = {}
    if results:
        print(f"[{el()}] resuming: {len(results)} repositories already measured in "
              f"{os.path.basename(out_path)}", flush=True)

    # Per-stratum shuffled candidate order, fetched lazily, with an advancing cursor. The
    # shuffle is seeded per stratum so the order does not depend on the round schedule.
    cand_cache: dict = {}
    cursor: dict = {}
    totals: dict = {}
    exhausted: dict = {}

    def stratum_candidates(lang, tier):
        key = (lang, tier)
        if key not in cand_cache:
            print(f"[{el()}]   fetching GitHub candidates for {lang}/{tier} ...",
                  flush=True)
            cands, total, err = _candidates(lang, tier, pool, exclude, pause)
            rng = random.Random(f"{seed}:{lang}:{tier}")
            rng.shuffle(cands)
            cand_cache[key] = cands
            cursor[key] = 0
            totals[key] = total
            print(f"[{el()}]   {lang}/{tier}: {total} matched, {len(cands)} fetched"
                  + (f"  SEARCH ERROR {err}" if err else ""), flush=True)
        return cand_cache[key]

    work = tempfile.mkdtemp(prefix="cdiv-grow-")
    selection: dict = {}
    n_cloned = 0

    def measure(url, lang_stratum, tier, stars, rank) -> dict:
        nonlocal n_cloned
        name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        selection[url] = {"stratum_lang": lang_stratum, "tier": tier, "stars": stars, "rank": rank}
        dest = os.path.join(work, name)
        shutil.rmtree(dest, ignore_errors=True)
        n_cloned += 1
        tstart = time.time()
        print(f"[{el()}]   [{n_cloned}] {lang_stratum}/{tier} {name}: cloning "
              f"({stars} stars) ...", flush=True)
        try:
            cl = subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, dest],
                                capture_output=True, text=True, timeout=CLONE_TIMEOUT)
            cloned = cl.returncode == 0
            clone_err = "clone failed" if not cloned else None
        except subprocess.TimeoutExpired:
            cloned, clone_err = False, f"clone timeout ({CLONE_TIMEOUT}s)"
        if not cloned:
            row = {"repo": url, "name": name, "error": clone_err, **selection[url]}
        else:
            sha = study._git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
            date = study._git(["show", "-s", "--format=%ci", "HEAD"], cwd=dest).stdout.strip()[:10]
            lang = study._lang(dest)
            n_src = len(extract.source_files(dest))
            print(f"[{el()}]        measuring {name} ({lang}, {n_src} source files) ...",
                  flush=True)
            try:
                a = study.analyse(dest, pmd if lang == "java" else None,
                                  ck if lang == "java" else None)
            except Exception as e:
                a = {"error": f"{type(e).__name__}: {e}"}
            row = {"repo": url, "name": name, "lang": lang, "commit": sha, "date": date,
                   **selection[url], **a}
            # substance floor: recorded for audit, but does not count toward any target
            if not a.get("error") and (a.get("n_funcs") or 0) < min_funcs:
                row["excluded_min_substance"] = True
        results[url] = row
        json.dump(list(results.values()), open(out_path, "w"), indent=2)
        if not keep and os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        cv = row.get("class_vs_wmc47", {})
        tag = " EXCLUDED(small)" if row.get("excluded_min_substance") else ""
        secs = int(time.time() - tstart)
        print(f"[{el()}]        -> {row.get('lang','?'):6} "
              f"{str(row.get('n_funcs','-')):>7} fns  class lift {cv.get('lift')}"
              + (f"  ERROR: {row['error']}" if row.get("error") else "") + tag
              + f"  ({secs}s)", flush=True)
        return row

    def logs_by_lang():
        by: dict = {L: [] for L in langs}
        for r in results.values():
            ll = _usable_loglift(r, min_funcs, min_univ)
            if ll is not None and r.get("lang") in by:
                by[r["lang"]].append(ll)
        return by

    rnd = 0
    while n_cloned < max_clones:
        rnd += 1
        by = logs_by_lang()
        short = [L for L in langs
                 if not exhausted.get(L) and _needs_more(by[L], target, cap, hw)]
        done = [L for L in langs if L not in short]
        # per-language progress dashboard, so the run's state is always visible
        def _cell(L):
            n = len(by[L])
            mark = "x" if exhausted.get(L) else ("ok" if L in done else "..")
            return f"{L}:{n}/{target}{mark}"
        print(f"\n[{el()}] ===== round {rnd} | cloned {n_cloned} | "
              f"done {len(done)}/{len(langs)} languages =====\n"
              f"        " + "  ".join(_cell(L) for L in langs), flush=True)
        if not short:
            break
        print(f"[{el()}] topping up: " + ", ".join(short), flush=True)
        drew_any = False
        for L in short:
            took = 0
            for tier in tiers:
                cands = stratum_candidates(L, tier)
                key = (L, tier)
                while cursor[key] < len(cands) and took < BATCH_PER_TIER * len(tiers):
                    it = cands[cursor[key]]
                    cursor[key] += 1
                    if it["url"] in results:
                        continue
                    measure(it["url"], L, tier, it["stars"], it["rank"])
                    took += 1
                    drew_any = True
                    if took >= BATCH_PER_TIER:
                        break
                if n_cloned >= max_clones:
                    break
            # all strata for this language drained and still short -> mark exhausted
            if took == 0 and all(cursor.get((L, t), 0) >= len(stratum_candidates(L, t))
                                 for t in tiers):
                exhausted[L] = True
                print(f"[{el()}]   {L}: strata exhausted at {len(by[L])} usable "
                      f"(population too small for target {target}) -- reported as-is",
                      flush=True)
            if n_cloned >= max_clones:
                break
        if not drew_any:
            break

    rows = list(results.values())
    _write_manifest(manifest_path, rows, selection, totals, exhausted, seed, target, cap, hw,
                    min_funcs, min_univ, langs, tiers, sorted(exclude))
    _write_repos(repos_path, rows)
    study.write_csv(rows, os.path.splitext(out_path)[0] + ".csv")
    study._summary(rows)
    _report_targets(rows, langs, target, cap, hw, min_funcs, min_univ, exhausted)
    return rows


def _write_manifest(path, rows, selection, totals, exhausted, seed, target, cap, hw,
                    min_funcs, min_univ, langs, tiers, exclude) -> None:
    strata: dict = {}
    for r in rows:
        key = (r.get("stratum_lang"), r.get("tier"))
        if key[0] is None:
            continue
        strata.setdefault(key, []).append(
            {"url": r["repo"], "full_name": r.get("name"),
             "stars": r.get("stars"), "rank": r.get("rank")})
    manifest = {
        "generated_by": "coupling-divergence grow",
        "sampled_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        "seed": seed, "mode": "adaptive-grow",
        "target_per_lang": target, "cap_per_lang": cap, "halfwidth_target_loglift": hw,
        "min_funcs": min_funcs, "min_univ": min_univ,
        "langs": langs, "tiers": {t: TIERS[t] for t in tiers},
        "excluded_owners": exclude,
        "exhausted_langs": sorted(k for k, v in exhausted.items() if v),
        "note": ("Frozen frame, grown adaptively until each language met the usable-repo "
                 "target and precision bar (or its population was exhausted). Re-running "
                 "reproduces the METHOD; GitHub moves, so the exact list is pinned here."),
        "strata": [{"lang": k[0], "tier": k[1], "stars_qualifier": TIERS.get(k[1]),
                    "total_matched": totals.get(k), "selected": v}
                   for k, v in sorted(strata.items())],
    }
    json.dump(manifest, open(path, "w"), indent=2)
    print(f"\nwrote manifest -> {path}")


def _write_repos(path, rows) -> None:
    by_lang: dict = {}
    for r in rows:
        by_lang.setdefault(r.get("stratum_lang") or "?", []).append(r)
    with open(path, "w") as fh:
        fh.write("# Repositories sampled by `coupling-divergence grow` (adaptive, seeded).\n")
        fh.write("# The method is in coupling_divergence/grow.py; the frozen frame with every\n")
        fh.write("# query, match count and selection is in the manifest beside this file.\n")
        for lang in sorted(by_lang):
            fh.write(f"# --- {lang} ---\n")
            for r in sorted(by_lang[lang], key=lambda x: str(x.get("tier"))):
                fh.write(f"{r['repo']}   # {r.get('tier')}, {r.get('stars')} stars\n")
    print(f"wrote repo list -> {path} ({len(rows)} repos)")


def _report_targets(rows, langs, target, cap, hw, min_funcs, min_univ, exhausted) -> None:
    by: dict = {L: [] for L in langs}
    for r in rows:
        ll = _usable_loglift(r, min_funcs, min_univ)
        if ll is not None and r.get("lang") in by:
            by[r["lang"]].append(ll)
    print("\n" + "=" * 72 + "\nPER-LANGUAGE TARGET STATUS (usable class-level observations)")
    for L in sorted(by):
        logs = by[L]
        n = len(logs)
        hwv = _halfwidth(logs)
        met = (n >= target and hwv <= hw) or exhausted.get(L)
        why = "exhausted" if exhausted.get(L) else ("OK" if met else "short")
        lift = round(math.exp(sum(logs) / n), 2) if n else None
        print(f"  {L:7} usable {n:>3}/{target}  lift {str(lift):>5}  "
              f"half-width {hwv:.3f}{'' if hwv<=hw else ' (>'+str(hw)+')'}  [{why}]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default="repos.txt", help="(informational) repo list path")
    ap.add_argument("-O", "--study-out", default="coupling-divergence_study.json")
    ap.add_argument("-m", "--manifest", default="sampling-manifest.json")
    ap.add_argument("--target", type=int, default=STUDY["target"],
                    help=f"usable repos per language, the floor (study default {STUDY['target']})")
    ap.add_argument("--cap", type=int, default=STUDY["cap"],
                    help=f"stop a language at this many usable even if the precision bar is "
                         f"unmet (study default {STUDY['cap']})")
    ap.add_argument("--hw", type=float, default=STUDY["hw"],
                    help=f"95%% half-width target on log-lift (study default {STUDY['hw']})")
    ap.add_argument("--seed", type=int, default=STUDY["seed"],
                    help=f"the draw-fixing seed (study default {STUDY['seed']})")
    ap.add_argument("--pool", type=int, default=STUDY["pool"],
                    help=f"max candidates fetched per stratum (study default {STUDY['pool']})")
    ap.add_argument("--min-funcs", type=int, default=STUDY["min_funcs"],
                    help=f"substance floor: skip repos with fewer parsed functions "
                         f"(study default {STUDY['min_funcs']})")
    ap.add_argument("--min-univ", type=int, default=STUDY["min_univ"],
                    help=f"substance floor: minimum class-level universe for a usable "
                         f"observation (study default {STUDY['min_univ']})")
    ap.add_argument("--pmd", default=None, help="PMD binary for the Java GodClass reference; "
                    "if omitted, a `pmd` on PATH is used automatically")
    ap.add_argument("--ck", default=None, help="CK jar (mauricioaniche/ck) for the Java "
                    "LCOM/TCC/CBO cohesion-and-coupling god definitions (optional)")
    ap.add_argument("--anc", default=None, help="write the ancillary-data package here")
    ap.add_argument("--keep", action="store_true", help="do not delete clones")
    ap.add_argument("--max-clones", type=int, default=STUDY["max_clones"],
                    help=f"hard ceiling on total clones (study default {STUDY['max_clones']})")
    ap.add_argument("--pause", type=float, default=STUDY["pause"],
                    help=f"seconds between GitHub searches (study default {STUDY['pause']})")
    ap.add_argument("--langs", default=STUDY["langs"])
    ap.add_argument("--tiers", default=STUDY["tiers"])
    args = ap.parse_args(argv)
    if not args.pmd:
        args.pmd = shutil.which("pmd")      # auto-detect so the Java reference needs no flag
    langs = [x.strip() for x in args.langs.split(",") if x.strip() in LANGS]
    tiers = [x.strip() for x in args.tiers.split(",") if x.strip() in TIERS]
    canonical = all(getattr(args, k.replace("-", "_")) == v for k, v in STUDY.items())
    pmd_label = args.pmd or "not found on PATH -- Java rows will omit the PMD comparison"
    banner = ("=> CANONICAL STUDY CONFIGURATION (reproduces the published run)"
              if canonical else "=> non-default parameters: this is a DIFFERENT study")
    print(f"adaptive grow: target {args.target}/lang (cap {args.cap}, half-width {args.hw}) "
          f"over {len(langs)} languages x {len(tiers)} tiers  seed {args.seed}\n"
          f"substance floor: >= {args.min_funcs} functions, >= {args.min_univ} class universe\n"
          f"Java GodClass reference (PMD): {pmd_label}\n"
          f"{banner}\n", flush=True)
    rows = grow(langs, tiers, args.target, args.cap, args.hw, args.seed, args.pool,
                args.min_funcs, args.min_univ, args.pmd, args.study_out, args.manifest,
                args.out, args.keep, args.pause, args.max_clones, ck=args.ck)
    if args.anc:
        study.write_anc(rows, args.anc, frame_files=[args.out, args.manifest])
    return 0


if __name__ == "__main__":
    sys.exit(main())
