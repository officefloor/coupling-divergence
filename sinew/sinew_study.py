"""Cross-project sinew study and qualification.

Three layers on top of the per-project `analyze.sinew`:

  * STUDY  -- mine + analyse every usable repository of a sample drawn by
    open-source-repository-sampler, apply the per-project OUTLIER test, and aggregate the
    verdicts into one table. The sampler's run loop clones each repository deep (every commit
    up to its pinned commit, none after) and deletes it once mined. Resumable: a repo already
    done is skipped, and a partly mined one resumes its mine.
  * DOSSIER -- for each flagged (CONCERN) cluster, capture its member methods -- current
    source (if the clone is present), the data they thread, their co-change commits and
    whether an indirect call path exists -- into a markdown file, so WHY the sinew exists can
    be inspected rather than asserted.
  * PREDICT -- the value test. Measure sinews as of an early cut of history, then test on the
    FUTURE commits whether sinew members accrue more change than complexity-matched peers. If
    past sinew strength predicts future volatility BEYOND complexity, the signal has value.

Java only (the resolver is Java-specific). Standard library + git, plus
open-source-repository-sampler for the sample and its run loop.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

from . import analyze as A, mine as mine_mod


# ---------------------------------------------------------------------------
# per-project outlier test: a sinew is a SIGNAL when it is extreme for its own project
# ---------------------------------------------------------------------------
def flag_outliers(s: dict) -> list:
    """Rank this project's heavy-sinew clusters and tag each. CONCERN = span>=4 (accidental
    sprawl); a cluster is additionally an OUTLIER when its heaviness is an extreme value
    (Iglewicz-Hoaglin modified z-score > 3.5) among the project's clusters -- anomalous
    against the project's own change distribution, not merely large in the abstract."""
    spans = s["heavy"]["spans"]
    masses = []
    for sp in spans:
        # cluster heaviness proxy: span * links * mean-thickness is unavailable here, so use
        # links (co-change bonds) weighted by span as the extent of the knot.
        masses.append(sp["span"] * max(sp["links"], 1))
    med = A._median(masses) if masses else 0.0
    mad = A._median([abs(x - med) for x in masses]) or 1.0
    out = []
    for sp, m in zip(spans, masses):
        madz = 0.6745 * (m - med) / mad
        kind = ("contract" if sp["span"] <= 3 else
                "god-hub" if sp["symmetry"] < 0.55 else "duplication-clique")
        out.append({**sp, "madz": round(madz, 2),
                    "concern": sp["span"] >= 4,
                    "outlier": madz > 3.5,
                    "kind": kind})
    out.sort(key=lambda d: (d["concern"], d["span"], d["links"]), reverse=True)
    return out


def aggregate_row(base: str) -> dict:
    s = A.sinew(base)
    flags = flag_outliers(s)
    concern = [f for f in flags if f["concern"]]
    return {
        "base": os.path.basename(base),
        "methods": s["n_methods"], "latent": s["heavy"]["n_latent_pairs"],
        "top_decile": s["heavy"]["top_decile_share"], "n_heavy": s["heavy"]["n_heavy_madz"],
        "clusters": len(flags), "concern": len(concern),
        "max_span": max((f["span"] for f in flags), default=0),
        "out_cc_partial": s["controls"]["OUT ~ degree | cc (partial)"],
        "flags": flags,
    }


def _print_aggregate(rows: list) -> None:
    print("\nSINEW STUDY — per project")
    print(f"  {'project':36} {'meth':>6} {'latent':>6} {'topdec':>6} {'heavy':>5} "
          f"{'clust':>5} {'concern':>7} {'maxspan':>7} {'OUT|cc':>7}")
    for r in rows:
        oc = r["out_cc_partial"]
        print(f"  {r.get('full_name') or r['base']:36} {r['methods']:>6} {r['latent']:>6} "
              f"{r['top_decile']:>6.2f} {r['n_heavy']:>5} {r['clusters']:>5} "
              f"{r['concern']:>7} {r['max_span']:>7} "
              f"{'   n/a' if oc is None else f'{oc:>+7.3f}'}")
    # the headline the study exists to produce: does the OUT|cc signal replicate, and how
    # often does span>=4 sprawl actually occur?
    ocs = [r["out_cc_partial"] for r in rows if r["out_cc_partial"] is not None]
    if ocs:
        print(f"\n  OUT~degree|cc replicates: mean {sum(ocs) / len(ocs):+.3f} over "
              f"{len(ocs)} projects (range {min(ocs):+.3f}..{max(ocs):+.3f})")
    print(f"  projects with >=1 span>=4 sprawl cluster: "
          f"{sum(1 for r in rows if r['concern'])}/{len(rows)}")


