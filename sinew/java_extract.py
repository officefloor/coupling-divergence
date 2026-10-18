"""Java-specific raw method-shape extraction via tree-sitter-java.

Same output contract as extract.method_shapes (so mine.py and the analysis layer treat the
two uniformly), but with real, package-aware call resolution instead of lexical name-match.
Because Java is statically typed, a method's local variables, parameters and fields carry
their declared types in the source, so a call's receiver can be resolved to a type -- and
thus to a package -- without a compiler or a classpath, by a small scoped symbol table.

Resolution is deliberately SHALLOW, matching the research rule:
  * Only IMPLEMENTED methods (a declaration with a body) are recorded. Interface/abstract
    signatures are not methods here; they still count as repo TYPES for classifying a
    receiver, because calling your own repository interface is internal coupling.
  * Inheritance is NOT chased. A call is classified by the receiver's DECLARED type being a
    class/type defined in this repository -- never by finding where the method truly lives.
    `super.x()` and inherited calls are ordinary out-edges like any other call.
  * When the receiver's type cannot be determined without type inference (a chained call
    `a.b().c()`, an unresolved `var`, an inherited/implicit field), the edge is recorded
    as `unresolved` with its raw receiver text, rather than guessed. Nothing is thrown away.

Each call edge carries: callee, args, kind (internal|external|unresolved), via (how the
receiver was resolved) and target (the resolved type, FQN or simple name, when known). The
per-method rollups split argument flow and call counts by kind. Call ownership follows the
same rule as the lizard extractor: a method owns the calls in its body except those inside a
nested method/constructor (named or anonymous), which are recorded on their own records.
"""
from __future__ import annotations

import os

import tree_sitter_java as _tsjava
from tree_sitter import Language, Parser

from . import discover
from . import cognitive as _cognitive

_LANG = Language(_tsjava.language())


def _parser() -> Parser:
    try:
        return Parser(_LANG)                     # tree-sitter >= 0.22
    except TypeError:                            # pragma: no cover - older API
        p = Parser()
        p.set_language(_LANG)
        return p


COMPLEXITY_FIELDS = discover.COMPLEXITY_FIELDS    # reuse the same metric keys

TYPE_DECLS = {"class_declaration", "interface_declaration", "enum_declaration",
              "record_declaration", "annotation_type_declaration"}
METHOD_DECLS = {"method_declaration", "constructor_declaration",
                "compact_constructor_declaration"}
# Decision points for a McCabe-style cyclomatic count (base 1 + these).
_DECISION = {"if_statement", "for_statement", "enhanced_for_statement", "while_statement",
             "do_statement", "catch_clause", "ternary_expression",
             "switch_label", "switch_rule"}
# Control structures that add a nesting level, for the max-nesting-depth metric.
_NEST = {"if_statement", "for_statement", "enhanced_for_statement", "while_statement",
         "do_statement", "switch_expression", "switch_statement", "try_statement",
         "try_with_resources_statement", "catch_clause", "synchronized_statement"}
# java.lang and other ubiquitous library simple names -> always external, no import needed.
_JAVA_LANG = {
    "String", "Integer", "Long", "Short", "Byte", "Boolean", "Character", "Double", "Float",
    "Object", "Number", "CharSequence", "Math", "System", "Thread", "Runnable", "Void",
    "Exception", "RuntimeException", "Throwable", "Error", "Class", "Enum", "Iterable",
    "Comparable", "StringBuilder", "StringBuffer", "Optional", "List", "Map", "Set",
    "Collection", "ArrayList", "HashMap", "HashSet", "LinkedList", "Arrays", "Collections",
    "Objects", "Stream", "Collectors", "Pattern", "Matcher", "BigDecimal", "BigInteger",
    "LocalDate", "LocalDateTime", "LocalTime", "Instant", "Duration", "UUID",
}


def _txt(node) -> str:
    return node.text.decode("utf-8", "replace")


def _base_type(node) -> str | None:
    """A declared type node -> its simple base name, generics/arrays/qualifier stripped.
    Primitives and `void` return None (they are never call receivers). `var` returns 'var'
    so the caller can try to infer it from an initializer."""
    if node is None:
        return None
    t = node.type
    if t in ("integral_type", "floating_point_type", "boolean_type", "void_type"):
        return None
    if t == "type_identifier":
        name = _txt(node)
        return name
    if t == "generic_type":
        inner = node.child_by_field_name("name")
        if inner is None:
            for c in node.children:
                if c.type in ("type_identifier", "scoped_type_identifier"):
                    inner = c
                    break
        return _base_type(inner) if inner is not None else _txt(node).split("<")[0].strip()
    if t == "scoped_type_identifier":
        return _txt(node).split(".")[-1]
    if t == "array_type":
        return _base_type(node.child_by_field_name("element"))
    if t == "annotated_type":
        for c in node.children:
            b = _base_type(c)
            if b:
                return b
        return None
    txt = _txt(node)
    return txt if txt and txt[0].isupper() else ("var" if txt == "var" else None)


