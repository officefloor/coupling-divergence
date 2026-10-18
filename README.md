# Sinew — heavy change-coupling

Find the **sinews** in a codebase: bundles of methods that thread the same data down a chain
and are repeatedly edited together. A sinew is connective tissue — it bears load under tension
and is expensive to sever. Most couplings are light capillaries; the few heavy sinews are where
change concentrates and where refactoring becomes expensive, so they are the ones worth
reconsidering *before* they strengthen.

The philosophy and the full method are in **[`paper/SINEW.md`](paper/SINEW.md)**. In short:

- **A thread link is data sent and consumed.** Two methods are *thread-linked* when a
  parameter one of them **forwards** (hands on to a call) reaches a method that **declares**
  it: directly, when the call resolves to that method, or along a chain of such hops in which
  every intermediary declares the parameter and forwards it on. A key both merely declare, or
  that one forwards somewhere else, is not a link. Parameters are matched on their **key**:
  the declared **type** when the type identifies something (`Order`, `RequestContext`), else
  the **name** — so an untyped language is matched by name, and a parameter of a universal
  type (`String`, `int`, `List<T>`, a type variable) is matched by name too, because `String`
  names nothing in particular. Calls are resolved by name, same file first; no type resolver
  is needed, so every language lizard parses is measured the same way.
- **A bond is a thread link that co-changes.** A sinew = **threaded × co-changing × heavy**.
  It builds on *logical / evolutionary coupling* (Gall 1998, Zimmermann 2004) and adds
  **thickness** (the number of parameter keys threaded between the pair); heaviness is
  co-change count × thickness. Co-change without threading is counted, but is not a sinew.
- **The cost is in the threading, not at the ends.** Whether two co-changing methods also
  call each other directly is recorded but is *not* a criterion: a conduit threads data to a
  terminal through intermediaries.
- **Span is the verdict.** Heavy bonds are joined into a graph; each component is a sinew. A
  small one (span ≤ 3) is an intrinsic contract — leave it. A large one (span ≥ 4) is
  accidental sprawl — a hub or a parallel-maintenance clique to reconsider.
- **Observed against expected.** Every count is set against a permutation null that keeps
  each commit's size and each method's change frequency and shuffles who changes together,
  so a project's heavy bonds, spans and concerns are reported with the probability the null
  produces as much.

This repository is the **method**, not the data. It contains only the experiment code and its
documentation; nothing is precomputed. All output is written under a git-ignored results
directory and is published as the paper's ancillary content, not committed here.

## Hypotheses, analysis plan and decision rules

Every threshold below is in `sinew/analyze.py` (`PARAMS`) and is written into the results
file with the environment that produced them; `sinew_study.pooled` applies each decision rule
mechanically and writes the verdict (`decisions`) into the results, so no judgement is made
after the numbers are seen.

**Provenance, stated plainly.** This plan was NOT registered before any data was seen. The
construct was developed, and redefined three times, on 16-18 October 2026 against a pilot: a
hand-curated list of Java repositories and a seed-1 stratified draw. Those repositories are
listed in `pilot-repositories.txt`; if any of them appears in a sample it is analysed and
reported but excluded from every pooled estimate and every decision. The confirmatory sample
is a fresh draw (seed 2, `step3_sample.sh`). Register this file's hypotheses externally with a
timestamp (for example on OSF) *before* running `./step3_sample.sh`, and cite the registration;
until then the study is confirmatory in design only, not in record.

