import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from verifier.references import extract_module_references

pytest.importorskip("fastapi", reason="the public reference parser needs the service extra")

from submission_api.routers.catalog import _reference


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


def test_extracts_plain_reference_heading(tmp_path):
    source = tmp_path / "Problem.lean"
    source.write_text(
        """/-!
# Problem

References:
- [A source](https://example.com/source)
-/
""",
        encoding="utf-8",
    )

    assert extract_module_references(source) == (
        "[A source](https://example.com/source)",
    )


def test_extracts_italic_reference_heading_without_colon(tmp_path):
    source = tmp_path / "Problem.lean"
    source.write_text(
        """/-!
# Problem

*References*
- [A source](https://example.com/source)
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


# --- publication: `submission_api.routers.catalog._reference` -------------------------------
#
# The other half of the same pipeline. `extract_module_references` above produces free-form
# citations that *contain* a Markdown link — see the `[Er46](https://doi.org/…) Erdős, Paul. …`
# case it is asserted to emit — and `_reference` is what splits one into the `{label, url}` pair
# the public API publishes. Tested here rather than in test_api_catalog.py because it is a pure
# function and that module skips itself without a database.

# Each case is a real shape from the pinned catalog, and each one the earlier parser got wrong. It
# anchored on the string's own ends — `startswith("[")`, `endswith(")")` — and split at the first
# `](`, so it only worked when the entire reference was exactly one link.
REFERENCE_CASES = (
    (
        # A trailing year makes the string end in `)`, which used to satisfy the whole-string
        # test. The URL then ran from the first `](` to the end, swallowing the authors.
        "[A Proof of the Kahn–Kalai Conjecture](https://arxiv.org/abs/2203.17207)"
        " by *Jinyoung Park* and *Huy Tuan Pham* (2022)",
        "A Proof of the Kahn–Kalai Conjecture by *Jinyoung Park* and *Huy Tuan Pham* (2022)",
        "https://arxiv.org/abs/2203.17207",
    ),
    (
        # What `_with_reference_link` above emits: a bibliography key linked to its DOI, then the
        # citation, ending in a year. Both failure modes at once.
        "[Er46](https://doi.org/10.2307/2305092) Erdős, Paul. On sets of distances. (1946)",
        "Er46 Erdős, Paul. On sets of distances. (1946)",
        "https://doi.org/10.2307/2305092",
    ),
    (
        # A citation key, then the link, then the citation. Two `[`, and the first is not a link.
        "[Ar25] Archivara, [An Additive Counterexample](https://archivara.org/paper/df04) (2025)",
        "[Ar25] Archivara, An Additive Counterexample (2025)",
        "https://archivara.org/paper/df04",
    ),
    (
        # The link sits mid-string, so the old guard never fired and the whole thing was published
        # as a label with `url: null` — Markdown for the browser to deal with.
        "Leinster, Tom (2001). [arXiv:math/0104012](https://arxiv.org/abs/math/0104012)",
        "Leinster, Tom (2001). arXiv:math/0104012",
        "https://arxiv.org/abs/math/0104012",
    ),
    (
        # Balanced parentheses inside the URL. Truncating here would produce a link that looks
        # usable and 404s, which is worse than no link at all.
        "[Wikipedia, *Diameter (group theory)*]"
        "(https://en.wikipedia.org/wiki/Diameter_(group_theory))",
        "Wikipedia, *Diameter (group theory)*",
        "https://en.wikipedia.org/wiki/Diameter_(group_theory)",
    ),
    (
        # A DOI with two parenthesised groups, one of them mid-path.
        "[B. Reed, J. Graph Theory 27 (1998) 177-212.]"
        "(https://onlinelibrary.wiley.com/doi/10.1002/(SICI)1097-0118(199804)27:4)",
        "B. Reed, J. Graph Theory 27 (1998) 177-212.",
        "https://onlinelibrary.wiley.com/doi/10.1002/(SICI)1097-0118(199804)27:4",
    ),
    (
        # The Markdown title form, written without quotes. The address ends at the whitespace; the
        # rest names the link and is not part of it.
        "[Ben Green's Open Problem 1]"
        "(https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf#section.1 Problem 1)",
        "Ben Green's Open Problem 1",
        "https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf#section.1",
    ),
    (
        # Parenthesised prose where a link goes. There is no address here, so there is no `url` —
        # the old parser published the sentence as one.
        "[Yufei Zhao](Via Personal Communication with Ben Green)",
        "[Yufei Zhao](Via Personal Communication with Ben Green)",
        None,
    ),
    (
        # Two links: the paper and its mirror. One `url` field holds one address, and the leading
        # link is the one the bibliography led with. Both texts survive in the label.
        "[Mills' constant is irrational](https://doi.org/10.1112/mtk.70027) by *Kota Saito*,"
        " [arXiv:2404.19461](https://arxiv.org/abs/2404.19461)",
        "Mills' constant is irrational by *Kota Saito*, arXiv:2404.19461",
        "https://doi.org/10.1112/mtk.70027",
    ),
    (
        # No link at all: 40% of the pool. Verbatim, and honest about having no address.
        "Arora, Sanjeev, and Boaz Barak. Computational complexity. Cambridge, 2009.",
        "Arora, Sanjeev, and Boaz Barak. Computational complexity. Cambridge, 2009.",
        None,
    ),
)


@pytest.mark.parametrize("raw,label,url", REFERENCE_CASES)
def test_a_reference_link_is_found_wherever_the_citation_puts_it(raw, label, url):
    reference = _reference(raw)
    assert reference.label == label
    assert reference.url == url


def test_no_reference_in_the_pinned_catalog_yields_a_malformed_url():
    """A URL with whitespace or unbalanced parentheses in it is a parse failure, not a style.

    Run over the whole checked-in catalog rather than over examples, because this is a parser
    against data nobody here wrote: the next pin rotation can introduce a citation shape no
    hand-written case anticipated, and the failure mode is silent — a link that renders and 404s.
    """
    catalog = json.loads(
        (Path(__file__).resolve().parent.parent / "data" / "catalog.json").read_text(
            encoding="utf-8"
        )
    )
    references = _every_reference(catalog)
    # Guard the guard: a walker that silently found nothing would make this test vacuous.
    assert len(references) > 500, len(references)

    for raw in references:
        url = _reference(raw).url
        if url is None:
            continue
        parsed = urlparse(url)
        assert parsed.scheme in ("http", "https"), raw
        assert parsed.netloc, raw
        assert not any(character.isspace() for character in url), raw
        assert url.count("(") == url.count(")"), raw
        # No label may still carry link syntax the client would have to render itself.
        assert "](" not in _reference(raw).label, raw


def _every_reference(node) -> list[str]:
    """Every `references` entry anywhere in the catalog document."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "references" and isinstance(value, list):
                found.extend(entry for entry in value if isinstance(entry, str))
            else:
                found.extend(_every_reference(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_every_reference(item))
    return found
