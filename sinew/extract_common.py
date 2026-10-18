"""The per-method record contract shared by every extractor, and the rollups derived from a
method's call edges.

The extractor (lizard_extract, one instrument for every language) emits one record per
implemented method with:

  file, package, type, qualname, name, start, end      identity and placement
  cc, cognitive, nloc, tokens, params, nesting          shape (COMPLEXITY_FIELDS)
  param_names                                           the formal parameter names
  calls                                                 raw outgoing edges, each
                                                          {callee, args, fwd, kind, via, target}
  + the rollups below

`fwd` on an edge is the list of the method's OWN parameters passed on unchanged as bare
identifiers at that call. Summed over the method they give the forward metrics -- the measure
of a conduit: a method that accepts data mainly to hand it onward. These need no type
resolution, only parameter names and argument identifiers, so every language reports them the
same way. `kind` (internal / external) is descriptive metadata about where the data goes; the
sinew analysis does not depend on it.
"""
from __future__ import annotations

COMPLEXITY_FIELDS = ("cc", "cognitive", "nloc", "tokens", "params", "nesting")

# Receivers passed as a first formal parameter in some languages; not data the method threads.
RECEIVER_PARAMS = {"self", "cls", "this"}


def rollups(calls: list, n_params: int) -> dict:
    """Argument-flow and call-count rollups by edge kind, plus the forward metrics:

      fwd_args    argument slots filled with one of the method's own parameters, unchanged
      fwd_params  distinct parameters forwarded at least once
      fwd_ratio   fwd_params / params  (1.0 = every parameter is handed onward; 0 if none)
      fwd_names   the forwarded parameter names (for finding the identifier a cluster threads)
    """
    fwd_names = sorted({nm for c in calls for nm in c.get("fwd", ())})
    return {
        "out_args_own": sum(c["args"] for c in calls if c["kind"] == "internal"),
        "out_args_ext": sum(c["args"] for c in calls if c["kind"] == "external"),
        "out_args_unresolved": sum(c["args"] for c in calls if c["kind"] == "unresolved"),
        "n_calls_own": sum(1 for c in calls if c["kind"] == "internal"),
        "n_calls_ext": sum(1 for c in calls if c["kind"] == "external"),
        "n_calls_unresolved": sum(1 for c in calls if c["kind"] == "unresolved"),
        "fwd_args": sum(len(c.get("fwd", ())) for c in calls),
        "fwd_params": len(fwd_names),
        "fwd_ratio": round(len(fwd_names) / n_params, 3) if n_params else 0.0,
        "fwd_names": fwd_names,
    }
