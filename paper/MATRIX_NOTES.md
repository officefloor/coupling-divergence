# Signal × algorithm × definition matrix — methodology, hypothesis, and caveats

This note governs the exploratory comparison produced by `study.analyse` (the `matrix`
block in each `study.json` row) and rendered by `paper/compare_signals.py`. It exists so the
comparison is read honestly rather than as a fishing expedition.

## The question

This is **identification**, not prediction: at a single checkout, does a *concentration
spike* mark the same classes/methods that a textbook god-class definition marks? We vary
three things and hold the repo fixed:

- **signal** — coupling divergence (`gain`); each complexity definition (`cc` cyclomatic,
  `cognitive` Campbell, `nloc`, `tokens`, `params`, `nesting`; `nom` at class level); and
  divergence **weighted** by each complexity definition (`div_x_cc`, …).
- **algorithm** — the relative outlier rule that turns a signal into a flagged set:
  `mad_z` (Iglewicz–Hoaglin, the paper baseline), `double_mad`, `iqr` (Tukey),
  `adj_boxplot` (Hubert–Vandervieren), `evt_pot` (peaks-over-threshold GPD), `pct_top1`,
  `zscore` (classic, parametric control).
- **definition (oracle)** — `WMC≥47`, `CC≥14`, the PMD design panel, and the CK
  cohesion/coupling definitions (`ck_LCOM`, `ck_TCClow`, `ck_CBOhigh`) plus an approximate
  DECOR `decor_blob`; at method level `CC≥14`, `cognitive≥15`, `nesting≥5`, `nloc≥50`,
  `params≥5`.

Both method and class/file level.

## Two metrics, kept separate on purpose

A signal is continuous; an algorithm thresholds it. Mixing them confounds "is this a better
signal" with "was its threshold set tighter". So:

- **Signal quality = AUC / average precision** of the raw signal value against the
  definition's label, over the common universe — **threshold-free, no algorithm**
  (Mann–Whitney AUC; AP for the rare-class view).
- **Algorithm quality = MCC** of the flagged set at the algorithm's own threshold (Matthews
  correlation, the balanced choice for imbalanced labels; Chicco & Jurman 2020).

Signals are ranked by AUC; algorithms by MCC.

## Aggregation

Per the standard for comparing many methods over many criteria (Demšar 2006): rank within
each `(repo, definition)` instance, average the ranks, test with Friedman, and read the
Nemenyi critical difference for significance — reported next to the plain mean.

## Pre-registered PRIMARY hypothesis (everything else is exploratory)

> **H1.** At the class level, **`div_x_cc`** (coupling divergence weighted by cyclomatic
> complexity) identifies the **composite** god definitions (`pmd_GodClass`, `decor_blob`)
> with **higher AUC** than any pure-complexity signal and than plain `divergence`.
>
> **H2.** Plain **`divergence`** identifies the **coupling** definitions
> (`pmd_CouplingBetweenObjects`, `ck_CBOhigh`) with higher AUC than any pure-complexity
> signal.

H1/H2 are the claims to be confirmed on the full, frozen population (ideally on a held-out
half, since the matrix is large — see caveat 1). Any other "winner" that emerges from the
~2,000 cells is a hypothesis for a future study, not a result of this one.

## Caveats (read every headline number against these)

1. **Multiple comparisons.** 13–14 signals × 7 algorithms × ~11 definitions × 2 levels is a
   large search; the single best cell is upward-biased. Confirm the pre-registered H1/H2
   only, and prefer a train/test split of the repositories for any ranking claim.
2. **Correlated definitions.** `WMC`, `TooManyMethods`, `NcssCount`, `ExcessiveClassLength`,
   `TooManyFields` are all essentially size — several votes for one dimension. They are
   therefore **grouped** (size/complexity, cohesion, coupling, composite) and never averaged
   into one number; a size signal winning the size group is tautological, not a finding.
3. **Type-aware oracle vs type-blind signal.** PMD and CK parse Java and resolve
   inheritance/dispatch; the signals are name-based and type-blind by design. On
   abstraction/polymorphism the oracle can mark a god the signal structurally cannot, so a
   signal's measured AUC is a **floor**, not its true ceiling.
4. **Java-only panel.** Beyond `WMC`/`CC`, every definition (PMD, CK, DECOR) is Java-only, so
   the multi-definition comparison is a **Java** result; the other ten languages contribute
   only the size definitions.
5. **No human ground truth.** Every oracle is tool-derived and itself imperfect. This
   measures **agreement between detectors**, not correctness. The cohesion (`ck_*`) and
   DECOR thresholds are defensible but debatable; `decor_blob` omits DECOR's data-class
   association clause, so it is a faithful-but-partial Blob, not the certified Ptidej rule.
6. **Cognitive complexity** is ImpactGate's faithful approximation, not the certified
   SonarSource value; fine for a relative spike, read as an estimate.

## References

- D. Chicco, G. Jurman. *The advantages of the Matthews correlation coefficient (MCC) over
  F1 score and accuracy in binary classification evaluation.* BMC Genomics, 2020.
- J. Demšar. *Statistical Comparisons of Classifiers over Multiple Data Sets.* JMLR 7, 2006.
- M. Hubert, E. Vandervieren. *An adjusted boxplot for skewed distributions.* CSDA, 2008.
- N. Moha et al. *DECOR: A Method for the Specification and Detection of Code and Design
  Smells.* IEEE TSE 36(1), 2010.