- **H1 (threading is entangling beyond the known causes).** Across methods alive at the pinned
  commit, the forward ratio (share of a method's parameters it threads into the project's own
  code) is positively associated with co-change degree *after partialling out change
  frequency, size (nloc), cyclomatic complexity, internal call out-degree and parameter count
  together*. The last two matter: a method that forwards must call, and a method with more
  parameters has more to forward, so without them the partial re-tests call coupling and
  interface size. Statistic: partial Spearman per project with a 95% interval from a cluster
  bootstrap that resamples *files* (methods of one file are not independent draws); pooled
  across projects by random effects on Fisher's z. Smallest effect of interest: a pooled
  partial correlation of 0.1 (`sesoi`). Decision: H1 holds if the pooled interval lies above
  zero, the pooled estimate is at least 0.1, and the sign is positive in a majority of
  projects. Reported beside it: the three-control partial, the *strict* forward ratio (only
  callees that resolve: defined in the caller's file or once in the project), the unrestricted
  ratio (guards and library calls counted), the rival predictor (out-degree with the three
  controls), and the pair cap at 10, 20 and 50 changed methods.
- **H2 (threading picks out the co-changing pairs, beyond file locality).** The observed
  heavy-bond mass exceeds what a *within-file* permutation null produces: curveball trades
  confined to one file at a time, so every commit keeps its size per file, every method its
  change frequency, and only *which* methods of a file change together is randomised. A null
  that keeps only the margins is beaten by same-file locality alone, the best-known regularity
  in change histories, and is reported as a secondary. One-sided p from 100 draws per project
  (so the smallest p is about 0.01); combined over projects by Stouffer's method, with the
  share of projects below 0.05 tested against the 5% expected by chance (binomial). Decision:
  H2 holds if the Stouffer p for heavy mass under the within-file null is below 0.05. Note the
  null's property: a file whose methods are always edited wholesale leaves it nothing to
  permute, so a sinew that is simply "the whole file" cannot be found rare here; the
  fixed-marginal null still can.
- **H2b (the counterfactual).** Among pairs where one method calls the other, the share that
  co-change (k ≥ 2) is compared between thread-linked pairs and pairs that pass no parameter
  along, as a risk ratio pooled by random effects on the log scale. This is the comparison the
  thread-link construct must win to add anything to plain call coupling; it is reported, not a
  decision criterion.
- **H3 (sinew strength has predictive value).** Measured at a cut 70% through the mined
  history, a method's sinew strength (sum over its thread-linked co-change pairs of k ×
  thickness) predicts its change frequency in the remaining 30% *beyond past change frequency,
  unweighted past co-change strength, nloc, cyclomatic complexity, out-degree and parameter
  count*. The increment is tested directly: the sinew partial minus the unweighted co-change
  partial given the same other controls, with a paired file-cluster bootstrap interval per
  project, pooled by random effects. Decision: H3 holds if the pooled sinew partial's interval
  lies above zero with an estimate of at least 0.1 *and* the pooled increment's interval lies
  above zero. A positive sinew partial with a null increment means co-change count predicts,
  not thickness.
- **Span verdict validation.** The span ≥ 4 rule is a rule, not a result. It is checked by a
  blind rating of dossiers in which concern clusters (span ≥ 4) are mixed with contract
  clusters (heavy, thread-linked, span ≤ 3) and decoys (co-changing methods that thread
  nothing), each decoy matched to a concern cluster on size and on number of co-change commits.
  The primary contrast is concern against contract: both are heavy and both are threaded, so
  only the span differs. Concern against decoy is secondary and tests whether threading itself
  is visible to a rater. The truth is held in a key file written beside the dossiers.

Everything else the code prints (typology, the heavy-bond quantile rule, the `relate` and
`tangle` views) is descriptive, a sensitivity, or exploratory, and is labelled so. The paper
should also report yield: the number of projects with any heavy bond, with any concern
cluster, and the number of informative commits (commits that change at least one method) in
each window.

### Rating the span verdict with AI raters

`sinew rate` has one or more models read each blind dossier and answer one neutral question:
would you consolidate the data these methods pass around? The prompt never uses the method's
vocabulary, the blind dossiers carry no instrument metrics and no size or commit counts in
their header (they list the members and the ten most recent co-change commits), the decision
is a structured field asked for before the justification, and every rating is logged with the
model, the prompt hash, the request id, the model that actually served it, the raw response
and the justification, so anyone can audit or re-run it.

