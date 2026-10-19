# Sinew — data coupled to the control flow

> **Status (19 October 2026).** This document states the construct as it now stands. It
> replaces the earlier "heavy latent change-coupling" framing (co-change × thickness, span ≥ 4
> verdict, AI-rated dossiers), which is what the harness in this repository still implements.
> The study is on hold until the companion
> [complexity-concentration study](https://github.com/officefloor/complexity-concentration-study)
> finishes, because that study supplies this one's outcome; the harness will be reworked to the
> design below if its results warrant it. Everything under *Exploratory
> observations* was seen before any hypothesis here was written down; nothing in this document
> is registered yet.

## The claim in one paragraph

A **sinew** is data coupled to the control flow: data that reaches the code that needs it only
by being passed down the call chain, method to method. The more data rides on the control flow,
the stronger the sinew. Strong sinews make code hard to refactor: every method on the path
carries the data in its signature, so changing the data, inserting a step or reordering steps
means editing the whole path. Dependency injection weakens sinews by taking objects off the
control flow and making them available by declaration. OfficeFloor's function injection removes
them from the flow altogether: procedures receive their data as injected values (`@Val`,
`Out<>`) rather than as arguments threaded through callers. The study asks whether sinews can be
seen in code, whether they do harm, and whether they are where complexity concentrates into god
classes and methods.

## Why sinews are bad: four harms

1. **Ripple.** Changing the data on a path (adding a field, a parameter, a new piece of
   context) means editing every method that carries it, including the ones that only pass it on.
2. **Rigidity.** A step cannot be inserted, removed or reordered without re-plumbing the
   signatures on either side of it.
3. **Comprehension.** To know where a value came from or where it goes, a reader follows the
   whole path.
4. **Attraction.** Data in scope invites logic. A method that already holds much of the data a
   change needs is where the next change is easiest to put, so complexity collects where the
   data is.

Attraction is the harm that links sinews to god classes and methods, and it is the one this
study tests first.

## Measuring a sinew: coupling divergence

The construct began as **coupling divergence**: per method, the data going in against the data
going out. Used to find outliers among high change-coupling pairs, it was good at finding god
methods. Per method, at a given commit:

```
in   = parameters the method declares
out  = Σ over its calls into the project's own code of the arguments passed
d    = out − in
```

Only parameters are counted. Return values and exceptions are part of the interface and add to
the sinew, but they need the whole call graph to follow; parameters are what visibly travels.

**Roles, by analogy with a pipe system:**

| d | role | reading |
|---|---|---|
| > 0 | **junction** | creates and directs flow: hands on more data than it receives |
| = 0 | **pipe** | passes data through, transforming it |
| < 0 | **catchment** | a leaf: receives data and processes it |

**The Kay rule.** Following Alan Kay's view of objects reacting to a message, a well-built
method receives one message. The first parameter of a method and the first argument of each
call are the natural control flow, not coupling, so a sinew starts at the second:

```
in   = max(parameters − 1, 0)
out  = Σ over calls of max(arguments − 1, 0)
```

**Complexity as area.** The companion petclinic degradation study found that complexity is
conserved: changes move it rather than create or destroy it. The system's complexity is then the
sum over its methods, and each method takes up area in proportion to its complexity. The
hypothesis is that in a healthy system most of the area sits in catchments, with leaf processing
doing the work, while junctions stay thin. A system out of balance puts its area in junctions:
methods that hand on much more data than they receive and also hold the logic.

## The link to complexity concentration

The [complexity-concentration study](https://github.com/officefloor/complexity-concentration-study)
identifies god classes and methods at a single checkout as **scale-free complexity outliers**:
units whose complexity is an outlier for their own codebase (modified z ≥ 3.5, Iglewicz and
Hoaglin), with no raw-scale constant. If complexity is conserved, its concentration is where the
god units are.

That gives this study its outcome without a rater. Coupling divergence becomes an **early marker**
of concentration: when a unit starts to concentrate complexity, its coupling should be visible
first and should grow quickly. The question is prediction, not identification:

> Does a unit's coupling now predict that it becomes a complexity outlier later, *beyond the
> complexity it already has*?

"Beyond" is the point: complex units getting more complex is not a finding.

**Where that study stands.** The complexity-concentration study is still underway. Its results
so far are positive: relative complexity outliers do recover god classes and methods. But they
flag many false positives, units that are complex for their codebase without being god units.
Sinews may be the means to remove them: an outlier that is also accumulating data on the control
flow is a god unit growing, while one that is merely complex leaf processing, a catchment, is
not. This study is therefore on hold, and will be taken up again if the concentration study's
results warrant it.

### The signature of a growing god unit

When a unit starts sending more data out, two different things can be happening:

| change in one step | reading |
|---|---|
| outgoing data up, complexity **down** | **delegation**: complexity pushed out to the leaf nodes it now calls. Healthy. |
| outgoing data up, complexity unchanged | new wiring, no new logic |
| outgoing data up, complexity **up or holding** | **accumulation**: the unit is taking on both data and logic. The marker of a god unit growing. |

The comparison that tests the marker is accumulation against complexity rising *without* more
outgoing data. Comparing against untouched code only shows that edited code is where outliers
appear.

## Dependency injection and OfficeFloor

Code built with OfficeFloor has two layers, and the claim differs between them:

- **The flow layer.** The configured flow between procedures. Data is injected, so no data rides
  on the control flow between procedures: sinews cannot form here, by construction.
- **The Java layer.** Ordinary Java inside a procedure: helper methods, plain calls. Sinews can
  form here exactly as in any Java code, but they are bounded by the procedure.

So the claim is not that OfficeFloor code cannot become complex, or cannot grow god units. It
is that complexity cannot concentrate *by riding the flow*, and that a sinew's reach is bounded
by the procedure. OfficeFloor code should therefore behave differently under these measures, and
the difference has to be **predicted before it is observed** (below). Explaining any OfficeFloor
result afterwards as "OfficeFloor works differently" would make the theory unfalsifiable.

Dependency injection sits in between: injected collaborators are off the control flow, but the
request's data is still passed method to method.

**Two measurement problems** follow from this and are open:

- Injected parameters of an OfficeFloor procedure (`@Val`, `Out<>`, injected services,
  `@RequestBody`) look like parameters to a parameter count, while Spring's field injection is
  invisible to it. The arms are only comparable once the count knows which parameters are
  injected and which are threaded.
- Data bundled into one object (a DTO, a request context) is stamp coupling: one parameter that
  carries many items. A parameter count misses it. In the petclinic run, Spring's largest god
  method, `OwnerRestControllerV1.addOwner(OwnerDto)`, has one parameter. Counting the data items
  read from a received object is the likely fix.

## Where this sits in the literature

- **Tramp data and stamp coupling** (Yourdon and Constantine, *Structured Design*, 1979): data
  passed through modules that do not use it, and records passed where one field is needed. A
  sinew is tramp data seen as a path. Their transaction analysis and "mosque" shape (high fan-in
  at the bottom, thin top) is the healthy-system picture above.
- **Information flow** (Henry and Kafura 1981): complexity × (fan-in × fan-out)². The
  replication by Kitchenham et al. (1990) found size predicted as well; any result here has to
  beat size and complexity, not just correlate.
- **System complexity** (Card and Glass 1990): structural (fan-out²) plus data complexity; a
  replication found fan-out² significant.
- **Data clumps** (Fowler, *Refactoring*): the same group of parameters travelling together.
- **Logical / evolutionary coupling** (Gall et al. 1998; Zimmermann et al. 2004): co-change,
  the basis of the ripple test.
- **Functional core, imperative shell**: logic in the leaves, wiring at the top; the same
  balance as catchments holding the area.
- **Dependency injection** has little empirical evidence about coupling; there are small
  studies and catalogues of DI anti-patterns, but no history-based test of whether DI reduces
  data threaded down call chains.

Lehman's laws state that complexity grows unless work is done to reduce it; they do not state
that it is conserved. Conservation is the companion study's finding, and this study depends on it.

## Exploratory observations (petclinic, not evidence)

Source: the petclinic degradation study's control run (`blind-202608100006`, strategy
`just-solve`): 10 chains per arm, 61 checkpoints each, the same feature requests applied by AI
agents to a Spring and an OfficeFloor implementation. Outgoing arguments exclude calls to the
method's own class unless stated. Petclinic code is small and was evolved by agents, so none of
this generalises; it was also the sixth way of measuring the same run, so it shapes hypotheses
and tests none.

**Role shares.** OfficeFloor started more catchment-heavy than Spring (64% of complexity vs
42%) with fewer junctions. Over the chains Spring's catchment share rose (49% → 58% with
same-class calls excluded), while OfficeFloor's junction share rose (9% → 13%) in all 10 chains.
Several of OfficeFloor's top junctions were artefacts: `throw new …Exception(x)` and setters on
injected entities counted as outgoing data. Under the Kay rule most complexity sits in methods
that neither receive nor send more than one item (about 80% in Spring, 63–66% in OfficeFloor).

