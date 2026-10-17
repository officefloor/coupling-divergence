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

Counting rules, so each call is counted exactly once: calls nested in arguments, conditions
and inline lambdas/callbacks count; a function's own signature is not a call; a named nested
function owns its own calls while an anonymous inline one belongs to its parent; strings and
comments are blanked per language. Test files (by directory and by each language's
colocated-test naming, plus Rust #[cfg(test)] modules) are excluded unless include_tests.

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
# Per language, because a token that opens a comment in one language is code in another:
# `//` is floor division in Python, `#` is a private field in JS and an attribute in Rust,
# and `'a` is a Rust lifetime, not the start of a character literal.
_DQ = r'"(?:\\.|[^"\\])*"'
_SQ = r"'(?:\\.|[^'\\])*'"
_CHAR = r"'(?:\\.|[^'\\])'|'\\[^'\n]{1,10}'"       # a single char literal only
_BTICK = r'`(?:\\.|[^`\\])*`'
_TRIPLE = r'"""(?:\\.|[^\\])*?"""|' + r"'''(?:\\.|[^\\])*?'''"
_SLASH = r'//[^\n]*|/\*.*?\*/'
_HASH = r'#[^\n]*'
_PREPROC = r'(?m:^[ \t]*#[^\n]*)'
_STRIP_PARTS = {
    "py": (_TRIPLE, _DQ, _SQ, _HASH),
    "rb": (_DQ, _SQ, _BTICK, _HASH),
    "php": (_DQ, _SQ, _SLASH, _HASH),
    "rs": (_DQ, _CHAR, _SLASH),
    "swift": (_TRIPLE, _DQ, _SLASH),
    "lua": (_DQ, _SQ, r'--[^\n]*'),
    "c": (_DQ, _SQ, _SLASH, _PREPROC),
    "js": (_DQ, _SQ, _BTICK, _SLASH),
    "": (_TRIPLE, _DQ, _SQ, _SLASH),               # Java, Kotlin, Scala, Groovy, ...
}
_STRIP_EXT = {"pyw": "py", "pyi": "py", "jsx": "js", "ts": "js", "tsx": "js", "go": "js",
              "h": "c", "hh": "c", "hpp": "c", "cc": "c", "cpp": "c", "cxx": "c", "m": "c",
              "mm": "c", "cs": "c"}
_STRIP_BY = {k: re.compile("|".join(v), re.S) for k, v in _STRIP_PARTS.items()}


def _strip(text: str, ext: str) -> str:
    """Blank strings and comments, keeping every newline so line positions are kept."""
    ext = ext.lower()
    rx = _STRIP_BY.get(_STRIP_EXT.get(ext, ext), _STRIP_BY[""])
    return rx.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
_TEST_DIR = re.compile(r"(?:^|/)(?:test|tests|testing|spec|specs|__tests__|testdata"
                       r"|[^/]+\.(?:tests?|unittests|integrationtests|specs))(?:/|$)", re.I)
# Test files that live BESIDE the code they test, by each language's naming convention
# (Go *_test.go, JS/TS *.test.* / *.spec.*, pytest test_*.py / *_test.py, RSpec *_spec.rb,
# gtest *_test.cc / *_unittest.cc, Rust tests.rs). A directory filter alone misses them.
_TEST_FILE = re.compile(
    r"(?:_test\.go"
    r"|[.-](?:test|spec)\.(?:js|jsx|ts|tsx)"
    r"|(?:^|/)(?:test_[^/]*|[^/]*_test|conftest|tests)\.py"
    r"|(?:^|/)(?:test_[^/]*|[^/]*_(?:test|spec))\.rb"
    r"|_(?:unit)?test\.(?:c|cc|cpp|cxx)"
    r"|(?:^|/)tests\.rs)$", re.I)
# A Rust inline test module: `#[cfg(test)]` followed by `mod name {`.
_RUST_CFG_TEST = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]\s*(?:pub(?:\([^)]*\))?\s+)?"
                            r"mod\s+\w+\s*\{")
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
            relp = "/" + rel.replace(os.sep, "/")
            if not include_tests and (_TEST_DIR.search(relp) or _TEST_FILE.search(relp)):
                continue
            try:
                if os.path.getsize(os.path.join(dirpath, fn)) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(rel)
    return out


