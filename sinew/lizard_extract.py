"""Language-neutral raw method-shape extraction via lizard -- the one extractor, for every
language including Java.

lizard gives the function list (name, line range, parameter text, cyclomatic complexity,
nloc, tokens) for some twenty languages. Everything above that is lexical: the method's
body is blanked of strings, comments and annotations per language, every `name(` ... `)` is a
call site, its top-level commas split the arguments, and an argument that hands on one of the
method's own parameters -- as a bare identifier, or as the value of a named argument
(`f(ctx=ctx)`, `f(ctx: ctx)`) -- is a FORWARD. That is all the forward metrics need, so a
conduit is measured the same way in Python, Go or C# as in Java.

Parameters are parsed from lizard's full parameter text, per language family, into a NAME and
a declared TYPE (None where the language, or the declaration, has none). lizard's own bare
parameter list is not used: it returns type names for Go and TypeScript. Each parameter also
gets a MATCH KEY: its type when the type identifies something (`T:Order`), else its name
(`N:ctx`). Universal types -- primitives, String, Object, bare collections, a type variable --
name nothing in particular, so a parameter of such a type is matched by name, as in an
untyped language. The analysis links a method that forwards a parameter to a method that
declares one with the same key: the data one sends and the other consumes.

Call classification here is lexical, and descriptive only: `kind` is internal when the callee
name is a function defined somewhere in the tree and external otherwise, `via` is "lexical"
and `target` is None. Ownership: a NAMED nested function owns its own lines, carved out of
the enclosing method's; an ANONYMOUS one -- a callback, a lambda, or a method of a Java
anonymous class -- belongs to the enclosing method and is not a method of its own, so the
listener idiom `btn.addListener(new Listener() { public void run() { ... } })` is one
method's code, and two anonymous `run`s in one file are not merged into a phantom method.
"""
from __future__ import annotations

import bisect
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
# A named argument whose value is a bare identifier: `ctx=ctx` (Python, Kotlin, C#),
# `ctx: ctx` (Swift, Ruby keywords, Rust struct-ish calls). `a == b` does not match.
_NAMED_ARG = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*\s*(?:=|:)\s*([A-Za-z_$][A-Za-z0-9_$]*)$")

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

# An annotation / decorator with arguments (`@RequestParam("id")`, `@Foo(a = {1, 2})`) is
# metadata, not a call. Blanked after strings, in every language where `@name(` is one
# (not Python or Ruby, where decorators sit on their own line outside the body anyway and `@`
# is an instance variable in Ruby).
_ANNOT = re.compile(r"@[A-Za-z_][\w.]*\s*\((?:[^()]|\([^()]*\))*\)")
_NO_ANNOT_EXTS = {"py", "pyw", "pyi", "rb"}

# A Rust inline test module: `#[cfg(test)]` followed by `mod name {`.
_RUST_CFG_TEST = re.compile(r"#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]\s*(?:pub(?:\([^)]*\))?\s+)?"
                            r"mod\s+\w+\s*\{")


def _blank(m) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


def _strip(text: str, ext: str) -> str:
    """Blank strings, comments and annotation arguments, keeping every newline so line
    positions are kept."""
    ext = ext.lower()
    rx = _STRIP_BY.get(_STRIP_EXT.get(ext, ext), _STRIP_BY[""])
    text = rx.sub(_blank, text)
    if ext not in _NO_ANNOT_EXTS:
        text = _ANNOT.sub(_blank, text)
    return text


# ---------------------------------------------------------------------------
# parameters: name, declared type, match key
# ---------------------------------------------------------------------------
# Language families by how a parameter is written.
_COLON_TYPED = {"py", "pyw", "pyi", "kt", "kts", "swift", "scala", "rs"}   # name : Type
_NAME_FIRST = {"go", "ts", "tsx"}                                           # name Type
_TYPE_FIRST = {"java", "c", "h", "cc", "cpp", "cxx", "hh", "hpp", "cs", "m", "mm", "groovy"}
_UNTYPED = {"js", "jsx", "rb", "lua", "php"}

