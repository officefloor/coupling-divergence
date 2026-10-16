Coupling Divergence: god-class/method detection study (ancillary data)
================================================================================

WHAT THIS IS
  results.csv holds one row per open-source repository, measuring how well the Coupling
  Divergence placement outlier detects textbook god classes and god methods at the
  repository's latest default-branch commit. This package is self-contained: it needs
  neither the paper nor any external repository to be understood. Each row records the
  exact commit measured, so any result can be reproduced by checking that commit out.

THE MEASURE (per function v, over calls to code defined in the same repository)
  out(v) = arguments the function passes over those calls (information it sends on)
  in(v)  = arguments passed into it by its callers
  divergence delta(v) = max(out(v) - in(v), 0)   -- net coupling flow the function creates
  A god method is a modified-z outlier (Iglewicz-Hoaglin, cutoff 3.5) in delta across the
  functions that create (delta>0). A god class is the outlier of the per-class sum of delta.
  Calls leaving into libraries do not count, so converters and mappers score near zero.

REFERENCES (the textbook "ground truth" each outlier is scored against)
  god class  = file WMC >= 47          (Lanza-Marinescu size threshold; WMC = sum of method
                                         cyclomatic complexity)
  god method = function CC >= 14       (McCabe high-risk)
  Java only  = PMD's GodClass rule     (the full Lanza-Marinescu strategy: WMC and ATFD and
                                         TCC together)

COLUMNS
  name, repo, lang, commit, date   repository identity and the exact revision measured
  kind                             application or library, a post-hoc heuristic (container or
                                   deploy descriptor, a binary-crate / __main__ / main entry);
                                   reported so the sample's type mix is visible, not controlled
  stratum_lang, tier, stars, rank  selection metadata from the sampling manifest: the
                                   language and popularity tier the repo was drawn from, its
                                   star count and rank at sampling time. Group results by
                                   these (tier, language, kind) to check the effect is stable
                                   across strata. `repo` is the full clone URL and `commit`
                                   the exact revision, so any row checks back against the code.
  n_files, n_funcs                 source files and functions parsed (tests excluded)
  wmc_gods                         files with WMC >= 47
  gen_class_outliers               files whose cumulated divergence is a modified-z outlier
  class_lift                       enrichment: P(WMC-god | divergence outlier) / base rate.
                                   1.0 = no better than chance; >1 = divergence concentrates
                                   on the god classes
  class_recall                     share of WMC-gods that contain a divergence outlier
  class_fisher_p                   one-sided Fisher exact p for that enrichment in this repo
  cc_gods, gen_method_outliers, method_lift, method_recall, method_fisher_p
                                   the same, at method level vs CC >= 14
  pmd_gods, class_lift_vs_pmd      Java only: enrichment against the full PMD GodClass
  wmc_only_leaves                  files WMC>=47 that PMD does NOT call a god (self-contained
                                   complex algorithms; Java only)
  wmc_only_leaves_flagged_by_gen   of those, how many the divergence signal flags (should be
                                   low: divergence excludes essential algorithmic complexity)

HEADLINE (this run)
  98 repositories, 11 languages (c, c#, c++, go, java, js, php, python, ruby, rust, ts).
  Class divergence outlier vs WMC>=47: mean lift 2.81, 95% CI [2.54, 3.1] across repos;
  individually significant (Fisher p<0.05) in 57/79 repositories.
  Interpretation: a divergence outlier is several times more likely than chance to be a
  textbook god, consistently across languages. The measure detects gods; it is not a
  before-the-fact predictor of a forming god (a separate, unproven claim).

REPRODUCE
  pip install coupling-divergence   (or: pip install -e . from the Coupling Divergence source)
  coupling-divergence study --repos <the repo URLs in this table> --anc <out dir>
  Each clone is shallow, measured, and deleted; only the recorded commit is kept. Re-running
  is resumable. For the Java PMD reference, pass --pmd <pmd binary>.
