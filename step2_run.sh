#!/usr/bin/env bash
# Step 2 of 3 — run the sinew experiment across the sample of repositories.
#
# Co-ordination only: the actual work is `sinew study` (sinew/sinew_study.py). This runs it
# with explicit parameters and then prints the exact command for step 3.
#
#   ./step2_run.sh [--repos FILE] [--out DIR] [--recent N] [--lib-dir DIR]
#
#   --repos FILE    the sample, one git URL per line   (default repos.txt)
#   --out DIR       results directory                  (default results/<today>)
#   --recent N      mine the most recent N commits     (default 500)
#   --lib-dir DIR   vendored dependency directory      (default ./lib)
#
# The run is resumable: re-running with the same --out re-uses repositories already mined.
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
REPOS="repos.txt"
OUT="results/$(date +%F)"
RECENT=500
LIB_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --repos)   REPOS="$2"; shift 2 ;;
    --out)     OUT="$2"; shift 2 ;;
    --recent)  RECENT="$2"; shift 2 ;;
    --lib-dir) LIB_ARGS=(--lib-dir "$2"); shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown argument: $1 (see --help)"; exit 2 ;;
  esac
done

[ -x "$VENV/bin/sinew" ] || { echo "ERROR: $VENV/bin/sinew not found — run ./step1_setup.sh first"; exit 1; }
[ -f "$REPOS" ] || { echo "ERROR: repos file not found: $REPOS"; exit 1; }

CMD=("$VENV/bin/sinew" "${LIB_ARGS[@]}" study --repos "$REPOS" -o "$OUT" --recent "$RECENT")
echo "==> ${CMD[*]}"
"${CMD[@]}"

NEXT="./step3_analyse.sh $OUT"
if [ "$VENV" != ".venv" ]; then
  NEXT="VENV=$VENV $NEXT"
fi

echo
echo "Step 2 (run) complete; results in $OUT/. Next, produce the paper's tables:"
echo "    $NEXT"