# ---------------------------------------------------------------------------
# dossier: capture the methods of a flagged cluster so WHY it exists can be inspected
# ---------------------------------------------------------------------------
def _cochange_commits(base: str, members: set) -> list:
    """Commits that touched >=2 of the cluster's members, with sha/date/changed-subset, so a
    reviewer can read what change bound them. Commit messages need the clone (added later)."""
    hits = []
    for c in A._commits(base):
        cm = {tuple(m) for m in (c.get("changed_methods") or [])}
        inter = cm & members
        if len(inter) >= 2:
            hits.append({"sha": c["sha"], "i": c["i"], "atime": c["atime"],
                         "touched": sorted(n for _, n in inter)})
    return hits


def _slice_source(repo: str, file: str, start: int, end: int) -> str | None:
    path = os.path.join(repo, file)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    return "".join(lines[max(0, start - 1):end])


def _msg(repo: str, sha: str) -> str:
    if not repo:
        return ""
    r = mine_mod._git(repo, ["log", "-1", "--pretty=%s", sha])
    return r.stdout.strip() if r.returncode == 0 else ""


def dossier(base: str, out_dir: str, repo: str | None = None, max_clusters: int = 10) -> int:
    """Write one markdown dossier per CONCERN cluster into out_dir. If `repo` points at a
    checkout at HEAD, member source bodies and commit messages are included; otherwise the
    dossier carries metrics and the co-change commit list from the mine alone."""
    os.makedirs(out_dir, exist_ok=True)
    s = A.sinew(base)
    rows = A.head_rows(base)
    byname = {(r["file"], r["qualname"]): r for r in rows}
    # name -> candidate (file, qualname) at HEAD (cluster members are stored by name only)
    name_nodes: dict = defaultdict(list)
    for (f, q), r in byname.items():
        name_nodes[r["name"]].append((f, q, r))
    flags = [f for f in flag_outliers(s) if f["concern"]]
    written = 0
    for idx, cl in enumerate(flags[:max_clusters], 1):
        memnames = [m[0] for m in cl["members"]]
        nodes = []
        for nm in memnames:
            for f, q, r in name_nodes.get(nm, []):
                nodes.append((f, q, r))
        members_keys = {(f, q) for f, q, _ in nodes}
        cc = _cochange_commits(base, members_keys)
        proj = os.path.basename(base)
        lines = [f"# Sinew dossier — {proj} cluster {idx}", "",
                 f"- **span** {cl['span']}  **links** {cl['links']}  "
                 f"**density** {cl['density']}  **symmetry** {cl['symmetry']}",
                 f"- **verdict** {cl['kind'].upper()}  "
                 f"(outlier madz={cl['madz']}: {'YES' if cl['outlier'] else 'no'})", "",
                 "## Members", ""]
        for f, q, r in sorted(nodes, key=lambda t: t[2].get("out_args_own", 0)
                              + t[2].get("out_args_ext", 0), reverse=True):
            IN = r.get("params", 0)
            OUT = r.get("out_args_own", 0) + r.get("out_args_ext", 0)
            lines.append(f"### `{r['name']}`  ({f}:{r.get('start')}-{r.get('end')})")
            lines.append(f"IN={IN} OUT={OUT} surface={IN + OUT} cc={r.get('cc', 0)} "
                         f"nloc={r.get('nloc', 0)}")
            src = _slice_source(repo, f, r.get("start", 1), r.get("end", 1)) if repo else None
            if src:
                lines += ["", "```java", src.rstrip(), "```", ""]
            else:
                lines.append("")
        lines += ["## Co-change commits (bound >=2 members)", ""]
        for h in cc[-30:]:
            m = _msg(repo, h["sha"]) if repo else ""
            lines.append(f"- `{h['sha'][:10]}` i={h['i']} {', '.join(h['touched'])}"
                         + (f" — {m}" if m else ""))
        lines += ["", "## Inspect: why does this sinew exist?", "",
                  "- intrinsic contract / dual?  (then span is small and this is a false flag)",
                  "- missed abstraction — a data clump these pass around that should be an "
                  "object?",
                  "- copy-paste duplication — N near-identical shapes edited in lockstep?",
                  "- cross-cutting concern threaded by hand?", ""]
        p = os.path.join(out_dir, f"{proj}-cluster{idx}-{cl['kind']}.md")
        with open(p, "w") as fh:
            fh.write("\n".join(lines))
        written += 1
    return written


