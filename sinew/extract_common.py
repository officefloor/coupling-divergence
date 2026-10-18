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

`fwd` on an edge is the list of the method's OWN parameters passed on at that call: as a bare
identifier, or as the value of a named argument (`f(ctx=ctx)`, `f(ctx: ctx)`), so a
keyword-argument language is measured like a positional one.

The forward METRICS count only the forwards that carry data onward into the project's own
code: edges whose callee is defined in the tree (`kind == internal`) and is not a guard (a
null-check, assertion, logger or formatter -- GUARD_CALLEES). A parameter handed to
`requireNonNull` or `log.debug` is checked or printed, not threaded, and a project that
defines its own `checkNotNull` must not turn every guarded parameter into a forward. The
unrestricted versions (`*_any`) are kept beside them for a sensitivity analysis.
"""
from __future__ import annotations

COMPLEXITY_FIELDS = ("cc", "cognitive", "nloc", "tokens", "params", "nesting")

# Receivers passed as a first formal parameter in some languages; not data the method threads.
RECEIVER_PARAMS = {"self", "cls", "this"}

# Callees that take a parameter to check, log or render it, not to carry it onward.
GUARD_CALLEES = {
    # null / argument / state checks (Java Objects, Guava Preconditions, Spring Assert, ...)
    "requireNonNull", "requireNonNullElse", "requireNonNullElseGet", "checkNotNull",
    "checkArgument", "checkState", "checkNotEmpty", "checkIndex", "checkElementIndex",
    "checkPositionIndex", "isNull", "nonNull", "notNull", "notEmpty", "notBlank", "isTrue",
    "isEmpty", "isBlank", "hasText", "hasLength", "isInstanceOf", "isAssignable", "state",
    "validate", "verify", "ensure", "require", "check", "precondition",
    # test assertions
    "assertNotNull", "assertNull", "assertTrue", "assertFalse", "assertEquals",
    "assertNotEquals", "assertSame", "assertThat", "assertEqual", "assertIs",
    # logging / printing / rendering
    "debug", "info", "warn", "warning", "error", "trace", "fatal", "log", "logger",
    "print", "println", "printf", "printStackTrace", "format", "valueOf", "toString", "str",
    "repr", "equals", "hashCode", "hash", "isinstance", "len", "type",
}


def _fwd_names(calls: list) -> list:
    return sorted({nm for c in calls for nm in c.get("fwd", ())})


def rollups(calls: list, n_params: int) -> dict:
    """Argument-flow and call-count rollups by edge kind, plus the forward metrics:

      fwd_args    argument slots filled with one of the method's own parameters, at a
                  threading call (internal callee, not a guard)
      fwd_params  distinct parameters forwarded at least once (threading calls)
      fwd_ratio   fwd_params / params  (1.0 = every parameter is handed onward; 0 if none)
      fwd_names   the forwarded parameter names (the identifiers this method threads)
      *_any       the same three over EVERY call, guards and library calls included
      *_strict    the same three over threading calls whose callee RESOLVES: a name defined
                  in the caller's own file or exactly once in the project (`strict` on the
                  edge), so `list.add(x)` in a project that defines twenty `add`s is not a
                  forward into the project's own code
    """
    thread = [c for c in calls if c["kind"] == "internal" and c["callee"] not in GUARD_CALLEES]
    strict = [c for c in thread if c.get("strict")]
    fwd_names = _fwd_names(thread)
    fwd_any = _fwd_names(calls)
    fwd_strict = _fwd_names(strict)
    return {
        "out_args_own": sum(c["args"] for c in calls if c["kind"] == "internal"),
        "out_args_ext": sum(c["args"] for c in calls if c["kind"] == "external"),
        "out_args_unresolved": sum(c["args"] for c in calls if c["kind"] == "unresolved"),
        "n_calls_own": sum(1 for c in calls if c["kind"] == "internal"),
        "n_calls_ext": sum(1 for c in calls if c["kind"] == "external"),
        "n_calls_unresolved": sum(1 for c in calls if c["kind"] == "unresolved"),
        "fwd_args": sum(len(c.get("fwd", ())) for c in thread),
        "fwd_params": len(fwd_names),
        "fwd_ratio": round(len(fwd_names) / n_params, 3) if n_params else 0.0,
        "fwd_names": fwd_names,
        "fwd_args_any": sum(len(c.get("fwd", ())) for c in calls),
        "fwd_params_any": len(fwd_any),
        "fwd_ratio_any": round(len(fwd_any) / n_params, 3) if n_params else 0.0,
        "fwd_names_any": fwd_any,
        "fwd_args_strict": sum(len(c.get("fwd", ())) for c in strict),
        "fwd_params_strict": len(fwd_strict),
        "fwd_ratio_strict": round(len(fwd_strict) / n_params, 3) if n_params else 0.0,
        "fwd_names_strict": fwd_strict,
    }
