"""Language-independent extraction of the coupling-divergence ("god math") from a tree.

Built on lizard (multi-language function list, cyclomatic complexity and line ranges),
the same substrate impact-gate uses, so it works on any language lizard parses without a
per-language call-graph analyser. Everything above lizard is lexical and language-neutral.

THE MATH. Treat the arguments passed at a call as the flow over a control edge. For each
function v, over its calls to OUR OWN code (a callee whose name is a function defined
somewhere in the tree), count the arguments it pushes:

    out_gen(v) = sum over internal call sites of (argument count)
    in_gen(v)  = sum of arguments pushed INTO v by its callers (attributed by name)
    gain(v)    = max(out_gen(v) - in_gen(v), 0)          # net flow MANUFACTURED here

gain is the "generation" of a pipe joint that creates flow instead of splitting it. A god
is the dominant, persistent outlier in gain (modified z-score, threshold-free), the node
that manufactures more coupling than it receives. Calls that leave the tree (library/SQL)
are not counted, which is what excludes converters and mappers without any type analysis.

WMC per FILE (sum of function cyclomatic complexity) is the textbook size reference
(Lanza-Marinescu god-class threshold is WMC >= 47); a file is the language-neutral unit.
"""
from __future__ import annotations

import os
import re

import lizard

from . import cognitive as _cognitive

# Per-function complexity definitions captured alongside the flow signal, so the comparison
# matrix can spike each one (all language-neutral: five straight from lizard, cognitive from
# the vendored Campbell estimator). Class-level values are the per-file sums of these.
COMPLEXITY_FIELDS = ("cc", "cognitive", "nloc", "tokens", "params", "nesting")

