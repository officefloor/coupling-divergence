# Paper

Detection study for coupling divergence. Companion to arXiv:2610.14130 (conserved
amount, negotiable placement).

## Build

```
./build.sh          # pdflatex twice, writes main.pdf
```

No BibTeX. The bibliography is a `thebibliography` block in `main.tex`.

## Files

- `main.tex` the paper.
- `stats.tex` every cited number as a macro, defined once. Reconciling the draft
  with a fresh run is a one-line edit per value here.
- `pilot-stats.txt` the numbers the current draft uses, dumped from the pilot run.
- `data/pilot-2026-10-16/` the frozen pilot results the draft is written against
  (study.json, study.csv, and the self-contained ancillary package). Kept so the
  draft has a stable reference while the clean run proceeds.

## Data status

The committed numbers are from the pilot run. The clean run (fixed Fisher table,
exact p on every repository) replaces them. When it lands, update `stats.tex` and
`data/` and rebuild. Lift and recall were already exact in the pilot, so only the
per-repository p-values and the three previously clamped repositories can move.

## Submit

```
tar czf arxiv.tar.gz main.tex stats.tex
```
