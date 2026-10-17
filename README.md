# coupling-divergence

Watch coupling divergence to catch a god class **before it forms**.

A god class is usually defined by thresholds measured after the damage is done: a class is
flagged once it is already large, low-cohesion and reaching into everyone's data. coupling-divergence
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
  `coupling-divergence evaluate` and the note in Status.

It is standalone and language-independent. [lizard](https://github.com/terryyin/lizard)
supplies the multi-language function list, cyclomatic complexity and line ranges; every
measure above lizard is lexical, so coupling-divergence runs on any language lizard parses with no
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
zero, such as a request handler the framework calls, scores zero. coupling-divergence uses a
directional **difference** built to find sources, so it flags the entry handler that the
product misses. It is also threshold-free, where the classic god-class detectors gate on a
cohesion term (`TCC < 1/3`) that dependency injection defeats, since a shared injected
service makes an enormous class look cohesive.

## Use

```
coupling-divergence report   <repo>              # areas of concern at HEAD: the forming gods
coupling-divergence collect  <repo> -o hist.json # the divergence trajectory over sampled history
coupling-divergence evaluate <repo> [repo ...]   # lead-lag: does divergence predict the god first
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
(jsoup) to 3.8x (commons-lang) enriched in God Classes. Whether it separates "coupled" from
"merely complex" is **not established**: on the pilot's Java repos it flagged 52% of the
WMC-only leaves (WMC >= 47, not a PMD God Class) and 49% of the WMC-sized PMD God Classes,
i.e. no discrimination. The study now reports both shares (`wmc_only_leaves_flagged_by_gen`
vs `wmc_pmd_gods_flagged_by_gen`) and compares every headline lift with a size baseline
(a methods-per-file and a lines spike on the same universe), so the question is answered by
the clean run rather than asserted. Pilot numbers predate the extractor fixes below.

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

The whole study — sampling, measurement and statistics — is **one command with no
arguments**:

```
git clone <this repo> && cd coupling-divergence
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
coupling-divergence grow --anc anc/coupling-divergence-study     # that's it
```

`grow` reproduces the published **method**. GitHub search results move, so a fresh `grow`
draws a similar but not identical population; to re-measure the exact published population,
run `coupling-divergence study --repos repos.txt`, which clones every repository at the
commit pinned in that file (`URL COMMIT` per line, written by `grow`). Every study parameter
is a built-in default (`coupling_divergence/grow.py`, the `STUDY` dict), so running it bare
is the canonical method: seed `1`, a target of **30 usable repositories per language** (up to **40**
for the noisy ones, until each language's 95% interval half-width on log-lift is ≤ `0.2`),
a substance floor of **≥ 50 functions and a ≥ 15-file class universe** so near-empty repos
cannot enter the analysis, across **11 languages × 3 popularity tiers**, each tier drawn
across five star sub-bands in proportion to their population (GitHub returns only the top
1000 of a search, so drawing a tier whole would sample only its most-starred end). A repo is
*usable* when it clears the floor and has at least one reference god and one outlier to
score; whether the outliers hit a god is never part of the rule, so a failing repo stays in
as a negative observation (0.5-corrected lift) instead of being replaced by a fresh draw.

The oracles are part of the configuration. A `pmd` (PMD 7) on `PATH` is picked up for the
Java design panel, run on exactly the files the extractor measured; the CK jar
(mauricioaniche/ck) for the cohesion/coupling panel comes from `--ck` or `CK_JAR`. `grow`
prints `=> CANONICAL STUDY CONFIGURATION` only when no default is overridden **and** both
oracles are present; without CK the pre-registered H1/H2 cannot be tested and it says so.
Each row records the lizard/PMD/CK versions that produced it.

Because a repository's language and whether it yields a usable measurement are only known
*after* it is cloned and measured (a GitHub "C++" repo often measures as C, half the Ruby
draws are tiny gems), `grow` couples sampling to measurement: it draws a seeded batch,
measures it, and keeps topping up only the languages still short, round by round, until
every language meets the target or its population is exhausted (recorded honestly in the
manifest, never hidden). The stopping rule watches the observed spread of log-lift
(sequential, optional stopping on variance), which is recorded in the manifest and should
be disclosed. Each repo is shallow-cloned at its latest default-branch commit,
measured, the commit sha recorded, and the clone deleted — nothing is kept on disk but the
recorded commits, and the public repositories at those commits are the source of truth. The
run prints per repo as it goes, is **resumable** (re-running skips repos already in the
output), and writes `repos.txt`, `sampling-manifest.json`, the results JSON/CSV and (with
`--anc`) the self-contained ancillary package.

To run a *different* study, override a default — e.g. `--target 20`, `--seed 2`,
`--langs java,go` — and `grow` prints `=> non-default parameters: this is a DIFFERENT
study` so the change is never silent.

### How the study population is chosen (the honest part)

The author's ecosystem is one of the things this metric is applied to, so the only sound
defence against self-serving selection is to put the sampling in code and let anyone rerun
it. The sampler draws from the GitHub search API, stratified by **language** and by
**popularity tier** (a proxy for the spread from mature to rough code), with a seeded random
pick within each stratum; `grow` drives that draw adaptively to the per-language target.
`sampling-manifest.json` is the auditable frame: it records the seed, the date, every search
query, how many repositories matched each stratum (so the selection probability is visible),
which were chosen with their star counts, and which languages (if any) exhausted their
population before the target. `repos-exemplar.txt` keeps the earlier hand-picked well-known
set for sanity.

The older fixed-size sampler is still available for a flat draw:

```
coupling-divergence sample --k 3 --seed 1 -o repos.txt -m sampling-manifest.json
coupling-divergence study  --repos repos.txt --anc anc/   # measure a fixed list, resumable
```

The sampler **excludes the author's own GitHub owners** (`officefloor`, `sagenschneider`)
by construction, applied both as a `-user:` query qualifier and as a post-fetch filter and
recorded in the manifest, so the author's own code can never enter the sample. That is the
conflict-of-interest guard, encoded rather than trusted.

One caveat stated plainly: GitHub is a moving target, so rerunning the sampler reproduces
the **method**, not necessarily the identical list. The committed manifest and the per-repo
commits in the results pin the exact population used. Popularity tier is a proxy for
quality, not a measurement of it, and application-vs-library type is not controlled by the
sampler; the study classifies each repo post-hoc (a heuristic) and reports the realized
mix, so the sample's composition is visible rather than assumed.

`--anc` bundles the frame (the repo list and `sampling-manifest.json`) with the results and
README, so the paper's attachment shows how the population was chosen as well as what was
found.

What it measures per repo: the class-cumulated generation outlier against the god-class size
reference `WMC >= 47`, the method generation outlier against the brain-method reference
`CC >= 14`, and (Java, with `--pmd`) PMD's full `GodClass`. For each it reports the
enrichment (lift), recall, and a one-sided **Fisher exact p-value** for that repo.

At the end it prints the pooled result: per language, and across all repos the geometric-
mean lift with a Student-t 95% interval, how many repos are individually significant, the
number of repos needed to reach a target CI half-width, and for each headline the paired
divergence/size-baseline lift ratio (lift is bounded by 1/base-rate, so a lift on its own
cannot show divergence is more than size). That last number is a **stopping rule**:
because the run is resumable, grow `repos.txt` and re-run until the repos-needed estimate is
at or below the repos measured, then the sample is large enough.

`--anc DIR` writes the paper's ancillary-data package there: `results.csv` (one row per
repo, with the exact commit) and a self-contained `README.txt` explaining every column and
the method, so the table stands alone without the paper or this repository.

### Result on the default 25-repo list (11 languages)

Class-generation outlier vs `WMC >= 47`, mean lift per language: C# 5.2, JS 6.6, TS 5.7,
C++ 4.1, Ruby 4.7, Java 4.1, Rust 3.4, Go 3.1, Python 1.9, PHP 1.7, C 1.5. The detector
carries across every language tried (lift above 1 everywhere, mostly 3 to 6x), weakest in
C and Python. Exact commits are in the committed `example-study.json`.

## For ImpactGate: per-change placement check

`coupling_divergence/extract.py` is vendored into ImpactGate as `impact_gate/core/generation.py`, and
`impact_gate/placement_check.py` uses it to flag, for a change request, the added or mutated
methods that are generation outliers in the resulting tree (a coupling vortex), reported
beside the impact score as a separate axis. See `impact_gate/tests/test_placement_check.py`.

## License

Apache-2.0.