```
sinew rate --check --save results/rater-check --models claude-opus-5-5   # step 2: verify + save
sinew dossier <base> --repo <checkout> --blind -o rated/<project>        # concern + contract + matched decoy
sinew rate rated/<project>                                               # raters from rater.json
sinew rate rated/<project> --human-template                              # a sheet for human raters
sinew rate rated/<project> --score                                       # unblind: scorecard.json
```

`--check` rates the planted history (`sinew/planted.py`: five chains, two contracts, five
matched decoys) first. The gate is fixed in `rate.GATE`: a rater must say "consolidate" for at
least 80% of the planted chains and at most 20% of the planted decoys, with no errors. The
probability that a coin-flip rater passes that gate is computed exactly (0.035 for five and
five) and printed with the result, so the gate's strength is on record. The raters that pass
are saved to `rater.json` for `sinew study --rate`. Rating and scoring are separate so a dossier
set can be rated before anyone opens the key.

The scorecard gives, per rater, the share of concern, contract and decoy dossiers rated
"consolidate"; the **concern-minus-contract gap is the test of the span rule** and the
concern-minus-decoy gap is the test that threading is visible at all; each model's
self-consistency across runs; and Cohen's kappa between raters. A rating produced by
Anthropic's server-side fallback (a safety decline re-run on the default model) is scored as
its own rater, `requested -> served`, never mixed into the requested model's rates. Two models
from one vendor are not independent raters; their kappa is agreement, not inter-rater
reliability. An AI rater is an instrument, not ground truth: `--human-template` writes
`human-ratings.csv` beside the dossiers for a human subset of 20 to 30, which `--score` scores
as raters named `human:<id>` and includes in the kappa table. `rate` needs the optional pinned
dependency (`pip install 'sinew[rate]'`) and Anthropic credentials in the environment; it
defaults to `claude-opus-5-5` at high effort.

## Replicate the experiment — five steps

```
./step1_setup.sh                 # pinned, vendored dependencies (runs ./setup.sh)
./step2_rater.sh                 # choose + VERIFY the AI raters on planted ground truth -> rater.json
./step3_sample.sh                # draw the sample of Java repositories -> sample/
./step4_run.sh --limit 5 --out results/pilot-yield   # PILOT: the instrument's yield on 5 repositories
./step4_run.sh                   # mine + analyse every usable repository; rate blind dossiers
./step5_analyse.sh results/<date>   # results → the paper's tables
```

Each step ends by printing the **exact command for the next step**, with its parameters
filled in, so a reviewer only has to copy what is printed. The `stepN_*.sh` scripts only
co-ordinate; the work is done by `setup.sh`, `sinew rate --check`,
`open-source-repository-sampler`, `sinew study` and `paper/make_tables.py`, which they call
(and echo) unchanged. Everything they produce is generated by code: data under the git-ignored
`results/` directory, and tables in `paper/tables.md` / `paper/aggregate.tex` (also
git-ignored).

Step 2 runs **first among the judgment calls**: it has the chosen model (default
`claude-opus-5-5`) rate the blind dossiers of a planted history whose answer is known, prints
the scorecard with the gate's chance-pass probability, and saves the raters to `rater.json`
only if each rated at least 80% of the planted chains and at most 20% of the matched decoys
"consolidate". Step 4 reads `rater.json`, so the same raters, runs and effort carry through the
study; `./step2_rater.sh --skip` runs the study without AI rating. Commit `rater.json` with the
sample. `./step2_rater.sh --help`, `./step4_run.sh --help` (`--sample`, `--out`, `--recent`,
`--lib-dir`, `--no-rate`, `--limit`) and `sinew study --help` (`--null`, `--bootstrap`,
`--all-languages`, `--rate`) list the options.

## What is recorded, per method, at every commit