# Parameter modifiers, PER LANGUAGE. A word that is a modifier in one language is an ordinary
# parameter name in another (`in`, `out`, `ref`, `var` and `val` are common Java names), so the
# set a language's declarations are cleaned with holds only that language's modifiers.
_MODS_BY_LANG = {
    "java": {"final"},
    "groovy": {"final", "def"},
    "scala": {"final", "val", "var", "implicit"},
    "c": {"const", "volatile", "register", "static", "restrict"},
    "cs": {"ref", "out", "in", "params", "readonly", "this", "scoped"},
    "kt": {"vararg", "crossinline", "noinline", "val", "var"},
    "swift": {"inout", "unowned", "weak", "borrowing", "consuming"},
    "rs": {"mut"},
}
_MODS_LANG_BY_EXT = {"kts": "kt", "h": "c", "hh": "c", "hpp": "c", "cc": "c", "cpp": "c",
                     "cxx": "c", "m": "c", "mm": "c"}
# The union, used only to clean TYPE text (a modifier never names a type).
_MODIFIERS = set().union(*_MODS_BY_LANG.values())


def modifiers_for(ext: str) -> set:
    """The parameter modifiers of the language with this file extension."""
    return _MODS_BY_LANG.get(_MODS_LANG_BY_EXT.get(ext, ext), set())

# Types that name nothing in particular: a parameter of such a type is matched by NAME.
# Case-sensitive, across languages (Java / C# / Python / Go / Rust / TS / Kotlin / Swift).
COMMON_TYPES = {
    "void", "int", "long", "short", "byte", "char", "boolean", "bool", "float", "double",
    "Integer", "Long", "Short", "Byte", "Character", "Boolean", "Float", "Double", "Number",
    "String", "string", "CharSequence", "StringBuilder", "StringBuffer", "Object", "object",
    "Void", "Any", "any", "unknown", "never", "number", "bigint", "symbol",
    "str", "bytes", "bytearray", "dict", "list", "tuple", "set", "frozenset", "None",
    "List", "Map", "Set", "Collection", "Iterable", "Iterator", "Optional", "Array",
    "ArrayList", "HashMap", "HashSet", "LinkedList", "Dict", "Sequence", "Mapping",
    "Callable", "Function", "Supplier", "Consumer", "Runnable", "Predicate", "Future",
    "CompletableFuture", "Promise", "Stream", "Class", "Type", "Enum", "Throwable",
    "Exception", "Error", "error", "Context", "context", "Date", "LocalDate", "LocalDateTime",
    "Instant", "Duration", "UUID", "URI", "URL", "File", "Path", "InputStream", "OutputStream",
    "Reader", "Writer", "BigDecimal", "BigInteger", "Locale", "Logger",
    "i8", "i16", "i32", "i64", "i128", "u8", "u16", "u32", "u64", "u128", "isize", "usize",
    "f32", "f64", "Self", "Vec", "Box", "Rc", "Arc", "Option", "Result", "HashMapRs",
    "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64",
    "float32", "float64", "rune", "interface", "struct", "unsigned", "signed", "size_t",
    "Int", "Unit", "Nothing", "Char", "Byte", "ByteArray", "IntArray", "CGFloat",
}
_TYPEVAR = re.compile(r"^[A-Z][0-9]?$")          # T, K, V, E, T1 ...
_TYPE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def informative(typ: str | None) -> bool:
    """Whether a declared type identifies something: at least one identifier in it is not a
    universal type, a type variable, or a modifier."""
    if not typ:
        return False
    idents = _TYPE_IDENT.findall(typ)
    return any(i not in COMMON_TYPES and i not in _MODIFIERS and not _TYPEVAR.match(i)
               for i in idents)


def _norm_type(tokens: list[str]) -> str | None:
    """Type text from its tokens: no whitespace, `...` as `[]`, modifiers dropped."""
    toks = [t for t in tokens if t not in _MODIFIERS and t != "@"]
    if not toks:
        return None
    s = "".join(toks).replace("...", "[]")
    return s or None


def _split_default(text: str) -> str:
    return text.split("=", 1)[0]