def _rust_test_lines(src: str) -> list[tuple[int, int]]:
    """1-based inclusive line ranges of the `#[cfg(test)] mod name { ... }` blocks in a Rust
    source, so the inline unit tests Rust keeps beside the code are excluded like test files."""
    text = _strip(src, "rs")
    out = []
    for m in _RUST_CFG_TEST.finditer(src):
        if text[m.end() - 1] != "{":         # the match began inside a string or comment
            continue
        depth, p = 0, m.end() - 1
        while p < len(text):
            if text[p] == "{":
                depth += 1
            elif text[p] == "}":
                depth -= 1
                if depth == 0:
                    break
            p += 1
        out.append((src.count("\n", 0, m.start()) + 1, src.count("\n", 0, p) + 1))
    return out


def _call_sites(body: str, with_pos: bool = False):
    """Yield (callee name, argument count) for every call expression in `body`, in order of
    the opening parenthesis. Calls nested inside another call's parentheses are yielded
    too: an argument (`h(g(x))`), a condition (`if (g(x))`), and a lambda or callback passed
    as an argument (`xs.forEach(x -> g(x))`) each hold calls of their own. With `with_pos`,
    each tuple also carries the offset of the opening parenthesis."""
    n = len(body)
    for i in range(n):
        if body[i] != "(":
            continue
        j = i - 1
        while j >= 0 and body[j].isspace():
            j -= 1
        if j < 0 or not (body[j].isalnum() or body[j] in "_$"):
            continue
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
        yield (word, (args + 1 if content else 0), i) if with_pos else \
            (word, args + 1 if content else 0)


def _owned_lines(raw: list) -> list[list[int]]:
    """Per raw function, the 1-based lines whose calls it owns, so each call is counted once.

    lizard reports a nested function as its own record, inside its parent's line range.
      * A NAMED nested function (a Python inner def, a JS function declaration, a method of
        a Java anonymous class) owns its lines; they are carved out of every enclosing one.
      * An ANONYMOUS nested function (a callback or lambda passed inline) is part of the
        enclosing function's code, the same way a Java lambda is (lizard does not report
        those at all), so it stays with the parent and owns nothing itself. Only a
        top-level anonymous function, with no enclosing function, owns its lines."""
    def anon(i):
        return raw[i][1].startswith("(") or not raw[i][1]

    by_file: dict = {}
    for idx, r in enumerate(raw):
        by_file.setdefault(r[0], []).append(idx)
    owned: list = [None] * len(raw)
    for idxs in by_file.values():
        idxs.sort(key=lambda i: (raw[i][2], -raw[i][3]))
        enclosed = set()
        for pos, i in enumerate(idxs):
            s, e = raw[i][2], raw[i][3]
            lines = set(range(s, e + 1))
            for j in idxs[pos + 1:]:
                s2, e2 = raw[j][2], raw[j][3]
                if s2 > e:
                    break
                if e2 <= e and (s2, e2) != (s, e):
                    enclosed.add(j)
                    if not anon(j):
                        lines.difference_update(range(s2, e2 + 1))
            owned[i] = sorted(lines)
        for j in enclosed:
            if anon(j):
                owned[j] = []
    return owned


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
        ext = os.path.splitext(rel)[1].lstrip(".")
        lines_cache[rel] = _strip("\n".join(src_lines), ext).split("\n")
        skip = (_rust_test_lines("\n".join(src_lines))
                if ext == "rs" and not include_tests else [])
        for fn in analysis.function_list:
            s, e = int(fn.start_line), int(fn.end_line)
            if any(a <= s <= b for a, b in skip):
                continue
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
    owned = _owned_lines(raw)
    out_gen: dict = {}
    in_by_name: dict = {}
    agg: dict = {}                      # fid -> complexity metrics, merged by max over overloads
    for idx, (rel, short, start, end, md) in enumerate(raw):
        fid = (rel, short)
        cur = agg.get(fid)
        if cur is None:
            agg[fid] = dict(md)
        else:
            for k, v in md.items():
                if v > cur.get(k, 0):
                    cur[k] = v
        src = lines_cache.get(rel, [])
        body = "\n".join(src[ln - 1] for ln in owned[idx] if ln - 1 < len(src))
        # offset where the body's first line ends, if that line is the signature line
        first_line = (len(src[start - 1]) if owned[idx] and owned[idx][0] == start
                      and start <= len(src) else 0)
        og = 0
        for callee, nargs, pos in _call_sites(body, with_pos=True):
            if callee == short and pos < first_line:
                first_line = -1         # the function's own signature, not a call: once only
                continue
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
