#!/usr/bin/env bash
# Step 3 of 5 — draw the sample of Java repositories, with open-source-repository-sampler.
#
# Co-ordination only: the actual work is `open-source-repository-sampler sample`. This runs it
# with the experiment's criteria and then prints the exact command for step 4.
#
#   ./step3_sample.sh [--out DIR] [any other `open-source-repository-sampler sample` flag]
#
#   --out DIR   where the two sample files are written   (default sample/)
#
# Writes open-source-repository-sample.csv (every candidate drawn, each pinned to the commit that
# met the criteria) and open-source-repository-sample.derivation.json (how it was drawn). Those
# two files are the experiment's population and go in the paper's ancillary data unchanged.
# Skip this step to reuse an existing sample.
#
# The criteria are set here, once, as code. Each flag is written out even when it equals the
# sampler's default, so the design is readable here and recorded in the derivation:
#   - Java by default (pass --langs java,python,... for a multi-language study; the extractor
#     covers any language lizard parses), across all 3 popularity tiers, grown until 30 usable
#     repositories PER language;
#   - >= 500 commits, so every repository fills step 3's mining window (--recent 500). This is a
#     history criterion, so the sampler clones each candidate with its full history (it says so,
#     and records it in the derivation);
#   - >= 50 methods in >= 15 source files with a method, so a near-empty repository cannot take
#     a slot;
#   - the author's own ecosystem excluded (the conflict-of-interest guard).
# Extra flags are passed straight through (a later flag overrides an earlier one), e.g. a
# different --seed for an independent sample. Interrupted? Re-run the same command to resume.
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
OUT="sample"
ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --out)     OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *)         ARGS+=("$1"); shift ;;
  esac
done

SAMPLER="$VENV/bin/open-source-repository-sampler"
[ -x "$SAMPLER" ] || { echo "ERROR: $SAMPLER not found — run ./step1_setup.sh first"; exit 1; }

CMD=("$SAMPLER" sample
     --langs java
     --tiers high,mid,low
     --target 30
     --seed 1
     --pool 400
     --min-functions 50
     --min-files-with-functions 15
     --min-commits 500
     --exclude-owners officefloor,sagenschneider
     --out-dir "$OUT"
     ${ARGS[@]+"${ARGS[@]}"})
echo "==> ${CMD[*]}"
"${CMD[@]}"

NEXT="./step4_run.sh --sample $OUT/open-source-repository-sample.csv"
if [ "$VENV" != ".venv" ]; then
  NEXT="VENV=$VENV $NEXT"
fi
echo
echo "Step 3 (sample) complete. Next, run the experiment on it:"
echo "    $NEXT"
