# GodWatch

Watch coupling divergence to catch a god class **before it forms**.

A god class is usually defined by thresholds measured after the damage is done: a class is
flagged once it is already large, low-cohesion and reaching into everyone's data. GodWatch
measures the mechanism instead of the symptom: it finds the method that manufactures more
coupling than it receives, the orchestrator everything piles onto.

What is established and what is not, measured not asserted:

- **Detection holds.** On open-source Java libraries a divergence outlier is 1.8 to 4.5
  times more likely to sit in a god-sized file than chance, and the top generator is the
  real monster (`TokeniserState.read`, `ArrayUtils.shift`, `InsnDecoder.decode`).
- **Early warning is not yet demonstrated on organic history.** In a controlled
  single-handler accretion (an endpoint growing 60 rules, the AI-pipeline case) the
  divergence outlier formed about three commits before the file crossed `WMC >= 47`. On
  2000 to 10000 commits of real library history that lead did not replicate: the outlier
  tended to coincide with or lag the size threshold. The leading-indicator claim is
  therefore scoped to gradual single-locus accretion and is still under validation; see
  `godwatch evaluate` and the note in Status.

It is standalone and language-independent. [lizard](https://github.com/terryyin/lizard)
supplies the multi-language function list, cyclomatic complexity and line ranges; every
measure above lizard is lexical, so GodWatch runs on any language lizard parses with no
per-language call-graph analyser. (Same substrate as
[impact-gate](https://github.com/officefloor/ImpactGate).)

## The god math

Treat the arguments passed at a call as flow over a control edge. For each function `v`,
counting only calls to **our own code** (a callee whose name is a function defined
somewhere in the tree, so calls into libraries and the database do not count):

```
out_gen(v) = sum over internal call sites of (argument count)   # flow it sends on
in_gen(v)  = arguments pushed into v by its callers             # flow it receives
gain(v)    = max(out_gen(v) - in_gen(v), 0)                      # flow it MANUFACTURES
```

A conventional pipe joint splits flow and conserves it. A god joint *generates* flow: it
takes a little in and radiates a lot out, so every purpose routed through it piles on. A
god is the dominant, persistent **outlier** in `gain` (Iglewicz-Hoaglin modified z-score,
so it is a relative outlier, not a tuned threshold), the node that creates more coupling
than it receives.

Two filters carry the whole meaning, which is why no "is this a domain type" test is
needed. Internal-only excludes the converter and the row mapper: they push their columns
onto a call that leaves the system, so their generation is near zero. Out-minus-in zeroes
anything that takes more than it emits. What is left is the orchestrator that manufactures
flow, which is the god worth catching.

`WMC` per file (sum of function cyclomatic complexity) is the textbook size reference; the
Lanza-Marinescu god-class size threshold is `WMC >= 47`.

## Why net flow, not the classic fan-in × fan-out

The canonical information-flow metric (Henry & Kafura, 1981) is `length × (fan-in ×
fan-out)²`, a symmetric **product**. A product cannot see a source: a method with fan-in
zero, such as a request handler the framework calls, scores zero. GodWatch uses a
directional **difference** built to find sources, so it flags the entry handler that the
product misses. It is also threshold-free, where the classic god-class detectors gate on a
cohesion term (`TCC < 1/3`) that dependency injection defeats, since a shared injected
service makes an enormous class look cohesive.

## Use

```
godwatch report   <repo>              # areas of concern at HEAD: the forming gods
godwatch collect  <repo> -o hist.json # the divergence trajectory over sampled history
godwatch evaluate <repo> [repo ...]   # lead-lag: does divergence predict the god first
```

`report` ranks functions by generation and marks each as a god already, a forming god (an
outlier not yet in a god-sized file), or ordinary. `collect` emits the per-commit
trajectory for a dashboard. `evaluate` is the empirical test: over every file that is a god
at HEAD, it checks whether the divergence outlier appeared at or before the commit the file
crossed `WMC >= 47`, and reports the lead.

## Status

Alpha. The extractor and the lead-lag evaluation are in place and self-tested
(`tests/test_extract.py`), and the detection result above is measured on real libraries.

**Detection.** Validated against the authoritative God Class detector (PMD's `GodClass`,
the full Lanza-Marinescu strategy) on non-DI libraries: class-cumulated generation is 2.9x
(jsoup) to 3.8x (commons-lang) enriched in God Classes, and it excludes roughly three
quarters of the complex-leaf algorithms that `WMC >= 47` alone wrongly flags, the same
"coupled, not merely complex" distinction the textbook needs its ATFD and TCC terms to
make. It catches the out-flow (orchestrator) gods, not the in-flow (foreign-data-access)
gods, so it is complementary to the ATFD axis, not a replacement.

**Detection is a placement question, so it needs no history.** A god is a spike in the
distribution of generation across units, and a spike is visible the instant the unit
exists. On commons-lang's very first commit (13 files) `StringUtils` is already the rank-1
generation outlier, flagged on day one, whether it was imported mature or grew in view. So
the tool does not watch a build-up to detect a god; it tests placement on the current
snapshot and flags the outlier.

**Live monitoring is that same test, re-run per commit.** Recompute the placement outliers
on each commit and flag a unit the moment a commit creates or grows it into a spike. In an
AI pipeline you catch the agent's own commit that made the vortex, on that commit. "The
instant it forms", not "before it forms".

**Predicting strictly *before* formation is a separate, harder claim, and it is unproven.**
Tested every fair way (rename-following, formation-within-window, absolute and relative
references), the generation outlier shows no systematic lead over the size signal; it
coincides or lags, with a few strong exceptions in concentrated single-locus accretion
(jsoup `Node` by 22 commits; a controlled single-handler accretion by 3). Being imported
mature breaks only this predictive claim, never detection. Treat before-the-fact
prediction as **unproven and data-starved** on open-source history; the regime where it
appears is the AI pipeline's own gradual accretion, which is where to study it if wanted.

## Reproduce the study

The detection result is reproducible by anyone, with nothing kept on disk but the recorded
commits. Each repo is shallow-cloned at its default branch's latest commit, measured, the
commit sha recorded in the output JSON, and the clone deleted. The source of truth is the
public repositories at those commits.

```
git clone <this repo> && cd GodWatch
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
godwatch study --repos repos.txt                      # progress to console; writes godwatch_study.json
godwatch study --repos repos.txt --pmd /path/to/pmd   # also use PMD GodClass on Java repos
```

It prints per repo as it goes (`[i/N] name: cloning ...` then the measured line), is
**resumable** (re-running skips repos already in the output), and scales to 100+ repos by
just adding URLs to `repos.txt`. Each row in the JSON records `commit` and `date` so a run
is pinned to exact revisions.

What it measures per repo: the class-cumulated generation outlier against the god-class size
reference `WMC >= 47`, the method generation outlier against the brain-method reference
`CC >= 14`, and (Java, with `--pmd`) against PMD's full `GodClass`. It reports enrichment
(lift) and recall, pooled per language.

### Result on the default 25-repo list (11 languages)

Class-generation outlier vs `WMC >= 47`, mean lift per language: C# 5.2, JS 6.6, TS 5.7,
C++ 4.1, Ruby 4.7, Java 4.1, Rust 3.4, Go 3.1, Python 1.9, PHP 1.7, C 1.5. The detector
carries across every language tried (lift above 1 everywhere, mostly 3 to 6x), weakest in
C and Python. Exact commits are in the committed `example-study.json`.

## For ImpactGate: per-change placement check

`godwatch/extract.py` is vendored into ImpactGate as `impact_gate/core/generation.py`, and
`impact_gate/placement_check.py` uses it to flag, for a change request, the added or mutated
methods that are generation outliers in the resulting tree (a coupling vortex), reported
beside the impact score as a separate axis. See `impact_gate/tests/test_placement_check.py`.

## License

Apache-2.0.