**Attraction, by parameters.** Parameters beyond the first did not predict later complexity gain
in either arm (partial Spearman ≈ 0, controlling complexity, size and earlier changes).
Complexity concentrated about equally in both arms (Gini of method complexity 0.23 → 0.34 in
Spring, 0.22 → 0.35 in OfficeFloor).

**Early marker.** Among methods that were not outliers, outgoing arguments at a checkpoint
predicted becoming an outlier within 10 checkpoints, comparing only methods of equal complexity
(AUC 0.67 Spring, 0.87 OfficeFloor, pooled). Parameters in added little (0.56 both). Per chain,
ten checkpoints before each method's first crossing: OfficeFloor 0.76–0.97 in 8 of 9 chains;
Spring split, three chains at 0.95–0.98 and four below 0.5. The outgoing side carries the
signal, not the balance.

**The signature.** Units that were not outliers, by what happened in one step, and the share that
were outliers 10 checkpoints later:

| change in the step | Spring methods | Spring classes | OfficeFloor methods | OfficeFloor classes |
|---|---|---|---|---|
| outgoing up, complexity down | 0 of 3 | none | 1 of 3 | 0 of 4 |
| outgoing up, complexity same | 3% | 0 of 10 | 3% | 5% |
| outgoing up, complexity up | 64% (22) | 13 of 13 | 31% (35) | 20% (64) |
| complexity up, outgoing not up | 42% (50) | 34% (153) | 40% (25) | 46% (91) |

