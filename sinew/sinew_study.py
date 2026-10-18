"""Cross-project sinew study and qualification.

Three layers on top of the per-project `analyze.sinew`:

  * STUDY  -- mine + analyse every usable repository of a sample drawn by
    open-source-repository-sampler, and aggregate the per-project results, the null-model
    comparisons and the predictive test into one results file, with the parameters and the
    environment that produced them, the per-cluster records, and a random-effects pooling of
    the primary correlations across projects. A project whose analysis fails is recorded as
    a failure row, so attrition is visible. Resumable.
  * DOSSIER -- for each flagged cluster, capture its member methods -- current source (if the
    clone is present), the parameters they accept and hand onward (the data the cluster
    threads) and their co-change commits -- into a markdown file, so WHY the sinew exists can
    be inspected rather than asserted. `blind=True` writes the concern clusters, the small
    (contract) clusters and decoy clusters (co-changing methods that thread nothing) under
    random numbers with the truth in a separate key, for a rater study of the span verdict.
  * PREDICT -- the value test. Measure each method's sinew strength as of an early cut of
    history, then test on the FUTURE commits whether it predicts change frequency beyond the
    things already known to: past change frequency, unweighted past co-change, size and
    complexity. Only an increment over those baselines is evidence the signal has value.

Any language lizard parses (one extractor for all). Standard library + git, plus
open-source-repository-sampler for the sample and its run loop.
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import platform
import random
import subprocess
import sys
from collections import Counter, defaultdict

from . import analyze as A, discover, mine as mine_mod

SENSITIVITY_CAPS = (10, 20, 50)      # max_commit_size values the main counts are re-run at

PRIMARY = "fwd_ratio ~ degree | freq,nloc,cc (partial)"          # H1
PRIMARY_PREDICT = "sinew~future | freq,cochange,nloc,cc (partial)"  # H3


# ---------------------------------------------------------------------------
# per-project flags: span verdict + null-calibrated outlier
# ---------------------------------------------------------------------------
def flag_outliers(s: dict) -> list:
    """This project's heavy-bond clusters, each with its span verdict (concern = span >=
    span_concern) and whether its mass exceeds what the permutation null produces as the
    LARGEST cluster in 95% of draws (outlier). Sorted concern first, then by span and links."""
    spans = s["heavy"]["spans"]
    null = s["heavy"].get("null") or {}
    q95 = (null.get("max_cluster_mass") or {}).get("null_q95")
    out = []
    for sp in spans:
        out.append({**sp, "outlier": (q95 is not None and sp["mass"] > q95),
                    "null_q95_mass": q95})
    out.sort(key=lambda d: (d["concern"], d["span"], d["links"]), reverse=True)
    return out


def _languages_at_head(rows: list) -> dict:
    return dict(Counter(discover.language(f) or "?" for f in {x["file"] for x in rows}))


def aggregate_row(base: str, params: dict | None = None, rows: list | None = None,
                  commits: list | None = None, with_predict: bool = True) -> dict:
    """One project's results: the sinew analysis at the registered parameters, the same
    counts at the sensitivity caps, the commit-window description, and the predictive test.
    The mine is read once and shared."""
    p = dict(A.PARAMS, **(params or {}))
    rows = A.head_rows(base) if rows is None else rows
    commits = A._commits(base) if commits is None else commits
    s = A.sinew(base, rows=rows, commits=commits, params=p)
    flags = flag_outliers(s)
    concern = [f for f in flags if f["concern"]]
    sens = {}
    for cap in SENSITIVITY_CAPS:
        if cap == p["max_commit_size"]:
            continue
        t = A.sinew(base, rows=rows, commits=commits,
                    params={**p, "max_commit_size": cap, "null_samples": 0, "bootstrap": 0})
        sens[str(cap)] = {"pairs": t["heavy"]["n_pairs"], "n_heavy": t["heavy"]["n_heavy_madz"],
                          "concern": t["heavy"]["n_concern"], "max_span": t["heavy"]["max_span"],
                          "primary": t["controls"][PRIMARY]}
    nl = s["heavy"]["null"]
    row = {
        "base": os.path.basename(base),
        "methods": s["n_methods"], "pairs": s["heavy"]["n_pairs"],
        "cochange_pairs": s["heavy"]["n_cochange_pairs"],
        "share_threaded": s["heavy"]["share_threaded"],
        "params_mean": s["params_stats"]["mean"], "params_p90": s["params_stats"]["p90"],
        "share_typed_keys": s["params_stats"]["share_typed_keys"],
        "fwd_ratio_mean": s["params_stats"]["mean_fwd_ratio"], "conduits": s["n_conduits"],
        "top_decile": s["heavy"]["top_decile_share"], "n_heavy": s["heavy"]["n_heavy_madz"],
        "clusters": len(flags), "concern": len(concern),
        "outliers": sum(1 for f in flags if f["outlier"]),
        "max_span": s["heavy"]["max_span"],
        "null": {k: {kk: v for kk, v in nl[k].items()} for k in A._NULL_STATS}
        if nl.get("samples") else None,
        "primary": s["controls"][PRIMARY],
        "primary_ci95": s["controls"][PRIMARY + " ci95"],
        "primary_any": s["controls"]["fwd_ratio_any ~ degree | freq,nloc,cc (partial)"],
        "out_cc_partial": s["controls"]["OUT ~ degree | cc (partial)"],
        "in_cc_partial": s["controls"]["IN ~ degree | cc (partial)"],
        "fwd_cc_partial": s["controls"]["fwd_args ~ degree | cc (partial)"],
        "fwd_nloc_partial": s["controls"]["fwd_args ~ degree | nloc (partial)"],
        "fwd_full_partial": s["controls"]["fwd_args ~ degree | freq,nloc,cc (partial)"],
        "controls": s["controls"],
        "window": s["window"],
        "languages": _languages_at_head(rows),
        "sensitivity": sens,
        "flags": flags,
    }
    if with_predict:
        row["predict"] = predicts(base, p["split"], p["max_commit_size"], p["bootstrap"],
                                  p["seed"], commits=commits, head=rows)
    return row


def pooled(rows: list) -> dict:
    """Random-effects pooling of the primary per-project correlations, and the share of
    projects in which the null is beaten on the span statistics."""
    ok = [r for r in rows if not r.get("error")]
    out = {
        "n_projects": len(ok), "n_failed": len(rows) - len(ok),
        "H1 " + PRIMARY: A.pool_correlations([r["primary"] for r in ok],
                                             [r["methods"] for r in ok]),
        "H1 sensitivity fwd_ratio_any": A.pool_correlations([r["primary_any"] for r in ok],
                                                            [r["methods"] for r in ok]),
    }
    for cap in SENSITIVITY_CAPS:
        rs = [(r["sensitivity"].get(str(cap)) or {}).get("primary") for r in ok]
        out[f"H1 at cap {cap}"] = A.pool_correlations(rs, [r["methods"] for r in ok])
    pr = [r["predict"] for r in ok if r.get("predict") and not r["predict"].get("error")]
    out["H3 " + PRIMARY_PREDICT] = A.pool_correlations(
        [q[PRIMARY_PREDICT] for q in pr], [q["n_methods_at_cut"] for q in pr])
    out["H3 cochange~future | freq,nloc,cc (baseline)"] = A.pool_correlations(
        [q["cochange~future | freq,nloc,cc (partial)"] for q in pr],
        [q["n_methods_at_cut"] for q in pr])
    withnull = [r for r in ok if r.get("null")]
    out["H2 projects"] = len(withnull)
    for stat in ("n_heavy", "heavy_mass", "max_span", "n_concern"):
        ps = [r["null"][stat]["p"] for r in withnull]
        out[f"H2 {stat} p<0.05 in"] = sum(1 for x in ps if x < 0.05)
        out[f"H2 {stat} observed>null_mean in"] = sum(
            1 for r in withnull if r["null"][stat]["observed"] > r["null"][stat]["null_mean"])
    out["projects with >=1 concern cluster"] = sum(1 for r in ok if r["concern"])
    out["projects with >=1 outlier concern cluster"] = sum(
        1 for r in ok if any(f["concern"] and f["outlier"] for f in r.get("flags", [])))
    return out


def _print_aggregate(rows: list) -> None:
    def fmt(v):
        return "   n/a" if v is None else f"{v:>+7.3f}"
    print("\nSINEW STUDY — per project")
    print(f"  {'project':36} {'meth':>6} {'pairs':>6} {'thr%':>5} {'typed':>5} {'fwd':>5} "
          f"{'heavy':>5} {'p':>6} {'clust':>5} {'concern':>7} {'outl':>4} {'maxspan':>7} "
          f"{'H1':>7} {'ci95':>16} {'H3':>7}")
    for r in rows:
        name = r.get("full_name") or r["base"]
        if r.get("error"):
            print(f"  {name:36} FAILED: {r['error']}")
            continue
        pn = (r.get("null") or {}).get("n_heavy", {}).get("p")
        ci = r.get("primary_ci95")
        q = r.get("predict") or {}
        print(f"  {name:36} {r['methods']:>6} {r['pairs']:>6} {r['share_threaded'] * 100:>4.0f}% "
              f"{r['share_typed_keys'] * 100:>4.0f}% {r['fwd_ratio_mean']:>5.2f} "
              f"{r['n_heavy']:>5} {pn if pn is not None else 'n/a':>6} {r['clusters']:>5} "
              f"{r['concern']:>7} {r['outliers']:>4} {r['max_span']:>7} "
              f"{fmt(r['primary'])} {str(ci) if ci else 'n/a':>16} "
              f"{fmt(q.get(PRIMARY_PREDICT)) if q and not q.get('error') else '    n/a':>7}")
    pl = pooled(rows)
    print("\n  POOLED (random effects on Fisher z; H2 = permutation null)")
    for k, v in pl.items():
        print(f"    {k:52} {v}")


# ---------------------------------------------------------------------------
# dossier: capture the methods of a flagged cluster so WHY it exists can be inspected
# ---------------------------------------------------------------------------
def _cochange_commits(commits: list, members: set) -> list:
    hits = []
    for c in commits:
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


def _fence(file: str) -> str:
    return (discover.language(file) or "").replace("c#", "csharp").replace("c++", "cpp")


def _decoys(s: dict, rows: list, commits: list, n: int, rng: random.Random) -> list:
    """Decoy clusters for a blind rating: components of the graph of co-changing pairs that
    thread NOTHING (k >= min_k), sized like the concern clusters. These are co-change without
    data flow; if raters cannot tell them from the concern clusters, the span verdict is not
    discriminating threading."""
    p = s["params"]
    M = A.methods_at_head(rows)
    _, pair = A.pair_counts(A.change_sets(commits), p["max_commit_size"])
    adj: dict = defaultdict(set)
    nodes: set = set()
    for pr, k in pair.items():
        if k < p["min_k"]:
            continue
        a, b = tuple(pr)
        if a in M and b in M and not A.thread_keys(M[a], M[b]):
            adj[a].add(b)
            adj[b].add(a)
            nodes.update((a, b))
    comps = [c for c in A._components(nodes, adj) if 2 <= len(c) <= 12]
    rng.shuffle(comps)
    out = []
    for comp in comps[:n]:
        members = sorted(comp, key=lambda mk: len(adj[mk] & set(comp)), reverse=True)
        out.append({"span": len(comp), "links": sum(len(adj[mk] & set(comp)) for mk in comp) // 2,
                    "kind": "decoy", "threaded": [], "member_keys": [list(mk) for mk in members]})
    return out


def dossier(base: str, out_dir: str, repo: str | None = None, max_clusters: int = 10,
            blind: bool = False, seed: int = 1, s: dict | None = None,
            rows: list | None = None, commits: list | None = None) -> int:
    """Write one markdown dossier per CONCERN cluster into out_dir. If `repo` points at a
    checkout at HEAD, member source bodies and commit messages are included. With `blind`,
    contract clusters and decoys are written too, under shuffled numbers, with the truth in
    <out_dir>/<project>.key.json and no verdict in the dossier text."""
    os.makedirs(out_dir, exist_ok=True)
    rows = A.head_rows(base) if rows is None else rows
    commits = A._commits(base) if commits is None else commits
    s = A.sinew(base, rows=rows, commits=commits, null_samples=0, bootstrap=0) if s is None else s
    bykey: dict = defaultdict(list)
    for r in rows:
        bykey[(r["file"], r["qualname"])].append(r)
    flags = flag_outliers(s)
    concern = [f for f in flags if f["concern"]][:max_clusters]
    items = [("concern", f) for f in concern]
    if blind:
        rng = random.Random(seed)
        contracts = [f for f in flags if not f["concern"]]
        rng.shuffle(contracts)
        items += [("contract", f) for f in contracts[:max(1, len(concern))]]
        items += [("decoy", d) for d in _decoys(s, rows, commits, max(1, len(concern)), rng)]
        rng.shuffle(items)
    proj = os.path.basename(base)
    key = []
    written = 0
    for idx, (truth, cl) in enumerate(items, 1):
        members_keys = {tuple(k) for k in cl["member_keys"]}
        nodes = [(f, q, r) for f, q in members_keys for r in bykey.get((f, q), [])]
        cc = _cochange_commits(commits, members_keys)
        lines = ([f"# Cluster {idx}", "", f"- **methods** {len(members_keys)}  "
                  f"**commits editing two or more of them together** {len(cc)}"]
                 if blind else [f"# Sinew dossier — {proj} cluster {idx}", ""])
        if not blind:
            lines += [f"- **span** {cl['span']}  **links** {cl['links']}  **mass** {cl['mass']}  "
                      f"**density** {cl['density']}  **hubness** {cl['hubness']}  "
                      f"**files** {cl['files']}",
                      f"- **conduits** {cl['n_conduits']}/{cl['span']}  "
                      f"**mean forward ratio** {cl['mean_fwd_ratio']}  **threaded** "
                      + (", ".join(f"`{t}`×{n}" for t, n in cl["threaded"]) or "(none)"),
                      f"- **verdict** {cl['kind'].upper()}  (outlier against the null: "
                      f"{'YES' if cl.get('outlier') else 'no'}; null q95 cluster mass "
                      f"{cl.get('null_q95_mass')})"]
        lines += ["", "## Members", ""]
        order = ((lambda t: (t[0], t[2].get("start", 0))) if blind      # source order
                 else (lambda t: -t[2].get("fwd_args", 0)))              # most forwarding first
        for f, q, r in sorted(nodes, key=order):
            IN = r.get("params", 0)
            OUT = r.get("out_args_own", 0) + r.get("out_args_ext", 0)
            sig = ", ".join(f"{n}:{t or '?'}" for n, t in
                            zip(r.get("param_names", []), r.get("param_types", [])))
            lines.append(f"### `{r['name']}`  ({f}:{r.get('start')}-{r.get('end')})")
            if blind:                        # signature only: no instrument metrics
                lines.append(f"parameters: {sig or '(none)'}")
            else:
                lines.append(f"IN={IN} ({sig or '-'}) OUT={OUT} "
                             f"forwards={', '.join(r.get('fwd_names', [])) or '-'} "
                             f"fwd_ratio={r.get('fwd_ratio', 0.0)} cc={r.get('cc', 0)} "
                             f"nloc={r.get('nloc', 0)}")
            src = _slice_source(repo, f, r.get("start", 1), r.get("end", 1)) if repo else None
            if src:
                lines += ["", f"```{_fence(f)}", src.rstrip(), "```", ""]
            else:
                lines.append("")
        lines += ["## Commits that edited two or more of these methods" if blind else
                  "## Co-change commits (bound >=2 members)", ""]
        for h in cc[-30:]:
            m = _msg(repo, h["sha"]) if repo else ""
            lines.append(f"- `{h['sha'][:10]}` i={h['i']} {', '.join(h['touched'])}"
                         + (f" — {m}" if m else ""))
        if blind:                            # no checklist: it names the causes we expect
            lines.append("")
        else:
          lines += ["", "## Inspect: why does this cluster exist?", "",
                    "- intrinsic contract / dual?  (a small, tight set that must change together)",
                    "- tramp data — parameters handed down a chain to a terminal that does not "
                    "change? Then the cost is the threading: a context / parameter object cuts it.",
                    "- missed abstraction — a data clump these pass around that should be an "
                    "object?",
                    "- copy-paste duplication — N near-identical shapes edited in lockstep?",
                    "- cross-cutting concern threaded by hand?",
                    "- co-change without data flow — edited together for some other reason "
                    "(build, API rename, shared constant)?", ""]
        name = f"{proj}-cluster{idx}" + ("" if blind else f"-{cl['kind']}") + ".md"
        with open(os.path.join(out_dir, name), "w") as fh:
            fh.write("\n".join(lines))
        key.append({"file": name, "truth": truth, "kind": cl.get("kind"),
                    "span": cl.get("span"), "outlier": cl.get("outlier")})
        written += 1
    if blind:
        with open(os.path.join(out_dir, f"{proj}.key.json"), "w") as fh:
            json.dump(key, fh, indent=2)
    return written


# ---------------------------------------------------------------------------
# predictive-value test: does PAST sinew strength predict FUTURE change, beyond the baselines?
# ---------------------------------------------------------------------------
def predicts(base: str, split: float = A.PARAMS["split"],
             max_commit_size: int = A.PARAMS["max_commit_size"],
             bootstrap: int = A.PARAMS["bootstrap"], seed: int = A.PARAMS["seed"],
             commits: list | None = None, head: list | None = None) -> dict:
    """Cut history at `split`. From the snapshot at the cut and the PAST window compute, per
    method: past sinew strength (sum over its threaded co-change pairs of k x thickness),
    the unweighted past co-change strength (sum of k over all its pairs), past change
    frequency, nloc and cc. Test whether sinew predicts FUTURE change frequency beyond those
    baselines (partial Spearman, bootstrap CI). Methods gone by the end of history keep the
    changes they accrued before vanishing; their number is reported."""
    commits = A._commits(base) if commits is None else commits
    if len(commits) < 10:
        return {"base": os.path.basename(base), "error": "too few commits"}
    cut = commits[int(split * len(commits))]["i"]
    last_i = commits[-1]["i"]

    snap = None
    for i_idx, _, _, rows in A.iter_blocks(base):
        if i_idx <= cut:
            snap = rows
        else:
            break
    if not snap:
        return {"base": os.path.basename(base), "error": "no snapshot at cut"}
    M = A.methods_at_head(snap)

    past = [c for c in commits if c["i"] <= cut]
    fut = [c for c in commits if cut < c["i"] <= last_i]
    past_cc, past_pair = A.pair_counts(A.change_sets(past), max_commit_size)
    fut_cc, _ = A.pair_counts(A.change_sets(fut), max_commit_size)

    sinew_x: Counter = Counter()
    coch_x: Counter = Counter()
    for pr, k in past_pair.items():
        a, b = tuple(pr)
        if a in M and b in M:
            coch_x[a] += k
            coch_x[b] += k
            thick = len(A.thread_keys(M[a], M[b]))
            if thick:
                sinew_x[a] += k * thick
                sinew_x[b] += k * thick

    keys = list(M)
    x = [sinew_x.get(mk, 0) for mk in keys]
    coch = [coch_x.get(mk, 0) for mk in keys]
    freq = [past_cc.get(mk, 0) for mk in keys]
    nloc = [M[mk]["nloc"] for mk in keys]
    cc = [M[mk]["cc"] for mk in keys]
    y = [fut_cc.get(mk, 0) for mk in keys]
    full = [freq, coch, nloc, cc]
    n = len(keys)

    def lift(xs):
        order = sorted(range(n), key=lambda j: xs[j], reverse=True)
        dec = max(1, n // 10)
        top = [y[j] for j in order[:dec]]
        rest = [y[j] for j in order[dec:]]
        mt = sum(top) / len(top) if top else 0.0
        mr = sum(rest) / len(rest) if rest else 0.0
        return round(mt, 3), round(mr, 3), (round(mt / mr, 2) if mr else None)

    def _primary(idx):
        return A.partial_spearman([x[i] for i in idx], [y[i] for i in idx],
                                  [[z[i] for i in idx] for z in full])
    head_keys = {(r["file"], r["qualname"]) for r in head} if head is not None else None
    top_s, rest_s, lift_s = lift(x)
    top_c, rest_c, lift_c = lift(coch)
    return {
        "base": os.path.basename(base), "commits": len(commits), "cut_i": cut,
        "n_methods_at_cut": n,
        "n_gone_by_end": (sum(1 for mk in keys if mk not in head_keys)
                          if head_keys is not None else None),
        "sinew~future (raw)": A.spearman(x, y),
        "cochange~future (raw)": A.spearman(coch, y),
        "freq~future (raw)": A.spearman(freq, y),
        "cc~future (raw)": A.spearman(cc, y),
        "nloc~future (raw)": A.spearman(nloc, y),
        "sinew~future | cc (partial)": A.partial_spearman(x, y, [cc]),
        "sinew~future | freq (partial)": A.partial_spearman(x, y, [freq]),
        "sinew~future | cochange (partial)": A.partial_spearman(x, y, [coch]),
        PRIMARY_PREDICT: A.partial_spearman(x, y, full),
        PRIMARY_PREDICT + " ci95": A.bootstrap_ci(_primary, n, bootstrap, seed),
        "cochange~future | freq,nloc,cc (partial)": A.partial_spearman(coch, y, [freq, nloc, cc]),
        "future_changefreq_top_decile": top_s, "future_changefreq_rest": rest_s, "lift": lift_s,
        "baseline_cochange_top_decile": top_c, "baseline_cochange_rest": rest_c,
        "baseline_lift": lift_c,
    }


def _print_predicts(bases: list, split: float = A.PARAMS["split"]) -> None:
    print(f"\nSINEW PREDICTIVE VALUE — history cut at {split:.0%}: does PAST sinew predict "
          f"FUTURE change beyond past frequency, past co-change, size and complexity?")
    print(f"  {'project':22} {'sinew~fut':>9} {'coch~fut':>8} {'freq~fut':>8} "
          f"{'sinew|all':>9} {'ci95':>16} {'coch|rest':>9} {'lift':>5} {'base':>5}")
    for b in bases:
        p = predicts(b, split)
        if p.get("error"):
            print(f"  {p['base']:22} {p['error']}")
            continue
        f = lambda k: A._fmt(p[k])  # noqa: E731
        ci = p[PRIMARY_PREDICT + " ci95"]
        print(f"  {p['base']:22} {f('sinew~future (raw)'):>9} {f('cochange~future (raw)'):>8} "
              f"{f('freq~future (raw)'):>8} {f(PRIMARY_PREDICT):>9} "
              f"{str(ci) if ci else 'n/a':>16} "
              f"{f('cochange~future | freq,nloc,cc (partial)'):>9} "
              f"{(p['lift'] if p['lift'] is not None else 0):>5} "
              f"{(p['baseline_lift'] if p['baseline_lift'] is not None else 0):>5}")


# ---------------------------------------------------------------------------
# the study orchestrator: clone + mine + analyse a sample
# ---------------------------------------------------------------------------
def _mine_name(full_name: str) -> str:
    """A mine's file name: owner__name, so two owners' same-named repositories never collide."""
    return full_name.replace("/", "__")


def environment() -> dict:
    """What produced the results: interpreter, platform, tool versions, this code's commit."""
    import importlib.metadata as md

    def ver(name):
        try:
            return md.version(name)
        except md.PackageNotFoundError:
            return None
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", here, "status", "--porcelain"], capture_output=True,
                           text=True).stdout.strip()
    return {
        "python": sys.version.split()[0], "platform": platform.platform(),
        "git": subprocess.run(["git", "--version"], capture_output=True,
                              text=True).stdout.strip(),
        "lizard": ver("lizard"), "open-source-repository-sampler":
        ver("open-source-repository-sampler"), "sinew": ver("sinew"),
        "sinew_commit": r.stdout.strip() if r.returncode == 0 else None,
        "sinew_dirty": bool(dirty),
        "run_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _load_study(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        d = json.load(fh)
    return {r["full_name"]: r for r in d.get("projects", []) if r.get("full_name")}


def study(sample_path: str, out_dir: str, recent: int = 500, emit_dossiers: bool = True,
          params: dict | None = None, restrict_language: bool = True,
          rater: dict | None = None, rate_call=None) -> list:
    """Mine the recent history of every usable repository of the sample, analyse its sinews,
    emit dossiers for its CONCERN clusters (with member source and commit messages, read
    from the clone while it exists), then aggregate, pool, and write everything to
    <out_dir>/sinew-study.json: parameters, environment, one row per project (failures
    included), and the pooled estimates. Per-cluster records go to <out_dir>/clusters.jsonl.

    open-source-repository-sampler's run loop does the cloning: deep, at each repository's
    pinned commit, so the mined history ends exactly where the sample says, and the clone is
    deleted afterwards. With `restrict_language`, a repository is mined on the files of the
    language it was sampled as. With `rater` ({models, runs, effort}, from rater.json), each
    project's BLIND dossiers (concern, contract, decoy) are written to <out_dir>/rated/<project>
    while the clone exists, then rated by those models and scored, per project and pooled
    (<out_dir>/rated/scorecard.json). Resumable via the run log."""
    from open_source_repository_sampler import for_each, read_sample
    from . import rate as rate_mod

    p = dict(A.PARAMS, **(params or {}))
    mines_dir = os.path.join(out_dir, "mines")
    os.makedirs(mines_dir, exist_ok=True)
    study_path = os.path.join(out_dir, "sinew-study.json")
    clusters_path = os.path.join(out_dir, "clusters.jsonl")
    rows: dict = _load_study(study_path)
    env = environment()

    def save(rating_card: dict | None = None) -> None:
        projects = list(rows.values())
        with open(study_path, "w") as fh:
            json.dump({"parameters": {**p, "recent": recent, "sample": sample_path,
                                      "restrict_language": restrict_language,
                                      "sensitivity_caps": list(SENSITIVITY_CAPS),
                                      "rater": rater},
                       "environment": env, "projects": projects,
                       "pooled": pooled(projects) if projects else None,
                       "rating": rating_card}, fh, indent=2)

    def experiment(repo) -> None:
        base = os.path.join(mines_dir, _mine_name(repo.full_name))
        langs = {repo.language} if restrict_language and repo.language else None
        mine_mod.mine(repo.path, base, recent=recent, langs=langs)
        ident = {"full_name": repo.full_name, "url": repo.url, "commit": repo.commit,
                 "language": repo.language}
        try:
            head = A.head_rows(base)
            commits = A._commits(base)
            if not head:
                raise RuntimeError("empty mine (no methods at HEAD)")
            row = aggregate_row(base, p, rows=head, commits=commits)
        except Exception as e:
            rows[repo.full_name] = {**ident, "error": f"{type(e).__name__}: {e}"}
            save()
            print(f"  analyse failed for {repo.full_name}: {e}")
            return
        with open(clusters_path, "a") as fh:
            for f in row["flags"]:
                fh.write(json.dumps({"full_name": repo.full_name, **f}) + "\n")
        rows[repo.full_name] = {**ident, **{k: v for k, v in row.items() if k != "flags"}}
        save()
        if emit_dossiers and row["concern"]:
            n = dossier(base, os.path.join(out_dir, "dossiers"), repo=repo.path,
                        rows=head, commits=commits)
            print(f"  {repo.full_name}: {row['concern']} concern clusters, {n} dossiers")
        if rater and row["concern"]:
            n = dossier(base, os.path.join(out_dir, "rated", _mine_name(repo.full_name)),
                        repo=repo.path, blind=True, rows=head, commits=commits)
            print(f"  {repo.full_name}: {n} blind dossiers for rating")

    usable = read_sample(sample_path)
    print(f"sinew study: {len(usable)} usable repositories in {sample_path}, mining the most "
          f"recent {recent} commits of each (deep clone at its pinned commit) -> {out_dir}",
          flush=True)
    for_each(sample_path, experiment, deep=True,
             run_log=os.path.join(out_dir, "run-log.jsonl"))
    ordered = [rows[r["full_name"]] for r in usable if r["full_name"] in rows]
    _print_aggregate(ordered)
    if rater:
        rated_dir = os.path.join(out_dir, "rated")
        dirs = sorted(d for d in glob.glob(os.path.join(rated_dir, "*")) if os.path.isdir(d))
        print(f"\nRATING {len(dirs)} projects' blind dossiers with {rater['models']} "
              f"(runs={rater.get('runs', 1)}, effort={rater.get('effort')})")
        for d in dirs:
            rate_mod.rate(d, rater["models"], rater.get("runs"), call=rate_call,
                          effort=rater.get("effort"))
            rate_mod.score(d)
        card = rate_mod.score_many(dirs, os.path.join(rated_dir, "scorecard.json"))
        for r in ordered:
            if not r.get("error"):
                sc = os.path.join(rated_dir, _mine_name(r["full_name"]), "scorecard.json")
                if os.path.exists(sc):
                    with open(sc) as fh:
                        r["rating"] = json.load(fh)["per_model"]
        save(card)
    else:
        save()
    print(f"\nwrote {study_path}, {clusters_path}"
          + ("" if not emit_dossiers else f" and dossiers in {out_dir}/dossiers/")
          + ("" if not rater else f" and ratings in {out_dir}/rated/"))
    return ordered
