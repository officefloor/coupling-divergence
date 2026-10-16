#!/usr/bin/env bash
# Build the paper. Two pdflatex passes, the second resolves references.
# No BibTeX: the bibliography is a thebibliography environment in main.tex.
set -e
cd "$(dirname "$0")"
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
echo "built main.pdf"
