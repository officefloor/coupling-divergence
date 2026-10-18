"""Source-file discovery for the sinew experiment: walk a repository tree and return the
Java source files that are the project's own maintained code, excluding tests, dependencies,
build output and generated/vendored blobs. This is the only shared file-system concern; the
per-method shape extraction is in `java_extract`.
"""
from __future__ import annotations

import os
import re

# Per-method complexity fields captured alongside the coupling signal (all computed in
# java_extract from the tree-sitter AST / text; cognitive from the vendored estimator).
COMPLEXITY_FIELDS = ("cc", "cognitive", "nloc", "tokens", "params", "nesting")

# The experiment is Java-only (the resolver is Java-specific).
SOURCE_EXTS = {".java"}

# A test tree, by directory name as a path component -- including Google/Bazel `javatests`
# (which a plain `test`/`tests` component match misses).
_TEST_DIR = re.compile(r"(?:^|/)(?:test|tests|testing|testdata|javatests|kotlintests)(?:/|$)",
                       re.I)

# Directories that are not the project's own code: dependencies, build output, and vendored
# or generated trees.
_SKIP_DIRS = {".git", "node_modules", "build", "target", "dist", ".venv", "venv",
              "vendor", "vendored", "third_party", "thirdparty", "third-party",
              "generated", "externals", "external", "deps", ".gradle", "out"}

# A source file larger than this is a generated/vendored blob, not hand-maintained code.
MAX_FILE_BYTES = 1_000_000


def source_files(root: str, include_tests: bool = False) -> list[str]:
    """Repo-relative paths of the Java source files under `root` that are the project's own
    maintained code. Tests, dependency/build/generated directories and oversized blobs are
    excluded (tests only when include_tests is False)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in SOURCE_EXTS:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            relp = "/" + rel.replace(os.sep, "/")
            if not include_tests and _TEST_DIR.search(relp):
                continue
            try:
                if os.path.getsize(os.path.join(dirpath, fn)) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(rel)
    return out
