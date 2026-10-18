"""Dependency pinning and validation, for reproducibility.

The experiment's results must be reproducible, so every third-party dependency is pinned to an
exact version and validated at start-up (fail-closed on drift). `setup.sh` installs these exact
versions and also vendors their wheels into a known `lib/` directory, so a replicator gets the
same versions offline. At run time the lib directory is put on the import path first; its
location defaults to `./lib` and is overridable with `--lib-dir` or the `SINEW_LIB` env var.
"""
from __future__ import annotations

import importlib.metadata as _md
import os
import sys

# Exact pinned versions. Bump here AND re-run setup.sh; never loosen to `>=`.
PINNED = {
    # the one extractor: function list, parameter names and complexity, every language
    "lizard": "1.23.0",
    # draws the sample (step 2) and runs the clone loop over it (step 3)
    "open-source-repository-sampler": "0.1.0",
}

DEFAULT_LIB = "lib"


def lib_dir(override: str | None = None) -> str:
    """Resolve the dependency directory: --lib-dir, else $SINEW_LIB, else ./lib."""
    return override or os.environ.get("SINEW_LIB") or DEFAULT_LIB


def use_lib(override: str | None = None) -> str:
    """Put the resolved lib directory first on sys.path so vendored packages win, and return
    it. A missing directory is not an error here (the venv may already hold the deps);
    `validate` is what enforces presence and version."""
    d = os.path.abspath(lib_dir(override))
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)
    return d


def validate(override: str | None = None) -> None:
    """Fail closed unless every pinned dependency is importable at its exact version. Names
    the resolved lib directory and `setup.sh` in the error so the fix is obvious."""
    use_lib(override)
    bad = []
    for name, want in PINNED.items():
        try:
            got = _md.version(name)
        except _md.PackageNotFoundError:
            bad.append(f"  {name}: MISSING (need {want})")
            continue
        if got != want:
            bad.append(f"  {name}: {got} installed, need exactly {want}")
    if bad:
        raise SystemExit(
            "dependency check failed — results would not be reproducible:\n"
            + "\n".join(bad)
            + f"\n\nrun ./setup.sh (installs the pinned versions and vendors them into "
            f"{lib_dir(override)}/), or pass --lib-dir to point at an existing vendored set.")
