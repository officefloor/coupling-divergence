#!/usr/bin/env bash
# Step 2 of 5 — choose and VERIFY the AI raters, before anything real is rated.
#
# Co-ordination only: the actual work is `sinew rate --check --save`. It builds the planted
# history with known ground truth (sinew/planted.py: five four-method chains that each thread
# two types and are edited together, two heavy pairs, five decoy pairs that are edited together
# but pass nothing, and a quiet pair), mines it, writes its BLIND dossiers, has each chosen
# model rate them with a neutral question, unblinds against the key and prints the scorecard.
# The check passes only when every model rates at least 80% of the planted chains and at most
# 20% of the matched decoys "consolidate", with no errors (a coin-flip rater passes with
# probability 0.035, printed with the result); it then saves the raters to rater.json, which
# step 4 reads so the same raters, runs and effort carry through the whole study. Every decision and its justification is on record
# in <work-dir>/blind/ratings.jsonl for anyone to read.
#
#   ./step2_rater.sh [--models a,b] [--runs N] [--effort E] [--work DIR] [--skip]
#
#   --models   comma list of model ids       (default claude-opus-5-5; several families =
#                                             independent raters, e.g. claude-opus-5-5,claude-sonnet-5-5)
#   --runs     ratings per model per dossier (default 1; 2+ measures self-consistency)
#   --effort   thinking effort               (default high)
#   --work     work directory for the check  (default results/rater-check)
#   --skip     record that the study runs WITHOUT AI rating (removes rater.json)
#
# Needs the optional pinned dependency (installed here into the venv) and Anthropic
# credentials: ANTHROPIC_API_KEY in the environment, or an `ant auth login` profile.
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
MODELS="claude-opus-5-5"
RUNS=1
EFFORT="high"
WORK="results/rater-check"
SKIP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --models) MODELS="$2"; shift 2 ;;
    --runs)   RUNS="$2"; shift 2 ;;
    --effort) EFFORT="$2"; shift 2 ;;
    --work)   WORK="$2"; shift 2 ;;
    --skip)   SKIP=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown argument: $1 (see --help)"; exit 2 ;;
  esac
done

[ -x "$VENV/bin/sinew" ] || { echo "ERROR: $VENV/bin/sinew not found — run ./step1_setup.sh first"; exit 1; }

NEXT="./step3_sample.sh"
[ "$VENV" != ".venv" ] && NEXT="VENV=$VENV $NEXT"

if [ "$SKIP" = 1 ]; then
  rm -f rater.json
  echo "Step 2 (rater) skipped: no rater.json, so step 4 will run the study WITHOUT AI rating."
  echo "Next, draw the sample (needs \`gh auth login\`):"
  echo "    $NEXT"
  exit 0
fi

# the optional pinned dependency, exactly as sinew/deps.py OPTIONAL says
PIN=$("$VENV/bin/python" -c 'from sinew import deps; print("anthropic==" + deps.OPTIONAL["anthropic"])')
echo "==> installing $PIN"
"$VENV/bin/python" -m pip install --quiet "$PIN"

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] && ! command -v ant >/dev/null 2>&1; then
  echo "ERROR: no Anthropic credentials: export ANTHROPIC_API_KEY, or run \`ant auth login\`"
  exit 1
fi

CMD=("$VENV/bin/sinew" rate --check --save "$WORK" --models "$MODELS" --runs "$RUNS" --effort "$EFFORT")
echo "==> ${CMD[*]}"
"${CMD[@]}"

echo
echo "Step 2 (rater) complete: rater.json holds the verified raters; commit it with the sample."
echo "Inspect the decisions and justifications:  $WORK/blind/ratings.jsonl  (key: $WORK/blind/mine.key.json)"
echo "Next, draw the sample (needs \`gh auth login\`):"
echo "    $NEXT"