# ---------------------------------------------------------------------------
# predictive-value test: does PAST sinew strength predict FUTURE change, beyond complexity?
# ---------------------------------------------------------------------------
def _windowed_cochange(base: str, lo_i: int, hi_i: int, max_commit_size: int = 30):
    """(change_count, pair) over commits with lo_i <= i <= hi_i."""
    change_count: Counter = Counter()
    pair: Counter = Counter()
    for c in A._commits(base):
        if not (lo_i <= c["i"] <= hi_i):
            continue
        cm = list(dict.fromkeys(tuple(m) for m in (c.get("changed_methods") or [])))
        for m in cm:
            change_count[m] += 1
        if 2 <= len(cm) <= max_commit_size:
            for i in range(len(cm)):
                for j in range(i + 1, len(cm)):
                    pair[frozenset((cm[i], cm[j]))] += 1
    return change_count, pair


def predicts(base: str, split: float = 0.7, max_commit_size: int = 30) -> dict:
    """Cut history at `split`. Compute each method's PAST sinew strength (co-change bonds
    weighted by data-flow thickness) from the snapshot at the cut and the past co-change
    window; then test whether it predicts FUTURE change frequency, controlling for complexity.
    A positive partial correlation = the sinew signal has value independent of complexity."""
    commits = A._commits(base)
    if len(commits) < 10:
        return {"base": base, "error": "too few commits"}
    cut = commits[int(split * len(commits))]["i"]
    last_i = commits[-1]["i"]

    # snapshot at the cut commit: method shapes present then
    snap = None
    for i_idx, _, _, rows in A.iter_blocks(base):
        if i_idx <= cut:
            snap = rows
        else:
            break
    if not snap:
        return {"base": base, "error": "no snapshot at cut"}
    surf = {}
    ccv = {}
    for r in snap:
        mk = (r["file"], r["qualname"])
        surf[mk] = r.get("params", 0) + r.get("out_args_own", 0) + r.get("out_args_ext", 0)
        ccv[mk] = r.get("cc", 0)

    past_cc, past_pair = _windowed_cochange(base, 0, cut, max_commit_size)
    fut_cc, _ = _windowed_cochange(base, cut + 1, last_i, max_commit_size)

    # PAST sinew strength per method = Σ over its past co-change bonds of k * min(surface)
    past_sinew: Counter = Counter()
    for pr, k in past_pair.items():
        a, b = tuple(pr)
        if a in surf and b in surf:
            w = k * min(surf[a], surf[b])
            past_sinew[a] += w
            past_sinew[b] += w

    keys = [mk for mk in surf if mk in ccv]
    sinew_x = [past_sinew.get(mk, 0) for mk in keys]
    cc_x = [ccv[mk] for mk in keys]
    fut_y = [fut_cc.get(mk, 0) for mk in keys]

    # top-decile past-sinew methods vs the rest: future change-frequency ratio
    order = sorted(range(len(keys)), key=lambda j: sinew_x[j], reverse=True)
    dec = max(1, len(order) // 10)
    top = [fut_y[j] for j in order[:dec]]
    rest = [fut_y[j] for j in order[dec:]]
    mtop = sum(top) / len(top) if top else 0.0
    mrest = sum(rest) / len(rest) if rest else 0.0

    return {
        "base": os.path.basename(base), "commits": len(commits), "cut_i": cut,
        "n_methods_at_cut": len(keys),
        "sinew~future (raw)": A.spearman(sinew_x, fut_y),
        "cc~future (raw)": A.spearman(cc_x, fut_y),
        "sinew~future | cc (partial)": A._partial_spearman(sinew_x, fut_y, cc_x),
        "future_changefreq_top_decile": round(mtop, 3),
        "future_changefreq_rest": round(mrest, 3),
        "lift": round(mtop / mrest, 2) if mrest else None,
    }


def _print_predicts(bases: list, split: float = 0.7) -> None:
    print(f"\nSINEW PREDICTIVE VALUE — history cut at {split:.0%}, "
          f"does PAST sinew predict FUTURE change?")
    print(f"  {'project':22} {'sinew~fut':>9} {'cc~fut':>7} {'sinew|cc':>9} "
          f"{'top10%':>7} {'rest':>6} {'lift':>5}")
    for b in bases:
        p = predicts(b, split)
        if p.get("error"):
            print(f"  {p['base']:22} {p['error']}")
            continue

        def f(k):
            v = p[k]
            return "   n/a" if v is None else f"{v:+.3f}"
        print(f"  {p['base']:22} {f('sinew~future (raw)'):>9} {f('cc~future (raw)'):>7} "
              f"{f('sinew~future | cc (partial)'):>9} "
              f"{p['future_changefreq_top_decile']:>7} {p['future_changefreq_rest']:>6} "
              f"{(p['lift'] if p['lift'] is not None else 0):>5}")


# ---------------------------------------------------------------------------
# the study orchestrator: clone + mine + analyse a sample
# ---------------------------------------------------------------------------
def _mine_name(full_name: str) -> str:
    """A mine's file name: owner__name, so two owners' same-named repositories never collide."""
    return full_name.replace("/", "__")


def study(sample_path: str, out_dir: str, recent: int = 500,
          emit_dossiers: bool = True) -> list:
    """Mine the recent history of every usable repository of the sample (Java resolver),
    analyse its sinews, emit dossiers for its CONCERN clusters (with member source and commit
    messages, read from the clone while it exists), then aggregate and run the predictive test.

    open-source-repository-sampler's run loop does the cloning: deep, at each repository's
    pinned commit, so the mined history ends exactly where the sample says, and the clone is
    deleted afterwards. Rows are written to sinew-study.json after each repository; the run log
    lets a re-run skip repositories already done."""
    from open_source_repository_sampler import for_each, read_sample

    mines_dir = os.path.join(out_dir, "mines")
    os.makedirs(mines_dir, exist_ok=True)
    study_path = os.path.join(out_dir, "sinew-study.json")
    rows: dict = {}
    if os.path.exists(study_path):
        with open(study_path) as fh:
            rows = {r["full_name"]: r for r in json.load(fh) if r.get("full_name")}

    def save() -> None:
        with open(study_path, "w") as fh:
            json.dump(list(rows.values()), fh, indent=2)

    def experiment(repo) -> None:
        base = os.path.join(mines_dir, _mine_name(repo.full_name))
        mine_mod.mine(repo.path, base, recent=recent)
        try:
            row = aggregate_row(base)
        except Exception as e:  # a mine can be empty (no Java methods); record and move on
            print(f"  analyse failed for {repo.full_name}: {e}")
            return
        # The per-cluster flags feed the dossiers only; the stored row is the summary.
        rows[repo.full_name] = {"full_name": repo.full_name, "url": repo.url,
                                "commit": repo.commit,
                                **{k: v for k, v in row.items() if k != "flags"}}
        save()
        if emit_dossiers and row["concern"]:
            n = dossier(base, os.path.join(out_dir, "dossiers"), repo=repo.path)
            print(f"  {repo.full_name}: {row['concern']} concern clusters, {n} dossiers")

    usable = read_sample(sample_path)
    print(f"sinew study: {len(usable)} usable repositories in {sample_path}, mining the most "
          f"recent {recent} commits of each (deep clone at its pinned commit) -> {out_dir}",
          flush=True)
    for_each(sample_path, experiment, deep=True,
             run_log=os.path.join(out_dir, "run-log.jsonl"))
    ordered = [rows[r["full_name"]] for r in usable if r["full_name"] in rows]
    _print_aggregate(ordered)
    save()
    _print_predicts([os.path.join(mines_dir, _mine_name(r["full_name"])) for r in ordered])
    print(f"\nwrote {study_path}" + ("" if not emit_dossiers else
          f" and dossiers in {out_dir}/dossiers/"))
    return ordered
