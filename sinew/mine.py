"""Mine the raw shape of every method at every commit over a project's history.

This is a data-collection instrument, not an analysis. For each commit on the chosen ref
(first-parent by default, oldest first) it checks the tree out in a throwaway detached
worktree, extracts every method's raw shape (extract.method_shapes, in any language: parameter
names, types and match keys, complexity, the outgoing call edges with per-call argument counts
and the parameters each call forwards, each callee tagged own/external), and records which
methods the commit changed, from `git diff` against the parent -- ignoring whitespace-only
and blank-line changes, so a reformat is not a change. NOTHING is aggregated or derived here:
class/file rollups, in-coupling, the call graph and placement-over-time are all left to be
reconstructed downstream from this raw table, so the same mine can be looked at many ways.

Output is JSON Lines, append-only and resumable (a re-run skips commits already recorded; the
rows of a commit that was interrupted before its index row was written are discarded first,
so a resumed mine never holds a commit twice):

  <out>.commits.jsonl   one row per commit -- sha, parent, author/commit time, index on the
                        walked ref, method/file counts, the files with a non-whitespace change,
                        and the ids of the methods it changed.
  <out>.methods.jsonl   one row per (commit, method) -- the commit sha and index, the method's
                        identity and placement (file, qualname, name, line range), its shape
                        (params + complexity), its raw outgoing edges, and whether/how much
                        this commit changed it (changed flag + added-line churn).

Method identity across commits is (file, qualname); a method moved to another file or
renamed starts a new thread (a documented limitation, fine for an append-only codebase and
flagged for repos with heavy refactoring). Overloads share one identity.

`langs` restricts the mine to the files of the given language keys (e.g. {"java"}), so a
repository sampled as Java is measured on its Java and not on a JavaScript front end that
happens to live beside it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import discover, extract


def _git(repo: str, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    # errors="replace": a diff or log can carry non-UTF-8 bytes (latin-1 source, binary
    # hunks); decoding must never crash the walk. Line/offset parsing is unaffected.
    return subprocess.run(["git", "-C", cwd or repo, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def commit_list(repo: str, ref: str = "HEAD", first_parent: bool = True) -> list[dict]:
    """[{sha, parent, atime, ctime}] from the first commit to `ref`, oldest first. `parent`
    is the first parent (None at the root). Walking first-parent follows the mainline of a
    merge-based history; pass first_parent=False to include every ancestor in topo order."""
    fmt = "%H|%P|%at|%ct"
    args = ["log", "--reverse", "--pretty=" + fmt]
    if first_parent:
        args.append("--first-parent")
    args.append(ref)
    out = _git(repo, args).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 4 or len(parts[0]) < 7:
            continue
        sha, parents, at, ct = parts
        first = parents.split()[0] if parents.strip() else None
        rows.append({"sha": sha, "parent": first, "atime": int(at), "ctime": int(ct)})
    return rows


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_ranges(repo: str, parent: str | None, sha: str) -> dict | None:
    """{file -> [(lo, hi), ...]} of the NEW-side line ranges a commit touched, parsed from
    `git diff --unified=0 -w --ignore-blank-lines` against the parent (so whitespace and
    blank-line changes do not count). None means "no parent" (root commit): every file is
    new, so the caller treats every method as changed. A pure-deletion hunk (no new lines) is
    recorded as a zero-width touch at the line it followed, so the surrounding method still
    reads as changed."""
    if parent is None:
        return None
    out = _git(repo, ["diff", "--unified=0", "--no-color", "--no-renames", "-w",
                      "--ignore-blank-lines", parent, sha]).stdout
    ranges: dict = {}
    cur: list | None = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                cur = None
            else:
                cur = ranges.setdefault(path[2:] if path.startswith("b/") else path, [])
            continue
        if cur is None:
            continue
        m = _HUNK.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count == 0:                      # pure deletion: touch the line it followed
                cur.append((start, start))
            else:
                cur.append((start, start + count - 1))
    return ranges


def _overlap_churn(ranges: list, start: int, end: int) -> int:
    """Added lines within [start, end] across the hunk ranges, 0 if none overlap."""
    churn = 0
    for lo, hi in ranges:
        a, b = max(lo, start), min(hi, end)
        if a <= b:
            churn += b - a + 1
    return churn


def _method_id(m: dict) -> list:
    return [m["file"], m["qualname"]]


def _discard_orphans(methods_path: str, done: set) -> int:
    """Truncate `methods_path` at the first row whose commit is not in `done`: the rows of a
    commit interrupted before its index row was written. Rows are contiguous by commit, so
    the orphans are a tail. Returns the number of bytes discarded."""
    if not os.path.exists(methods_path):
        return 0
    cut = None
    with open(methods_path, "rb") as fh:
        pos = 0
        for line in fh:
            try:
                sha = json.loads(line)["sha"]
            except Exception:
                sha = None
            if sha not in done:
                cut = pos
                break
            pos += len(line)
    if cut is None:
        return 0
    size = os.path.getsize(methods_path)
    with open(methods_path, "r+b") as fh:
        fh.truncate(cut)
    return size - cut


def mine(repo: str, out_base: str, ref: str = "HEAD", first_parent: bool = True,
         include_tests: bool = False, max_commits: int | None = None,
         recent: int | None = None, langs=None) -> None:
    """Walk the history and append the raw per-method mine to <out_base>.methods.jsonl and
    the per-commit index to <out_base>.commits.jsonl (lizard, every language, or only the
    languages in `langs`). Resumable: commits already in the index are skipped."""
    exts = discover.exts_for(langs) if langs else None
    commits_path = out_base + ".commits.jsonl"
    methods_path = out_base + ".methods.jsonl"
    d = os.path.dirname(out_base)
    if d:
        os.makedirs(d, exist_ok=True)

    done: set = set()
    if os.path.exists(commits_path):
        with open(commits_path) as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["sha"])
                except Exception:
                    continue
    dropped = _discard_orphans(methods_path, done)
    if dropped:
        print(f"  discarded {dropped} bytes of an interrupted commit's rows before resuming")

    commits = commit_list(repo, ref, first_parent)
    if max_commits:
        commits = commits[:max_commits]        # the OLDEST N (immature early project)
    if recent:
        commits = commits[-recent:]            # the most RECENT N (mature codebase)
    total = len(commits)
    print(f"mining {repo} @ {ref}: {total} commits "
          f"({'first-parent' if first_parent else 'full ancestry'}"
          f"{', ' + ','.join(sorted(langs)) if langs else ''}), "
          f"{len(done)} already done -> {commits_path} / {methods_path}", flush=True)

    # the throwaway worktree lives BESIDE the mine, not under /tmp: a checkout of a large
    # repository is hundreds of MB, and /tmp is often a small quota-limited tmpfs
    work = tempfile.mkdtemp(prefix=os.path.basename(out_base) + ".wt-",
                            dir=os.path.dirname(os.path.abspath(out_base)))
    wt = os.path.join(work, "wt")
    _git(repo, ["worktree", "prune"])
    added_wt = False
    cf = mf = None
    try:
        for i, c in enumerate(commits):
            sha = c["sha"]
            if sha in done:
                continue
            if not added_wt:
                r = _git(repo, ["worktree", "add", "--detach", "--force", wt, sha])
                if r.returncode != 0:
                    print(f"  worktree add failed at {sha[:10]}: {r.stderr.strip()[:200]}")
                    return
                added_wt = True
            else:
                _git(repo, ["clean", "-qfdx"], cwd=wt)
                r = _git(repo, ["checkout", "--detach", "--force", "--quiet", sha], cwd=wt)
                if r.returncode != 0:
                    print(f"  checkout failed at {sha[:10]}: {r.stderr.strip()[:200]}")
                    continue

            shapes = extract.method_shapes(wt, include_tests, exts)
            ranges = changed_ranges(repo, c["parent"], sha)
            files = {m["file"] for m in shapes}

            changed_ids: list = []
            if cf is None:
                cf = open(commits_path, "a")
                mf = open(methods_path, "a")
            for m in shapes:
                fr = None if ranges is None else ranges.get(m["file"], [])
                if ranges is None:                       # root commit: everything is new
                    changed, churn = True, m["nloc"]
                elif fr:
                    churn = _overlap_churn(fr, m["start"], m["end"])
                    changed = churn > 0 or any(m["start"] <= lo <= m["end"]
                                               or m["start"] <= hi <= m["end"]
                                               for lo, hi in fr)
                else:
                    changed, churn = False, 0
                if changed:
                    changed_ids.append(_method_id(m))
                row = {"sha": sha, "i": i, "atime": c["atime"], **m,
                       "changed": changed, "churn_add": churn}
                mf.write(json.dumps(row) + "\n")
            mf.flush()                                   # methods first, then the index row
            cf.write(json.dumps({
                "sha": sha, "parent": c["parent"], "i": i,
                "atime": c["atime"], "ctime": c["ctime"],
                "n_methods": len(shapes), "n_files": len(files),
                "changed_files": None if ranges is None else sorted(ranges),
                "n_changed_methods": len(changed_ids), "changed_methods": changed_ids,
            }) + "\n")
            cf.flush()
            if i % 25 == 0 or i == total - 1:
                print(f"  [{i + 1}/{total}] {sha[:10]} {len(shapes):>5} methods, "
                      f"{len(changed_ids):>4} changed", flush=True)
    finally:
        if cf:
            cf.close()
        if mf:
            mf.close()
        if added_wt:
            _git(repo, ["worktree", "remove", "--force", wt])
        shutil.rmtree(work, ignore_errors=True)
        _git(repo, ["worktree", "prune"])
    print(f"done: {total} commits -> {methods_path}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", help="path to a local git repository")
    ap.add_argument("-o", "--out", default=None,
                    help="output base path (default: <repo-name>-mine); writes "
                         "<out>.methods.jsonl and <out>.commits.jsonl")
    ap.add_argument("--ref", default="HEAD", help="ref/branch to walk (default HEAD)")
    ap.add_argument("--full-ancestry", action="store_true",
                    help="walk every ancestor commit, not just the first-parent mainline")
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--max-commits", type=int, default=None,
                    help="cap to the OLDEST N commits (a quick look at early history)")
    ap.add_argument("--recent", type=int, default=None,
                    help="mine only the most RECENT N commits (the mature codebase)")
    ap.add_argument("--langs", default=None,
                    help="comma list of language keys to mine (default: every language)")
    args = ap.parse_args(argv)
    out = args.out or (os.path.basename(os.path.abspath(args.repo.rstrip("/"))) + "-mine")
    langs = {l.strip() for l in args.langs.split(",") if l.strip()} if args.langs else None
    mine(args.repo, out, args.ref, not args.full_ancestry, args.include_tests,
         args.max_commits, args.recent, langs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