# Extensions lizard can parse that we scan. A superset; lizard skips what it cannot handle.
SOURCE_EXTS = {
    ".java", ".kt", ".scala", ".groovy",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".m", ".mm",
    ".cs", ".js", ".jsx", ".ts", ".tsx", ".py", ".go", ".rb", ".php",
    ".swift", ".rs", ".lua",
}
# Tokens that take a parenthesis but are not calls, across common languages.
NOT_CALL = {
    "if", "while", "for", "switch", "catch", "return", "synchronized", "with",
    "elif", "foreach", "lock", "using", "when", "match", "do", "else", "case",
    "and", "or", "not", "in", "is", "assert", "throw", "throws", "yield", "await",
    "func", "def", "function", "sub", "super", "this", "self",
}
# Lightweight comment/string strip so commas inside literals are not counted as arguments.
_STRIP = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`'
    r'|//[^\n]*|#[^\n]*|/\*.*?\*/', re.S)
_TEST_DIR = re.compile(r"(?:^|/)(?:test|tests|testing|spec|__tests__)(?:/|$)")
# Directories that are not the project's own code: dependencies, build output, and
# vendored or generated trees (where a single tree-sitter parser.c can be ~100 MB).
_SKIP_DIRS = {".git", "node_modules", "build", "target", "dist", ".venv", "venv",
              "vendor", "vendored", "third_party", "thirdparty", "third-party",
              "generated", "externals", "external", "deps", ".gradle", "out"}
# A source file larger than this is a generated/minified/vendored blob, not a unit anyone
# maintains by hand (real source is rarely over a few hundred KB). Skipping it keeps the
# measure meaningful AND keeps the lexical scan from stalling on a 100 MB generated file.
MAX_FILE_BYTES = 1_000_000


def source_files(root: str, include_tests: bool = False) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in SOURCE_EXTS:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            if not include_tests and _TEST_DIR.search("/" + rel.replace(os.sep, "/")):
                continue
            try:
                if os.path.getsize(os.path.join(dirpath, fn)) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(rel)
    return out


def _call_sites(body: str):
    """Yield (callee name, argument count) for every call expression in `body`."""
    i, n = 0, len(body)
    while i < n:
        if body[i] == "(":
            j = i - 1
            while j >= 0 and body[j].isspace():
                j -= 1
            if j >= 0 and (body[j].isalnum() or body[j] in "_$"):
                k = j
                while k >= 0 and (body[k].isalnum() or body[k] in "_$"):
                    k -= 1
                word = body[k + 1:j + 1]
                depth, p, args, content = 0, i, 0, False
                while p < n:
                    c = body[p]
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    elif c == "," and depth == 1:
                        args += 1
                    elif depth == 1 and not c.isspace():
                        content = True
                    p += 1
                yield word, (args + 1 if content else 0)
                i = p + 1
                continue
        i += 1


def _short(name: str) -> str:
    return name.split("::")[-1].split(".")[-1]


def analyze_tree(root: str, include_tests: bool = False) -> dict:
    """Per-function records plus per-file WMC for one checkout.

    Returns {"funcs": {func_id: {...}}, "wmc": {file: total_cc}}, where func_id is
    (file, short_name) and each record has out_gen, in_gen, gain, file, name, and the
    complexity definitions in COMPLEXITY_FIELDS (cc, cognitive, nloc, tokens, params,
    nesting) so the matrix can spike each one."""
    lines_cache: dict = {}
    raw: list = []                      # (file, short, start, end, metrics_dict)
    for rel in source_files(root, include_tests):
        path = os.path.join(root, rel)
        try:
            analysis = lizard.analyze_file(path)
        except Exception:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                src_lines = fh.read().splitlines()
        except OSError:
            continue
        lines_cache[rel] = src_lines
        ext = os.path.splitext(rel)[1].lstrip(".")
        for fn in analysis.function_list:
            s, e = int(fn.start_line), int(fn.end_line)
            body_raw = "\n".join(src_lines[max(s - 1, 0):e])
            md = {"cc": int(fn.cyclomatic_complexity),
                  "nloc": int(getattr(fn, "nloc", 0) or 0),
                  "tokens": int(getattr(fn, "token_count", 0) or 0),
                  "params": int(getattr(fn, "parameter_count", 0) or 0),
                  "nesting": int(getattr(fn, "max_nesting_depth", 0) or 0),
                  "cognitive": _cognitive.cognitive_complexity(body_raw, ext)}
            raw.append((rel, _short(fn.name), s, e, md))
    if not raw:
        return {"funcs": {}, "wmc": {}}

    names = {r[1] for r in raw}
    out_gen: dict = {}
    in_by_name: dict = {}
    agg: dict = {}                      # fid -> complexity metrics, merged by max over overloads
    for rel, short, start, end, md in raw:
        fid = (rel, short)
        cur = agg.get(fid)
        if cur is None:
            agg[fid] = dict(md)
        else:
            for k, v in md.items():
                if v > cur.get(k, 0):
                    cur[k] = v
        body = "\n".join(lines_cache.get(rel, [])[max(start - 1, 0):end])
        body = _STRIP.sub(lambda m: " " * len(m.group(0)), body)
        og = 0
        for callee, nargs in _call_sites(body):
            if callee in NOT_CALL or nargs == 0 or callee not in names:
                continue
            og += nargs
            in_by_name[callee] = in_by_name.get(callee, 0) + nargs
        out_gen[fid] = out_gen.get(fid, 0) + og

    wmc: dict = {}                      # per-file WMC (sum of method cyclomatic complexity)
    for rel, short, start, end, md in raw:
        wmc[rel] = wmc.get(rel, 0) + md["cc"]

    funcs: dict = {}
    for fid in set(out_gen) | set(agg):
        og = out_gen.get(fid, 0)
        ig = in_by_name.get(fid[1], 0)
        md = agg.get(fid, {})
        funcs[fid] = {"file": fid[0], "name": fid[1],
                      "out_gen": og, "in_gen": ig, "gain": max(og - ig, 0),
                      **{k: md.get(k, 0) for k in COMPLEXITY_FIELDS}}
    return {"funcs": funcs, "wmc": wmc}


def modified_z(values: list[float]):
    """Iglewicz-Hoaglin modified z-score function over the positive `values`."""
    xs = sorted(v for v in values if v > 0)
    n = len(xs)
    if n == 0:
        return lambda x: 0.0
    med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    dev = sorted(abs(x - med) for x in xs)
    mad = dev[n // 2] if n % 2 else (dev[n // 2 - 1] + dev[n // 2]) / 2
    if mad > 0:
        return lambda x: 0.6745 * (x - med) / mad
    mad2 = sum(abs(x - med) for x in xs) / n
    return (lambda x: (x - med) / (1.2533 * mad2)) if mad2 > 0 else (lambda x: 0.0)


OUTLIER_Z = 3.5
WMC_GOD = 47


def outliers(funcs: dict) -> set:
    """Function ids whose gain is a modified-z outlier among the generators (method-level
    god-method signal)."""
    gains = {fid: r["gain"] for fid, r in funcs.items() if r["gain"] > 0}
    if not gains:
        return set()
    z = modified_z(list(gains.values()))
    return {fid for fid, g in gains.items() if z(g) >= OUTLIER_Z}


def class_gain(funcs: dict) -> dict:
    """file -> cumulated generation (sum of its methods' gain). The class-level signal:
    a god CLASS accretes manufactured flow across several methods, so its generation is
    the sum, matched to WMC (also a per-class sum) rather than to a single method."""
    cg: dict = {}
    for fid, r in funcs.items():
        cg[fid[0]] = cg.get(fid[0], 0) + r["gain"]
    return cg


def class_outliers(cg: dict) -> set:
    """Files whose cumulated generation is a modified-z outlier (god-class signal)."""
    xs = [v for v in cg.values() if v > 0]
    if not xs:
        return set()
    z = modified_z(xs)
    return {f for f, v in cg.items() if v > 0 and z(v) >= OUTLIER_Z}
