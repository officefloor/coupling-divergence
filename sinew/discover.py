"""Source-file discovery for the sinew experiment: walk a repository tree and return the
source files that are the project's own maintained code, excluding tests, dependencies, build
output and generated/vendored blobs, and say which language each file is. This is the only
shared file-system concern; the per-method shape extraction is in `lizard_extract`.
"""
from __future__ import annotations

import os
import re

from .extract_common import COMPLEXITY_FIELDS  # noqa: F401  (re-exported for callers)

# Extension -> language key. The keys match open-source-repository-sampler's language labels
# where it has one, so a project's detected language and its sampled stratum agree.
LANG_BY_EXT = {
    ".java": "java",
    ".kt": "kotlin", ".scala": "scala", ".groovy": "groovy",
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rb": "ruby", ".php": "php", ".rs": "rust", ".cs": "c#",
    ".c": "c", ".h": "c",
    ".cc": "c++", ".cpp": "c++", ".cxx": "c++", ".hh": "c++", ".hpp": "c++",
    ".m": "objective-c", ".mm": "objective-c", ".swift": "swift", ".lua": "lua",
}
SOURCE_EXTS = set(LANG_BY_EXT)

# A test tree, by directory name as a path component -- including Google/Bazel `javatests`
# and the JS/Ruby spec conventions.
_TEST_DIR = re.compile(r"(?:^|/)(?:test|tests|testing|testdata|javatests|kotlintests|spec|specs"
                       r"|__tests__|[^/]+\.(?:tests?|unittests|integrationtests|specs))(?:/|$)",
                       re.I)
# Test files that live BESIDE the code they test, by each language's naming convention
# (Go *_test.go, JS/TS *.test.* / *.spec.*, pytest test_*.py / *_test.py, RSpec *_spec.rb,
# gtest *_test.cc / *_unittest.cc, Rust tests.rs). A directory filter alone misses them.
_TEST_FILE = re.compile(
    r"(?:_test\.go"
    r"|[.-](?:test|spec)\.(?:js|jsx|ts|tsx)"
    r"|(?:^|/)(?:test_[^/]*|[^/]*_test|conftest|tests)\.py"
    r"|(?:^|/)(?:test_[^/]*|[^/]*_(?:test|spec))\.rb"
    r"|_(?:unit)?test\.(?:c|cc|cpp|cxx)"
    r"|(?:^|/)tests\.rs)$", re.I)

# Directories that are not the project's own code: dependencies, build output, and vendored
# or generated trees.
_SKIP_DIRS = {".git", "node_modules", "build", "target", "dist", ".venv", "venv",
              "vendor", "vendored", "third_party", "thirdparty", "third-party",
              "generated", "externals", "external", "deps", ".gradle", "out"}

# A source file larger than this is a generated/vendored blob, not hand-maintained code.
MAX_FILE_BYTES = 1_000_000


def language(rel: str) -> str | None:
    """The language key of a source file by its extension, or None if not a source file."""
    return LANG_BY_EXT.get(os.path.splitext(rel)[1].lower())


def source_files(root: str, include_tests: bool = False, exts: set | None = None) -> list[str]:
    """Repo-relative paths of the source files under `root` that are the project's own
    maintained code, optionally restricted to the extensions in `exts`. Tests, dependency /
    build / generated directories and oversized blobs are excluded (tests only when
    include_tests is False)."""
    want = SOURCE_EXTS if exts is None else {e.lower() for e in exts}
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in want:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            relp = "/" + rel.replace(os.sep, "/")
            if not include_tests and (_TEST_DIR.search(relp) or _TEST_FILE.search(relp)):
                continue
            try:
                if os.path.getsize(os.path.join(dirpath, fn)) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(rel)
    return out
