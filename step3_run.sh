#!/usr/bin/env bash
# Step 3 of 4 — run the sinew experiment across the sample of repositories.
#
# Co-ordination only: the actual work is `sinew study` (sinew/sinew_study.py), driven by
# open-source-repository-sampler's run loop: each usable repository of the sample is cloned deep
# (every commit up to its pinned commit, none after), mined and analysed, then deleted. This
# runs it with explicit parameters and then prints the exact command for step 4.
#
#   ./step3_run.sh [--sample FILE] [--out DIR] [--recent N] [--lib-dir DIR]
#
#   --sample FILE   the sample (from step 2, or any other)   (default sample/open-source-repository-sample.csv)
#   --out DIR       results directory                        (default results/<today>)
#   --recent N      mine the most recent N commits           (default 500)
#   --lib-dir DIR   vendored dependency directory            (default ./lib)
#
# The sample is verified against its derivation first. The run is resumable: re-running with the
# same --out skips repositories its run log records as done, and resumes a partly mined one.
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
SAMPLE="sample/open-source-repository-sample.csv"
OUT="results/$(date +%F)"
RECENT=500
LIB_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --sample)  SAMPLE="$2"; shift 2 ;;
    --out)     OUT="$2"; shift 2 ;;
    --recent)  RECENT="$2"; shift 2 ;;
    --lib-dir) LIB_ARGS=(--lib-dir "$2"); shift 2 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown argument: $1 (see --help)"; exit 2 ;;
  esac
done

[ -x "$VENV/bin/sinew" ] || { echo "ERROR: $VENV/bin/sinew not found — run ./step1_setup.sh first"; exit 1; }
[ -f "$SAMPLE" ] || { echo "ERROR: no sample at $SAMPLE — run ./step2_sample.sh, or pass --sample"; exit 1; }

"$VENV/bin/open-source-repository-sampler" verify "$SAMPLE"
CMD=("$VENV/bin/sinew" ${LIB_ARGS[@]+"${LIB_ARGS[@]}"} study --sample "$SAMPLE" -o "$OUT" --recent "$RECENT")
echo "==> ${CMD[*]}"
"${CMD[@]}"

echo
echo "Step 3 (run) complete. Next, build the paper's tables:"
echo "    ./step4_analyse.sh $OUT"
