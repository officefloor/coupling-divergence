#!/usr/bin/env bash
# Step 1 of 5 — set up the sinew experiment.
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
RUN="${PREFIX}./step4_run.sh"
if [ -n "${LIB_DIR:-}" ] && [ "$LIB_DIR" != "lib" ]; then
  RUN="$RUN --lib-dir $LIB_DIR"
fi

echo
echo "Step 1 (setup) complete. Next, choose and verify the AI raters (needs Claude Code, logged in):"
echo "    ${PREFIX}./step2_rater.sh                 # default rater claude-opus-5-5"
echo "    ${PREFIX}./step2_rater.sh --skip          # run the study without AI rating"
echo "then draw the sample (needs \`gh auth login\`):"
echo "    ${PREFIX}./step3_sample.sh"
echo "or skip step 3 and run on an existing sample (e.g. the paper's, or another study's):"
echo "    $RUN --sample path/to/open-source-repository-sample.csv"
