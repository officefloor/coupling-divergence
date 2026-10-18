#!/usr/bin/env python3
"""Transform a results directory into publication-ready tables for the paper.

This is the ONLY place the paper's numbers come from: it reads the raw data the experiment
wrote to results/<date>/ and emits LaTeX/Markdown tables into this paper/ directory. Nothing
is calculated here that the experiment did not already produce — it only selects, orders and
formats — so every figure in the paper is traceable to a file in results/.

    python paper/make_tables.py results/<date>            # -> paper/tables.md, paper/*.tex

Run the experiment first (`./step3_run.sh`, i.e. `sinew study --sample <csv> -o results/<date>`).
"""
from __future__ import annotations

import json
import os
import sys

# The complexity/size-controlled signals the study reports per project, in table order.
SIGNALS = (
    ("in_cc_partial", "IN → co-change degree | cc", r"IN$\mid$cc"),
    ("out_cc_partial", "OUT → co-change degree | cc", r"OUT$\mid$cc"),
    ("fwd_cc_partial", "forwarded args → co-change degree | cc", r"fwd$\mid$cc"),
    ("fwd_nloc_partial", "forwarded args → co-change degree | nloc", r"fwd$\mid$nloc"),
)


def _load(results_dir: str) -> list:
    path = os.path.join(results_dir, "sinew-study.json")
    if not os.path.exists(path):
        sys.exit(f"no sinew-study.json in {results_dir} — run `sinew study` first")
    with open(path) as fh:
        return json.load(fh)


def _project(r: dict) -> str:
    """owner/name as sampled (older results carry only the mine's base name)."""
    return r.get("full_name") or r["base"]


def _fmt(v, nd=3):
    return "n/a" if v is None else f"{v:+.{nd}f}" if isinstance(v, float) else str(v)


def _replication_lines(rows: list, bullet: str = "*") -> list:
    """One line per signal: mean, range and how many projects are positive."""
    out = []
    for key, label, _ in SIGNALS:
        xs = [r[key] for r in rows if r.get(key) is not None]
        if xs:
            out.append(f"{bullet}{label}: mean {sum(xs) / len(xs):+.3f} over {len(xs)} "
                       f"projects (range {min(xs):+.3f}…{max(xs):+.3f}), positive in "
                       f"{sum(1 for x in xs if x > 0)}/{len(xs)}.{bullet}")
    return out


def aggregate_table(rows: list) -> str:
    """Per-project summary: size, co-changing pairs, declared parameters and forward ratio,
    concentration, sprawl, and the complexity/size-controlled signals."""
    head = ("| project | methods | pairs | params (mean) | fwd ratio | top-decile | heavy | "
            "clusters | concern | max span | "
            + " | ".join(s[2].replace("$\\mid$", " \\| ") for s in SIGNALS) + " |\n"
            "|---|" + "--:|" * (9 + len(SIGNALS)))
    out = [head]
    for r in sorted(rows, key=lambda r: -r.get("methods", 0)):
        sig = " | ".join(_fmt(r.get(k)) for k, _, _ in SIGNALS)
        out.append(f"| {_project(r)} | {r['methods']} | {r['pairs']} | "
                   f"{r.get('params_mean', 0):.2f} | {r.get('fwd_ratio_mean', 0):.2f} | "
                   f"{r['top_decile']:.2f} | {r['n_heavy']} | {r['clusters']} | "
                   f"{r['concern']} | {r['max_span']} | {sig} |")
    rep = _replication_lines(rows)
    if rep:
        out.append("")
        out += rep
    return "\n".join(out)


def latex_aggregate(rows: list) -> str:
    cols = "lrrrrrrr" + "r" * len(SIGNALS)
    lines = [rf"\begin{{tabular}}{{{cols}}}", r"\toprule",
             r"project & methods & pairs & params & fwd & top-dec. & concern & max span & "
             + " & ".join(s[2] for s in SIGNALS) + r" \\",
             r"\midrule"]
    for r in sorted(rows, key=lambda r: -r.get("methods", 0)):
        name = _project(r).replace("_", "\\_")             # LaTeX-escape underscores
        sig = " & ".join("n/a" if r.get(k) is None else f"{r[k]:+.3f}" for k, _, _ in SIGNALS)
        lines.append(f"{name} & {r['methods']} & {r['pairs']} & "
                     f"{r.get('params_mean', 0):.2f} & {r.get('fwd_ratio_mean', 0):.2f} & "
                     f"{r['top_decile']:.2f} & {r['concern']} & {r['max_span']} & {sig} \\\\")
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
