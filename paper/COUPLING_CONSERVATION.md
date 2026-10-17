# Is coupling conserved? A divergence-theorem account of decomposition

*Research note for experiment 1 (temporal coupling). Companion to [the three-dimensions
model] and the coupling-divergence paper. This records the conservation argument precisely,
says where it is exact and where it only approximates real code, and sets up the measurements
to make over history.*

## 1. The claim, in the author's words

> We start a program as a single function. As it grows we separate it into two functions, and
> in doing so we create coupling between them. The outward parameters of the first and the
> inward parameters of the second negate each other, so the net stays balanced. If we ignore
> the initial call into the system, coupling is conserved: we added a method and the coupling
> divergence went up by the number of methods added. The interesting question is change-on-
> change — whether this conserves, or heads in a direction that indicates a *concentration* of
> coupling (high positive scores: "god coupling") that marks accidental coupling to reduce.

This is correct, and it is a known law once the accounting is made explicit. It is the
**discrete divergence theorem** (equivalently **Kirchhoff's current law**) applied to the
call graph.

## 2. The accounting

Model the program at one checkout as a directed graph `G = (V, E)`: nodes `V` are the own-code
functions/methods, and an edge `u → v` exists when `u` calls `v`. On each edge put a weight =
the amount of *call-argument generality* that flows along it (the quantity the paper's `gain`
already counts). For a node `v`:

- `in(v)`  = total argument flow arriving on edges `· → v` (what `v` receives from callers),
- `out(v)` = total argument flow leaving on edges `v → ·` (what `v` passes to callees).

Define the **signed divergence** at a node — the quantity experiment 1 needs, *without* the
paper's `max(·,0)` clamp:

```
δ(v) = out(v) − in(v)
```

- `δ(v) > 0` — a **source / forwarder / hub**: `v` emits more coupling than it absorbs.
  Argument flow passes *through* `v` on its way elsewhere. This is "god coupling."
- `δ(v) < 0` — a **sink / leaf worker**: `v` absorbs more than it emits. Flow *terminates*
  here: the function does the work locally rather than delegating onward.
- `δ(v) ≈ 0` — a **conduit in balance**: what comes in goes back out.

(The paper's published measure is `max(out−in, 0)`, which keeps only the source side because
for *static* concentration you only care where flow piles up. Experiment 1 needs the sign,
because the whole point is to tell a sink from a hub.)

## 3. Why the net is conserved

Every unit of argument flow that *leaves* one node on an edge *arrives* at exactly one other
node on that same edge. So when we sum over the whole internal graph, each edge is counted once
as an "out" and once as an "in", and they cancel:

```
Σ_{v ∈ V} δ(v)  =  Σ_v out(v) − Σ_v in(v)  =  Σ_{edges} w − Σ_{edges} w  =  0
```

**The total signed divergence over a closed call graph is zero.** Sources are exactly balanced
by sinks. This is the author's "they negate each other."

The caveat the author already named — *"ignore the initial function being called"* — is the
**boundary**. The program is not truly closed: argument flow enters at **entry points** (main,
request handlers, test methods, public API) from outside the system, and leaves at **boundary
calls** (the standard library, the OS, the network). Entry points are pure sources; external
sinks are where flow exits. Writing `∂V` for that boundary, the exact statement is the
divergence theorem:

```
Σ_{v ∈ interior} δ(v)  =  (net flow across the boundary ∂V)
```

If you quotient out the boundary — count only own-code-to-own-code edges, which is exactly what
the tool already does — the interior sum is **0**. Net internal coupling is conserved. The flux
across `∂V` is the **system's external contract**: the real inputs and outputs the program must
have. You cannot refactor that away; you can only move it around inside.

## 4. "Adding a method raises divergence by the number of methods added"

Splitting one function into two inserts a node and an edge. The new callee receives the
arguments it needs (`in`) and — if it is a true leaf — passes little on (`out ≈ 0`), so it
registers as a **sink** (`δ < 0`); the caller now emits those arguments (`out` up), nudging it
toward **source** (`δ > 0`). The two moves are equal and opposite: the pair's contribution to
`Σδ` is still 0. What changed is not the sum but the **spread**: you created one more source
term and one more sink term. Decomposition does not create or destroy net coupling — **it
redistributes a conserved quantity into more, smaller pieces**, exactly as it does with
complexity (the paper's thesis). Coupling is the *surface area* of that redistribution.

So the right invariant is not "coupling is small" but "coupling flux is conserved, and the
question is how it is *distributed*." Which gives the measurement.

## 5. The measurement: concentration of the δ-field over time

Because `Σδ = 0` is fixed (under a fixed external contract), the sum tells you nothing — the
**distribution** is the whole signal. Over a repository's history, at each snapshot compute the
signed `δ(v)` and track the **concentration of the positive side** — the same concentration
machinery the project already has (Theil / Gini / top-share / EVT tail), applied to `{δ(v) > 0}`:

- **Flat, stable δ-field** → coupling is spread across many small conduits. Healthy.
- **Concentrating δ-field** (a rising top-share, a heavier positive tail over commits) →
  argument flow is piling up at a few **god-coupling** nodes. Coupling debt accruing. These
  high-positive-δ nodes are the specific, named targets for reducing **accidental** coupling:
  flow is routed through them that need not be.
- **Direction matters more than level.** A single god-coupling hub may be load-bearing; a hub
  whose δ is *rising commit-on-commit while the external contract is unchanged* is the
  signature of decomposition going wrong — methods added, but the flow re-concentrated instead
  of dispersing. That is the "cosmetic split" of experiment 1, now stated as a conservation
  violation in the distribution rather than the sum.

This reframes experiment 1 cleanly: **good decomposition disperses the conserved δ-flux into
sinks (δ<0 leaves); bad decomposition re-concentrates it into sources (δ>0 hubs).** Signed-δ
concentration, tracked change-on-change, is the diagnostic.

## 6. Where cohesion (co-change) enters — and the open questions

The author's uncertainty is the honest part, so state it as open. Conservation and
concentration tell you *that* flux piles up at a node and *where*; they cannot by themselves
tell you whether that coupling is **necessary** or **accidental**. Co-change — which methods
change together across commits — is the missing discriminator, and it resolves the hub:

- **Necessary coupling.** A high-δ hub whose neighbours genuinely **co-change** with it is
  load-bearing: things that must change together flow through it. Leave it.
- **Accidental coupling.** A high-δ hub whose neighbours **do not** co-change with it is routing
  flow between things that evolve independently. This is the accidental coupling to reduce —
  and it is invisible to structure alone; only the temporal (cohesion) graph exposes it.
- **Cohesion duplication (the dual failure).** Methods that **co-change with no edge between
  them** — they move together because they are *copies*, not because they are connected. This is
  coupling's mirror image: cohesion with zero coupling flux, a missed abstraction. Structure
  cannot see it (no edge); δ is silent (no flow); only co-change reveals it.

So the three regimes the author sketched — methods *splitting*, *joining*, *driving new
coupling*, or *duplicating* — are the four ways the structural δ-field and the temporal
co-change field can agree or disagree:

| | structural edge / δ-flux | no structural edge |
|---|---|---|
| **co-change high** | necessary coupling (keep) | duplication — missed abstraction |
| **co-change low** | accidental coupling — the target to reduce | correctly independent |

This is precisely the experiment-3 bridge, and it should *not* be built until experiments 1
(does signed-δ concentration behave as a conserved, redistributable flux over history?) and 2
(does static complexity concentration identify god units at all?) are understood.

## 7. Where the conservation is exact, and where it leaks

Honesty about the model, because real code is not an ideal incompressible flow:

- **Return values** are a reverse channel. A called function hands a value *back*; the pure
  caller→callee argument accounting ignores it. A fuller model has a second, opposing flow.
- **Fan-out / fan-in.** One value passed to many callees, or many callers into one callee,
  conserves at the node sum but changes what "coupling" means per edge.
- **Shared mutable state / globals / fields** are hidden edges not on the call graph at all —
  real coupling flux through an unmodelled channel. This is itself a source of *accidental*
  coupling and a reason measured δ is a floor.
- **Polymorphism / dispatch.** One static edge may resolve to many dynamic targets; name-based,
  type-blind attribution (the tool's design) smears the flux across candidates. The conserved
  sum survives (flow still goes *somewhere*), but per-node δ is approximate.
- **Accidental vs essential.** Conservation holds for the *essential* flux set by the external
  contract. Redundant code adds flux through unmodelled channels, so measured coupling is
  conserved only *modulo* accidental coupling — and the gap is, again, a signal.

The conclusion is not that the law fails but that **the leaks are the interesting part**: a
conserved, boundary-fixed flux whose interior concentration drifts, or whose measured sum fails
to close, is pointing at exactly the accidental coupling the author wants to find.

## 8. What to measure in experiment 1 (summary)

1. Add **signed δ** = `out − in` at method and class level (keep the clamped `max(·,0)` for the
   paper's static measure).
2. Walk history: per file, the commits that touched it → checkout → extract → per-snapshot
   signed-δ field, with approximate method identity across snapshots.
3. Check **conservation**: does `Σδ` over own-code edges stay ≈ 0, and does `Σcomplexity(class)`
   stay ≈ const across a decomposition event (method count up, peak complexity down)?
4. Track **concentration** of the positive δ-field over commits (Theil / Gini / top-share /
   tail). Falsifiable: *low/negative-δ splits are durable (concentration does not return, low
   future churn); rising-δ splits are cosmetic (a god-coupling hub re-emerges, high future
   churn)* — and signed-δ concentration predicts durability better than the complexity delta
   alone.
5. Leave the cohesion/co-change discriminator (§6) for experiment 3.
