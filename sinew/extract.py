"""Per-method shape extraction for a checkout, in any language, through ONE instrument:
lizard's function list plus the lexical call scan in `lizard_extract`, for every discovered
source file alike. One tool for every language keeps cc, nloc, tokens, call sites and the
forward metrics comparable across a multi-language sample. `languages` gives the per-language
file counts, so the study can report what each project is made of.
"""
from __future__ import annotations

from collections import Counter

from . import discover, lizard_extract


def languages(root: str, include_tests: bool = False) -> dict:
    """language key -> number of source files, for the project's own code."""
    return dict(Counter(discover.language(f)
                        for f in discover.source_files(root, include_tests)))


def method_shapes(root: str, include_tests: bool = False) -> list[dict]:
    """Every implemented method of the checkout, across languages, as raw records in the
    extract_common contract."""
    return lizard_extract.method_shapes(root, include_tests)
