"""Walk a repository's git history and compute the god-math at sampled commits.

Uses detached worktrees so the repo's own checkout is never touched. Sampling keeps the
cost bounded: the divergence trajectory of a slow-forming god is visible at coarse
resolution, which is all the lead-lag question needs."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from . import extract


def _git(repo: str, args: list[str]) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout


def commit_list(repo: str) -> list[tuple[str, int]]:
    """[(sha, unix_time)] from first commit to HEAD, oldest first."""
    out = _git(repo, ["rev-list", "--reverse", "--first-parent", "--format=%H %ct", "HEAD"])
    rows = []
    for line in out.splitlines():
        if line.startswith("commit "):
            continue
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) >= 7:
            rows.append((parts[0], int(parts[1])))
    return rows


def sample(rows: list, k: int) -> list:
    """Evenly pick at most k commits, always keeping the first and last."""
    n = len(rows)
    if n <= k:
        return rows
    idx = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
    return [rows[i] for i in idx]


def at_commit(repo: str, sha: str, work_root: str, include_tests: bool = False) -> dict:
    wt = os.path.join(work_root, "wt")
    subprocess.run(["git", "-C", repo, "worktree", "prune"], capture_output=True, text=True)
    shutil.rmtree(wt, ignore_errors=True)
    add = subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", wt, sha],
                         capture_output=True, text=True)
    try:
        if add.returncode != 0:
            return {"funcs": {}, "wmc": {}}
        return extract.analyze_tree(wt, include_tests)
    finally:
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt],
                       capture_output=True, text=True)
        shutil.rmtree(wt, ignore_errors=True)


def walk(repo: str, samples: int = 40, include_tests: bool = False,
         work_root: str | None = None) -> list[dict]:
    """Per sampled commit: {sha, time, wmc, outlier_files, top_by_file}. `wmc` is the
    file -> total-CC map; `outlier_files` the files holding a gain outlier; `top_by_file`
    the (name, gain) of each file's top generator, for the files that have one."""
    rows = sample(commit_list(repo), samples)
    root = work_root or tempfile.mkdtemp(prefix="godwatch-hist-")
    os.makedirs(root, exist_ok=True)
    series = []
    for i, (sha, t) in enumerate(rows):
        res = at_commit(repo, sha, root, include_tests)
        funcs, wmc = res["funcs"], res["wmc"]
        outs = extract.outliers(funcs)                     # method-level god-method signal
        cgain = extract.class_gain(funcs)
        class_outs = extract.class_outliers(cgain)         # class-level god-class signal
        top_by_file: dict = {}
        for fid, r in funcs.items():
            if r["gain"] <= 0:
                continue
            cur = top_by_file.get(fid[0])
            if cur is None or r["gain"] > cur[1]:
                top_by_file[fid[0]] = (fid[1], r["gain"])
        series.append({
            "i": i, "sha": sha, "time": t,
            "wmc": wmc,
            "method_outlier_files": sorted({fid[0] for fid in outs}),
            "class_outlier_files": sorted(class_outs),
            "top_by_file": top_by_file,
        })
    return series
