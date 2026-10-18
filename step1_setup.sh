#!/usr/bin/env bash
# Step 1 of 4 — set up the sinew experiment.
#
# Co-ordination only: the actual work is in ./setup.sh (which also installs the pinned
# open-source-repository-sampler from PyPI). This runs it and then prints the exact command for step 2.
#
#   ./step1_setup.sh                       # venv in .venv, deps vendored in ./lib
#   LIB_DIR=/opt/sinew-lib ./step1_setup.sh   # vendor deps elsewhere (carried into step 3)
set -euo pipefail
cd "$(dirname "$0")"

./setup.sh

PREFIX=""
if [ -n "${VENV:-}" ] && [ "$VENV" != ".venv" ]; then
  PREFIX="VENV=$VENV "
fi
RUN="${PREFIX}./step3_run.sh"
if [ -n "${LIB_DIR:-}" ] && [ "$LIB_DIR" != "lib" ]; then
  RUN="$RUN --lib-dir $LIB_DIR"
fi

echo
echo "Step 1 (setup) complete. Next, draw the sample (needs \`gh auth login\`):"
echo "    ${PREFIX}./step2_sample.sh"
echo "or skip step 2 and run on an existing sample (e.g. the paper's, or another study's):"
echo "    $RUN --sample path/to/open-source-repository-sample.csv"
