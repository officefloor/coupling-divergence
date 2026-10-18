"""Language-neutral raw method-shape extraction via lizard -- the one extractor, for every
language including Java.

lizard gives the function list (name, line range, parameter names, cyclomatic complexity,
nloc, tokens) for some twenty languages. Everything above that is lexical: the method's
body is blanked of strings and comments per language, every `name(` ... `)` is a call site,
its top-level commas split the arguments, and an argument that is a bare identifier naming
one of the method's own parameters is a FORWARD. That is all the forward metrics need, so a
conduit is measured the same way in Python, Go or C# as in Java.

Call classification here is lexical, and descriptive only: `kind` is internal when the callee
name is a function defined somewhere in the tree and external otherwise, `via` is "lexical"
and `target` is None (the analysis resolves such edges by callee name when it needs an edge at
all). Ownership: a named nested function (including a method of a Java anonymous class) owns
its own lines, carved out of the enclosing method's; an anonymous inline one (a callback or
lambda) belongs to its parent. The carve-out is by line, so a nested function on the same
single line as its parent cannot be separated (not a shape real code takes).
"""
from __future__ import annotations

import os
import re

import lizard

from . import cognitive as _cognitive
from . import discover
from . import extract_common

COMPLEXITY_FIELDS = extract_common.COMPLEXITY_FIELDS

# Tokens that take a parenthesis but are not calls, across common languages.
NOT_CALL = {
    "if", "while", "for", "switch", "catch", "return", "synchronized", "with",
    "elif", "foreach", "lock", "using", "when", "match", "do", "else", "case",
    "and", "or", "not", "in", "is", "assert", "throw", "throws", "yield", "await",
    "func", "def", "function", "sub", "super", "this", "self", "sizeof", "typeof",
}
_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

# Comment/string strip per language, so commas inside literals are not argument separators
# and `(` inside a string is not a call. Per language because a comment opener in one is code
# in another: `//` is floor division in Python, `#` a private field in JS, `'a` a Rust lifetime.
_DQ = r'"(?:\\.|[^"\\])*"'
_SQ = r"'(?:\\.|[^'\\])*'"
_CHAR = r"'(?:\\.|[^'\\])'|'\\[^'\n]{1,10}'"
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

# A Rust inline test module: `#[cfg(test)]` followed by `mod name {`.
_RUST_CFG_TEST = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]\s*(?:pub(?:\([^)]*\))?\s+)?"
                            r"mod\s+\w+\s*\{")


def _strip(text: str, ext: str) -> str:
    """Blank strings and comments, keeping every newline so line positions are kept."""
    ext = ext.lower()
    rx = _STRIP_BY.get(_STRIP_EXT.get(ext, ext), _STRIP_BY[""])
    return rx.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _rust_test_lines(src: str) -> list[tuple[int, int]]:
    """1-based inclusive line ranges of `#[cfg(test)] mod name { ... }` blocks, so the inline
    unit tests Rust keeps beside the code are excluded like test files."""
    text = _strip(src, "rs")
    out = []
    for m in _RUST_CFG_TEST.finditer(src):
        if text[m.end() - 1] != "{":
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


def _call_sites(body: str):
    """Yield (callee, [argument texts], offset of the opening parenthesis) for every call
    expression in `body`, in order. Calls nested inside another call's parentheses are
    yielded too (an argument `h(g(x))`, a condition, a callback body). Arguments are split on
    commas at bracket depth 1, counting ( [ { alike so a literal list or generic argument is
    not split."""
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
        depth, p, seg, args = 0, i, i + 1, []
        while p < n:
            c = body[p]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    break
            elif c == "," and depth == 1:
                args.append(body[seg:p])
                seg = p + 1
            p += 1
        args.append(body[seg:p])
        args = [a.strip() for a in args]
        if len(args) == 1 and not args[0]:
            args = []
        yield word, args, i