def _iter_owned(node):
    """Every descendant of `node`, NOT descending into a nested method/constructor body
    (those are separate records that own their own calls)."""
    stack = list(node.children)
    while stack:
        n = stack.pop()
        yield n
        if n.type in METHOD_DECLS:
            continue
        stack.extend(n.children)


def _parse_imports(root):
    """(imports, statics, wildcard_pkgs): simple name -> FQN for type imports; static member
    name -> owning class FQN for `import static`; the set of wildcard-imported packages."""
    imports: dict = {}
    statics: dict = {}
    wildcard: set = set()
    for n in root.children:
        if n.type != "import_declaration":
            continue
        is_static = any(c.type == "static" for c in n.children)
        asterisk = any(c.type == "asterisk" for c in n.children)
        scoped = None
        for c in n.children:
            if c.type in ("scoped_identifier", "identifier"):
                scoped = c
        if scoped is None:
            continue
        fqn = _txt(scoped)
        if asterisk:
            wildcard.add(fqn)
        elif is_static:
            statics[fqn.split(".")[-1]] = fqn.rsplit(".", 1)[0]
        else:
            imports[fqn.split(".")[-1]] = fqn
    return imports, statics, wildcard


def _package(root) -> str:
    for n in root.children:
        if n.type == "package_declaration":
            for c in n.children:
                if c.type in ("scoped_identifier", "identifier"):
                    return _txt(c)
    return ""


def _type_names(root) -> set:
    out: set = set()
    stack = list(root.children)
    while stack:
        n = stack.pop()
        if n.type in TYPE_DECLS:
            nm = n.child_by_field_name("name")
            if nm is not None:
                out.add(_txt(nm))
        stack.extend(n.children)
    return out


class _Repo:
    """The repository-wide index built in one pass over every Java file, so a receiver type
    can be judged repo-defined or external."""

    def __init__(self) -> None:
        self.types: set = set()         # every type SIMPLE name declared anywhere in the repo
        self.packages: set = set()      # every package declared in the repo

    def is_repo_type(self, simple, imports, wildcard):
        """True (repo-defined), False (external), or None (cannot tell) for a simple type."""
        if simple is None or simple == "var":
            return None
        if simple in self.types:
            return True
        fqn = imports.get(simple)
        if fqn:
            pkg = fqn.rsplit(".", 1)[0]
            return pkg in self.packages or any(pkg.startswith(p + ".") for p in self.packages)
        if simple in _JAVA_LANG:
            return False
        # imported only via a wildcard: internal if a repo package is wildcarded, else unknown
        if any(w in self.packages for w in wildcard):
            return None
        return None


def _enclosing_type(node) -> str:
    names = []
    p = node.parent
    while p is not None:
        if p.type in TYPE_DECLS:
            nm = p.child_by_field_name("name")
            names.append(_txt(nm) if nm is not None else "?")
        elif p.type == "object_creation_expression":
            names.append("<anon>")
        p = p.parent
    return ".".join(reversed(names))


def _collect_fields(method_node) -> dict:
    """name -> declared base type for the fields of every class enclosing this method."""
    fields: dict = {}
    p = method_node.parent
    while p is not None:
        if p.type in TYPE_DECLS:
            body = p.child_by_field_name("body")
            if body is not None:
                for c in body.children:
                    if c.type == "field_declaration":
                        bt = _base_type(c.child_by_field_name("type"))
                        for d in c.children:
                            if d.type == "variable_declarator":
                                nm = d.child_by_field_name("name")
                                if nm is not None:
                                    fields.setdefault(_txt(nm), bt)
        p = p.parent
    return fields


def _collect_params(method_node) -> tuple[dict, list]:
    """(name -> type, [type, ...]) for the method's formal parameters."""
    params: dict = {}
    types: list = []
    fp = method_node.child_by_field_name("parameters")
    if fp is None:
        return params, types
    for c in fp.children:
        if c.type in ("formal_parameter", "spread_parameter"):
            bt = _base_type(c.child_by_field_name("type"))
            nm = c.child_by_field_name("name")
            if nm is None:                       # spread_parameter holds a variable_declarator
                for g in c.children:
                    if g.type == "variable_declarator":
                        nm = g.child_by_field_name("name")
            types.append(bt)
            if nm is not None:
                params[_txt(nm)] = bt
    return params, types