The raw mine (`results/<date>/mines/<owner>__<name>.methods.jsonl`) keeps, for every
implemented method at every mined commit: identity and placement (file, qualified name, line
range); the declared parameters — **names, types and match keys** (`param_names`,
`param_types`, `param_keys`); complexity (`cc`, `cognitive`, `nloc`, `tokens`, `nesting`);
every outgoing call with its argument count, which of the method's own parameters it
**forwards** (`fwd`, as a bare identifier or the value of a named argument) and its kind
(internal / external); the rollups `out_args_*`, `n_calls_*`, and the forward metrics
`fwd_args`, `fwd_params`, `fwd_ratio`, `fwd_names`, `fwd_keys` (over threading calls:
internal callees that are not guards, loggers or formatters), their unrestricted twins `*_any`
and their strict twins `*_strict` (callees that resolve: defined in the caller's file or once
in the project); and whether this commit changed the method and by how many added lines. Changes are
detected ignoring whitespace and blank lines, so a reformat is not a change. Anonymous
functions and Java anonymous-class methods belong to the method that encloses them; overloads
share one identity and take the widest overload's shape; same-name, same-signature functions
of one file (enum-constant bodies) are separate identities (`read#1`, `read#2`). The derived panel
(`sinew analyze <base> -o panel.csv`) adds per-commit deltas.

## The sample

The repositories are drawn by
[open-source-repository-sampler](https://github.com/officefloor/open-source-repository-sampler),
a separate tool, so the population is chosen independently of this experiment. The same release
(`open-source-repository-sampler==0.1.0` from PyPI, pinned in `sinew/deps.py` and vendored and
validated by `setup.sh`) both draws the sample in step 2 and runs the clone loop over it in
step 4. `./step3_sample.sh` runs it with this experiment's criteria, written out in the script:

- Java by default (`./step3_sample.sh --langs java,python,go` draws a multi-language sample;
  the target is per language), across GitHub's popularity tiers (high >5000 stars, mid
  1000–5000, low 200–999), seeded and stratified, grown until there are 30 usable
  repositories per language;
- at least 500 commits, so every repository fills the mining window (`--recent 500`);
- at least 50 methods in at least 15 source files with a method;
- the author's own ecosystem excluded, as a conflict-of-interest guard.

It writes two files into `sample/`: `open-source-repository-sample.csv` (every candidate drawn,
usable or excluded with its reason, each pinned to the commit that met the criteria) and
`open-source-repository-sample.derivation.json` (how the sample was drawn and how to redo it).
They are the experiment's population, and go into the paper's ancillary data unchanged. Commit
them once drawn, so the population is pinned in the repository.

**Skip step 3** to run on an existing sample, such as the paper's or another study's, so both
studies use exactly the same code. Pass it with `./step4_run.sh --sample <csv>`.

A repository is mined on the files of the **language it was sampled as** (a Java project's
JavaScript front end is not mixed into its co-change graph); `--all-languages` lifts that.
Source discovery mirrors the sampler's rules, so the code a repository was judged usable on is
the code that is mined.

## Setup — one script, no manual steps

```
./setup.sh
. .venv/bin/activate
```

`setup.sh` creates a virtualenv, vendors the **exact pinned** dependencies into a known
`./lib` directory (git-ignored), installs them from there, installs this package, and
validates every version. The pins are the single source of truth in `sinew/deps.py` and are
re-validated at every run, so results are reproducible. Point the vendored deps elsewhere with
`LIB_DIR=/path ./setup.sh`, then run with `--lib-dir /path` (default `./lib`, or `$SINEW_LIB`).

