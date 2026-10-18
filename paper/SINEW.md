# Sinew — heavy latent change-coupling

## The claim in one paragraph

Coupling is not conserved. In a body the circulatory system is a closed loop — what the
arteries push out, the veins bring back — so flow is conserved and you can reason about it
by conservation. Software has no such closure: *any* method can be a heart, manufacturing
flow out of fields, static state or freshly constructed objects. So looking for a conserved
quantity is the wrong instrument. What matters instead is **refactor cost**: how hard a
given coupling is to cut. Most couplings are light capillaries — thin, rarely disturbed,
cheap to re-route. A few are **sinews**: thick bundles of data threaded between methods that
are repeatedly edited together and have no clean call boundary to cut along. A sinew is
connective tissue — it bears load under tension and is expensive to sever. The diagnostic is
not a system-wide average; it is the **heavy tail**. Find the sinews, decide which are
accidental, and reconsider them before they strengthen into something no one can afford to
refactor.

## Where this sits in the literature

Co-change — methods that change together across history — is **logical coupling** (Gall et
al. 1998) or **evolutionary coupling** (Zimmermann et al. 2004). That body of work treats
co-change as a flat, binary signal: pair A,B changed together *k* times. Sinew keeps that
spine and adds three things the classic measure lacks:

1. **Weight.** A co-change is not a unit. It carries the *data-flow thickness* between the
   two methods — how many parameters they thread. A fat bond is harder to cut than a thin
   one, so co-change is weighted by parameter surface, not counted.
2. **Direction.** A method is not a point. It has an in/out profile (below). Direction tells
   you which end is upstream and lets you tell a *conduit* from a *leaf*.
3. **A discriminator.** Logical coupling cannot separate the `equals/hashCode/toString`
   contract (which *should* co-change) from accidental sprawl (which should not). Sinew does,
   by the **span** of the cluster.

Everything is computed from the raw history mine (`sinew.mine`), which records
every method's shape at every commit, so no live call-graph analyser is needed and the same
mine can be re-analysed many ways.

## Vocabulary

Per method, at a given commit:

```
IN      = declared parameter count                    # data it accepts
OUT     = Σ arguments it forwards to the methods it calls   # data it pushes onward
surface = IN + OUT                                    # total data plumbing (artery + vein)
net     = OUT − IN                                    # orientation: + source, − sink
```

Direction classes (thresholds = the project's own upper tercile on IN and on OUT):

- **source** — high OUT, low IN. Upstream; manufactures/forwards flow.
- **sink** — high IN, low OUT. Downstream; consumes flow.
- **joint** — high on *both*. A conduit that receives a lot and forwards a lot — the
  load-bearing articulation point of a data pathway.
- **minor** — low on both. Ignored; not part of any sinew.

A **latent sinew** is a pair of methods that (a) co-change at least *k* times in history and
(b) have **no direct call edge** between them. The "no call edge" is the point: an explicit
call is honest, visible coupling you can see and cut along. A latent bond is data threaded
between two methods *through intermediaries* — implicit, and therefore the coupling a
call-graph view misses entirely.

- **thickness** = `min(surface_a, surface_b)` — a sinew is only as hard to cut as its
  thinner (breakable) end, so the thinner end sets thickness.
- **heaviness** = `co-change count × thickness` — thick *and* repeatedly moved together.

A **sinew cluster** is a connected component of the heavy-sinew graph (heavy latent bonds as
edges). Its shape is summarised by:

- **span** — number of methods it ropes together.
- **density** — links / possible links; 1.0 means every member co-changes with every other
  (a clique).
- **symmetry** — mean `min(surface)/max(surface)` over its bonds; 1.0 means both ends are
  equally thick.

## The verdict rule

Span is the discriminator (validated below): a small tight cluster is intrinsic cohesion, a
large one is accidental sprawl. Symmetry sub-classifies *which kind* of sprawl.

| cluster shape | reading | action |
|---|---|---|
| **span ≤ 3** | intrinsic contract / dual (e.g. `equals·hashCode·toString`) | leave it — this is good cohesion |
| **span ≥ 4, low symmetry** | one fat method roping thin satellites | **God-method hub** — extract the abstraction |
| **span ≥ 4, high symmetry, high density** | many equally-heavy methods edited in lockstep | **parallel-maintenance clique** — unify the duplication |

The `≤ 3` line is a heuristic; the **span number** is the real output and a reviewer sets the
line for their codebase.

## What the experiment measures

The experiment produces, per project, from the raw history mine alone:

- **The edge-weight relationship** — co-change frequency binned by a bond's parameter weight,
  to test whether heavier bonds co-change more (the "tangle" report).
- **The directional, complexity-controlled signal** — the partial rank correlation of a
  method's outward data-flow with its co-change entanglement, holding cyclomatic complexity
  constant, so the result cannot be a restatement of "complex methods change a lot."
- **The concentration of sinew mass** — how heavy-tailed the latent bonds are (what share of
  total mass sits in the top decile), the mathematical test of "not conserved."
- **The span verdict** — each heavy-sinew cluster's span, density and symmetry, and the
  contract / God-hub / duplication-clique classification that follows from them.

No measured values appear in this document or in the experiment code: they are *results*,
produced by running the experiment and reported in the paper from the data in `results/`.

## Qualification — is a flagged sinew really a problem?

The span rule produces a high-**recall** candidate list and a plausible verdict. Whether a
flagged sinew is a *real* design problem is the qualification question, in two parts, both
implemented by the study:

1. **Is it an anomaly?** A sinew is a *signal* only when it is an **outlier** against its own
   system's change distribution — anomalously heavy / high-span relative to that project's
   baseline, not merely large in absolute terms. This is a per-project modified-z-score
   (Iglewicz–Hoaglin) test.

2. **Does the anomaly have value?** Two complementary tests:

   - **Predictive value (empirical, no labels).** Measure sinews as of an early cut of
     history, then test — on the *future* commits — whether sinew members accrue more change
     than complexity-matched non-sinew methods. If sinew membership predicts future
     volatility beyond what complexity already predicts, the signal has value independent of
     anyone's opinion.
   - **Interpretive value (why does the sinew exist?).** Capture each flagged cluster's
     member methods — current source, the parameters they thread, their co-change commits
     (sha, date, message), and whether an indirect call path exists — into a per-sinew
     **dossier**, and inspect *why* the bond exists: intrinsic contract, missed abstraction
     (a data clump that should be an object), copy-paste duplication, or a cross-cutting
     concern. The span/symmetry verdict is a proxy for what inspection confirms; the dossier
     is how the proxy is checked, by a human or by an LLM pass.

The study runs the detector across a sample of open-source Java projects, applies the outlier
test per project, runs the predictive test, and emits the dossiers — so a sinew's value is
qualified rather than asserted.
