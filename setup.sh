#!/usr/bin/env bash
# Set up the sinew experiment so it can be run by anyone with no manual steps.
#
# Creates a virtualenv, vendors the EXACT pinned dependencies (sinew/deps.py)
# as wheels into ./lib (a known, git-ignored location), installs them from there, installs
# this package, and validates every version. Re-running is idempotent.
#
#   ./setup.sh                 # venv in .venv, deps vendored in ./lib
#   LIB_DIR=/opt/sinew-lib ./setup.sh   # vendor deps elsewhere (then run with --lib-dir)
#
# After setup:   . .venv/bin/activate   &&   sinew --help
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
LIB_DIR="${LIB_DIR:-lib}"
PY="${PYTHON:-python3}"

echo "==> python: $("$PY" --version)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' \
  || { echo "ERROR: Python >= 3.10 required"; exit 1; }

# 1. virtualenv
if [ ! -d "$VENV" ]; then
  echo "==> creating virtualenv in $VENV"
  "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"
"$VPY" -m pip install --quiet --upgrade pip

# 2. read the exact pinned versions from the single source of truth (deps.py)
mapfile -t PINS < <("$VPY" - <<'PY'
from sinew import deps
for name, ver in deps.PINNED.items():
    print(f"{name}=={ver}")
PY
) || { echo "ERROR: could not read pins from sinew/deps.py"; exit 1; }
echo "==> pinned dependencies: ${PINS[*]}"

# 3. vendor the wheels into the known lib location (so replication is offline + reproducible).
#    `pip wheel`, not `pip download`: a pin with no wheel for this Python arrives as an sdist,
#    which is built into a wheel here, while the network is up, so the offline install below
#    never has to build anything. (The current pins are pure Python: one wheel fits every
#    interpreter >= 3.10.)
echo "==> vendoring wheels into $LIB_DIR/"
mkdir -p "$LIB_DIR"
if ! "$VPY" -m pip wheel --quiet --wheel-dir "$LIB_DIR" "${PINS[@]}"; then
  echo "WARNING: could not download wheels (no network?); will install from $LIB_DIR if present"
fi

# 4. install the pinned deps, preferring the vendored lib, then this package
if ls "$LIB_DIR"/*.whl >/dev/null 2>&1; then
  "$VPY" -m pip install --quiet --no-index --find-links "$LIB_DIR" "${PINS[@]}"
else
  "$VPY" -m pip install --quiet "${PINS[@]}"
fi
"$VPY" -m pip install --quiet -e .

# 5. validate — fail closed unless every pinned version is present and exact
echo "==> validating dependency versions"
SINEW_LIB="$LIB_DIR" "$VPY" -c 'from sinew import deps; deps.validate(); print("   all pinned dependencies present and correct")'

echo
echo "Done. Activate and run:"
echo "    . $VENV/bin/activate"
echo "    sinew --help"