def parse_parameter(full: str, ext: str) -> tuple[str | None, str | None]:
    """(name, type) of one parameter from lizard's full text, by the language family. The
    type is None in an untyped language or an untyped declaration."""
    text = full.strip()
    if not text:
        return None, None
    if ext in _UNTYPED:
        ids = re.findall(r"\$?([A-Za-z_][A-Za-z0-9_]*)", _split_default(text))
        return (ids[-1] if ids else None), None
    if ext in _COLON_TYPED:
        if ":" in text:
            before, after = text.split(":", 1)
            ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", before)
            typ_toks = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*|\[\]|[*&?]",
                                              _split_default(after)) if t not in ("&", "*")]
            return (ids[-1] if ids else None), _norm_type(typ_toks)
        ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _split_default(text))
        return (ids[-1] if ids else None), None
    toks = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*|\.\.\.|\[\s*\]|[*&.@]", _split_default(text))
    toks = [t.replace(" ", "") for t in toks]
    # drop annotation names (`@ Named`) and THIS language's modifiers
    mods = modifiers_for(ext)
    clean, skip = [], False
    for t in toks:
        if t == "@":
            skip = True
            continue
        if skip:
            skip = False
            continue
        if t in mods:
            continue
        clean.append(t)
    toks = clean
    idents = [i for i, t in enumerate(toks) if _IDENT.match(t)]
    if not idents:
        return None, None
    if ext in _NAME_FIRST:                       # Go / TypeScript: name, then the type
        name_i = idents[0]
        rest = toks[name_i + 1:]
        return toks[name_i], _norm_type(rest)
    # type-first (Java, C family, C#): the name is the last identifier; `name[]` is an array
    name_i = idents[-1]
    typ = toks[:name_i] + [t for t in toks[name_i + 1:] if t == "[]"]
    return toks[name_i], (_norm_type(typ) if typ else None)


def parameters(fn, ext: str) -> tuple[list[str], list[str | None]]:
    """(names, types) of a lizard function's parameters, receivers dropped."""
    fulls = list(getattr(fn, "full_parameters", None) or [])
    if ext == "php" and len(fulls) == 1 and fulls[0].count("$") > 1:   # lizard joins them
        fulls = ["$" + p for p in fulls[0].split("$") if p.strip()]
    names, types = [], []
    for full in fulls:
        n, t = parse_parameter(full, ext)
        if not n or n in extract_common.RECEIVER_PARAMS:
            continue
        names.append(n)
        types.append(t)
    if not fulls:                                 # no full text: fall back to lizard's names
        for n in (getattr(fn, "parameters", None) or []):
            if n not in extract_common.RECEIVER_PARAMS:
                names.append(n)
                types.append(None)
    return names, types


def match_key(name: str, typ: str | None) -> str:
    """The key two parameters are matched on: the type when it identifies something, else
    the name."""
    return f"T:{typ}" if informative(typ) else f"N:{name}"


# ---------------------------------------------------------------------------
# call sites
# ---------------------------------------------------------------------------
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


def forwarded(arg: str, pset: set) -> str | None:
    """The method parameter an argument hands on unchanged, or None: a bare identifier, or
    the value of a named argument (`ctx=ctx`, `label: ctx`)."""
    if _IDENT.match(arg):
        return arg if arg in pset else None
    m = _NAMED_ARG.match(arg)
    if m and m.group(1) in pset:
        return m.group(1)
    return None


def _is_anon(r: dict) -> bool:
    """A callback, lambda or Java anonymous-class method: code of the enclosing method."""
    return "(anonymous)" in r["qual"] or r["short"].startswith("(") or not r["short"]


def _owned_lines(raw: list) -> list[list[int]]:
    """Per raw function, the 1-based lines whose calls it owns, so each call is counted once.
    A NAMED nested function owns its lines (carved out of every enclosing one); an ANONYMOUS
    one stays with its parent and owns nothing itself."""
    by_file: dict = {}
    for idx, r in enumerate(raw):
        by_file.setdefault(r["file"], []).append(idx)
    owned: list = [None] * len(raw)
    for idxs in by_file.values():
        idxs.sort(key=lambda i: (raw[i]["start"], -raw[i]["end"]))
        for pos, i in enumerate(idxs):
            if _is_anon(raw[i]):
                owned[i] = []
                continue
            s, e = raw[i]["start"], raw[i]["end"]
            lines = set(range(s, e + 1))
            for j in idxs[pos + 1:]:
                s2, e2 = raw[j]["start"], raw[j]["end"]
                if s2 > e:
                    break
                if e2 <= e and (s2, e2) != (s, e) and not _is_anon(raw[j]):
                    lines.difference_update(range(s2, e2 + 1))
            owned[i] = sorted(lines)
    return owned


def _short(name: str) -> str:
    return name.split("::")[-1].split(".")[-1]


