#!/usr/bin/env bash
# Step 5 of 5 — transform the experiment's results into the paper's tables.
#
# Co-ordination only: the actual work is paper/make_tables.py, which reads
# <results-dir>/sinew-study.json and writes paper/tables.md and paper/aggregate.tex.
#
#   ./step5_analyse.sh <results-dir>        # e.g. results/2026-10-18 (printed by step 4)
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"

if [ $# -ne 1 ]; then
  echo "usage: $0 <results-dir>   (the directory printed at the end of ./step4_run.sh)"
  exit 2
fi
RESULTS="$1"

[ -x "$VENV/bin/python" ] || { echo "ERROR: $VENV/bin/python not found — run ./step1_setup.sh first"; exit 1; }
[ -f "$RESULTS/sinew-study.json" ] || { echo "ERROR: no $RESULTS/sinew-study.json — run ./step4_run.sh first"; exit 1; }

CMD=("$VENV/bin/python" paper/make_tables.py "$RESULTS")
echo "==> ${CMD[*]}"
"${CMD[@]}"

echo
echo "Step 5 (analyse) complete. The experiment is finished:"
echo "    data:    $RESULTS/   (sinew-study.json, clusters.jsonl, mines/, dossiers/, rated/)"
echo "    tables:  paper/tables.md, paper/aggregate.tex"
