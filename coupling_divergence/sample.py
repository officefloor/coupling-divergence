"""Seeded, stratified sample of GitHub repositories, in code so the frame is auditable.

This exists for one reason: the author is measuring a metric on code, and some of that code
is the author's own ecosystem, so the honest defence against bias is to let anyone reproduce
exactly how the study population was chosen. This module draws that population from the
GitHub search API, stratified by language and by popularity tier (a proxy for the spread
from mature to rough code), picking a seeded random subset within each stratum. It writes
two files:

  * a repo list (one git URL per line), which the study then measures;
  * a manifest (JSON) recording the seed, the sampling date, every search query, how many
    repositories matched each stratum, and which were selected with their star counts and
    rank. That manifest IS the frozen frame.

A caveat stated plainly, because transparency is the point: GitHub is a moving target. Stars
change and repositories appear and disappear, so re-running the sampler later reproduces the
METHOD, not necessarily the identical list. The committed manifest and repo list pin the
exact population used; re-running shows how it was obtained. Popularity tier is a proxy for
quality, not a measurement of it, and application-vs-library type is reported post-hoc by the
study, not controlled here (GitHub topics are too sparse to stratify on reliably).

Requires the `gh` CLI, authenticated (`gh auth status`), for the higher search rate limit.

    coupling-divergence sample --k 3 --seed 1 -o repos.txt -m sampling-manifest.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import subprocess
import sys
import time

# Our label -> GitHub `language:` qualifier.
LANGS = {
    "java": "Java", "python": "Python", "javascript": "JavaScript",
    "typescript": "TypeScript", "go": "Go", "c": "C", "c++": "C++",
    "c#": "C#", "ruby": "Ruby", "php": "PHP", "rust": "Rust",
}
# Popularity tiers, a proxy for the spread from heavily-maintained to rougher code.
TIERS = {"high": "stars:>5000", "mid": "stars:1000..5000", "low": "stars:200..1000"}


def _search(query: str, per_page: int) -> dict:
    """One GitHub search/repositories page, by stars descending. Returns the parsed JSON."""
    out = subprocess.run(
        ["gh", "api", "-X", "GET", "search/repositories",
         "-f", f"q={query}", "-f", "sort=stars", "-f", "order=desc",
         "-f", f"per_page={per_page}"],
        capture_output=True, text=True)
    if out.returncode != 0:
        return {"total_count": 0, "items": [], "error": out.stderr.strip()[:200]}
    try:
        return json.loads(out.stdout)
    except Exception as e:
        return {"total_count": 0, "items": [], "error": str(e)}


def sample(langs: list[str], tiers: list[str], k: int, seed: int, pool: int,
           pause: float = 2.0) -> dict:
    rng = random.Random(seed)
    chosen: dict = {}            # full_name -> (lang, tier, item)
    strata: list = []
    for lang in langs:
        for tier in tiers:
            q = f"language:{LANGS[lang]} {TIERS[tier]} archived:false fork:false"
            res = _search(q, pool)
            items = [it for it in res.get("items", []) if it.get("full_name")]
            fetched = [{"full_name": it["full_name"], "url": it["clone_url"],
                        "stars": it.get("stargazers_count"), "rank": r}
                       for r, it in enumerate(items, 1)]
            cand = [it for it in fetched if it["full_name"] not in chosen]
            rng.shuffle(cand)
            picked = cand[:k]
            for it in picked:
                chosen[it["full_name"]] = (lang, tier, it)
            strata.append({"lang": lang, "tier": tier, "stars_qualifier": TIERS[tier],
                           "query": q, "total_matched": res.get("total_count", 0),
                           "fetched": len(fetched), "selected": picked,
                           "error": res.get("error")})
            print(f"  {lang:11} {tier:4}  matched {res.get('total_count', 0):>6}  "
                  f"fetched {len(fetched):>3}  selected {len(picked)}"
                  + (f"  ERROR {res['error']}" if res.get("error") else ""), flush=True)
            time.sleep(pause)
    return {
        "generated_by": "coupling-divergence sample",
        "sampled_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        "seed": seed, "k_per_stratum": k, "pool_per_stratum": pool,
        "langs": langs, "tiers": {t: TIERS[t] for t in tiers},
        "note": ("Frozen frame. Re-running reproduces the METHOD; GitHub is a moving target, "
                 "so the exact list is pinned here, not guaranteed identical on re-run. "
                 "Tier is a popularity proxy for quality; app-vs-library is reported post-hoc "
                 "by the study."),
        "n_selected": len(chosen), "strata": strata,
    }


def write_repos(manifest: dict, path: str) -> None:
    with open(path, "w") as fh:
        fh.write(f"# Repositories sampled by `coupling-divergence sample` "
                 f"(seed {manifest['seed']}, {manifest['sampled_at_utc']}).\n")
        fh.write("# Stratified by language x popularity tier; seeded random pick per stratum.\n")
        fh.write("# The method is in coupling_divergence/sample.py; the frozen frame with "
                 "every query and\n# match count is in the manifest beside this file. "
                 "GitHub moves, so this list is the record.\n")
        by_lang: dict = {}
        for s in manifest["strata"]:
            for it in s["selected"]:
                by_lang.setdefault(s["lang"], []).append((s["tier"], it))
        for lang in sorted(by_lang):
            fh.write(f"# --- {lang} ---\n")
            for tier, it in sorted(by_lang[lang], key=lambda x: x[0]):
                fh.write(f"{it['url']}   # {tier}, {it['stars']} stars\n")
    print(f"wrote repo list -> {path} ({manifest['n_selected']} repos)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default="repos.txt")
    ap.add_argument("-m", "--manifest", default="sampling-manifest.json")
    ap.add_argument("--k", type=int, default=3, help="repos per (language, tier) stratum")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--pool", type=int, default=100, help="candidates fetched per stratum")
    ap.add_argument("--langs", default=",".join(LANGS),
                    help="comma list from: " + ",".join(LANGS))
    ap.add_argument("--tiers", default="high,mid,low")
    args = ap.parse_args(argv)
    langs = [x.strip() for x in args.langs.split(",") if x.strip() in LANGS]
    tiers = [x.strip() for x in args.tiers.split(",") if x.strip() in TIERS]
    print(f"stratified GitHub sample: {len(langs)} languages x {len(tiers)} tiers x "
          f"k={args.k}  (seed {args.seed})\n", flush=True)
    manifest = sample(langs, tiers, args.k, args.seed, args.pool)
    json.dump(manifest, open(args.manifest, "w"), indent=2)
    write_repos(manifest, args.out)
    print(f"wrote manifest -> {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