def method_shapes(root: str, include_tests: bool = False,
                  files: list[str] | None = None) -> list[dict]:
    """Raw per-method records (the extract_common contract) for every named function lizard
    finds in `files` (default: every source file discovered under the checkout). Anonymous
    functions are folded into the method that encloses them."""
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
            pnames, ptypes = parameters(fn, ext)
            qual = fn.name.replace("::", ".")
            body_raw = "\n".join(src_lines[max(s - 1, 0):e])
            raw.append({
                "file": rel, "ext": ext, "qual": qual, "short": _short(fn.name),
                "type": qual.rsplit(".", 1)[0] if "." in qual else "",
                "start": s, "end": e, "params": pnames, "ptypes": ptypes,
                "md": {"cc": int(fn.cyclomatic_complexity),
                       "nloc": int(getattr(fn, "nloc", 0) or 0),
                       "tokens": int(getattr(fn, "token_count", 0) or 0),
                       "params": len(pnames),
                       "nesting": int(getattr(fn, "max_nesting_depth", 0) or 0),
                       "cognitive": _cognitive.cognitive_complexity(body_raw, ext)},
            })
    if not raw:
        return []

    # Identity. Overloads share (file, qualname) downstream, but lizard also gives the SAME
    # name and signature to every enum-constant body and every nested-class method of one
    # name, which would merge whole state machines (74 `read` bodies of one enum) into one
    # phantom method. Functions of one file with identical name AND signature are told apart
    # by their order of appearance (`read#1`, `read#2`, ...); the identity shifts if one is
    # inserted or removed before another, a documented limitation.
    sig_count: dict = {}
    for r in raw:
        if not _is_anon(r):
            sk = (r["file"], r["qual"], tuple(t or n for n, t in zip(r["params"], r["ptypes"])))
            r["_sig"] = sk
            sig_count[sk] = sig_count.get(sk, 0) + 1
    seen: dict = {}
    for r in raw:
        sk = r.get("_sig")
        if sk is not None and sig_count[sk] > 1:
            seen[sk] = seen.get(sk, 0) + 1
            r["qual"] = f"{r['qual']}#{seen[sk]}"

    names = {r["short"] for r in raw if not _is_anon(r)}
    # how many functions define a callee name, and which names each file defines: a call to a
    # name defined ONCE in the project, or defined in the caller's own file, resolves to a
    # known target (`strict`); `get` or `add` defined in twenty classes does not
    name_count: dict = {}
    file_names: dict = {}
    for r in raw:
        if not _is_anon(r):
            name_count[r["short"]] = name_count.get(r["short"], 0) + 1
            file_names.setdefault(r["file"], set()).add(r["short"])
    # the signature of an anonymous function on a line its parent owns is not a call
    anon_sigs = {(r["file"], r["start"], r["short"]) for r in raw if _is_anon(r) and r["short"]}
    owned = _owned_lines(raw)
    out: list = []
    for idx, r in enumerate(raw):
        if _is_anon(r):
            continue
        src = stripped.get(r["file"], [])
        lines = [ln for ln in owned[idx] if ln - 1 < len(src)]
        body = "\n".join(src[ln - 1] for ln in lines)
        starts = []                          # offset at which each owned line begins
        off = 0
        for ln in lines:
            starts.append(off)
            off += len(src[ln - 1]) + 1
        first_line = (len(src[r["start"] - 1]) if lines and lines[0] == r["start"] else 0)
        pset = set(r["params"])
        calls = []
        for callee, args, pos in _call_sites(body):
            if callee == r["short"] and pos < first_line:
                first_line = -1              # the function's own signature, once
                continue
            if callee in NOT_CALL:
                continue
            if anon_sigs:
                ln = lines[bisect.bisect_right(starts, pos) - 1]
                if (r["file"], ln, callee) in anon_sigs:
                    continue
            kind = "internal" if callee in names else "external"
            fwd = [f for f in (forwarded(a, pset) for a in args) if f]
            strict = kind == "internal" and (callee in file_names.get(r["file"], ())
                                             or name_count.get(callee, 0) == 1)
            calls.append({"callee": callee, "args": len(args), "fwd": fwd,
                          "kind": kind, "via": "lexical", "target": None,
                          "receiver": None, "own": kind == "internal", "strict": strict})
        keys = [match_key(n, t) for n, t in zip(r["params"], r["ptypes"])]
        key_of = dict(zip(r["params"], keys))
        roll = extract_common.rollups(calls, len(r["params"]))
        out.append({
            "file": r["file"], "package": "", "type": r["type"],
            "qualname": r["qual"], "name": r["short"],
            "start": r["start"], "end": r["end"],
            **{k: r["md"][k] for k in COMPLEXITY_FIELDS},
            "param_names": r["params"], "param_types": r["ptypes"], "param_keys": keys,
            "calls": calls,
            **roll,
            "fwd_keys": sorted({key_of[n] for n in roll["fwd_names"]}),
            "fwd_keys_strict": sorted({key_of[n] for n in roll["fwd_names_strict"]}),
        })
    return out