def _collect_locals(body) -> dict:
    """name -> declared base type for locals, for-each vars and catch params in the body
    (not descending into nested methods). `var` is inferred from a `new T(...)` initializer."""
    locals_: dict = {}
    for n in _iter_owned(body):
        if n.type == "local_variable_declaration":
            bt = _base_type(n.child_by_field_name("type"))
            for d in n.children:
                if d.type == "variable_declarator":
                    nm = d.child_by_field_name("name")
                    t = bt
                    if t == "var":
                        val = d.child_by_field_name("value")
                        if val is not None and val.type == "object_creation_expression":
                            t = _base_type(val.child_by_field_name("type"))
                        else:
                            t = None
                    if nm is not None:
                        locals_[_txt(nm)] = t
        elif n.type == "enhanced_for_statement":
            nm = n.child_by_field_name("name")
            if nm is not None:
                locals_[_txt(nm)] = _base_type(n.child_by_field_name("type"))
        elif n.type == "catch_formal_parameter":
            nm = n.child_by_field_name("name")
            if nm is not None:
                locals_[_txt(nm)] = _base_type(n.child_by_field_name("type"))
    return locals_


def _nargs(call) -> int:
    al = call.child_by_field_name("arguments")
    return sum(1 for _ in al.named_children) if al is not None else 0


def _metrics(body, body_text: str, ext: str = "java") -> dict:
    cc, nesting, tokens = 1, 0, 0

    def depth(n, d):
        nonlocal nesting, tokens, cc
        for c in n.children:
            if c.child_count == 0 and c.type not in ("\n",):
                tokens += 1
            if c.type in METHOD_DECLS:           # nested method: its own record
                continue
            if c.type in _DECISION:
                cc += 1
            if c.type == "binary_expression":
                if any(g.type in ("&&", "||") for g in c.children):
                    cc += 1
            nd = d + 1 if c.type in _NEST else d
            nesting = max(nesting, nd)
            depth(c, nd)
    depth(body, 0)
    nloc = sum(1 for ln in body_text.splitlines() if ln.strip())
    return {"cc": cc, "nesting": nesting, "tokens": tokens, "nloc": nloc,
            "cognitive": _cognitive.cognitive_complexity(body_text, ext)}


def _resolve_call(call, cls, params, locals_, fields, imports, statics, wildcard,
                  super_type, repo: _Repo) -> dict:
    """One method_invocation / object_creation_expression -> an edge dict."""
    if call.type == "object_creation_expression":
        simple = _base_type(call.child_by_field_name("type"))
        rep = repo.is_repo_type(simple, imports, wildcard)
        kind = "internal" if rep is True else "external" if rep is False else "unresolved"
        return {"callee": simple, "args": _nargs(call), "kind": kind, "via": "new",
                "target": simple, "receiver": None, "own": kind == "internal"}

    name = call.child_by_field_name("name")
    callee = _txt(name) if name is not None else "?"
    args = _nargs(call)
    obj = call.child_by_field_name("object")

    def by_type(simple, via):
        rep = repo.is_repo_type(simple, imports, wildcard)
        kind = "internal" if rep is True else "external" if rep is False else "unresolved"
        return {"callee": callee, "args": args, "kind": kind, "via": via,
                "target": simple, "receiver": None, "own": kind == "internal"}

    if obj is None:                                      # unqualified: foo(...)
        if callee in statics:                            # a static-imported method
            owner = statics[callee]
            pkg = owner.rsplit(".", 1)[0]
            internal = pkg in repo.packages or any(pkg.startswith(p + ".")
                                                   for p in repo.packages)
            kind = "internal" if internal else "external"
            return {"callee": callee, "args": args, "kind": kind, "via": "static",
                    "target": owner, "receiver": None, "own": internal}
        return {"callee": callee, "args": args, "kind": "internal", "via": "unqualified",
                "target": cls, "receiver": None, "own": True}

    ot = obj.type
    if ot == "this":
        return {"callee": callee, "args": args, "kind": "internal", "via": "this",
                "target": cls, "receiver": "this", "own": True}
    if ot == "super":
        rep = repo.is_repo_type(super_type, imports, wildcard) if super_type else None
        kind = "internal" if rep is True else "external" if rep is False else "unresolved"
        return {"callee": callee, "args": args, "kind": kind, "via": "super",
                "target": super_type, "receiver": "super", "own": kind == "internal"}
    if ot == "identifier":
        nm = _txt(obj)
        if nm in locals_:
            return by_type(locals_[nm], "local")
        if nm in params:
            return by_type(params[nm], "param")
        if nm in fields:
            return by_type(fields[nm], "field")
        if nm in repo.types or nm in imports or nm in _JAVA_LANG:
            return by_type(nm, "static")          # static call on a type name
        return {"callee": callee, "args": args, "kind": "unresolved", "via": "name",
                "target": None, "receiver": nm, "own": False}
    if ot == "field_access":
        rcv = obj.child_by_field_name("object")
        fld = obj.child_by_field_name("field")
        if rcv is not None and rcv.type == "this" and fld is not None and _txt(fld) in fields:
            return by_type(fields[_txt(fld)], "field")
        return {"callee": callee, "args": args, "kind": "unresolved", "via": "field_access",
                "target": None, "receiver": _txt(obj), "own": False}
    # chained call, array access, cast, parenthesised, etc.: receiver type needs inference
    return {"callee": callee, "args": args, "kind": "unresolved", "via": "chain",
            "target": None, "receiver": _txt(obj), "own": False}