The extractor covers **any language lizard parses** through one instrument: lizard's function
list (name, line range, parameter text, complexity) plus a lexical call scan that finds every
call site, counts its arguments and notes which of the method's own parameters it forwards.
Parameter names and types are parsed from lizard's full parameter text per language family
(lizard's bare name list is wrong for Go and TypeScript). The sampler needs an authenticated
`gh` CLI.

## Run

The whole experiment across a sample of repositories is one command (what `step4_run.sh` runs):

```
sinew study --sample sample/open-source-repository-sample.csv
```

The sampler's run loop clones each usable repository **deep**: every commit up to its pinned
commit, and none after, so the mined history ends exactly where the sample says. The study
mines its recent history (every method's shape at every commit), derives the bonds and spans,
runs the permutation null, the controls and the predictive test, and emits an inspection
**dossier** per concern cluster, with member source and commit messages read from the clone.
The clone is then deleted, so disk use stays at one repository. Everything lands under
`results/<date>/` (git-ignored):

- `sinew-study.json` — `parameters` (every threshold, the window, the sample path),
  `environment` (Python, platform, git, lizard, sampler and this code's commit), `projects`
  (one row per usable repository, **failures included** with their error, so attrition is
  visible; each row carries the null comparison, the controls with the H1 interval, the
  sensitivity caps, the commit-window description, the languages at HEAD and the H3
  predictive test), and `pooled` (the random-effects estimates and the H2 tallies);
- `clusters.jsonl` — every heavy-bond cluster of every project, as data;
- `mines/` — the raw mines; `dossiers/` — the concern-cluster dossiers; `run-log.jsonl`;
- `rated/<project>/` — with `--rate`: the blind dossiers, their key, every rating with its
  justification (`ratings.jsonl`) and the project's scorecard; `rated/scorecard.json` pools
  them, and the study file carries the pooled card under `rating`.

The run is resumable: re-running skips repositories the run log records as done, and a partly
mined repository resumes its mine (the rows of an interrupted commit are discarded first).

Per-repository commands, for inspecting one project:

```
sinew mine    <repo> [--langs java] # raw per-method shape at every commit (JSONL)
sinew sinew   <base> [--null N]     # bonds, heavy spans + verdicts, null, controls
sinew predict <base>                # does PAST sinew predict FUTURE change beyond the baselines?
sinew dossier <base> --repo <checkout> [--blind]   # dossiers (+ contracts and decoys, with a key)
sinew analyze <base> -o panel.csv   # the derived per-(commit,method) panel as data
sinew relate  <base>                # EXPLORATORY: complexity ↔ coupling ↔ cohesion
sinew tangle  <base>                # EXPLORATORY: edge-weight ladder + param-growth test
```

(`<base>` is a mine path written by `sinew mine`, i.e. `<base>.methods.jsonl` / `.commits.jsonl`.)

## Known limitations, stated

- **Survivorship.** Bonds are measured among methods alive at the pinned commit; a sinew that
  was refactored away inside the window is invisible. The predictive test reports how many
  methods present at the cut were gone by the end.
- **Co-change granularity differs between projects.** A merge-based history walked
  first-parent yields pull-request-sized units; a squash or rebase history yields small ones.
  Each project's commit-size distribution, calendar span, number of informative commits and
  the share of multi-method commits the cap excludes are recorded, and the main counts are
  re-run at caps of 10, 20 and 50. The sampler's commit criterion counts every ancestor while
  the mine walks first-parent, so a merge-heavy repository may not fill the 500-commit window;
  the window actually mined is recorded per project.
- **Call resolution is lexical.** A callee is matched by name: in the caller's own file first,
  else anywhere in the project. A common name (`process`, `add`) in several classes is
  ambiguous, and a library call to a name the project also defines counts as internal. The
  strict forward ratio and the strict thread links (same-file or unique callee) are the
  sensitivity for this.
- **Types are as lizard reports them.** Java generics are erased (`List<Order>` arrives as
  `List`, a universal type, so such a parameter is matched by name); Go's grouped parameters
  (`a, b int`) leave the first untyped. The share of parameter keys that are types is recorded
  per project.
- **Identity is (file, qualified name), plus an ordinal for same-signature duplicates.** A
  renamed or moved method starts a new thread. Overloads share one identity. Functions of one
  file with the same name *and* signature (every enum-constant body, every nested-class method
  of one name) are told apart by order of appearance (`read#1`, `read#2`); inserting or
  removing one shifts the identities after it in that file.
- **The within-file null cannot see whole-file structure.** A file whose methods always
  change together leaves nothing to permute within it, so H2 is conservative for small files
  edited wholesale; the fixed-marginal null is reported beside it for that case.
- **Name-only call resolution** backs the exploratory `relate` and `tangle` views, not the
  study's claims.

## The pipeline

```
repository git history
      │  sinew mine            → results/<date>/mines/<owner>__<name>.{methods,commits}.jsonl   (raw, append-only)
      ▼
   analysis (sinew/analyze.py)
      │  thread links (type/name keys) · co-change · bonds · heavy spans · permutation null · controls
      ▼
   study (sinew/sinew_study.py)
      │  per project: sensitivity, window, predictive test, dossiers · pooled estimates
      ▼
results/<date>/   (git-ignored data)
      │
      ▼
   paper/   scripts transform results/ → publication tables & figures
```

Nothing is calculated outside this code: the raw mine, every derived statistic and every
table in the paper is produced by a script in this repository, so each step can be
independently inspected and the whole experiment re-run by anyone.

## Tests

```
. .venv/bin/activate && python -m unittest discover -s tests -q
```

The suite includes a ground-truth test: a synthetic history (`sinew/planted.py`) with five
planted four-method chains that each thread two types and co-change eight times, two planted
heavy pairs (contracts), five planted pairs that co-change as often but thread nothing, a
planted pair that threads but never changes, and bystander methods in each chain file so the
within-file null has something to permute. The chains must come out as the five concern
spans and the contracts as the two cohesion spans, with heavy-mass p below 0.1 under both
nulls; none of the decoys may form a bond. Further tests pin the parts the review found
fragile: parameter names that are modifiers in another language (`JsonReader in`), same-name
same-signature bodies kept apart, a shared parameter name that is *not* a link unless the
forward reaches the partner, the within-file curveball's invariants, the cluster bootstrap,
the combined p-values, and the rater gate's chance-pass probability.

## Layout

```
sinew/   the experiment code (package)
  discover.py          source-file discovery, any language (the sampler's exclusion rules)
  extract.py           per-checkout extraction entry point + per-language file counts
  extract_common.py    the per-method record contract, guard callees, the forward rollups
  lizard_extract.py    per-method shape via lizard: parameter names/types/keys, call sites, forwards
  cognitive.py         descriptive cognitive-complexity estimator (no claim depends on it)
  mine.py              the full-history miner (detached-worktree walk, whitespace-insensitive, resumable)
  analyze.py           PARAMS; thread links, bonds, spans, permutation null, statistics; exploratory views
  sinew_study.py       cross-project study: rows, sensitivity, pooling, dossiers (blind option), predictive test
  rate.py              AI rating of blind dossiers: logged decisions + justifications, scorecards, the check
  planted.py           the synthetic history with known ground truth (tests + the rater check)
  deps.py              pinned dependency versions + start-up validation
  cli.py               the `sinew` command
tests/test_sinew.py    the self-tests, including the planted-sinew ground truth
paper/                 SINEW.md (the method) + scripts to transform results/ for publication
step1_setup.sh         step 1: setup         (co-ordinates setup.sh)
step2_rater.sh         step 2: the raters    (co-ordinates `sinew rate --check --save`)
step3_sample.sh        step 3: the sample    (co-ordinates open-source-repository-sampler)
step4_run.sh           step 4: run the study (co-ordinates `sinew study [--rate]`)
step5_analyse.sh       step 5: paper tables  (co-ordinates paper/make_tables.py)
rater.json             the verified raters, once chosen: models, runs, effort, check results
setup.sh               one-step, dependency-pinned, reproducible setup
sample/                the sample, once drawn: open-source-repository-sample.csv + .derivation.json
```

## License

Apache-2.0.