def _owned_lines(raw: list) -> list[list[int]]:
    """Per raw function, the 1-based lines whose calls it owns, so each call is counted once.
    A NAMED nested function owns its lines (carved out of every enclosing one); an ANONYMOUS
    inline one (a callback or lambda) stays with its parent and owns nothing itself."""
    def anon(i):
        return raw[i]["short"].startswith("(") or not raw[i]["short"]

    by_file: dict = {}
    for idx, r in enumerate(raw):
        by_file.setdefault(r["file"], []).append(idx)
    owned: list = [None] * len(raw)
    for idxs in by_file.values():
        idxs.sort(key=lambda i: (raw[i]["start"], -raw[i]["end"]))
        enclosed = set()
        for pos, i in enumerate(idxs):
            s, e = raw[i]["start"], raw[i]["end"]
            lines = set(range(s, e + 1))
            for j in idxs[pos + 1:]:
                s2, e2 = raw[j]["start"], raw[j]["end"]
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


def method_shapes(root: str, include_tests: bool = False,
                  files: list[str] | None = None) -> list[dict]:
    """Raw per-method records (the extract_common contract) for every function lizard finds in
    `files` (default: every source file discovered under the checkout)."""
    if files is None:
        files = discover.source_files(root, include_tests)
    raw: list = []
    stripped: dict = {}
    for rel in files:
        path = os.path.join(root, rel)
        try:
            analysis = lizard.analyze_file(path)
            with open(path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except Exception:
            continue
        ext = os.path.splitext(rel)[1].lstrip(".").lower()
        src_lines = src.splitlines()
        stripped[rel] = _strip("\n".join(src_lines), ext).split("\n")
        skip = _rust_test_lines(src) if ext == "rs" and not include_tests else []
        for fn in analysis.function_list:
            s, e = int(fn.start_line), int(fn.end_line)
            if any(a <= s <= b for a, b in skip):
                continue
            pnames = [p for p in (getattr(fn, "parameters", None) or [])
                      if p not in extract_common.RECEIVER_PARAMS]
            qual = fn.name.replace("::", ".")
            body_raw = "\n".join(src_lines[max(s - 1, 0):e])
            raw.append({
                "file": rel, "ext": ext, "qual": qual, "short": _short(fn.name),
                "type": qual.rsplit(".", 1)[0] if "." in qual else "",
                "start": s, "end": e, "params": pnames,
                "md": {"cc": int(fn.cyclomatic_complexity),
                       "nloc": int(getattr(fn, "nloc", 0) or 0),
                       "tokens": int(getattr(fn, "token_count", 0) or 0),
                       "params": len(pnames),
                       "nesting": int(getattr(fn, "max_nesting_depth", 0) or 0),
                       "cognitive": _cognitive.cognitive_complexity(body_raw, ext)},
            })
    if not raw:
        return []

    names = {r["short"] for r in raw}
    owned = _owned_lines(raw)
    out: list = []
    for idx, r in enumerate(raw):
        src = stripped.get(r["file"], [])
        body = "\n".join(src[ln - 1] for ln in owned[idx] if ln - 1 < len(src))
        first_line = (len(src[r["start"] - 1]) if owned[idx] and owned[idx][0] == r["start"]
                      and r["start"] <= len(src) else 0)
        pset = set(r["params"])
        calls = []
        for callee, args, pos in _call_sites(body):
            if callee == r["short"] and pos < first_line:
                first_line = -1              # the function's own signature, once
                continue
            if callee in NOT_CALL:
                continue
            kind = "internal" if callee in names else "external"
            calls.append({"callee": callee, "args": len(args),
                          "fwd": [a for a in args if _IDENT.match(a) and a in pset],
                          "kind": kind, "via": "lexical", "target": None,
                          "receiver": None, "own": kind == "internal"})
        out.append({
            "file": r["file"], "package": "", "type": r["type"],
            "qualname": r["qual"], "name": r["short"],
            "start": r["start"], "end": r["end"],
            **{k: r["md"][k] for k in COMPLEXITY_FIELDS},
            "param_names": r["params"],
            "calls": calls,
            **extract_common.rollups(calls, len(r["params"])),
        })
    return out
