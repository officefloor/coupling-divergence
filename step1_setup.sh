#!/usr/bin/env bash
# Step 1 of 3 — set up the sinew experiment.
#
# Co-ordination only: the actual work is in ./setup.sh. This runs it and then prints the
# exact command for step 2.
#
#   ./step1_setup.sh                       # venv in .venv, deps vendored in ./lib
#   LIB_DIR=/opt/sinew-lib ./step1_setup.sh   # vendor deps elsewhere (carried into step 2)
set -euo pipefail
cd "$(dirname "$0")"

./setup.sh

NEXT="./step2_run.sh"
if [ -n "${LIB_DIR:-}" ] && [ "$LIB_DIR" != "lib" ]; then
  NEXT="$NEXT --lib-dir $LIB_DIR"
fi
if [ -n "${VENV:-}" ] && [ "$VENV" != ".venv" ]; then
  NEXT="VENV=$VENV $NEXT"
fi

echo
echo "Step 1 (setup) complete. Next, run the experiment:"
echo "    $NEXT"
