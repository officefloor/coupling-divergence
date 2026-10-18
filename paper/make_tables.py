#!/usr/bin/env python3
"""Transform a results directory into publication-ready tables for the paper.

This is the ONLY place the paper's numbers come from: it reads the raw data the experiment
wrote to results/<date>/ and emits LaTeX/Markdown tables into this paper/ directory. Nothing
is calculated here that the experiment did not already produce — it only selects, orders and
formats — so every figure in the paper is traceable to a file in results/.

    python paper/make_tables.py results/<date>            # -> paper/tables.md, paper/*.tex

Run the experiment first (`sinew study --repos repos.txt -o results/<date>`).
"""
from __future__ import annotations

import json
import os
import sys


def _load(results_dir: str) -> list:
    path = os.path.join(results_dir, "sinew-study.json")
    if not os.path.exists(path):
        sys.exit(f"no sinew-study.json in {results_dir} — run `sinew study` first")
    with open(path) as fh:
        return json.load(fh)


def _fmt(v, nd=3):
    return "n/a" if v is None else f"{v:+.{nd}f}" if isinstance(v, float) else str(v)


def aggregate_table(rows: list) -> str:
    """Per-project summary: size, latent sinews, concentration, sprawl, the cc-controlled signal."""
    head = ("| project | methods | latent | top-decile | heavy | clusters | concern | "
            "max span | OUT | cc |\n"
            "|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    out = [head]
    for r in sorted(rows, key=lambda r: -r.get("methods", 0)):
        out.append(f"| {r['base']} | {r['methods']} | {r['latent']} | "
                   f"{r['top_decile']:.2f} | {r['n_heavy']} | {r['clusters']} | "
                   f"{r['concern']} | {r['max_span']} | {_fmt(r.get('out_cc_partial'))} |")
    ocs = [r["out_cc_partial"] for r in rows if r.get("out_cc_partial") is not None]
    if ocs:
        out.append("")
        out.append(f"*OUT → co-change-degree controlling for complexity replicates: "
                   f"mean {sum(ocs) / len(ocs):+.3f} over {len(ocs)} projects "
                   f"(range {min(ocs):+.3f}…{max(ocs):+.3f}).*")
    return "\n".join(out)


def latex_aggregate(rows: list) -> str:
    lines = [r"\begin{tabular}{lrrrrrr}", r"\toprule",
             r"project & methods & latent & top-dec. & concern & max span & OUT$\mid$cc \\",
             r"\midrule"]
    for r in sorted(rows, key=lambda r: -r.get("methods", 0)):
        oc = r.get("out_cc_partial")
        lines.append(f"{r['base'].replace('_', '-')} & {r['methods']} & {r['latent']} & "
                     f"{r['top_decile']:.2f} & {r['concern']} & {r['max_span']} & "
                     f"{'n/a' if oc is None else f'{oc:+.3f}'} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def main(argv: list) -> int:
    if not argv:
        sys.exit("usage: python paper/make_tables.py results/<date>")
    results_dir = argv[0]
    rows = _load(results_dir)
    here = os.path.dirname(os.path.abspath(__file__))

    md = ["# Sinew — results tables",
          f"\nGenerated from `{results_dir}` by `paper/make_tables.py`.\n",
          "## Per-project sinew summary\n", aggregate_table(rows), ""]
    with open(os.path.join(here, "tables.md"), "w") as fh:
        fh.write("\n".join(md))
    with open(os.path.join(here, "aggregate.tex"), "w") as fh:
        fh.write(latex_aggregate(rows) + "\n")
    print(f"wrote paper/tables.md and paper/aggregate.tex from {results_dir} "
          f"({len(rows)} projects)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
