"""Tests for _lang, the dominant-language detector.

The case that motivated the rewrite: a C++ project whose `.h` headers outnumber its `.cpp`
files must still read as C++, not C. A plain C project (only .c and .h) must read as C.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coupling_divergence.study import _lang


def _tree(files: dict) -> str:
    d = tempfile.mkdtemp()
    for name in files:
        path = os.path.join(d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(name) else None
        with open(path, "w") as fh:
            fh.write("int x;\n")
    return d


def test_cpp_with_header_plurality_reads_as_cpp():
    # 3 headers, 2 .cpp: headers are the plurality, but a C++ marker is present.
    root = _tree({"a.h": 1, "b.h": 1, "c.h": 1, "a.cpp": 1, "b.cpp": 1})
    assert _lang(root) == "c++", _lang(root)


def test_plain_c_reads_as_c():
    root = _tree({"a.h": 1, "b.h": 1, "a.c": 1})
    assert _lang(root) == "c", _lang(root)


def test_js_family_aggregates():
    # .ts is the single most common extension, but js (.js + .jsx) is the larger language.
    root = _tree({"a.js": 1, "b.js": 1, "c.jsx": 1, "x.ts": 1, "y.ts": 1, "z.ts": 1})
    assert _lang(root) == "js", _lang(root)


def test_empty_tree_is_unknown():
    assert _lang(tempfile.mkdtemp()) == "?"


if __name__ == "__main__":
    ok = True
    for fn in (test_cpp_with_header_plurality_reads_as_cpp, test_plain_c_reads_as_c,
               test_js_family_aggregates, test_empty_tree_is_unknown):
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
        except AssertionError as e:
            ok = False
            print(f"  [FAIL] {fn.__name__}: {e}")
    print("OVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
