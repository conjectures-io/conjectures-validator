from verifier.references import extract_module_references


def test_extracts_inline_reference(tmp_path):
    source = tmp_path / "Problem.lean"
    source.write_text(
        """/-!
# Problem

*Reference:* [A source](https://example.com/source)
-/
""",
        encoding="utf-8",
    )

    assert extract_module_references(source) == (
        "[A source](https://example.com/source)",
    )


def test_extracts_unbulleted_reference_below_singular_heading(tmp_path):
    source = tmp_path / "Problem.lean"
    source.write_text(
        """/-!
# Problem

*Reference:*
[E. DeLaVina, Written on the Wall II, Conjectures of Graffiti.pc](http://example.com/wowII/)
-/
""",
        encoding="utf-8",
    )

    assert extract_module_references(source) == (
        "[E. DeLaVina, Written on the Wall II, Conjectures of "
        "Graffiti.pc](http://example.com/wowII/)",
    )


def test_extracts_reference_list_and_joins_continuations(tmp_path):
    source = tmp_path / "Problem.lean"
    source.write_text(
        """/-!
# Problem

*References:*
- [Website](https://example.com)
- [AB24] A. Author and B. Author, A long title
  continued on the next line.

Description outside the reference list.
-/
""",
        encoding="utf-8",
    )

    assert extract_module_references(source) == (
        "[Website](https://example.com)",
        "[AB24] A. Author and B. Author, A long title continued on the next line.",
    )


def test_bulleted_list_under_singular_heading_keeps_every_reference(tmp_path):
    source = tmp_path / "Problem.lean"
    source.write_text(
        """/-!
# Problem

*Reference:*
- [First](https://example.com/first)
- [Second](https://example.com/second)
-/
""",
        encoding="utf-8",
    )

    assert extract_module_references(source) == (
        "[First](https://example.com/first)",
        "[Second](https://example.com/second)",
    )


def test_missing_reference_block_is_empty(tmp_path):
    source = tmp_path / "Problem.lean"
    source.write_text("/-! # Problem -/\n", encoding="utf-8")

    assert extract_module_references(source) == ()


def test_pairs_known_bibliography_key_with_stable_link(tmp_path):
    source = tmp_path / "Problem.lean"
    source.write_text(
        """/-!
# Problem

*References:*
- [Er46] Erdős, Paul. On sets of distances of n points.
- [Unknown] A citation not present in the curated link index.
- [Website](https://example.com) Already linked.
-/
""",
        encoding="utf-8",
    )

    assert extract_module_references(source) == (
        "[Er46](https://doi.org/10.2307/2305092) "
        "Erdős, Paul. On sets of distances of n points.",
        "[Unknown] A citation not present in the curated link index.",
        "[Website](https://example.com) Already linked.",
    )
