#!/usr/bin/env bash
# Build the paper. Two pdflatex passes, the second resolves references.
# No BibTeX: the bibliography is a thebibliography environment in main.tex.
set -e
cd "$(dirname "$0")"
# Optionally regenerate stats.tex + bylang.tex from a run's study.json before building, so
# every cited number is provably the data, never hand-typed. Pass the study.json as the
# first argument (or set STUDY_JSON); skipped if neither is given. Standalone, stdlib-only.
STUDY_JSON="${1:-${STUDY_JSON:-}}"
if [ -n "$STUDY_JSON" ]; then
  echo "regenerating stats from $STUDY_JSON"
  python3 make_stats.py "$STUDY_JSON"
fi
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
echo "built main.pdf"