- Delegation almost never happened: the agents added calls but kept the complexity where it was.
- Adding outgoing data without complexity was the safest edited group.
- In Spring classes accumulation was followed by outlier status every time, and was worse than
  complexity rising alone in all 10 chains (1.4 to 7 times). The 13 steps are probably largely
  the same controller class in each chain.
- In OfficeFloor the direction reversed: accumulation was less risky than complexity rising
  alone, in 8 of 10 chains.

**OfficeFloor's outgoing data.** OfficeFloor's outgoing arguments roughly doubled over the chains
(103 → 198–222) while Spring's rose 20–50% (134 → 164–201). Both arms ended at similar total
complexity. Of the complexity gained by classes that became outliers, about 18% came with
more outgoing data in OfficeFloor against about 33% in Spring. The doubling is expected to be the
Java layer inside procedures; the layers have not yet been separated.

## Draft hypotheses (not registered)

To be fixed, and registered with a timestamp, before the confirmatory sample is opened. Unit: the
class; outlier rule: the concentration study's, unchanged.

- **H1 (accumulation marks god growth).** Steps in which a class's outgoing arguments and its
  complexity both rise are followed by the class becoming a complexity outlier more often than
  steps in which its complexity rises without its outgoing arguments rising.
- **H2 (delegation is protective).** Steps in which outgoing arguments rise and complexity falls
  are followed by outlier status less often than either.
- **H3 (early marker).** Among non-outliers, outgoing arguments predict becoming an outlier
  within a fixed horizon, compared only among units of equal complexity.
- **H4 (DI weakens sinews).** Within a project, injected collaborators reduce the data threaded
  down call chains: compared before and after a project adopts DI, or between DI and non-DI
  code in the same project.
- **H5 (OfficeFloor removes them from the flow).** In the petclinic arms, data passed between
  procedures through the flow stays at zero while data passed between Spring components grows;
  OfficeFloor's outgoing data grows only inside procedures; and OfficeFloor outlier classes gain
  a smaller share of their complexity together with outgoing data than Spring's do.

Ripple (signature changes co-occurring along forwarding paths) is the most direct harm and can be
measured from the existing miner. It is a secondary test here, because attraction is the harm
that connects to god units.

## How it will be tested

1. **Explore on the pilot repositories** (`pilot-repositories.txt`, already excluded from every
   confirmatory estimate) and on the petclinic run: fix the outgoing measure (own-code arguments,
   same-class calls in or out, Kay rule), the horizon and the class aggregation.
2. **Extend the extraction**: data items read from received objects (bundled data); which
   parameters are injected; for OfficeFloor, which calls cross a procedure boundary.
3. **Register** the hypotheses above, with one primary estimand.
4. **Confirm** on a fresh, unopened sample drawn by
   [open-source-repository-sampler](https://github.com/officefloor/open-source-repository-sampler),
   shared with the concentration study's Java population, and on the petclinic runs not yet used
   (`impact_gated`, `cohesion-prompt`, `reviewed`).

Rigidity cannot be read from history (history shows the edits, not how hard they were); it
needs a controlled experiment: the same refactoring tasks given to agents on plain, Spring DI and
OfficeFloor versions of one application, measuring edits, failures and time.