def method_shapes(root: str, include_tests: bool = False) -> list[dict]:
    """Raw per-method records for every implemented Java method in the checkout, with
    package-aware call resolution. Same contract as extract.method_shapes plus `package`,
    `type`, and per-edge `kind`/`via`/`target`."""
    files = [f for f in discover.source_files(root, include_tests) if f.lower().endswith(".java")]
    parser = _parser()
    parsed: list = []                   # (rel, src_bytes, root_node, imports, statics, wild, pkg)
    repo = _Repo()
    for rel in files:
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        try:
            tree = parser.parse(data)
        except Exception:
            continue
        rn = tree.root_node
        imports, statics, wildcard = _parse_imports(rn)
        pkg = _package(rn)
        repo.packages.add(pkg)
        repo.types |= _type_names(rn)
        parsed.append((rel, data, rn, imports, statics, wildcard, pkg))

    out: list = []
    for rel, data, rn, imports, statics, wildcard, pkg in parsed:
        # superclass simple type per class node, for `super.x()` classification
        stack = list(rn.children)
        while stack:
            n = stack.pop()
            if n.type in METHOD_DECLS:
                body = n.child_by_field_name("body")
                if body is None:                 # abstract / interface signature: not a method
                    continue
                name_n = n.child_by_field_name("name")
                name = _txt(name_n) if name_n is not None else "?"
                enc = _enclosing_type(n)
                cls_fqn = (pkg + "." if pkg else "") + enc
                params, ptypes = _collect_params(n)
                fields = _collect_fields(n)
                locals_ = _collect_locals(body)
                super_type = None
                anc = n.parent
                while anc is not None:
                    if anc.type in TYPE_DECLS:
                        sc = anc.child_by_field_name("superclass")
                        if sc is not None:
                            for c in sc.children:
                                st = _base_type(c)
                                if st:
                                    super_type = st
                                    break
                        break
                    anc = anc.parent
                calls = [_resolve_call(c, cls_fqn, params, locals_, fields, imports, statics,
                                       wildcard, super_type, repo)
                         for c in _iter_owned(body)
                         if c.type in ("method_invocation", "object_creation_expression")]
                s, e = n.start_point[0] + 1, n.end_point[0] + 1
                body_text = data[n.start_byte:n.end_byte].decode("utf-8", "replace")
                md = _metrics(body, body_text)
                md["params"] = len(ptypes)
                rec = {
                    "file": rel, "package": pkg, "type": enc,
                    "qualname": (cls_fqn + "." + name) if enc else name, "name": name,
                    "start": s, "end": e,
                    **{k: md[k] for k in COMPLEXITY_FIELDS},
                    "param_types": ptypes, "calls": calls,
                    "out_args_own": sum(c["args"] for c in calls if c["kind"] == "internal"),
                    "out_args_ext": sum(c["args"] for c in calls if c["kind"] == "external"),
                    "out_args_unresolved": sum(c["args"] for c in calls
                                               if c["kind"] == "unresolved"),
                    "n_calls_own": sum(1 for c in calls if c["kind"] == "internal"),
                    "n_calls_ext": sum(1 for c in calls if c["kind"] == "external"),
                    "n_calls_unresolved": sum(1 for c in calls if c["kind"] == "unresolved"),
                }
                out.append(rec)
            else:
                stack.extend(n.children)
    return out
